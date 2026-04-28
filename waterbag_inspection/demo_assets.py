from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import numpy as np


CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 960


def _base_canvas() -> np.ndarray:
    image = np.full((CANVAS_HEIGHT, CANVAS_WIDTH, 3), 248, dtype=np.uint8)
    cv2.rectangle(image, (210, 70), (1070, 890), (229, 238, 244), thickness=-1)
    cv2.rectangle(image, (280, 120), (1000, 850), (255, 255, 255), thickness=-1)
    cv2.rectangle(image, (280, 120), (1000, 850), (215, 221, 224), thickness=8)
    cv2.putText(image, "WATERBAG DEMO", (345, 205), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (155, 165, 170), 4)
    return image


def _add_primary_defect(image: np.ndarray) -> np.ndarray:
    canvas = image.copy()
    cv2.circle(canvas, (640, 500), 42, (60, 60, 60), thickness=-1)
    cv2.circle(canvas, (664, 486), 13, (120, 120, 120), thickness=-1)
    return canvas


def _add_patch_defect(image: np.ndarray) -> np.ndarray:
    canvas = image.copy()
    cv2.circle(canvas, (780, 410), 12, (45, 45, 45), thickness=-1)
    cv2.circle(canvas, (790, 425), 5, (65, 65, 65), thickness=-1)
    return canvas


def _add_label(image: np.ndarray, text: str) -> np.ndarray:
    canvas = image.copy()
    cv2.putText(canvas, text, (320, 790), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (140, 150, 155), 3)
    return canvas


def _write_case(directory: Path, filename: str, image: np.ndarray) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    cv2.imwrite(str(path), image)
    return str(path)


def seed_demo_images(output_root: str, clean: bool = False) -> list[str]:
    root = Path(output_root)
    camera1 = root / "camera1"
    camera2 = root / "camera2"

    if clean and root.exists():
        shutil.rmtree(root)

    base = _base_canvas()
    cases = [
        (camera1, "bag_0001_cam1_good.jpg", _add_label(base, "BAG 0001 CAM1 GOOD")),
        (camera2, "bag_0001_cam2_good.jpg", _add_label(base, "BAG 0001 CAM2 GOOD")),
        (
            camera1,
            "bag_0002_cam1_defect_primary.jpg",
            _add_label(_add_primary_defect(base), "BAG 0002 CAM1 PRIMARY DEFECT"),
        ),
        (camera2, "bag_0002_cam2_good.jpg", _add_label(base, "BAG 0002 CAM2 GOOD")),
        (
            camera1,
            "bag_0003_cam1_defect_primary.jpg",
            _add_label(_add_primary_defect(base), "BAG 0003 CAM1 PRIMARY DEFECT REPEAT"),
        ),
        (camera2, "bag_0003_cam2_good.jpg", _add_label(base, "BAG 0003 CAM2 GOOD")),
        (camera1, "bag_0004_cam1_good.jpg", _add_label(base, "BAG 0004 CAM1 GOOD")),
        (
            camera2,
            "bag_0004_cam2_micro_patch.jpg",
            _add_label(_add_patch_defect(base), "BAG 0004 CAM2 MICRO PATCH"),
        ),
    ]
    outputs = [_write_case(directory, filename, image) for directory, filename, image in cases]
    return outputs
