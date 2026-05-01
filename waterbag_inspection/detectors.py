from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

import cv2
import numpy as np

from .config import ModelConfig, PatchConfig
from .schemas import DetectionBox


PRIMARY_MARKERS = {"ng", "defect", "abnormal", "anomaly", "dirty", "smudge"}
PATCH_MARKERS = {"patch", "micro", "tiny", "pinhole", "spot"}


def image_to_base64(image) -> str:
    ok, buffer = cv2.imencode(".jpg", image)
    if not ok:
        return ""
    return base64.b64encode(buffer).decode("utf-8")


def annotate_boxes(image, boxes: Iterable[DetectionBox], color: tuple[int, int, int]) -> None:
    for box in boxes:
        cv2.rectangle(image, (box.x1, box.y1), (box.x2, box.y2), color, 3)
        text = f"{box.label} {box.confidence:.2f}"
        cv2.putText(
            image,
            text,
            (box.x1, max(box.y1 - 10, 0)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            color,
            2,
            cv2.LINE_AA,
        )


def resolve_label(names, cls_id: int) -> str:
    if isinstance(names, dict):
        return str(names.get(cls_id, cls_id))
    if isinstance(names, list) and 0 <= cls_id < len(names):
        return str(names[cls_id])
    return str(cls_id)


class BaseDetector:
    def detect(self, image_path: str) -> tuple[object, list[DetectionBox]]:
        raise NotImplementedError

    def detect_multilight(
        self,
        light_image_paths: Mapping[str, str],
    ) -> tuple[object, list[DetectionBox]]:
        raise NotImplementedError(
            f"{type(self).__name__} does not support multi-light detection."
        )

    def detect_patches(self, image_path: str, patch_config: PatchConfig) -> tuple[object, list[DetectionBox]]:
        raise NotImplementedError


class MockDetector(BaseDetector):
    def __init__(self, label: str = "anomaly"):
        self.label = label

    @staticmethod
    def _load_image(image_path: str):
        image = cv2.imread(image_path)
        if image is None:
            raise FileNotFoundError(f"Unable to read image: {image_path}")
        return image

    def detect(self, image_path: str) -> tuple[object, list[DetectionBox]]:
        image = self._load_image(image_path)
        stem = Path(image_path).stem.lower()
        height, width = image.shape[:2]

        boxes: list[DetectionBox] = []
        if any(marker in stem for marker in PRIMARY_MARKERS):
            boxes.append(
                DetectionBox(
                    x1=int(width * 0.2),
                    y1=int(height * 0.2),
                    x2=int(width * 0.7),
                    y2=int(height * 0.7),
                    label=self.label,
                    confidence=0.92,
                )
            )

        annotated = image.copy()
        annotate_boxes(annotated, boxes, (255, 120, 0))
        return annotated, boxes

    def detect_multilight(
        self,
        light_image_paths: Mapping[str, str],
    ) -> tuple[object, list[DetectionBox]]:
        primary_path = next(iter(light_image_paths.values()))
        image = self._load_image(primary_path)
        stems = " ".join(Path(path).stem.lower() for path in light_image_paths.values())
        height, width = image.shape[:2]

        boxes: list[DetectionBox] = []
        if any(marker in stems for marker in PRIMARY_MARKERS | PATCH_MARKERS):
            boxes.append(
                DetectionBox(
                    x1=int(width * 0.2),
                    y1=int(height * 0.2),
                    x2=int(width * 0.7),
                    y2=int(height * 0.7),
                    label=self.label,
                    confidence=0.93,
                )
            )

        annotated = image.copy()
        annotate_boxes(annotated, boxes, (0, 160, 80))
        return annotated, boxes

    def detect_patches(self, image_path: str, patch_config: PatchConfig) -> tuple[object, list[DetectionBox]]:
        image = self._load_image(image_path)
        stem = Path(image_path).stem.lower()
        height, width = image.shape[:2]

        boxes: list[DetectionBox] = []
        if any(marker in stem for marker in PATCH_MARKERS):
            patch_width = width // max(patch_config.horizontal, 1)
            patch_height = height // max(patch_config.vertical, 1)
            boxes.append(
                DetectionBox(
                    x1=int(patch_width * 2.2),
                    y1=int(patch_height * 1.4),
                    x2=int(patch_width * 2.8),
                    y2=int(patch_height * 2.0),
                    label=self.label,
                    confidence=0.87,
                )
            )

        annotated = image.copy()
        annotate_boxes(annotated, boxes, (0, 70, 255))
        return annotated, boxes


class UltralyticsDetector(BaseDetector):
    def __init__(self, config: ModelConfig):
        self.config = config
        self._temporary_model_path: str | None = None
        self.model = self._load_model()

    def _resolve_model_path(self) -> str:
        if self.config.weights_path:
            return self.config.weights_path
        if not self.config.encrypted_path or not self.config.key_path:
            raise ValueError("Encrypted model requires both encrypted_path and key_path.")

        from cryptography.fernet import Fernet

        encrypted_path = Path(self.config.encrypted_path)
        key_path = Path(self.config.key_path)
        with key_path.open("rb") as handle:
            key = handle.read()
        with encrypted_path.open("rb") as handle:
            encrypted = handle.read()
        decrypted = Fernet(key).decrypt(encrypted)

        temp_file = tempfile.NamedTemporaryFile(prefix="waterbag_model_", suffix=".pt", delete=False)
        temp_file.write(decrypted)
        temp_file.flush()
        temp_file.close()
        self._temporary_model_path = temp_file.name
        return temp_file.name

    def _load_model(self):
        from ultralytics import YOLO

        model_path = self._resolve_model_path()
        return YOLO(model_path)

    def __del__(self):
        if self._temporary_model_path:
            try:
                Path(self._temporary_model_path).unlink(missing_ok=True)
            except OSError:
                pass

    def _predict(self, image, conf: float, iou: float):
        results = self.model.predict(
            source=image,
            imgsz=self.config.imgsz,
            conf=conf,
            iou=iou,
            device=self.config.device,
            verbose=False,
        )
        result = results[0]
        boxes: list[DetectionBox] = []
        if result.boxes is None:
            return boxes
        for prediction in result.boxes:
            x1, y1, x2, y2 = map(int, prediction.xyxy[0].tolist())
            cls_id = int(prediction.cls[0])
            confidence = float(prediction.conf[0])
            label = resolve_label(self.model.names, cls_id)
            boxes.append(
                DetectionBox(
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    label=label,
                    confidence=confidence,
                )
            )
        return boxes

    def detect(self, image_path: str) -> tuple[object, list[DetectionBox]]:
        image = cv2.imread(image_path)
        if image is None:
            raise FileNotFoundError(f"Unable to read image: {image_path}")
        boxes = self._predict(image, self.config.conf_thres, self.config.iou_thres)
        annotated = image.copy()
        annotate_boxes(annotated, boxes, (255, 120, 0))
        return annotated, boxes

    def detect_patches(self, image_path: str, patch_config: PatchConfig) -> tuple[object, list[DetectionBox]]:
        image = cv2.imread(image_path)
        if image is None:
            raise FileNotFoundError(f"Unable to read image: {image_path}")

        height, width = image.shape[:2]
        patch_width = width // max(patch_config.horizontal, 1)
        patch_height = height // max(patch_config.vertical, 1)

        boxes: list[DetectionBox] = []
        annotated = image.copy()
        patch_vis_dir = Path(patch_config.visualization_dir)
        if patch_config.save_visualizations:
            patch_vis_dir.mkdir(parents=True, exist_ok=True)

        for row in range(patch_config.vertical):
            for col in range(patch_config.horizontal):
                left = col * patch_width
                top = row * patch_height
                right = (col + 1) * patch_width if col < patch_config.horizontal - 1 else width
                bottom = (row + 1) * patch_height if row < patch_config.vertical - 1 else height
                patch = image[top:bottom, left:right]
                patch_boxes = self._predict(patch, patch_config.conf_thres, patch_config.iou_thres)
                if not patch_boxes:
                    continue

                if patch_config.save_visualizations:
                    patch_image = patch.copy()
                    annotate_boxes(patch_image, patch_boxes, (0, 70, 255))
                    patch_file = patch_vis_dir / f"{Path(image_path).stem}_r{row}_c{col}.jpg"
                    cv2.imwrite(str(patch_file), patch_image)

                for patch_box in patch_boxes:
                    boxes.append(
                        DetectionBox(
                            x1=patch_box.x1 + left,
                            y1=patch_box.y1 + top,
                            x2=patch_box.x2 + left,
                            y2=patch_box.y2 + top,
                            label=patch_box.label,
                            confidence=patch_box.confidence,
                        )
                    )

        annotate_boxes(annotated, boxes, (0, 70, 255))
        return annotated, boxes


def _torch_device(device: str):
    import torch

    if device.isdigit():
        return torch.device(f"cuda:{device}" if torch.cuda.is_available() else "cpu")
    if device.startswith("cuda") and not torch.cuda.is_available():
        return torch.device("cpu")
    return torch.device(device)


def _letterbox(image: np.ndarray, size: int) -> tuple[np.ndarray, float, tuple[float, float]]:
    height, width = image.shape[:2]
    ratio = min(size / height, size / width)
    new_width = int(round(width * ratio))
    new_height = int(round(height * ratio))
    resized = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    pad_x = (size - new_width) / 2
    pad_y = (size - new_height) / 2
    left = int(round(pad_x - 0.1))
    top = int(round(pad_y - 0.1))
    canvas[top:top + new_height, left:left + new_width] = resized
    return canvas, ratio, (float(left), float(top))


def _scale_xyxy_to_original(
    boxes: list[DetectionBox],
    *,
    ratio: float,
    pad: tuple[float, float],
    width: int,
    height: int,
) -> list[DetectionBox]:
    scaled = []
    pad_x, pad_y = pad
    for box in boxes:
        x1 = int(round((box.x1 - pad_x) / ratio))
        y1 = int(round((box.y1 - pad_y) / ratio))
        x2 = int(round((box.x2 - pad_x) / ratio))
        y2 = int(round((box.y2 - pad_y) / ratio))
        scaled.append(
            DetectionBox(
                x1=max(0, min(width - 1, x1)),
                y1=max(0, min(height - 1, y1)),
                x2=max(0, min(width - 1, x2)),
                y2=max(0, min(height - 1, y2)),
                label=box.label,
                confidence=box.confidence,
            )
        )
    return scaled


def _nms_indices(boxes, scores, iou_thres: float):
    import torch

    if boxes.numel() == 0:
        return torch.empty((0,), dtype=torch.long, device=boxes.device)
    x1, y1, x2, y2 = boxes.unbind(dim=1)
    areas = (x2 - x1).clamp(min=0) * (y2 - y1).clamp(min=0)
    order = scores.argsort(descending=True)
    keep = []
    while order.numel() > 0:
        index = order[0]
        keep.append(index)
        if order.numel() == 1:
            break
        rest = order[1:]
        xx1 = torch.maximum(x1[index], x1[rest])
        yy1 = torch.maximum(y1[index], y1[rest])
        xx2 = torch.minimum(x2[index], x2[rest])
        yy2 = torch.minimum(y2[index], y2[rest])
        inter = (xx2 - xx1).clamp(min=0) * (yy2 - yy1).clamp(min=0)
        union = areas[index] + areas[rest] - inter
        iou = inter / union.clamp(min=1e-6)
        order = rest[iou <= iou_thres]
    return torch.stack(keep) if keep else torch.empty((0,), dtype=torch.long, device=boxes.device)


class MultiLightTorchDetector(BaseDetector):
    def __init__(self, config: ModelConfig):
        self.config = config
        self.light_order = tuple(config.light_order)
        self.primary_light = (
            config.primary_light
            if config.primary_light in self.light_order
            else self.light_order[0]
        )
        self.device = _torch_device(config.device)
        self.model = self._load_model()

    def _load_model(self):
        import torch
        from torch import nn

        if not self.config.weights_path:
            raise ValueError("multilight_torch backend requires weights_path.")
        try:
            model = torch.jit.load(self.config.weights_path, map_location=self.device)
        except RuntimeError:
            checkpoint = torch.load(self.config.weights_path, map_location=self.device)
            if isinstance(checkpoint, nn.Module):
                model = checkpoint
            elif isinstance(checkpoint, dict) and isinstance(checkpoint.get("model"), nn.Module):
                model = checkpoint["model"]
            else:
                raise ValueError(
                    "Unsupported multilight_torch checkpoint. Use TorchScript or "
                    "a checkpoint containing an nn.Module under 'model'."
                )
        model.to(self.device)
        model.eval()
        return model

    def _load_multilight_tensor(
        self,
        light_image_paths: Mapping[str, str],
    ) -> tuple[Any, np.ndarray, float, tuple[float, float]]:
        import torch

        missing = [name for name in self.light_order if name not in light_image_paths]
        if missing:
            raise ValueError(f"Missing multi-light image(s): {', '.join(missing)}")

        primary = cv2.imread(light_image_paths[self.primary_light])
        if primary is None:
            raise FileNotFoundError(
                f"Unable to read image: {light_image_paths[self.primary_light]}"
            )

        tensors = []
        ratio = 1.0
        pad = (0.0, 0.0)
        expected_shape = primary.shape[:2]
        for index, light_name in enumerate(self.light_order):
            image = cv2.imread(light_image_paths[light_name])
            if image is None:
                raise FileNotFoundError(
                    f"Unable to read image: {light_image_paths[light_name]}"
                )
            if image.shape[:2] != expected_shape:
                raise ValueError(
                    "All multi-light images must have the same height and width before inference."
                )
            prepared, current_ratio, current_pad = _letterbox(image, self.config.imgsz)
            if index == 0:
                ratio, pad = current_ratio, current_pad
            rgb = cv2.cvtColor(prepared, cv2.COLOR_BGR2RGB)
            tensor = torch.from_numpy(rgb).permute(2, 0, 1).float() / 255.0
            tensors.append(tensor)

        stacked = torch.stack(tensors, dim=0).unsqueeze(0).to(self.device)
        if self.config.input_format.lower() in {"bchw", "b9hw", "stacked", "channel_stacked"}:
            stacked = stacked.reshape(
                1,
                len(self.light_order) * 3,
                self.config.imgsz,
                self.config.imgsz,
            )
        return stacked, primary, ratio, pad

    def _class_label(self, cls_id: int) -> str:
        if 0 <= cls_id < len(self.config.class_names):
            return self.config.class_names[cls_id]
        return str(cls_id)

    def _boxes_from_dict(self, item: Mapping[str, Any]) -> list[DetectionBox]:
        boxes = item.get("boxes")
        if boxes is None:
            return []
        scores = item.get("scores")
        labels = item.get("labels")
        return self._boxes_from_tensor_like(boxes, scores=scores, labels=labels)

    def _boxes_from_tensor_like(self, boxes, *, scores=None, labels=None) -> list[DetectionBox]:
        import torch

        boxes_t = torch.as_tensor(boxes, device=self.device).float()
        if boxes_t.ndim == 3:
            boxes_t = boxes_t[0]
        if boxes_t.numel() == 0:
            return []
        if scores is None and boxes_t.shape[-1] >= 6:
            scores_t = boxes_t[:, 4]
            labels_t = boxes_t[:, 5].long()
            boxes_t = boxes_t[:, :4]
        else:
            if scores is None:
                raise ValueError("Model output with boxes only must also provide scores.")
            scores_t = torch.as_tensor(scores, device=self.device).float()
            labels_t = (
                torch.as_tensor(labels, device=self.device).long()
                if labels is not None
                else torch.zeros_like(scores_t, dtype=torch.long)
            )

        mask = scores_t >= self.config.conf_thres
        boxes_t = boxes_t[mask]
        scores_t = scores_t[mask]
        labels_t = labels_t[mask]
        keep = _nms_indices(boxes_t, scores_t, self.config.iou_thres)
        boxes_t = boxes_t[keep].detach().cpu()
        scores_t = scores_t[keep].detach().cpu()
        labels_t = labels_t[keep].detach().cpu()
        return [
            DetectionBox(
                x1=int(row[0].item()),
                y1=int(row[1].item()),
                x2=int(row[2].item()),
                y2=int(row[3].item()),
                label=self._class_label(int(label.item())),
                confidence=float(score.item()),
            )
            for row, score, label in zip(boxes_t, scores_t, labels_t)
        ]

    def _boxes_from_yolo_tensor(self, output) -> list[DetectionBox]:
        import torch

        pred = output
        if isinstance(output, (list, tuple)):
            if output and isinstance(output[0], Mapping):
                return self._boxes_from_dict(output[0])
            tensor_items = [item for item in output if torch.is_tensor(item)]
            pred = tensor_items[0] if tensor_items else output[0]
        if isinstance(pred, (list, tuple)) and pred and isinstance(pred[0], Mapping):
            return self._boxes_from_dict(pred[0])
        if isinstance(pred, Mapping):
            return self._boxes_from_dict(pred)

        tensor = torch.as_tensor(pred, device=self.device).float()
        if tensor.ndim == 3:
            tensor = tensor[0]
        if tensor.numel() == 0:
            return []
        if tensor.shape[-1] == 6 or self.config.output_format == "xyxy_conf_cls":
            return self._boxes_from_tensor_like(tensor)
        if tensor.shape[-1] < 6:
            raise ValueError(f"Unsupported model output shape: {tuple(tensor.shape)}")

        xywh = tensor[:, :4]
        objectness = tensor[:, 4]
        class_scores, labels = tensor[:, 5:].max(dim=1)
        scores = objectness * class_scores
        xyxy = torch.empty_like(xywh)
        xyxy[:, 0] = xywh[:, 0] - xywh[:, 2] / 2
        xyxy[:, 1] = xywh[:, 1] - xywh[:, 3] / 2
        xyxy[:, 2] = xywh[:, 0] + xywh[:, 2] / 2
        xyxy[:, 3] = xywh[:, 1] + xywh[:, 3] / 2
        if self.config.output_normalized:
            xyxy = xyxy * float(self.config.imgsz)
        return self._boxes_from_tensor_like(xyxy, scores=scores, labels=labels)

    def detect_multilight(
        self,
        light_image_paths: Mapping[str, str],
    ) -> tuple[object, list[DetectionBox]]:
        import torch

        input_tensor, primary, ratio, pad = self._load_multilight_tensor(light_image_paths)
        with torch.no_grad():
            output = self.model(input_tensor)
        boxes = self._boxes_from_yolo_tensor(output)
        height, width = primary.shape[:2]
        boxes = _scale_xyxy_to_original(boxes, ratio=ratio, pad=pad, width=width, height=height)
        annotated = primary.copy()
        annotate_boxes(annotated, boxes, (0, 160, 80))
        return annotated, boxes

    def detect(self, image_path: str) -> tuple[object, list[DetectionBox]]:
        raise NotImplementedError("MultiLightTorchDetector requires detect_multilight().")

    def detect_patches(
        self,
        image_path: str,
        patch_config: PatchConfig,
    ) -> tuple[object, list[DetectionBox]]:
        raise NotImplementedError("MultiLightTorchDetector does not support patch detection.")


def build_detector(config: ModelConfig) -> BaseDetector:
    if config.backend == "mock":
        return MockDetector()
    if config.backend == "ultralytics":
        return UltralyticsDetector(config)
    if config.backend == "multilight_torch":
        return MultiLightTorchDetector(config)
    raise ValueError(f"Unsupported detector backend: {config.backend}")
