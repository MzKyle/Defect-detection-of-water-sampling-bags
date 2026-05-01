# 多光源特征级融合

## 目标

当前多光源方案偏向采图、对齐和结果层融合。下一阶段将检测模型升级为特征级融合：

```text
backlight image
darkfield image
polarized image
    -> 三分支 YOLO Backbone
    -> P3 / P4 / P5 cross-light Transformer Fusion
    -> 原 YOLO Neck
    -> 原 YOLO Head
```

Neck 和 Head 保持 YOLO 原结构，主要改造 Backbone 前端和 P3/P4/P5 三个特征层的融合方式。

## 输入约定

每个训练样本应是一组已经对齐的同袋同工位图像：

| 光源 | 建议名称 | 主要价值 |
| --- | --- | --- |
| 背光 | `backlight` | 破损、针孔、轮廓透光异常 |
| 暗场 | `darkfield` | 头发丝、划痕、凸起边缘 |
| 交叉偏振 | `polarized` | 污渍、浅色污染、反光伪缺陷抑制 |

工程上要求三张图尺寸一致、ROI 一致，并共享同一套标注坐标。若实际采集存在微小位移，应先在数据层做畸变校正、ROI 裁剪或单应性对齐。

## Fusion 方式

`waterbag_inspection.models.CrossLightAttentionFusion` 的输入是：

```text
[B, 3, C, H, W]
```

其中 3 表示背光、暗场、偏振。模块会把每个空间位置独立展开为长度为 3 的 token 序列：

```text
(B, H, W) 个独立序列，每个序列只有 3 个 light token
```

注意力只发生在同一空间位置的三种光源之间，不做全图 attention。因此计算量主要随 `B * H * W * 3^2` 增长，适合实时检测链路。

融合后输出仍为 YOLO Neck 期望的单尺度特征图：

```text
[B, C, H, W]
```

`MultiScaleTransformerFusion` 在 P3、P4、P5 上各放一个这样的 fusion block。`MultiLightYOLOFeatureFusion` 则提供三分支 Backbone + fusion + Neck + Head 的通用封装。

当前实现把 attention 和 FFN 残差缩放初始化为 0，输出投影初始化为恒等映射，因此初始行为接近三光源特征均值；接预训练 YOLO 时，Neck/Head 的输入分布不会一开始就被随机 Transformer 打乱。

## 预期收益

这种结构让模型自己学习不同缺陷和光源之间的依赖关系：

| 缺陷/状态 | 可能依赖 |
| --- | --- |
| 头发丝、细小异物 | 暗场权重更高 |
| 破损、针孔、透光异常 | 背光权重更高 |
| 污渍、浅色污染 | 交叉偏振权重更高 |
| 正常折痕、反光伪缺陷 | 多光源一致性/不一致性共同判断 |

与后处理融合相比，特征级融合可以在 Neck 之前完成多光源信息选择，减少“单光源误检已经成框后再补救”的问题。

## 运行流程

Python 实时链路已经支持多光源组包。启用 `multilight.enabled=true` 后，watch 目录不再把单张图片直接送检，而是等待 burst 模块写入 JSON manifest。每个 manifest 描述同一个袋、同一个相机、同一次触发下的三张光源图：

```json
{
  "bag_id": "bag_0001",
  "camera_id": 1,
  "lights": {
    "backlight": "bag_0001_cam1_backlight.jpg",
    "darkfield": "bag_0001_cam1_darkfield.jpg",
    "polarized": "bag_0001_cam1_polarized.jpg"
  }
}
```

服务收到 manifest 后会：

1. 等待 manifest 和三张图都落盘稳定。
2. 校验 `backlight / darkfield / polarized` 三张图齐全。
3. 备份整组三光源图。
4. 调用 `primary_detector.detect_multilight()`，一次模型调用输出一组检测框。
5. 跳过单图 Stage 2 patch 复检。
6. 继续走原有袋体级关联、重复缺陷判断、PLC 控制和结果持久化。

离线单次验证命令：

```bash
python -m waterbag_inspection.cli inspect-multilight \
  --config config/production.example.yaml \
  --camera-id 1 \
  --bag-id bag_0001 \
  --backlight captures/bag_0001_cam1_backlight.jpg \
  --darkfield captures/bag_0001_cam1_darkfield.jpg \
  --polarized captures/bag_0001_cam1_polarized.jpg
```

或直接传 manifest：

```bash
python -m waterbag_inspection.cli inspect-multilight \
  --config config/production.example.yaml \
  --manifest captures/bag_0001_cam1.json
```

## 配置

生产配置示例：

```yaml
models:
  primary:
    backend: multilight_torch
    weights_path: artifacts/models/multilight_yolo_feature_fusion.torchscript.pt
    device: 0
    imgsz: 640
    light_order: [backlight, darkfield, polarized]
    primary_light: backlight
    input_format: blchw

multilight:
  enabled: true
  light_order: [backlight, darkfield, polarized]
  primary_light: backlight
  manifest_suffixes: [".json", ".manifest"]
```

`input_format=blchw` 表示模型输入为 `[B, 3, 3, H, W]`；如果导出模型需要 `[B, 9, H, W]`，可改成 `input_format: bchw`。

## 落地顺序

1. 先生成多光源分组数据集，每个样本固定包含 `backlight`、`darkfield`、`polarized` 三张对齐图。
2. 用现有 YOLO 权重初始化三个 Backbone 分支；Neck 和 Head 沿用原权重。
3. 第一阶段冻结大部分 Backbone，只训练 P3/P4/P5 fusion 和检测头，观察召回与误检变化。
4. 第二阶段解冻 Backbone 后端，低学习率联合微调。
5. 导出时可使用 `[B, 3, C, H, W]` 输入，或在部署侧将三张图按通道堆叠为 `[B, 9, H, W]` 后由模型内部拆分。

## 当前代码位置

| 模块 | 说明 |
| --- | --- |
| `waterbag_inspection/models/multilight_fusion.py` | 三光源 token attention、P3/P4/P5 多尺度 fusion 和 YOLO 风格封装 |
| `waterbag_inspection/detectors.py` | `multilight_torch` 推理后端，负责三图预处理、一次模型调用和检测框解析 |
| `waterbag_inspection/multilight.py` | 多光源 manifest 解析和光源路径规范化 |
| `tests/test_multilight_fusion.py` | 形状、权重归一化和空间局部性测试 |

单图 `UltralyticsDetector` 仍保留为 fallback；多光源生产链路应使用 `multilight_torch` + manifest 组包。
