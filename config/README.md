# 配置目录

本目录集中存放当前项目仍在使用的配置文件。

| 文件 | 说明 |
| --- | --- |
| `demo.yaml` | Python demo、Web、回放和故障注入的默认配置 |
| `production.example.yaml` | Python 生产部署配置模板 |
| `waterbag.yaml` | Ultralytics YOLOv8 / YOLO11 训练数据集配置 |
| `cpp_backend/demo.ini` | C++ 实时后端 demo 配置 |
| `env.example` | 环境变量示例 |

历史 YOLOv5 上游示例数据集配置已经归档到 `yolo_legacy/data/`，当前训练入口默认使用 `config/waterbag.yaml`。

`cpp_backend/demo.ini` 中与实时链路安全相关的关键项：

```ini
[runtime]
defect_worker_count = 4
expected_burst_images_per_camera = 3
bag_capture_timeout_ms = 1500
sort_result_timeout_ms = 1500

[storage]
async_result_writes = true
result_queue_capacity = 512
drop_results_when_full = true
```

其中 `defect_worker_count` 控制并行推理线程数；同一 `bag_id` 固定进入同一个 worker，不同袋体可以并行。`bag_capture_timeout_ms` 用于 A/B 面 6 张 burst 图像齐套等待，`sort_result_timeout_ms` 用于末端分拣前的 BagID 顺序重排等待；两类超时均按 fail-safe NG 处理。

如果推理主要跑单 GPU，不建议盲目把 `defect_worker_count` 开大；现场应按吞吐、P95/P99 延迟和显存占用压测。C++ runtime 已将末端分拣 PLC 放入独立 sorter 线程，JSONL 结果也可异步写入，避免推理 worker 被串口 Ack 或磁盘 IO 阻塞。

如果要启用 C++ 后端的 ONNX Runtime CUDA 推理，请参考 [cpp_backend/README.md](../cpp_backend/README.md)，并在构建时打开 `WATERBAG_ENABLE_ONNXRUNTIME=ON`。更详细的配置字段说明见 [docs/configuration/demo-config.md](../docs/configuration/demo-config.md)。
