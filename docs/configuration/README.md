# 配置说明

当前运行配置以 C++ INI 为主，默认文件是：

```text
config/cpp_backend/demo.ini
```

这个文件同时服务 C++ 后端和 Python 看板：C++ 读取实时参数，Python 看板读取相机目录和 JSONL 路径。

## 配置总览

| 段 | 作用 |
| --- | --- |
| `[service]` | 服务启动和运行时长 |
| `[logging]` | 控制台日志和文件日志 |
| `[storage]` | JSONL 输出、异步写盘和队列策略 |
| `[detector.primary]` | stage-1 整图缺陷检测模型 |
| `[detector.patch]` | stage-2 微缺陷/patch 检测模型 |
| `[camera.*]` | 相机 ID、名称和 watch 目录 |
| `[runtime]` | 轮询、文件稳定等待、队列、worker、采图齐套和分拣超时 |
| `[detection]` | PLC presence gate、stage 阈值、patch 开关和工位放行策略 |
| `[correlation]` | A/B 面袋级关联、对端相机等待和 timeout 策略 |
| `[plc]` | PLC 开关、ack 超时、重试和 mock 故障注入 |

## Service

```ini
[service]
auto_start = true
run_for_ms = 0
```

| 键 | 说明 |
| --- | --- |
| `auto_start` | 预留给服务化运行的自动启动语义 |
| `run_for_ms` | watch 模式最长运行时间，`0` 表示持续运行直到收到停止信号 |

## Logging

```ini
[logging]
level = info
console = true
file = artifacts/cpp_backend/service.log
```

日志用于排查服务启动、配置加载、结果输出和 worker 状态。JSONL 是业务结果，log 是服务运行状态，两者不要混为一谈。

## Storage

```ini
[storage]
result_jsonl = artifacts/cpp_backend/results.jsonl
async_result_writes = true
result_queue_capacity = 512
drop_results_when_full = true
```

| 键 | 建议 | 说明 |
| --- | --- | --- |
| `result_jsonl` | 按产线或日期规划路径 | C++ 输出结果，Python 看板读取它 |
| `async_result_writes` | 生产建议 true | 用后台线程写 JSONL，减少磁盘 IO 抖动 |
| `result_queue_capacity` | 结合节拍和磁盘性能调整 | 异步写盘队列容量 |
| `drop_results_when_full` | 实时优先时 true，留档优先时 false | true 会丢最旧日志保障实时链路，false 会反压保存路径 |

如果项目要求完整审计留档，应将 `drop_results_when_full=false`，并确保磁盘写入能力足够。否则实时节拍和结果完整性之间会出现取舍。

## Detector

presence 已改为 PLC 激光到位消息，不再配置独立的 `detector.presence` 模型。当前只有 primary 和 patch 两个视觉 detector 段：

```ini
[detector.primary]
backend = mock
# backend = onnxruntime_cuda
# model_path = artifacts/models/primary.onnx
# use_cuda = true
# cuda_device_id = 0
# imgsz = 640
# nms_iou_threshold = 0.45
# max_detections = 100
```

| 键 | 说明 |
| --- | --- |
| `backend` | `mock`、`onnxruntime_cpu`、`onnxruntime_cuda` 等 |
| `model_path` | ONNX 模型路径 |
| `use_cuda` | 是否启用 CUDA Execution Provider |
| `cuda_device_id` | GPU ID |
| `imgsz` | letterbox 输入尺寸 |
| `nms_iou_threshold` | NMS IoU 阈值 |
| `max_detections` | 最大保留框数 |

ONNX Runtime 需要构建时开启：

```bash
cmake -S cpp_backend -B build/cpp_backend -DWATERBAG_ENABLE_ONNXRUNTIME=ON
cmake --build build/cpp_backend -j
```

如果没有安装 ONNX Runtime C++ headers/libs 或 OpenCV，构建会失败。mock 后端不需要这些依赖，适合开源读者先跑通链路。

## Camera

```ini
[camera.1]
id = 1
name = A-camera
watch_dir = demo_data/camera1

[camera.2]
id = 2
name = B-camera
watch_dir = demo_data/camera2
```

当前开源服务通过 watch 目录模拟相机输入。真实设备接入时可以保留相同 `camera_id` 和 `name`，将输入源替换为工业相机 SDK callback。

`bag_id` 在 demo 中从文件名推导，例如：

```text
bag_500_cam1_good.jpg -> bag_id = bag_500
bag_500_cam2_good.jpg -> bag_id = bag_500
```

生产现场建议由 PLC 或主控生成递增 `bag_id`，而不是依赖文件名。这样 `bag_id` 同时代表物理进入设备的顺序，便于顺序分拣。

## Runtime

```ini
[runtime]
poll_interval_ms = 100
file_stable_ms = 300
file_ready_timeout_ms = 5000
cooldown_ms = 300
queue_capacity = 256
defect_worker_count = 4
expected_burst_images_per_camera = 3
bag_capture_timeout_ms = 1500
sort_result_timeout_ms = 1500
```

| 键 | 作用 |
| --- | --- |
| `poll_interval_ms` | watch 模式扫描相机目录的间隔 |
| `file_stable_ms` | 文件大小和 mtime 需要稳定多久才读取 |
| `file_ready_timeout_ms` | 等待文件写完的最长时间 |
| `cooldown_ms` | 同一相机连续处理的最小间隔，避免重复触发 |
| `queue_capacity` | station、defect、sort 队列容量 |
| `defect_worker_count` | 缺陷推理 worker 数 |
| `expected_burst_images_per_camera` | 每个相机位置期望的 burst 图像数，当前默认 3 |
| `bag_capture_timeout_ms` | A/B 面和 burst 图齐套等待超时 |
| `sort_result_timeout_ms` | 末端分拣顺序队首等待超时 |

