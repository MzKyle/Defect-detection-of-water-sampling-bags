from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import torch
from PIL import Image
from torch import Tensor, nn


LIGHT_ORDER = ("backlight", "darkfield", "polarized")


@dataclass
class DetectionResult:
    boxes: Tensor
    scores: Tensor
    classes: Tensor
    stage: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def empty(cls, stage: str = "", metadata: dict[str, Any] | None = None) -> "DetectionResult":
        return cls(
            boxes=torch.empty((0, 4), dtype=torch.float32),
            scores=torch.empty((0,), dtype=torch.float32),
            classes=torch.empty((0,), dtype=torch.long),
            stage=stage,
            metadata=metadata or {},
        )

    @property
    def detected_defect(self) -> bool:
        return int(self.boxes.shape[0]) > 0

    @property
    def num_boxes(self) -> int:
        return int(self.boxes.shape[0])

    def cpu(self) -> "DetectionResult":
        return DetectionResult(
            boxes=self.boxes.detach().cpu(),
            scores=self.scores.detach().cpu(),
            classes=self.classes.detach().cpu(),
            stage=self.stage,
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        result = self.cpu()
        return {
            "stage": result.stage,
            "boxes": result.boxes.tolist(),
            "scores": result.scores.tolist(),
            "classes": result.classes.tolist(),
            "num_boxes": result.num_boxes,
            "metadata": result.metadata,
        }


@dataclass(frozen=True)
class BlockMetadata:
    sample_id: str
    block_index: int
    xyxy: tuple[int, int, int, int]
    original_size: tuple[int, int]
    model_input_size: tuple[int, int]
    resize_ratio: tuple[float, float]
    pad: tuple[int, int]
    light_order: tuple[str, ...] = LIGHT_ORDER

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "block_index": self.block_index,
            "xyxy": list(self.xyxy),
            "original_size": list(self.original_size),
            "model_input_size": list(self.model_input_size),
            "resize_ratio": list(self.resize_ratio),
            "pad": list(self.pad),
            "light_order": list(self.light_order),
        }


@dataclass
class SampleManifest:
    sample_id: str
    lights: dict[str, Path]
    raw: dict[str, Any] = field(default_factory=dict)
    manifest_path: Path | None = None


@dataclass
class PipelinePrediction:
    sample_id: str
    stage_source: str
    final_result: DetectionResult
    coarse_result: DetectionResult
    stage2_result: DetectionResult
    coarse_detected: bool
    timings_ms: dict[str, float]
    block_count: int
    positive_block_count: int
    block_metadata: list[BlockMetadata] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "stage_source": self.stage_source,
            "coarse_detected": self.coarse_detected,
            "coarse_num_boxes": self.coarse_result.num_boxes,
            "block_count": self.block_count,
            "positive_block_count": self.positive_block_count,
            "final_num_boxes": self.final_result.num_boxes,
            "timings_ms": self.timings_ms,
            "final_result": self.final_result.to_dict(),
            "coarse_result": self.coarse_result.to_dict(),
            "stage2_result": self.stage2_result.to_dict(),
            "block_metadata": [item.to_dict() for item in self.block_metadata],
        }

    def benchmark_row(self, metrics: Mapping[str, float | None] | None = None) -> dict[str, Any]:
        metrics = metrics or {}
        return {
            "sample_id": self.sample_id,
            "coarse_detected": self.coarse_detected,
            "coarse_num_boxes": self.coarse_result.num_boxes,
            "block_count": self.block_count,
            "final_num_boxes": self.final_result.num_boxes,
            "coarse_time_ms": _round_ms(self.timings_ms.get("coarse_time_ms", 0.0)),
            "tiling_time_ms": _round_ms(self.timings_ms.get("tiling_time_ms", 0.0)),
            "fine_inference_time_ms": _round_ms(self.timings_ms.get("fine_inference_time_ms", 0.0)),
            "mapping_time_ms": _round_ms(self.timings_ms.get("mapping_time_ms", 0.0)),
            "nms_time_ms": _round_ms(self.timings_ms.get("nms_time_ms", 0.0)),
            "total_time_ms": _round_ms(self.timings_ms.get("total_time_ms", 0.0)),
            "precision": metrics.get("precision"),
            "recall": metrics.get("recall"),
            "map50": metrics.get("map50"),
            "map50_95": metrics.get("map50_95"),
        }


