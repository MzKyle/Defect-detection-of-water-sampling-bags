# pipeline - 检测主链路

## 概述

`InspectionPipeline` 是系统最核心的业务链路。它接收 `FramePacket`，完成原图备份调度、二阶段检测、重复缺陷判断、袋体级关联、控制命令执行、结果图保存调度和 SQLite 留档。

备份图和结果图由 `ArtifactWriter` 写入磁盘。演示配置默认同步写入，生产配置可开启异步写入，让主链路先完成推理、判定和 PLC 通信，落盘只作为追溯与训练数据留存。

**路径**: `waterbag_inspection/pipeline.py`

## 构造依赖

| 依赖 | 来源 | 说明 |
| --- | --- | --- |
| `RuntimeConfig` | YAML 配置 | 备份目录、结果目录、上传目录等 |
| `PatchConfig` | YAML 配置 | 二阶段 patch 参数 |
| `CorrelationConfig` | YAML 配置 | 袋体级关联参数 |
| `RepeatConfig` | YAML 配置 | 重复缺陷历史参数 |
| `SQLiteDetectionRepository` | `storage.py` | 结果持久化 |
| `BasePLCController` | `plc.py` | 控制命令执行 |
| `BaseDetector` | `detectors.py` | Stage 1 / Stage 2 检测器 |
| `ArtifactWriter` | `artifacts.py` | 备份图和结果图写入，支持异步队列 |

## 主流程

```mermaid
flowchart TD
    A["FramePacket"] --> B["文件就绪 / 记录 queue_delay"]
    B --> ML{多光源?}
    ML -- 是 --> MLR["记录 light_paths / manifest ready"]
    ML -- 否 --> C["调度原图备份"]
    MLR --> C
    C --> D["Stage 1 整图检测"]
    D --> E{Stage 1 有缺陷?}
    E -- 是 --> V{多光源 shadow 评估?}
    E -- 否 --> F{启用 Stage 2?}
    F -- 否 --> V
    F -- 是 --> G["Stage 2 网格复检"]
    G --> V
    V -- 是 --> H["可见性矩阵记录证据"]
    V -- 否 --> RPT["重复缺陷判断"]
    H --> RPT
    RPT --> I["局部 DecisionResult"]
    I --> J["BagCorrelator 袋体级聚合"]
    J --> K["最终 DecisionResult"]
    K --> L{finalized 且需要新命令?}
    L -- 是 --> M["PLC execute"]
    L -- 否 --> N["跳过控制"]
    M --> O["调度结果图保存"]
    N --> O
    O --> P["SQLite 留档"]
    P --> Q["InspectionResult"]
```

## `process_image` 与 `process_packet`

| 方法 | 适用场景 | 行为 |
| --- | --- | --- |
| `process_image(camera, image_path)` | CLI 单图检查、故障注入 | 从相机配置和图片路径构造 `FramePacket` |
| `process_packet(frame_packet)` | 在线 runtime、replay | 使用已有 `FramePacket` 执行完整链路 |

在线运行时，`InspectionRuntime` 会先等待文件稳定，再补齐 `file_ready_at`、`processing_started_at`、`source_mtime_ns`，然后调用 `process_packet()`。

## 耗时拆解

`TimingBreakdown` 记录：

| 字段 | 说明 |
| --- | --- |
| `queue_delay_ms` | 从入队到开始处理的等待时间 |
| `backup_ms` | 原图备份耗时；异步模式下为入队调度耗时 |
| `stage1_inference_ms` | Stage 1 推理耗时 |
| `stage2_inference_ms` | Stage 2 推理耗时 |
| `visibility_assessment_ms` | 多光源可见性矩阵 shadow 评估耗时 |
| `decision_ms` | 重复缺陷与局部决策耗时 |
| `correlation_ms` | 袋体关联耗时 |
| `control_ms` | PLC 控制耗时 |
| `persist_ms` | SQLite 持久化耗时 |
| `total_ms` | pipeline 总耗时 |

## 异步落盘

生产配置建议开启：

```yaml
runtime:
  async_artifact_writes: true
  artifact_queue_size: 256
  artifact_drop_when_full: true
  artifact_flush_timeout_seconds: 2.0
```

开启后，主 worker 不再等待 `shutil.copy2` 或 `cv2.imwrite` 完成。`backup_path` 和 `result_image_path` 仍会写入结果记录，但实际文件由后台 `artifact-writer` 线程完成。

当磁盘或网络盘抖动导致队列积压时，`artifact_drop_when_full: true` 会优先保障分拣尾延迟，允许丢弃追溯图；如果现场必须保证每张追溯图都保存，可以改为 `false`，但队列满时主链路会等待。

## 超时处理

`flush_timeouts()` 会调用 `BagCorrelator.collect_timeouts()`，对等待另一侧相机超过 `pending_timeout_ms` 的袋体生成 `TimedOutBagContext`。

超时结果会走 `_build_timeout_result()`：

1. 基于最后一帧构造新的 `timeout-*` frame id
2. 沿用已有 Stage 1 / Stage 2 结果
3. 使用超时后的 `BagSummary`
4. 下发 `timeout_action`
5. 调度结果图保存并写入 SQLite 记录

## 输出结果

`InspectionResult` 包含：

- 输入帧信息：`frame_packet`
- 感知结果：`stage1_result`, `stage2_result`
- 决策结果：`decision_result`
- 袋体聚合：`bag_summary`
- 控制命令：`control_commands`
- 执行反馈：`execution_feedbacks`
- 耗时：`timing_breakdown`
- 状态轨迹：`state_trace`
- 图片路径和 base64：`backup_path`, `result_image_path`, `image_base64`