调参建议：

- `file_stable_ms` 太小可能读取半写入图片，太大增加响应延迟。
- `defect_worker_count` 需要结合 GPU/CPU 推理能力压测，不是越大越好。
- `bag_capture_timeout_ms` 应小于袋子到达末端分拣前的安全窗口。
- `sort_result_timeout_ms` 应根据机械距离、推理 P99 和分拣拨杆窗口设定。
- 两类超时默认走 NG，这是工业安全策略，不建议改成默认 OK。

## Detection

```ini
[detection]
presence_enabled = true
advance_on_presence = true
advance_trigger_camera_id = 0
patch_enabled = true
patch_horizontal = 4
patch_vertical = 5
primary_conf_threshold = 0.30
patch_conf_threshold = 0.20
```

| 键 | 说明 |
| --- | --- |
| `presence_enabled` | 是否启用 PLC 激光 presence gate |
| `advance_on_presence` | PLC presence 有袋后是否执行工位拨杆放行 |
| `advance_trigger_camera_id` | 指定哪个相机触发放行，`0` 表示每个相机工位控制自己的拨杆 |
| `patch_enabled` | stage-1 无缺陷时是否运行 stage-2 |
| `patch_horizontal` / `patch_vertical` | stage-2 patch 策略的配置位，便于生产 detector 实现切片策略 |
| `primary_conf_threshold` | stage-1 框过滤阈值 |
| `patch_conf_threshold` | stage-2 框过滤阈值 |

阈值建议从验证集和现场样本一起定，不要只看公开指标。水样袋项目通常更怕漏检，阈值调低会提升召回，但会增加误检和分拣 NG 数。

## Correlation

```ini
[correlation]
enabled = true
expected_camera_ids = 1,2
hold_non_defect_until_complete = true
pending_timeout_ms = 1500
timeout_action = reject
finalized_retention_ms = 5000
```

| 键 | 说明 |
| --- | --- |
| `enabled` | 是否启用袋级关联 |
| `expected_camera_ids` | 一个完整袋体需要等待的相机 ID |
| `hold_non_defect_until_complete` | 单面通过时是否等待其他面，建议 true |
| `pending_timeout_ms` | 等待对端相机结果的超时时间 |
| `timeout_action` | 对端超时后的动作，默认 `reject` |
| `finalized_retention_ms` | 已完成袋体在内存状态中的保留时间，用于过滤迟到旧帧 |

`hold_non_defect_until_complete=true` 是双面检测的核心安全参数：任何单面 OK 都不能代表整袋 OK。

## PLC

```ini
[plc]
enabled = true
ack_timeout_ms = 200
presence_message_timeout_ms = 200
max_retries = 1
retry_interval_ms = 50
mock_fail_first_attempts = 0
mock_ack_latency_ms = 0
mock_presence_latency_ms = 0
```

| 键 | 说明 |
| --- | --- |
| `enabled` | false 时 PLC 命令直接视作成功，适合纯软件验证 |
| `ack_timeout_ms` | 单次命令 ack 超时 |
| `presence_message_timeout_ms` | 等待 PLC 激光到位消息的超时，超时后 `status=timeout` 且 `reason=plc_laser_presence_timeout` |
| `max_retries` | 失败后的最大重试次数 |
| `retry_interval_ms` | 重试间隔 |
| `mock_fail_first_attempts` | mock 模式故障注入，前 N 次返回失败 |
| `mock_ack_latency_ms` | mock 模式模拟 ack 延迟 |
| `mock_presence_latency_ms` | mock 模式模拟 PLC 激光 presence 消息延迟 |

真实产线中，PLC 参数要和机构动作时间、通信协议、IO 响应和分拣窗口一起标定。ack 超时过短会误判失败，过长会拖慢 fail-safe 反应。

生产 PLC 适配器需要把现场激光传感器消息解码到 `PlcLaserPresence`：`bag_present=true` 时才进入 burst 采图；`bag_present=false` 时记录 `no_bag`；如果 PLC 消息携带递增 BagID，主流程会优先使用该 BagID 做组包和顺序分拣。

## Python 看板环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `WATERBAG_CPP_CONFIG` | `config/cpp_backend/demo.ini` | 默认 C++ 配置 |
| `WATERBAG_DASHBOARD_DB` | `artifacts/dashboard/inspection.db` | SQLite 路径 |
| `WATERBAG_DASHBOARD_UPLOAD_DIR` | `artifacts/uploads` | 上传暂存目录 |
| `WATERBAG_DASHBOARD_HOST` | `0.0.0.0` | 看板监听地址 |
| `WATERBAG_DASHBOARD_PORT` | `5000` | 看板端口 |
| `WATERBAG_DASHBOARD_NAME` | `Waterbag Inspection Dashboard` | 页面标题 |

## 训练数据配置

模型训练使用 Ultralytics YAML：

```text
config/waterbag.yaml
```

当前示例：

```yaml
path: ../datasets/waterbag
train: images/train
val: images/val
test:
names:
  0: anomaly
```

真实项目可以把 `names` 扩展成更细的缺陷类别，例如 `pin_hole`、`hair`、`foreign_object`、`seal_defect`、`stain`。但如果上线分拣只需要 OK/NG，早期用一个 `anomaly` 类可以减少标注一致性问题。
