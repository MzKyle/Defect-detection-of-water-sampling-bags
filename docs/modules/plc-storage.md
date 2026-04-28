# plc / storage - 执行与留档

## PLC 控制层

**路径**: `waterbag_inspection/plc.py`

PLC 层分为两层：

| 层 | 类 | 职责 |
| --- | --- | --- |
| Controller | `ReliablePLCController` | Ack 超时判断、重试、汇总反馈 |
| Transport | `MockPLCTransport`, `ModbusPLCTransport` | 实际发送一次命令 |

## 控制命令

`DefaultDecisionPolicy.build_commands()` 会生成：

| target | action | 说明 |
| --- | --- | --- |
| `bag_controller` | `accept` | 袋体放行 |
| `bag_controller` | `reject` | 袋体判退 |
| `repeat_alert` | `pulse` | 疑似重复缺陷，发出清洁/污染提示 |

## Ack / Retry

`ReliablePLCController` 的关键参数：

| 参数 | 说明 |
| --- | --- |
| `ack_timeout_ms` | 单次 Ack 超时阈值 |
| `max_retries` | 失败后的最大重试次数 |
| `retry_interval_ms` | 重试间隔 |
| `mock_fail_first_attempts` | mock 模式下前 N 次失败 |
| `mock_ack_latency_ms` | mock 模式下 Ack 延迟 |

执行结果为 `ExecutionFeedback`：

| 字段 | 说明 |
| --- | --- |
| `success` | 最终是否成功 |
| `latency_ms` | 总耗时 |
| `attempts` | 尝试次数 |
| `timed_out` | 是否超时 |
| `ack_timeout_ms` | 超时阈值 |
| `attempt_details` | 每次尝试的成功/超时/详情 |

## Modbus 写寄存器

`ModbusPLCTransport` 中：

- `accept` 写入值 `2`
- `reject` 写入值 `1`
- `repeat_alert` 会对 `alert` 寄存器先写 `1`，等待 `alert_pulse_seconds`，再写 `2`

寄存器地址来自配置：

```yaml
plc:
  registers:
    cam1: 100
    cam2: 102
    alert: 104
    bag: 106
```

## SQLite 留档

**路径**: `waterbag_inspection/storage.py`

表名：

```text
detection_results
```

主要字段：

| 字段 | 说明 |
| --- | --- |
| `created_at` | 检测时间 |
| `frame_id` | 帧 ID |
| `bag_id` | 袋体 ID |
| `camera_id`, `camera_name` | 相机信息 |
| `source_path` | 原始图片路径 |
| `backup_path` | 备份图片路径 |
| `result_image_path` | 结果图片路径 |
| `status` | 正常 / 异常 / 待确认 / 超时 |
| `is_defect` | 是否缺陷 |
| `repeated` | 是否重复缺陷 |
| `plc_success` | PLC 是否成功 |
| `decision_action` | accept / reject / await_peer_camera |
| `decision_reason` | 决策原因 |
| `bag_summary` | 袋体级聚合 JSON |
| `control_commands` | 控制命令 JSON |
| `execution_feedbacks` | PLC 反馈 JSON |
| `state_trace` | 状态轨迹 JSON |
| `timing_breakdown` | 耗时拆解 JSON |

## 指标聚合

`repository.metrics(limit)` 会返回：

- 正常 / 异常 / 待确认 / 超时数量
- 重复缺陷数量
- Ack retry 数量
- stale frame 数量
- PLC failure 数量
- 平均总耗时
- 平均控制耗时
- 平均 Ack 尝试次数
- 最近故障行
