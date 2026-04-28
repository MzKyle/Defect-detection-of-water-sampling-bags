# 数据存储结构

## artifacts 目录

运行产生的数据默认放在：

```text
artifacts/
├── backups/
├── results/
├── patch_vis/
├── uploads/
├── inspection.db
├── repeat_history.json
└── fault_injection/
```

## 图片文件

| 目录 | 说明 |
| --- | --- |
| `backups/` | 原始输入图备份 |
| `results/` | 绘制检测框后的结果图 |
| `patch_vis/` | Stage 2 patch 命中可视化 |
| `uploads/` | Web 上传原始文件 |

## SQLite 表

表名：

```text
detection_results
```

核心字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER | 自增 ID |
| `created_at` | TEXT | 结果创建时间 |
| `frame_id` | TEXT | 帧 ID |
| `bag_id` | TEXT | 袋体 ID |
| `camera_id` | INTEGER | 相机 ID |
| `camera_name` | TEXT | 相机名称 |
| `source_path` | TEXT | 原始图片 |
| `backup_path` | TEXT | 备份图片 |
| `result_image_path` | TEXT | 结果图片 |
| `status` | TEXT | 状态文本 |
| `is_defect` | INTEGER | 是否缺陷 |
| `repeated` | INTEGER | 是否重复 |
| `plc_success` | INTEGER | PLC 是否成功 |
| `decision_action` | TEXT | 控制动作 |
| `decision_reason` | TEXT | 决策原因 |
| `bag_summary` | TEXT | JSON |
| `stage1_boxes` | TEXT | JSON |
| `stage2_boxes` | TEXT | JSON |
| `final_boxes` | TEXT | JSON |
| `control_commands` | TEXT | JSON |
| `execution_feedbacks` | TEXT | JSON |
| `state_trace` | TEXT | JSON |
| `timing_breakdown` | TEXT | JSON |
| `latency_ms` | REAL | 总延迟 |

## 查询示例

```bash
sqlite3 artifacts/inspection.db \
  "select created_at, bag_id, camera_id, status, decision_action, decision_reason, latency_ms from detection_results order by id desc limit 10;"
```

统计异常：

```bash
sqlite3 artifacts/inspection.db \
  "select status, count(*) from detection_results group by status;"
```

## repeat_history

`repeat_history.json` 保存重复缺陷历史框。它是轻量方案，适合 demo 和小规模运行。

生产可演进方向：

- 迁移到 SQLite 表
- 增加时间窗口
- 增加相机位姿/批次维度
- 增加人工清洁后 reset 接口

## 清理建议

长期运行时重点清理：

- `artifacts/backups`
- `artifacts/results`
- `artifacts/patch_vis`
- `artifacts/uploads`

SQLite 和 repeat history 建议先备份再清理。