def _round_ms(value: float) -> float:
    return round(float(value), 3)


def _now() -> float:
    return time.perf_counter()


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _sync_if_cuda(device: str | torch.device) -> None:
    text = str(device)
    if text.startswith("cuda") and torch.cuda.is_available():
        torch.cuda.synchronize(torch.device(device))


def _device_from_arg(device: str | int | torch.device) -> torch.device:
    if isinstance(device, torch.device):
        return device
    text = str(device)
    if text.lower() == "cpu":
        return torch.device("cpu")
    first = text.split(",", maxsplit=1)[0]
    if first.isdigit() and torch.cuda.is_available():
        return torch.device(f"cuda:{first}")
    if text.startswith("cuda") and torch.cuda.is_available():
        return torch.device(text)
    return torch.device("cpu")


def _ensure_rgb(image: Image.Image) -> Image.Image:
    return image if image.mode == "RGB" else image.convert("RGB")


def _image_to_tensor(image: Image.Image) -> Tensor:
    array = np.asarray(_ensure_rgb(image), dtype=np.float32) / 255.0
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("expected an RGB image")
    return torch.from_numpy(array).permute(2, 0, 1).contiguous()


def _letterbox_to_tensor(
    image: Image.Image,
    target_size: int,
    pad_to_square: bool = True,
) -> tuple[Tensor, tuple[float, float], tuple[int, int]]:
    image = _ensure_rgb(image)
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError("cannot preprocess an empty image")
    if target_size <= 0:
        raise ValueError("target_size must be positive")

    if not pad_to_square:
        resized = image.resize((target_size, target_size), Image.Resampling.BILINEAR)
        return _image_to_tensor(resized), (target_size / width, target_size / height), (0, 0)

    ratio = min(target_size / width, target_size / height)
    resized_width = max(1, int(round(width * ratio)))
    resized_height = max(1, int(round(height * ratio)))
    resized = image.resize((resized_width, resized_height), Image.Resampling.BILINEAR)

    pad_left = max((target_size - resized_width) // 2, 0)
    pad_top = max((target_size - resized_height) // 2, 0)
    canvas = Image.new("RGB", (target_size, target_size), (114, 114, 114))
    canvas.paste(resized, (pad_left, pad_top))
    return _image_to_tensor(canvas), (ratio, ratio), (pad_left, pad_top)


def _axis_starts(length: int, block_size: int, stride: int) -> list[int]:
    if length <= block_size:
        return [0]
    last = length - block_size
    starts = list(range(0, last + 1, stride))
    if not starts or starts[-1] != last:
        starts.append(last)
    return starts


class CoarseDetector:
    """Stage 1 full-image detector using the existing YOLO-style boxes-not-empty rule."""

    def __init__(
        self,
        model_path: str | Path,
        imgsz: int = 640,
        device: str = "0",
        conf: float = 0.25,
        iou: float = 0.7,
        max_det: int = 300,
    ) -> None:
        from ultralytics import YOLO

        self.model_path = str(model_path)
        self.model = YOLO(self.model_path)
        self.imgsz = imgsz
        self.device = device
        self.conf = conf
        self.iou = iou
        self.max_det = max_det

    @staticmethod
    def detected_defect(result: DetectionResult) -> bool:
        return result.detected_defect

    def predict(self, image_path: str | Path, sample_id: str | None = None) -> DetectionResult:
        results = self.model.predict(
            source=str(image_path),
            imgsz=self.imgsz,
            device=self.device,
            conf=self.conf,
            iou=self.iou,
            max_det=self.max_det,
            verbose=False,
        )
        if not results:
            return DetectionResult.empty(stage="stage1", metadata={"sample_id": sample_id})

        result = results[0]
        boxes_obj = getattr(result, "boxes", None)
        if boxes_obj is None or getattr(boxes_obj, "xyxy", None) is None:
            return DetectionResult.empty(stage="stage1", metadata={"sample_id": sample_id})

        boxes = boxes_obj.xyxy.detach().cpu().float()
        scores = boxes_obj.conf.detach().cpu().float()
        classes = boxes_obj.cls.detach().cpu().long()
        return DetectionResult(
            boxes=boxes,
            scores=scores,
            classes=classes,
            stage="stage1",
            metadata={"sample_id": sample_id, "source_path": str(image_path)},
        )


class MultiLightBlockTiler:
    """Synchronously tile backlight/darkfield/polarized images into one 5D batch."""

    def __init__(
        self,
        block_size: int = 512,
        stride: int | None = None,
        overlap: float = 0.2,
        min_block_size: int = 1,
        pad_to_square: bool = True,
        max_blocks: int | None = None,
        model_input_size: int | None = None,
        light_order: Sequence[str] = LIGHT_ORDER,
    ) -> None:
        if block_size <= 0:
            raise ValueError("block_size must be positive")
        if not 0 <= overlap < 1:
            raise ValueError("overlap must be in [0, 1)")
        self.block_size = int(block_size)
        self.overlap = float(overlap)
        self.stride = int(stride) if stride is not None else max(1, int(round(block_size * (1.0 - overlap))))
        self.min_block_size = int(min_block_size)
        self.pad_to_square = bool(pad_to_square)
        self.max_blocks = max_blocks
        self.model_input_size = int(model_input_size or block_size)
        self.light_order = tuple(light_order)

    def tile_paths(
        self,
        light_paths: Mapping[str, str | Path],
        sample_id: str,
    ) -> tuple[Tensor, list[BlockMetadata]]:
        images: dict[str, Image.Image] = {}
        for light in self.light_order:
            if light not in light_paths:
                raise KeyError(f"manifest is missing required light: {light}")
            with Image.open(light_paths[light]) as image:
                images[light] = image.convert("RGB")
        return self.tile_images(images, sample_id)

    def tile_images(
        self,
        images: Mapping[str, Image.Image],
        sample_id: str,
    ) -> tuple[Tensor, list[BlockMetadata]]:
        for light in self.light_order:
            if light not in images:
                raise KeyError(f"missing required light image: {light}")

        sizes = {light: _ensure_rgb(images[light]).size for light in self.light_order}
        unique_sizes = set(sizes.values())
        if len(unique_sizes) != 1:
            raise ValueError(f"multi-light images must have identical sizes, got {sizes}")

        width, height = next(iter(unique_sizes))
        x_starts = _axis_starts(width, self.block_size, self.stride)
        y_starts = _axis_starts(height, self.block_size, self.stride)

        blocks: list[Tensor] = []
        metadata: list[BlockMetadata] = []
        block_index = 0
        for y1 in y_starts:
            for x1 in x_starts:
                if self.max_blocks is not None and block_index >= self.max_blocks:
                    break
                x2 = min(x1 + self.block_size, width)
                y2 = min(y1 + self.block_size, height)
                if x2 <= x1 or y2 <= y1:
                    continue
                if (x2 - x1) < self.min_block_size or (y2 - y1) < self.min_block_size:
                    continue

                light_tensors: list[Tensor] = []
                ratios: list[tuple[float, float]] = []
                pads: list[tuple[int, int]] = []
                for light in self.light_order:
                    crop = _ensure_rgb(images[light]).crop((x1, y1, x2, y2))
                    tensor, ratio, pad = _letterbox_to_tensor(
                        crop,
                        self.model_input_size,
                        pad_to_square=self.pad_to_square,
                    )
                    light_tensors.append(tensor)
                    ratios.append(ratio)
                    pads.append(pad)

                if len(set(ratios)) != 1 or len(set(pads)) != 1:
                    raise RuntimeError("light transforms diverged for a synchronized block")

                blocks.append(torch.stack(light_tensors, dim=0))
                metadata.append(
                    BlockMetadata(
                        sample_id=sample_id,
                        block_index=block_index,
                        xyxy=(x1, y1, x2, y2),
                        original_size=(width, height),
                        model_input_size=(self.model_input_size, self.model_input_size),
                        resize_ratio=ratios[0],
                        pad=pads[0],
                        light_order=self.light_order,
                    )
                )
                block_index += 1
            if self.max_blocks is not None and block_index >= self.max_blocks:
                break

        if not blocks:
            raise RuntimeError("tiling produced no blocks")
        return torch.stack(blocks, dim=0), metadata


class FineMultiLightDetector:
    """Stage 2 batched detector for [B, 3, 3, H, W] multi-light blocks."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        imgsz: int = 512,
        device: str | int | torch.device = "0",
        conf: float = 0.25,
        batch_size: int = 0,
        output_format: str = "auto",
        model: nn.Module | None = None,
    ) -> None:
        self.imgsz = int(imgsz)
        self.device = _device_from_arg(device)
        self.conf = float(conf)
        self.batch_size = int(batch_size)
        self.output_format = output_format
        self.model_path = Path(model_path) if model_path is not None else None
        self.model = model if model is not None else self._load_model(self.model_path)
        self.model.to(self.device)
        self.model.eval()

    def _load_model(self, model_path: Path | None) -> nn.Module:
        if model_path is None:
            raise ValueError("FineMultiLightDetector requires model_path or model")
        if not model_path.exists():
            raise FileNotFoundError(f"fine model not found: {model_path}")

        try:
            return torch.jit.load(str(model_path), map_location=self.device)
        except Exception:
            pass

        try:
            checkpoint = torch.load(model_path, map_location=self.device, weights_only=False)
        except TypeError:
            checkpoint = torch.load(model_path, map_location=self.device)

        if isinstance(checkpoint, nn.Module):
            return checkpoint
        if isinstance(checkpoint, Mapping):
            for key in ("ema", "model"):
                candidate = checkpoint.get(key)
                if isinstance(candidate, nn.Module):
                    return candidate.float()
            if "state_dict" in checkpoint:
                raise RuntimeError(
                    "fine model checkpoint is a state_dict; instantiate MultiLightFineYOLO "
                    "and pass the module before loading weights"
                )
        raise RuntimeError(
            "unsupported fine model checkpoint. Expected a TorchScript module or "
            "a PyTorch checkpoint containing an nn.Module that accepts [B, 3, 3, H, W]."
        )

    def predict(self, blocks: Tensor) -> list[DetectionResult]:
        if blocks.ndim != 5 or blocks.shape[1] != 3 or blocks.shape[2] != 3:
            raise ValueError("fine detector input must be [B, 3, 3, H, W]")
        if blocks.shape[0] == 0:
            return []

        chunks = [blocks]
        if self.batch_size > 0 and self.batch_size < blocks.shape[0]:
            chunks = list(torch.split(blocks, self.batch_size, dim=0))

        decoded: list[DetectionResult] = []
        with torch.inference_mode():
            for chunk in chunks:
                input_tensor = chunk.to(self.device, non_blocking=True).float()
                _sync_if_cuda(self.device)
                output = self.model(input_tensor)
                _sync_if_cuda(self.device)
                decoded.extend(self._decode_output(output, batch_size=int(chunk.shape[0])))
        return decoded

    def _decode_output(self, output: Any, batch_size: int) -> list[DetectionResult]:
        output = self._select_detection_output(output)

        if isinstance(output, Tensor):
            return self._decode_tensor(output.detach(), batch_size)

        if isinstance(output, (list, tuple)):
            if len(output) == batch_size and all(self._is_per_image_output(item) for item in output):
                return [self._decode_per_image(item) for item in output]
            if len(output) == 1:
                return self._decode_output(output[0], batch_size)

        if self._is_per_image_output(output) and batch_size == 1:
            return [self._decode_per_image(output)]

        raise RuntimeError(f"unsupported fine model output type: {type(output)!r}")

    def _select_detection_output(self, output: Any) -> Any:
        if isinstance(output, tuple) and output:
            return self._select_detection_output(output[0])
        return output

    def _is_per_image_output(self, item: Any) -> bool:
        if isinstance(item, Tensor):
            return item.ndim == 2
        if isinstance(item, Mapping):
            return "boxes" in item
        boxes = getattr(item, "boxes", None)
        return boxes is not None

    def _decode_per_image(self, item: Any) -> DetectionResult:
        if isinstance(item, Tensor):
            return self._decode_prediction_matrix(item.detach().float().cpu())
        if isinstance(item, Mapping):
            boxes = torch.as_tensor(item.get("boxes", []), dtype=torch.float32)
            scores = torch.as_tensor(item.get("scores", item.get("conf", [])), dtype=torch.float32)
            classes = torch.as_tensor(item.get("classes", item.get("labels", [])), dtype=torch.long)
        else:
            boxes_obj = item.boxes
            boxes = boxes_obj.xyxy.detach().cpu().float()
            scores = boxes_obj.conf.detach().cpu().float()
            classes = boxes_obj.cls.detach().cpu().long()
        return self._filter_result(DetectionResult(boxes, scores, classes, stage="stage2_block"))

    def _decode_tensor(self, predictions: Tensor, batch_size: int) -> list[DetectionResult]:
        predictions = predictions.detach().float().cpu()
        if predictions.ndim == 2 and batch_size == 1:
            predictions = predictions.unsqueeze(0)
        if predictions.ndim != 3:
            raise RuntimeError(f"unsupported tensor output shape: {tuple(predictions.shape)}")

        if predictions.shape[0] != batch_size:
            raise RuntimeError(
                f"fine model output batch {predictions.shape[0]} does not match input batch {batch_size}"
            )

        if predictions.shape[1] >= 5 and predictions.shape[1] < predictions.shape[2]:
            predictions = predictions.transpose(1, 2).contiguous()

        results = []
        for per_image in predictions:
            results.append(self._decode_prediction_matrix(per_image))
        return results

    def _decode_prediction_matrix(self, pred: Tensor) -> DetectionResult:
        if pred.numel() == 0:
            return DetectionResult.empty(stage="stage2_block")
        feature_count = pred.shape[1]
        if feature_count < 5:
            raise RuntimeError("fine model predictions need at least 5 features")

        fmt = self.output_format
        if fmt == "auto":
            fmt = "xyxy_conf_cls" if feature_count == 6 and self._looks_like_xyxy(pred[:, :4]) else "yolo"

        if fmt in {"xyxy_conf_cls", "xyxy-conf-cls"}:
            if feature_count < 6:
                raise RuntimeError("xyxy_conf_cls output requires [x1,y1,x2,y2,score,class]")
            boxes = pred[:, :4]
            scores = pred[:, 4]
            classes = pred[:, 5].long()
        elif fmt in {"yolo", "xywh_class"}:
            boxes = _xywh_to_xyxy(pred[:, :4])
            class_scores, classes = pred[:, 4:].max(dim=1)
            scores = class_scores
        elif fmt in {"yolo_obj", "yolo-obj", "xywh_obj_class"}:
            if feature_count < 6:
                raise RuntimeError("yolo_obj output requires [cx,cy,w,h,obj,classes...]")
            boxes = _xywh_to_xyxy(pred[:, :4])
            class_scores, classes = pred[:, 5:].max(dim=1)
            scores = pred[:, 4].sigmoid() * class_scores
        else:
            raise ValueError(f"unsupported fine output format: {self.output_format}")

        boxes = boxes.clamp(min=0.0, max=float(self.imgsz))
        return self._filter_result(DetectionResult(boxes, scores, classes.long(), stage="stage2_block"))

    def _filter_result(self, result: DetectionResult) -> DetectionResult:
        if result.num_boxes == 0:
            return result
        keep = result.scores >= self.conf
        if keep.ndim == 0:
            keep = keep.unsqueeze(0)
        boxes = result.boxes[keep]
        scores = result.scores[keep]
        classes = result.classes[keep]
        valid = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
        return DetectionResult(
            boxes=boxes[valid].float(),
            scores=scores[valid].float(),
            classes=classes[valid].long(),
            stage=result.stage,
            metadata=result.metadata,
        )

    @staticmethod
    def _looks_like_xyxy(boxes: Tensor) -> bool:
        if boxes.numel() == 0:
            return True
        valid_fraction = ((boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])).float().mean()
        return bool(valid_fraction >= 0.9)


class BlockResultMapper:
    """Map detections from letterboxed block coordinates back to full-image coordinates."""

    def map_one(self, result: DetectionResult, metadata: BlockMetadata) -> DetectionResult:
        if result.num_boxes == 0:
            return DetectionResult.empty(stage="stage2", metadata=metadata.to_dict())

        boxes = result.boxes.detach().cpu().float().clone()
        pad_x, pad_y = metadata.pad
        ratio_x, ratio_y = metadata.resize_ratio
        x1, y1, x2, y2 = metadata.xyxy
        image_width, image_height = metadata.original_size

        boxes[:, [0, 2]] = (boxes[:, [0, 2]] - float(pad_x)) / float(ratio_x)
        boxes[:, [1, 3]] = (boxes[:, [1, 3]] - float(pad_y)) / float(ratio_y)
        boxes[:, [0, 2]] += float(x1)
        boxes[:, [1, 3]] += float(y1)

        boxes[:, [0, 2]] = boxes[:, [0, 2]].clamp(0.0, float(image_width))
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clamp(0.0, float(image_height))
        valid = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])

        mapped_metadata = metadata.to_dict()
        mapped_metadata["source_stage"] = result.stage
        return DetectionResult(
            boxes=boxes[valid],
            scores=result.scores.detach().cpu().float()[valid],
            classes=result.classes.detach().cpu().long()[valid],
            stage="stage2",
            metadata=mapped_metadata,
        )

    def map_many(
        self,
        block_results: Sequence[DetectionResult],
        metadata: Sequence[BlockMetadata],
    ) -> list[DetectionResult]:
        if len(block_results) != len(metadata):
            raise ValueError("block result count must match block metadata count")
        return [self.map_one(result, meta) for result, meta in zip(block_results, metadata)]


class ResultMerger:
    """Merge block results with class-aware NMS."""

    def __init__(self, iou_threshold: float = 0.45, max_detections: int = 300) -> None:
        self.iou_threshold = float(iou_threshold)
        self.max_detections = int(max_detections)

    def merge(self, results: Sequence[DetectionResult]) -> DetectionResult:
        non_empty = [result.cpu() for result in results if result.num_boxes > 0]
        if not non_empty:
            return DetectionResult.empty(stage="stage2")

        boxes = torch.cat([result.boxes for result in non_empty], dim=0)
        scores = torch.cat([result.scores for result in non_empty], dim=0)
        classes = torch.cat([result.classes for result in non_empty], dim=0)

        keep_indices: list[Tensor] = []
        for class_id in classes.unique(sorted=True):
            class_indices = torch.where(classes == class_id)[0]
            kept_for_class = _nms(boxes[class_indices], scores[class_indices], self.iou_threshold)
            keep_indices.append(class_indices[kept_for_class])

        keep = torch.cat(keep_indices, dim=0) if keep_indices else torch.empty((0,), dtype=torch.long)
        if keep.numel() > 0:
            keep = keep[scores[keep].argsort(descending=True)]
            if self.max_detections > 0:
                keep = keep[: self.max_detections]
        return DetectionResult(
            boxes=boxes[keep],
            scores=scores[keep],
            classes=classes[keep],
            stage="stage2",
        )


class TwoStageMultiLightPipeline:
    """Run Stage 1 first; run Stage 2 only when Stage 1 has no defect boxes."""

    def __init__(
        self,
        coarse_detector: CoarseDetector,
        fine_detector: FineMultiLightDetector,
        tiler: MultiLightBlockTiler,
        mapper: BlockResultMapper | None = None,
        merger: ResultMerger | None = None,
        coarse_light: str = "backlight",
    ) -> None:
        self.coarse_detector = coarse_detector
        self.fine_detector = fine_detector
        self.tiler = tiler
        self.mapper = mapper or BlockResultMapper()
        self.merger = merger or ResultMerger()
        self.coarse_light = coarse_light

    def predict_manifest(self, manifest: SampleManifest) -> PipelinePrediction:
        return self.predict(manifest.lights, sample_id=manifest.sample_id)

    def predict(
        self,
        light_paths: Mapping[str, str | Path],
        sample_id: str,
    ) -> PipelinePrediction:
        total_started = _now()
        timings = {
            "coarse_time_ms": 0.0,
            "tiling_time_ms": 0.0,
            "fine_inference_time_ms": 0.0,
            "mapping_time_ms": 0.0,
            "nms_time_ms": 0.0,
            "total_time_ms": 0.0,
        }

        if self.coarse_light not in light_paths:
            raise KeyError(f"coarse light {self.coarse_light!r} not present in manifest")

        coarse_started = _now()
        coarse_result = self.coarse_detector.predict(light_paths[self.coarse_light], sample_id=sample_id)
        timings["coarse_time_ms"] = _elapsed_ms(coarse_started)

        if self.coarse_detector.detected_defect(coarse_result):
            timings["total_time_ms"] = _elapsed_ms(total_started)
            return PipelinePrediction(
                sample_id=sample_id,
                stage_source="stage1",
                final_result=coarse_result,
                coarse_result=coarse_result,
                stage2_result=DetectionResult.empty(stage="stage2"),
                coarse_detected=True,
                timings_ms=timings,
                block_count=0,
                positive_block_count=0,
            )

        tiling_started = _now()
        blocks, metadata = self.tiler.tile_paths(light_paths, sample_id=sample_id)
        timings["tiling_time_ms"] = _elapsed_ms(tiling_started)

        fine_started = _now()
        block_results = self.fine_detector.predict(blocks)
        timings["fine_inference_time_ms"] = _elapsed_ms(fine_started)

        mapping_started = _now()
        mapped_results = self.mapper.map_many(block_results, metadata)
        positive_block_count = sum(1 for result in mapped_results if result.detected_defect)
        timings["mapping_time_ms"] = _elapsed_ms(mapping_started)

        nms_started = _now()
        merged = self.merger.merge(mapped_results)
        timings["nms_time_ms"] = _elapsed_ms(nms_started)
        timings["total_time_ms"] = _elapsed_ms(total_started)

        return PipelinePrediction(
            sample_id=sample_id,
            stage_source="stage2" if merged.detected_defect else "none",
            final_result=merged,
            coarse_result=coarse_result,
            stage2_result=merged,
            coarse_detected=False,
            timings_ms=timings,
            block_count=len(metadata),
            positive_block_count=positive_block_count,
            block_metadata=metadata,
        )


def _xywh_to_xyxy(boxes: Tensor) -> Tensor:
    cx, cy, width, height = boxes.unbind(dim=1)
    half_w = width / 2.0
    half_h = height / 2.0
    return torch.stack((cx - half_w, cy - half_h, cx + half_w, cy + half_h), dim=1)


def _box_iou(box: Tensor, boxes: Tensor) -> Tensor:
    x1 = torch.maximum(box[0], boxes[:, 0])
    y1 = torch.maximum(box[1], boxes[:, 1])
    x2 = torch.minimum(box[2], boxes[:, 2])
    y2 = torch.minimum(box[3], boxes[:, 3])
    intersection = (x2 - x1).clamp(min=0) * (y2 - y1).clamp(min=0)

    box_area = (box[2] - box[0]).clamp(min=0) * (box[3] - box[1]).clamp(min=0)
    boxes_area = (boxes[:, 2] - boxes[:, 0]).clamp(min=0) * (boxes[:, 3] - boxes[:, 1]).clamp(min=0)
    union = box_area + boxes_area - intersection
    return intersection / union.clamp(min=1e-7)


def _nms(boxes: Tensor, scores: Tensor, iou_threshold: float) -> Tensor:
    if boxes.numel() == 0:
        return torch.empty((0,), dtype=torch.long)

    order = scores.argsort(descending=True)
    kept: list[int] = []
    while order.numel() > 0:
        current = int(order[0])
        kept.append(current)
        if order.numel() == 1:
            break
        remaining = order[1:]
        ious = _box_iou(boxes[current], boxes[remaining])
        order = remaining[ious <= iou_threshold]
    return torch.tensor(kept, dtype=torch.long)


def _load_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _sample_id_from_manifest(data: Mapping[str, Any], fallback: str) -> str:
    bag_id = data.get("bag_id") or data.get("sample_id") or data.get("id") or fallback
    camera_id = data.get("camera_id")
    if camera_id:
        return f"{bag_id}_{camera_id}"
    return str(bag_id)


def _coerce_manifest(data: Mapping[str, Any], base_dir: Path, manifest_path: Path | None = None) -> SampleManifest:
    lights = data.get("lights")
    if lights is None and all(light in data for light in LIGHT_ORDER):
        lights = {light: data[light] for light in LIGHT_ORDER}
    if not isinstance(lights, Mapping):
        raise ValueError("manifest must contain a lights mapping")

    resolved_lights: dict[str, Path] = {}
    for light in LIGHT_ORDER:
        if light not in lights:
            raise KeyError(f"manifest is missing required light: {light}")
        path = Path(str(lights[light]))
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        resolved_lights[light] = path

    fallback = manifest_path.stem if manifest_path is not None else "sample"
    return SampleManifest(
        sample_id=_sample_id_from_manifest(data, fallback),
        lights=resolved_lights,
        raw=dict(data),
        manifest_path=manifest_path,
    )


def load_manifest_file(path: str | Path) -> list[SampleManifest]:
    manifest_path = Path(path)
    data = _load_json_file(manifest_path)
    if isinstance(data, Mapping):
        return [_coerce_manifest(data, manifest_path.parent, manifest_path)]
    if isinstance(data, list):
        manifests: list[SampleManifest] = []
        for item in data:
            if isinstance(item, Mapping):
                manifests.append(_coerce_manifest(item, manifest_path.parent, manifest_path))
                continue
            item_path = Path(str(item))
            if not item_path.is_absolute():
                item_path = manifest_path.parent / item_path
            manifests.extend(load_manifest_file(item_path))
        return manifests
    raise ValueError(f"unsupported manifest JSON in {manifest_path}")


def discover_manifests(source: str | Path) -> list[SampleManifest]:
    source_path = Path(source)
    if source_path.is_dir():
        candidates = sorted(source_path.glob("*.manifest")) + sorted(source_path.glob("*.json"))
        manifests: list[SampleManifest] = []
        for candidate in candidates:
            manifests.extend(load_manifest_file(candidate))
        return manifests

    if not source_path.exists():
        raise FileNotFoundError(f"source not found: {source_path}")

    text = source_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"source is empty: {source_path}")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, Mapping):
        return [_coerce_manifest(parsed, source_path.parent, source_path)]
    if isinstance(parsed, list):
        manifests: list[SampleManifest] = []
        for item in parsed:
            if isinstance(item, Mapping):
                manifests.append(_coerce_manifest(item, source_path.parent, source_path))
            else:
                item_path = Path(str(item))
                if not item_path.is_absolute():
                    item_path = source_path.parent / item_path
                manifests.extend(load_manifest_file(item_path))
        return manifests

    manifests = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("{"):
            item = json.loads(line)
            manifests.append(_coerce_manifest(item, source_path.parent, source_path))
            continue
        item_path = Path(line)
        if not item_path.is_absolute():
            item_path = source_path.parent / item_path
        manifests.extend(load_manifest_file(item_path))
    return manifests


def save_prediction_json(prediction: PipelinePrediction, output_dir: str | Path) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in prediction.sample_id)
    path = output / f"{safe_name}.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(prediction.to_dict(), handle, ensure_ascii=False, indent=2)
    return path


def build_pipeline(
    coarse_model: str | Path,
    fine_model: str | Path,
    coarse_light: str = "backlight",
    imgsz_coarse: int = 640,
    imgsz_fine: int = 512,
    block_size: int = 512,
    block_overlap: float = 0.2,
    block_stride: int | None = None,
    min_block_size: int = 1,
    pad_to_square: bool = True,
    max_blocks: int | None = None,
    device: str = "0",
    coarse_conf: float = 0.25,
    coarse_iou: float = 0.7,
    fine_conf: float = 0.25,
    nms_iou: float = 0.45,
    max_detections: int = 300,
    fine_batch_size: int = 0,
    fine_output_format: str = "auto",
) -> TwoStageMultiLightPipeline:
    coarse_detector = CoarseDetector(
        coarse_model,
        imgsz=imgsz_coarse,
        device=device,
        conf=coarse_conf,
        iou=coarse_iou,
        max_det=max_detections,
    )
    fine_detector = FineMultiLightDetector(
        fine_model,
        imgsz=imgsz_fine,
        device=device,
        conf=fine_conf,
        batch_size=fine_batch_size,
        output_format=fine_output_format,
    )
    tiler = MultiLightBlockTiler(
        block_size=block_size,
        stride=block_stride,
        overlap=block_overlap,
        min_block_size=min_block_size,
        pad_to_square=pad_to_square,
        max_blocks=max_blocks,
        model_input_size=imgsz_fine,
    )
    return TwoStageMultiLightPipeline(
        coarse_detector=coarse_detector,
        fine_detector=fine_detector,
        tiler=tiler,
        mapper=BlockResultMapper(),
        merger=ResultMerger(iou_threshold=nms_iou, max_detections=max_detections),
        coarse_light=coarse_light,
    )
