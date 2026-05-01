# 多光源缺陷可见性矩阵

## 定位

当前代码的多光源输入固定为：

```text
backlight / darkfield / polarized
```

可见性矩阵不是把规则硬编码进检测流程，而是用于：

- 标注时记录“哪种光源最能证明该缺陷”
- 评估时检查模型是否依赖了合理光源证据
- 分析误杀时区分真实缺陷、正常折痕和反光伪缺陷
- 后续解释 Transformer Fusion 的 light weight 是否符合预期

矩阵配置在：

```text
config/multilight_visibility_matrix.yaml
```

## 当前矩阵

| 缺陷类型 | 背光 | 暗场 | 交叉偏振 | 主证据 |
| --- | --- | --- | --- | --- |
| 袋体破损 / 边缘异常 | 强 | 中 | 弱 | 背光 |
| 头发丝 / 细长异物 | 中 | 强 | 中 | 暗场 |
| 黑点 / 污点 / 淡色污染 | 中 | 中 | 强 | 交叉偏振 |
| 白色折痕 / 反光折线 | 弱 | 强 | 强 | 暗场 + 交叉偏振，但需要降误杀 |
| 气泡 / 压痕 / 局部凸凹 | 中 | 强 | 中 | 暗场 + 位置一致性 |
| 针孔 / 透光小缺陷 | 强 | 中 | 弱 | 背光 |

这里的“暗场”是当前代码中的单一路 `darkfield`。如果现场未来采双暗场，可以先在采图或预处理侧融合成当前 `darkfield` 证据，或再扩展 light_order。

## 评分输入

矩阵评估需要一组候选缺陷的证据：

| 输入 | 说明 |
| --- | --- |
| `defect_type` | 缺陷类型或别名，如 `hair`、`stain`、`pinhole` |
| `backlight / darkfield / polarized` | 每路 0-1 响应分数 |
| `consistency` | 多光源位置一致性，0-1 |
| `confidence` | 模型候选框置信度，0-1 |
| `cue` | 形态线索，如 `slender`、`wide_continuous`、`natural_texture_direction` |

输出是：

```text
ng / review / suppress
```

它适合作为离线分析和阈值校准，不建议直接替代模型输出。

## 使用示例

头发丝候选：

```bash
python -m waterbag_inspection.cli assess-visibility \
  --defect-type hair \
  --backlight 0.45 \
  --darkfield 0.95 \
  --polarized 0.55 \
  --consistency 0.82 \
  --confidence 0.90 \
  --cue slender \
  --cue high_aspect_ratio \
  --cue thin_continuous
```

正常白色折痕候选：

```bash
python -m waterbag_inspection.cli assess-visibility \
  --defect-type white_crease \
  --backlight 0.20 \
  --darkfield 0.90 \
  --polarized 0.88 \
  --consistency 0.70 \
  --confidence 0.80 \
  --cue linear_structure \
  --cue wide_continuous \
  --cue natural_texture_direction \
  --cue low_edge_sharpness
```

第二个例子虽然暗场和偏振响应都强，但由于形态更像正常折痕，会被矩阵降到 `review` 或 `suppress`，用于定位误杀来源。

## 后续评估建议

离线评估集建议记录以下字段：

```text
bag_id, camera_id, defect_type, gt_is_defect,
backlight_score, darkfield_score, polarized_score,
consistency_score, model_confidence, morphology_cues,
final_decision, is_false_reject, is_miss
```

按缺陷类型统计：

- 各光源响应均值和方差
- `ng / review / suppress` 分布
- 误杀样本中命中的 `suppress_cues`
- 漏检样本中缺失的 `required_cues`
- Transformer Fusion light weight 与矩阵主证据是否一致
