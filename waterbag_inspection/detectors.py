from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import Iterable

import cv2

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


def build_detector(config: ModelConfig) -> BaseDetector:
    if config.backend == "mock":
        return MockDetector()
    if config.backend == "ultralytics":
        return UltralyticsDetector(config)
    raise ValueError(f"Unsupported detector backend: {config.backend}")
