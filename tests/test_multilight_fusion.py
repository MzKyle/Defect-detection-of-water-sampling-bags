from __future__ import annotations

import torch
from torch import nn

from waterbag_inspection.models import (
    CrossLightAttentionFusion,
    MultiLightYOLOFeatureFusion,
    MultiScaleTransformerFusion,
    split_stacked_light_channels,
    stack_light_tensors,
)


def test_split_and_stack_channel_order() -> None:
    images = torch.randn(2, 9, 16, 16)

    split = split_stacked_light_channels(images, num_lights=3)
    stacked = stack_light_tensors(images)

    assert len(split) == 3
    assert stacked.shape == (2, 3, 3, 16, 16)
    assert torch.equal(stacked[:, 0], split[0])
    assert torch.equal(stacked[:, 1], split[1])
    assert torch.equal(stacked[:, 2], split[2])


def test_cross_light_attention_fusion_shape_and_weights() -> None:
    fuser = CrossLightAttentionFusion(channels=16, num_heads=4)
    features = torch.randn(2, 3, 16, 8, 8)

    fused, weights = fuser(features, return_weights=True)

    assert fused.shape == (2, 16, 8, 8)
    assert weights.shape == (2, 3, 8, 8)
    assert torch.allclose(weights.sum(dim=1), torch.ones(2, 8, 8), atol=1e-6)


def test_cross_light_attention_is_spatially_local() -> None:
    torch.manual_seed(7)
    fuser = CrossLightAttentionFusion(channels=8, num_heads=2).eval()
    base = torch.randn(1, 3, 8, 4, 4)
    changed = base.clone()
    changed[:, :, :, 1, 2] += torch.randn(1, 3, 8) * 5.0

    with torch.no_grad():
        base_out = fuser(base)
        changed_out = fuser(changed)

    untouched_positions = torch.ones(4, 4, dtype=torch.bool)
    untouched_positions[1, 2] = False

    assert torch.allclose(
        base_out[:, :, untouched_positions],
        changed_out[:, :, untouched_positions],
        atol=1e-5,
    )
    assert not torch.allclose(base_out[:, :, 1, 2], changed_out[:, :, 1, 2])


def test_multiscale_transformer_fusion_returns_pyramid() -> None:
    fusion = MultiScaleTransformerFusion([8, 16, 32], num_heads=4)
    features = [
        [torch.randn(2, 8, 32, 32) for _ in range(3)],
        [torch.randn(2, 16, 16, 16) for _ in range(3)],
        [torch.randn(2, 32, 8, 8) for _ in range(3)],
    ]

    fused = fusion(features)

    assert [tuple(tensor.shape) for tensor in fused] == [
        (2, 8, 32, 32),
        (2, 16, 16, 16),
        (2, 32, 8, 8),
    ]


class TinyBackbone(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.stem = nn.Sequential(nn.Conv2d(3, 8, 3, padding=1), nn.ReLU())
        self.p3 = nn.Sequential(nn.Conv2d(8, 8, 3, stride=2, padding=1), nn.ReLU())
        self.p4 = nn.Sequential(nn.Conv2d(8, 16, 3, stride=2, padding=1), nn.ReLU())
        self.p5 = nn.Sequential(nn.Conv2d(16, 32, 3, stride=2, padding=1), nn.ReLU())

    def forward(self, x: torch.Tensor) -> list[torch.Tensor]:
        x = self.stem(x)
        p3 = self.p3(x)
        p4 = self.p4(p3)
        p5 = self.p5(p4)
        return [p3, p4, p5]


class TinyHead(nn.Module):
    def forward(self, features: list[torch.Tensor]) -> list[torch.Tensor]:
        return [feature.mean(dim=(2, 3)) for feature in features]


def test_multilight_yolo_feature_fusion_wrapper() -> None:
    model = MultiLightYOLOFeatureFusion(
        backbones=[TinyBackbone(), TinyBackbone(), TinyBackbone()],
        fusion=MultiScaleTransformerFusion([8, 16, 32], num_heads=4),
        head=TinyHead(),
    )
    images = torch.randn(2, 3, 3, 64, 64)

    output, weights = model(images, return_fusion_weights=True)

    assert [tuple(tensor.shape) for tensor in output] == [(2, 8), (2, 16), (2, 32)]
    assert [tuple(tensor.shape) for tensor in weights] == [
        (2, 3, 32, 32),
        (2, 3, 16, 16),
        (2, 3, 8, 8),
    ]
