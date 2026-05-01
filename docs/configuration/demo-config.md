# Demo 配置

默认配置：

```text
config/demo.yaml
```

## app

```yaml
app:
  name: Waterbag Inspection Demo
  host: 0.0.0.0
  port: 5000
  auto_start: true
  open_browser: false
  browser_url: http://127.0.0.1:5000
```

| 字段 | 说明 |
| --- | --- |
| `name` | Web 页面标题 |
| `host` | Flask 监听地址 |
| `port` | Flask 端口 |
| `auto_start` | 服务启动后是否自动开始监听相机目录 |
| `open_browser` | 是否自动打开浏览器 |
| `browser_url` | 自动打开的地址 |

## cameras

```yaml
cameras:
  - id: 1
    name: A面相机
    watch_dir: demo_data/camera1
  - id: 2
    name: B面相机
    watch_dir: demo_data/camera2
```

`watch_dir` 可以是相对路径或绝对路径。相对路径会按项目根目录解析。

## models

Demo 默认使用 mock 检测器：

```yaml
models:
  primary:
    backend: mock
    device: cpu
    conf_thres: 0.3
    iou_thres: 0.3
  patch:
    backend: mock
    device: cpu
    conf_thres: 0.2
    iou_thres: 0.3
```

mock 检测器不加载权重，而是根据文件名关键词模拟缺陷。

## patch_detection

```yaml
patch_detection:
  enabled: true
  horizontal: 4
  vertical: 5
  conf_thres: 0.2
  iou_thres: 0.3
  save_visualizations: false
  visualization_dir: artifacts/patch_vis
```

| 字段 | 说明 |
| --- | --- |
| `enabled` | 是否启用 Stage 2 |
| `horizontal` | 横向 patch 数 |
| `vertical` | 纵向 patch 数 |
| `save_visualizations` | 是否保存 patch 可视化 |

## plc

Demo 默认 mock PLC：

```yaml
plc:
  backend: mock
  enabled: true
  ack_timeout_ms: 200
  max_retries: 1
  retry_interval_ms: 50
  mock_ack_latency_ms: 0
  mock_fail_first_attempts: 0
```

如果要演示 Ack retry，可以将 `mock_fail_first_attempts` 设置为 `1` 或通过故障注入命令运行。

## correlation

```yaml
correlation:
  enabled: true
  expected_camera_ids: [1, 2]
  hold_non_defect_until_complete: true
  pending_timeout_ms: 1500
  timeout_action: reject
  finalized_retention_ms: 5000
```

Demo 中，正常袋体需要等双相机到齐才会放行。

## runtime

```yaml
runtime:
  backup_dir: artifacts/backups
  result_dir: artifacts/results
  upload_dir: artifacts/uploads
  cooldown_seconds: 0.3
  file_ready_timeout_seconds: 5.0
  file_stable_seconds: 0.3
  queue_poll_interval_seconds: 0.2
  async_artifact_writes: false
  artifact_queue_size: 128
```

这些参数决定目录监听、worker 处理节奏和备份/结果图落盘方式。Demo 默认同步落盘，生产配置建议开启 `async_artifact_writes`。

## C++ 后端配置

C++ 实时链路使用 [config/cpp_backend/demo.ini](../../config/cpp_backend/demo.ini)。它把 detector、runtime、plc 和 service 分成独立的 INI 分区，默认仍然是 mock；只有在构建时打开 `WATERBAG_ENABLE_ONNXRUNTIME=ON`，并把 detector 分区的 `backend` 改成 `onnxruntime_cuda`，才会启用真实 ONNX Runtime 推理。

关键分区如下：

| 分区 | 说明 |
| --- | --- |
| `service` | 服务自动启动和运行时长 |
| `logging` | 日志级别、控制台输出和文件输出 |
| `storage` | JSONL 留档路径和异步写入队列 |
| `detector.presence` / `detector.primary` / `detector.patch` | 三路 detector 配置，支持 mock 或 onnxruntime_cuda |
| `runtime` | 目录监听、队列、worker 和超时 |
| `detection` | presence / patch 阈值 |
| `correlation` | 袋体级关联与超时处理 |
| `plc` | Ack / retry 和 mock PLC 参数 |

示例 detector 配置：

```ini
[detector.primary]
backend = mock
# backend = onnxruntime_cuda
# model_path = artifacts/models/primary.onnx
# use_cuda = true
# cuda_device_id = 0
# imgsz = 640
# nms_iou_threshold = 0.45
```

`detector.presence` 和 `detector.patch` 使用同样的字段结构。对应的 C++ 实时后端说明见 [cpp_backend/README.md](../../cpp_backend/README.md)。

C++ 后端默认启用异步 JSONL 写入：

```ini
[storage]
async_result_writes = true
result_queue_capacity = 512
drop_results_when_full = true
```

单 GPU 推理时，`defect_worker_count` 不等于实际加速倍数；建议用 1、2、4 分别压测 P95/P99，再选择现场配置。
