# 二阶段缺陷检测

## 设计目标

水样采集袋缺陷可能分为两类：

- 整图上较明显的脏污、破损、黑点
- 局部很小的针孔、微小异物、细小斑点

单次整图检测速度快，但微小缺陷在 640 输入尺寸下容易被压缩。二阶段方案通过“整图快速筛查 + 网格复检”提升微小缺陷召回。

## Stage 1: 整图检测

Stage 1 对原图整体运行检测器：

| 特点 | 说明 |
| --- | --- |
| 输入 | 原始图片 |
| 模型 | primary detector |
| 目标 | 快速发现明显缺陷 |
| 命中后 | 直接进入 reject 候选，不再跑 Stage 2 |

Stage 1 命中时：

```text
stage_source = stage1
control_action = reject
reason = stage1_detected_defect
```

## Stage 2: 网格复检

Stage 2 仅在 Stage 1 未命中且 `patch_detection.enabled=true` 时运行。

配置示例：

```yaml
patch_detection:
  enabled: true
  horizontal: 4
  vertical: 5
  conf_thres: 0.2
  iou_thres: 0.3
```

流程：

1. 将原图切成 `horizontal x vertical` 个 patch
2. 对每个 patch 调用 patch detector
3. 将 patch 内坐标加上 patch 偏移，映射回原图
4. 如果任一 patch 命中，则判为微小缺陷

Stage 2 命中时：

```text
stage_source = stage2
control_action = reject
reason = stage2_detected_micro_defect
```

## 性能取舍

| 策略 | 优点 | 风险 |
| --- | --- | --- |
| 只跑整图 | 延迟最低 | 微小缺陷漏检 |
| 每张都跑 patch | 召回更高 | 延迟高、算力占用高 |
| Stage 1 未命中才跑 patch | 延迟和召回折中 | Stage 1 阈值需要谨慎 |

## 参数建议

| 参数 | 建议 |
| --- | --- |
| `primary.conf_thres` | 不宜过低，否则误检会直接 reject |
| `patch.conf_thres` | 可略低，提高微小缺陷召回 |
| `horizontal / vertical` | 根据缺陷尺寸和推理预算选择 |
| `imgsz` | 真实模型建议固定，便于 benchmark 对比 |

## 可视化

如果配置：

```yaml
patch_detection:
  save_visualizations: true
  visualization_dir: artifacts/patch_vis
```

则命中 patch 会保存局部可视化图，方便分析误检和漏检。
