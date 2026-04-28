from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RepeatDefectTracker:
    def __init__(
        self,
        history_path: str,
        iou_threshold: float = 0.5,
        max_entries_per_camera: int = 50,
        namespace: str = "default",
    ):
        self.history_path = Path(history_path)
        self.iou_threshold = iou_threshold
        self.max_entries_per_camera = max_entries_per_camera
        self.namespace = namespace or "default"
        self.history_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not self.history_path.exists():
            return {}
        with self.history_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _save(self, payload: dict[str, Any]) -> None:
        with self.history_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def _load_scopes(self) -> dict[str, dict[str, list[list[float]]]]:
        payload = self._load()
        scopes = payload.get("scopes")
        if isinstance(scopes, dict):
            return scopes
        if payload and all(isinstance(value, list) for value in payload.values()):
            return {"default": payload}
        return {}

    def _build_namespace(self, scope: str | None) -> str:
        if not scope:
            return self.namespace
        if scope == self.namespace:
            return self.namespace
        return f"{self.namespace}:{scope}"

    @staticmethod
    def _normalize(box: dict[str, int | float | str]) -> list[float]:
        x1, y1, x2, y2 = float(box["x1"]), float(box["y1"]), float(box["x2"]), float(box["y2"])
        confidence = float(box.get("confidence", 1.0))
        return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2), confidence]

    @staticmethod
    def _compute_iou(first: list[float], second: list[float]) -> float:
        x_a = max(first[0], second[0])
        y_a = max(first[1], second[1])
        x_b = min(first[2], second[2])
        y_b = min(first[3], second[3])

        inter_area = max(0.0, x_b - x_a) * max(0.0, y_b - y_a)
        area_first = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
        area_second = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
        return inter_area / (area_first + area_second - inter_area + 1e-6)

    def is_repeated(self, camera_id: int, boxes: list[dict[str, int | float | str]], scope: str | None = None) -> bool:
        if not boxes:
            return False

        all_scopes = self._load_scopes()
        namespace = self._build_namespace(scope)
        history = all_scopes.get(namespace, {})
        key = str(camera_id)
        previous_boxes = history.get(key, [])

        normalized = [self._normalize(box) for box in boxes]
        repeated = any(
            self._compute_iou(current[:4], previous[:4]) >= self.iou_threshold
            for current in normalized
            for previous in previous_boxes
        )

        if not repeated:
            merged = (previous_boxes + normalized)[-self.max_entries_per_camera :]
            history[key] = merged
            all_scopes[namespace] = history
            self._save({"scopes": all_scopes})

        return repeated
