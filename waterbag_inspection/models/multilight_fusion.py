from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import torch
from torch import Tensor, nn


LIGHT_ORDER = ("backlight", "darkfield", "polarized")


def split_stacked_light_channels(images: Tensor, num_lights: int = 3) -> tuple[Tensor, ...]:
    """Split a channel-stacked multi-light batch into one tensor per light."""
    if images.ndim != 4:
        raise ValueError(
            "Channel-stacked multi-light input must have shape [B, L*C, H, W]."
        )
    if images.shape[1] % num_lights != 0:
        raise ValueError(
            f"Input channel count {images.shape[1]} is not divisible by {num_lights} lights."
        )
    return tuple(images.chunk(num_lights, dim=1))


def _same_shape(tensors: Sequence[Tensor]) -> bool:
    return all(tensor.shape == tensors[0].shape for tensor in tensors[1:])


def _ordered_mapping_values(items: Mapping[str, Tensor], order: Sequence[str]) -> list[Tensor]:
    missing = [name for name in order if name not in items]
    if missing:
        raise ValueError(f"Missing light tensor(s): {', '.join(missing)}")
    return [items[name] for name in order]


def stack_light_tensors(
    images: Tensor | Mapping[str, Tensor] | Sequence[Tensor],
    light_order: Sequence[str] = LIGHT_ORDER,
) -> Tensor:
    """Return multi-light images as [B, L, C, H, W].

    Accepted inputs are:
    - [B, L, C, H, W] tensor
    - [B, L*C, H, W] channel-stacked tensor
    - mapping keyed by light names
    - sequence of tensors in light_order
    """
    if torch.is_tensor(images):
        if images.ndim == 5:
            if images.shape[1] != len(light_order):
                raise ValueError(
                    f"Expected {len(light_order)} lights, got {images.shape[1]}."
                )
            return images
        split = split_stacked_light_channels(images, num_lights=len(light_order))
        return torch.stack(split, dim=1)

    if isinstance(images, Mapping):
        tensors = _ordered_mapping_values(images, light_order)
    else:
        tensors = list(images)

    if len(tensors) != len(light_order):
        raise ValueError(
            f"Expected {len(light_order)} light tensors, got {len(tensors)}."
        )
    if not tensors:
        raise ValueError("At least one light tensor is required.")
    if not _same_shape(tensors):
        shapes = ", ".join(str(tuple(tensor.shape)) for tensor in tensors)
        raise ValueError(f"All light tensors must share shape; got {shapes}.")
    return torch.stack(tensors, dim=1)


