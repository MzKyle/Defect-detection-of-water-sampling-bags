import cv2
import numpy as np
import torch
from torch import nn

from waterbag_inspection.config import ModelConfig
from waterbag_inspection.detectors import MultiLightTorchDetector


class ConstantBoxModel(nn.Module):
    def forward(self, x):
        batch = x.shape[0]
        box = x.new_tensor(
            [8.0, 9.0, 40.0, 41.0, 0.91, 0.0],
        )
        return box.view(1, 1, 6).repeat(batch, 1, 1)


def test_multilight_torch_detector_loads_torchscript_and_parses_boxes(tmp_path):
    model_path = tmp_path / "constant_box.pt"
    example = torch.zeros(1, 3, 3, 64, 64)
    traced = torch.jit.trace(ConstantBoxModel(), example)
    traced.save(str(model_path))

    image = np.full((64, 64, 3), 255, dtype=np.uint8)
    light_paths = {}
    for light_name in ("backlight", "darkfield", "polarized"):
        image_path = tmp_path / f"{light_name}.jpg"
        cv2.imwrite(str(image_path), image)
        light_paths[light_name] = str(image_path)

    detector = MultiLightTorchDetector(
        ModelConfig(
            backend="multilight_torch",
            weights_path=str(model_path),
            imgsz=64,
            conf_thres=0.3,
            iou_thres=0.5,
            class_names=["anomaly"],
        )
    )

    annotated, boxes = detector.detect_multilight(light_paths)

    assert annotated.shape == image.shape
    assert len(boxes) == 1
    assert boxes[0].x1 == 8
    assert boxes[0].y1 == 9
    assert boxes[0].x2 == 40
    assert boxes[0].y2 == 41
    assert boxes[0].label == "anomaly"
