# 配置目录

当前实时运行主线只保留 C++ 后端配置；Python 保留 Web 看板和 SQLite 结果库，用于读取 C++ 后端 JSONL。

| 文件 | 说明 |
| --- | --- |
| `cpp_backend/demo.ini` | C++ 实时后端 demo 配置 |
| `waterbag.yaml` | Ultralytics YOLOv8 / YOLO11 训练数据集配置 |
| `env.example` | 本地环境变量示例 |

`cpp_backend/demo.ini` 中与实时链路安全相关的关键项：

```ini
[runtime]
defect_worker_count = 4
expected_burst_images_per_camera = 3
bag_capture_timeout_ms = 1500
sort_result_timeout_ms = 1500

[plc]
presence_message_timeout_ms = 200

[storage]
async_result_writes = true
result_queue_capacity = 512
drop_results_when_full = true

[camera_driver]
backend = mock
# backend = hikvision_mvs
default_trigger_source = Line0
enable_ptp = true
enable_chunk_timestamp = true
```

其中 `presence_message_timeout_ms` 控制 PLC 激光到位消息等待时间；超时会跳过采图，记录 `status=timeout` 和 `plc_laser_presence_timeout`。`defect_worker_count` 控制并行推理线程数；同一 `bag_id` 固定进入同一个 worker，不同袋体可以并行。`bag_capture_timeout_ms` 用于 A/B 面 burst 图像齐套等待，`sort_result_timeout_ms` 用于末端分拣前的 BagID 顺序重排等待；后两类超时均按 fail-safe NG 处理。

`camera_driver.backend = hikvision_mvs` 会启用 `/opt/MVS` 海康 MVS SDK 真实相机驱动，按 `serial_number`、`device_user_id` 或 `device_index` 绑定 `[camera.N]`。Mock 已拆到独立 `mock_camera_driver` 包，主要用于本地 demo 和 C++ 回归测试。

如果主推理后端跑单 GPU，不建议盲目把 `defect_worker_count` 开大。现场应按吞吐、P95/P99 延迟、显存占用和 PLC 分拣窗口压测后调整。

ONNX Runtime CUDA 推理说明见 [cpp_backend/README.md](../cpp_backend/README.md)。

Web 看板默认读取 `[storage] result_jsonl`，并同步到：

```text
artifacts/dashboard/inspection.db
```

可用环境变量 `WATERBAG_DASHBOARD_DB` 覆盖 SQLite 路径。
