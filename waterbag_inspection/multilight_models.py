from __future__ import annotations

from collections.abc import Callable, Sequence

import torch
from torch import Tensor, nn


class CrossLightTransformerFusion(nn.Module):
    """Fuse same-position tokens from backlight, darkfield, and polarized features."""

    def __init__(
        self,
        channels: int,
        num_heads: int = 4,
        mlp_ratio: float = 4.0,
        dropout: float = 0.0,
    ) -> None:
        super().__init__()
        if channels <= 0:
            raise ValueError("channels must be positive")
        if num_heads <= 0:
            raise ValueError("num_heads must be positive")
        if channels % num_heads != 0:
            raise ValueError("channels must be divisible by num_heads")

        hidden_channels = max(channels, int(channels * mlp_ratio))
        self.norm1 = nn.LayerNorm(channels)
        self.attn = nn.MultiheadAttention(
            embed_dim=channels,
            num_heads=num_heads,
            dropout=dropout,
            batch_first=True,
        )
        self.norm2 = nn.LayerNorm(channels)
        self.ffn = nn.Sequential(
            nn.Linear(channels, hidden_channels),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_channels, channels),
            nn.Dropout(dropout),
        )
        self.light_weight = nn.Linear(channels, 1)
        self.mean_residual_gate = nn.Parameter(torch.zeros(1, channels, 1, 1))

    def forward(self, backlight: Tensor, darkfield: Tensor, polarized: Tensor) -> Tensor:
        if backlight.shape != darkfield.shape or backlight.shape != polarized.shape:
            raise ValueError(
                "all light features must have the same [B, C, H, W] shape"
            )
        if backlight.ndim != 4:
            raise ValueError("light features must be [B, C, H, W]")

        batch, channels, height, width = backlight.shape
        stacked = torch.stack([backlight, darkfield, polarized], dim=1)

        # Attention is only over the 3 light tokens at each spatial location.
        tokens = (
            stacked.permute(0, 3, 4, 1, 2)
            .contiguous()
            .view(batch * height * width, 3, channels)
        )

        attn_input = self.norm1(tokens)
        attn_output, _ = self.attn(attn_input, attn_input, attn_input, need_weights=False)
        tokens = tokens + attn_output
        tokens = tokens + self.ffn(self.norm2(tokens))

        light_weights = torch.softmax(self.light_weight(tokens), dim=1)
        fused_tokens = (tokens * light_weights).sum(dim=1)
        fused = (
            fused_tokens.view(batch, height, width, channels)
            .permute(0, 3, 1, 2)
            .contiguous()
        )

        mean_feature = stacked.mean(dim=1)
        gate = torch.sigmoid(self.mean_residual_gate)
        return fused + gate * mean_feature


class MultiLightFineYOLO(nn.Module):
    """Three-backbone feature-fusion wrapper for a YOLO neck and head.

    The supplied backbones must each return P3/P4/P5 feature tensors. The neck
    and head can be the original YOLO modules, so detection loss/head behavior
    stays outside this wrapper.
    """

    def __init__(
        self,
        backbone_backlight: nn.Module,
        backbone_darkfield: nn.Module,
        backbone_polarized: nn.Module,
        neck: nn.Module | Callable[[Sequence[Tensor]], Tensor | Sequence[Tensor]],
        head: nn.Module | Callable[[Tensor | Sequence[Tensor]], Tensor] | None = None,
        channels: Sequence[int] = (128, 256, 512),
        num_heads: int = 4,
        mlp_ratio: float = 4.0,
        dropout: float = 0.0,
    ) -> None:
        super().__init__()
        if len(channels) != 3:
            raise ValueError("channels must contain P3/P4/P5 channel counts")

        self.light_order = ("backlight", "darkfield", "polarized")
        self.backbone_backlight = backbone_backlight
        self.backbone_darkfield = backbone_darkfield
        self.backbone_polarized = backbone_polarized
        self.fusion_p3 = CrossLightTransformerFusion(
            channels[0], num_heads=num_heads, mlp_ratio=mlp_ratio, dropout=dropout
        )
        self.fusion_p4 = CrossLightTransformerFusion(
            channels[1], num_heads=num_heads, mlp_ratio=mlp_ratio, dropout=dropout
        )
        self.fusion_p5 = CrossLightTransformerFusion(
            channels[2], num_heads=num_heads, mlp_ratio=mlp_ratio, dropout=dropout
        )
        self.neck = neck
        self.head = head

    @classmethod
    def from_shared_yolo_parts(
        cls,
        backbone: nn.Module,
        neck: nn.Module | Callable[[Sequence[Tensor]], Tensor | Sequence[Tensor]],
        head: nn.Module | Callable[[Tensor | Sequence[Tensor]], Tensor] | None = None,
        **kwargs,
    ) -> "MultiLightFineYOLO":
        return cls(backbone, backbone, backbone, neck, head=head, **kwargs)

    def _run_backbone(self, backbone: nn.Module, image: Tensor) -> tuple[Tensor, Tensor, Tensor]:
        features = backbone(image)
        if not isinstance(features, (list, tuple)) or len(features) < 3:
            raise RuntimeError("each backbone must return at least P3/P4/P5 features")
        return features[-3], features[-2], features[-1]

    def forward(self, x: Tensor) -> Tensor | Sequence[Tensor]:
        if x.ndim != 5:
            raise ValueError("MultiLightFineYOLO input must be [B, 3, 3, H, W]")
        if x.shape[1] != 3 or x.shape[2] != 3:
            raise ValueError("expected light dimension=3 and RGB channel dimension=3")

        p3_b, p4_b, p5_b = self._run_backbone(self.backbone_backlight, x[:, 0])
        p3_d, p4_d, p5_d = self._run_backbone(self.backbone_darkfield, x[:, 1])
        p3_p, p4_p, p5_p = self._run_backbone(self.backbone_polarized, x[:, 2])

        fused_features = [
            self.fusion_p3(p3_b, p3_d, p3_p),
            self.fusion_p4(p4_b, p4_d, p4_p),
            self.fusion_p5(p5_b, p5_d, p5_p),
        ]
        neck_output = self.neck(fused_features)
        if self.head is None:
            return neck_output
        return self.head(neck_output)