class CrossLightAttentionFusion(nn.Module):
    """Fuse same-location feature tokens across lights.

    This module runs attention over the light dimension only. For a feature map
    [B, L, C, H, W], each spatial coordinate contributes an independent token
    sequence of length L, so positions never attend to other positions.
    """

    def __init__(
        self,
        channels: int,
        num_lights: int = 3,
        num_heads: int = 4,
        ff_multiplier: float = 2.0,
        dropout: float = 0.0,
    ):
        super().__init__()
        if channels <= 0:
            raise ValueError("channels must be positive.")
        if num_lights <= 1:
            raise ValueError("num_lights must be greater than 1.")
        if channels % num_heads != 0:
            raise ValueError(
                f"channels ({channels}) must be divisible by num_heads ({num_heads})."
            )

        self.channels = channels
        self.num_lights = num_lights
        self.num_heads = num_heads

        self.light_embedding = nn.Parameter(torch.zeros(num_lights, channels))
        self.attn_scale = nn.Parameter(torch.zeros(1))
        self.ffn_scale = nn.Parameter(torch.zeros(1))
        self.attn_norm = nn.LayerNorm(channels)
        self.attn = nn.MultiheadAttention(
            embed_dim=channels,
            num_heads=num_heads,
            dropout=dropout,
            batch_first=True,
        )
        self.ffn_norm = nn.LayerNorm(channels)
        hidden_channels = max(channels, int(channels * ff_multiplier))
        self.ffn = nn.Sequential(
            nn.Linear(channels, hidden_channels),
            nn.SiLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_channels, channels),
        )
        self.gate_norm = nn.LayerNorm(channels)
        self.light_gate = nn.Linear(channels, 1)
        self.out_proj = nn.Conv2d(channels, channels, kernel_size=1, bias=False)
        self.reset_parameters()

    def reset_parameters(self) -> None:
        nn.init.zeros_(self.light_embedding)
        nn.init.zeros_(self.attn_scale)
        nn.init.zeros_(self.ffn_scale)
        nn.init.zeros_(self.light_gate.weight)
        nn.init.zeros_(self.light_gate.bias)
        nn.init.dirac_(self.out_proj.weight)

    def forward(
        self,
        features: Tensor | Mapping[str, Tensor] | Sequence[Tensor],
        return_weights: bool = False,
    ) -> Tensor | tuple[Tensor, Tensor]:
        light_order = (
            LIGHT_ORDER
            if self.num_lights == len(LIGHT_ORDER)
            else tuple(str(i) for i in range(self.num_lights))
        )
        x = stack_light_tensors(features, light_order=light_order)
        if x.ndim != 5:
            raise ValueError("Cross-light fusion expects [B, L, C, H, W] features.")

        batch, lights, channels, height, width = x.shape
        if lights != self.num_lights:
            raise ValueError(f"Expected {self.num_lights} lights, got {lights}.")
        if channels != self.channels:
            raise ValueError(f"Expected {self.channels} channels, got {channels}.")

        tokens = x.permute(0, 3, 4, 1, 2).reshape(
            batch * height * width,
            lights,
            channels,
        )
        tokens = tokens + self.light_embedding.unsqueeze(0)

        attn_input = self.attn_norm(tokens)
        attended = self.attn(attn_input, attn_input, attn_input, need_weights=False)[0]
        tokens = tokens + self.attn_scale * attended
        tokens = tokens + self.ffn_scale * self.ffn(self.ffn_norm(tokens))

        light_logits = self.light_gate(self.gate_norm(tokens)).squeeze(-1)
        light_weights = light_logits.softmax(dim=1)
        fused = (tokens * light_weights.unsqueeze(-1)).sum(dim=1)
        fused = (
            fused.view(batch, height, width, channels)
            .permute(0, 3, 1, 2)
            .contiguous()
        )
        fused = self.out_proj(fused)

        if not return_weights:
            return fused

        weights = (
            light_weights.view(batch, height, width, lights)
            .permute(0, 3, 1, 2)
            .contiguous()
        )
        return fused, weights

    def extra_repr(self) -> str:
        return (
            f"channels={self.channels}, num_lights={self.num_lights}, "
            f"num_heads={self.num_heads}"
        )


class MultiScaleTransformerFusion(nn.Module):
    """Apply cross-light Transformer fusion to P3/P4/P5 feature scales."""

    def __init__(
        self,
        channels_by_scale: Mapping[str, int] | Sequence[int],
        num_lights: int = 3,
        num_heads: int = 4,
        ff_multiplier: float = 2.0,
        dropout: float = 0.0,
    ):
        super().__init__()
        if isinstance(channels_by_scale, Mapping):
            self.scale_names = tuple(channels_by_scale.keys())
            channels = list(channels_by_scale.values())
        else:
            channels = list(channels_by_scale)
            self.scale_names = tuple(f"P{i + 3}" for i in range(len(channels)))

        if not channels:
            raise ValueError("At least one feature scale is required.")

        self.fusers = nn.ModuleList(
            CrossLightAttentionFusion(
                scale_channels,
                num_lights=num_lights,
                num_heads=num_heads,
                ff_multiplier=ff_multiplier,
                dropout=dropout,
            )
            for scale_channels in channels
        )

    def __len__(self) -> int:
        return len(self.fusers)

    def forward(
        self,
        features_by_scale: Mapping[str, Any] | Sequence[Any],
        return_weights: bool = False,
    ) -> Any:
        if isinstance(features_by_scale, Mapping):
            missing = [
                name for name in self.scale_names if name not in features_by_scale
            ]
            if missing:
                raise ValueError(f"Missing feature scale(s): {', '.join(missing)}")
            fused_by_name = {}
            weights_by_name = {}
            for name, fuser in zip(self.scale_names, self.fusers):
                fused = fuser(features_by_scale[name], return_weights=return_weights)
                if return_weights:
                    fused_by_name[name], weights_by_name[name] = fused
                else:
                    fused_by_name[name] = fused
            return (fused_by_name, weights_by_name) if return_weights else fused_by_name

        scale_features = list(features_by_scale)
        if len(scale_features) != len(self.fusers):
            raise ValueError(
                f"Expected {len(self.fusers)} scales, got {len(scale_features)}."
            )

        fused_scales = []
        weights = []
        for fuser, features in zip(self.fusers, scale_features):
            fused = fuser(features, return_weights=return_weights)
            if return_weights:
                fused_feature, light_weights = fused
                fused_scales.append(fused_feature)
                weights.append(light_weights)
            else:
                fused_scales.append(fused)
        return (fused_scales, weights) if return_weights else fused_scales


class MultiLightYOLOFeatureFusion(nn.Module):
    """YOLO-style detector wrapper with three light-specific backbone branches.

    Each backbone must return a P3/P4/P5-like sequence. The fused feature list is
    passed to the supplied neck, then to the supplied head.
    """

    def __init__(
        self,
        backbones: Mapping[str, nn.Module] | Sequence[nn.Module],
        fusion: MultiScaleTransformerFusion,
        neck: nn.Module | None = None,
        head: nn.Module | None = None,
        light_order: Sequence[str] = LIGHT_ORDER,
    ):
        super().__init__()
        self.light_order = tuple(light_order)
        self.fusion = fusion
        self.neck = neck if neck is not None else nn.Identity()
        self.head = head if head is not None else nn.Identity()

        if isinstance(backbones, Mapping):
            missing = [name for name in self.light_order if name not in backbones]
            if missing:
                raise ValueError(f"Missing backbone(s): {', '.join(missing)}")
            self.backbones = nn.ModuleDict(
                {name: backbones[name] for name in self.light_order}
            )
        else:
            backbone_list = list(backbones)
            if len(backbone_list) != len(self.light_order):
                raise ValueError(
                    f"Expected {len(self.light_order)} backbones, "
                    f"got {len(backbone_list)}."
                )
            self.backbones = nn.ModuleDict(
                {name: module for name, module in zip(self.light_order, backbone_list)}
            )

    def _split_inputs(
        self,
        images: Tensor | Mapping[str, Tensor] | Sequence[Tensor],
    ) -> list[Tensor]:
        if isinstance(images, Mapping):
            return _ordered_mapping_values(images, self.light_order)
        stacked = stack_light_tensors(images, light_order=self.light_order)
        return [stacked[:, index] for index in range(len(self.light_order))]

    def forward(
        self,
        images: Tensor | Mapping[str, Tensor] | Sequence[Tensor],
        return_fusion_weights: bool = False,
    ) -> Any:
        light_images = self._split_inputs(images)

        branch_features = []
        for light_name, image in zip(self.light_order, light_images):
            features = self.backbones[light_name](image)
            if torch.is_tensor(features):
                raise ValueError(
                    "Each backbone must return a sequence of feature maps, "
                    "e.g. [P3, P4, P5]."
                )
            features = list(features)
            if len(features) != len(self.fusion):
                raise ValueError(
                    f"Backbone '{light_name}' returned {len(features)} scales; "
                    f"fusion expects {len(self.fusion)}."
                )
            branch_features.append(features)

        features_by_scale = [
            [branch[scale_index] for branch in branch_features]
            for scale_index in range(len(self.fusion))
        ]

        fused = self.fusion(features_by_scale, return_weights=return_fusion_weights)
        if return_fusion_weights:
            fused_features, fusion_weights = fused
        else:
            fused_features = fused
            fusion_weights = None

        neck_features = self.neck(fused_features)
        output = self.head(neck_features)
        return (output, fusion_weights) if return_fusion_weights else output
