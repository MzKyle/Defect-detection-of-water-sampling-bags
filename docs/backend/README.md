# C++ 后端

C++ 后端位于 `cpp_backend/`，是项目的唯一实时执行链路。它承担相机输入、多光源 burst、PLC 动作、缺陷推理调度、袋级状态机、顺序分拣和 JSONL 结果输出。

开源代码默认使用 mock 相机和 mock PLC，目的是让任何人都能编译测试；工程接口按真实现场设计，生产接入时替换底层适配器即可。

## 模块结构

| 模块 | 关键文件 | 职责 |
| --- | --- | --- |
| `camera_driver` | `burst_capture.hpp/.cpp` | `BurstPlan`、`CaptureSession`、`CaptureGroup`、多光源图像元数据、相机曝光时间 |
| `PLC_driver` | `plc.hpp/.cpp`、`plc_controller.hpp/.cpp` | PLC 激光到位消息、光源 burst 事件、工位拨杆、末端分拣、ack 超时、重试 |
| `detect_orchestrator` | `pipeline.cpp` | PLC presence、采图、缺陷推理、袋级决策、分拣命令 |
| `detect_orchestrator` | `runtime.cpp` | watch 模式线程、队列、worker 池、sorter |
| `detect_orchestrator` | `bag_runtime.cpp` | `BagCaptureAssembler` 和 `SortReorderBuffer` |
| `detect_orchestrator` | `correlator.cpp` | 双相机/多相机袋级状态关联 |
| `detect_orchestrator` | `detector.cpp` | Mock detector 和可选 ONNX Runtime detector |
| `detect_orchestrator` | `storage.cpp` | JSONL 序列化和可选异步写盘 |
| `detect_orchestrator` | `service_main.cpp` | `--once` / `--watch` 服务入口 |

## 实时流程

```text
station packet
  -> 文件稳定/相机帧就绪
  -> PLC laser presence gate
  -> arm burst
  -> start_light_burst
  -> CaptureGroup 对齐校验
  -> release_station_after_capture
  -> BagCaptureAssembler
  -> defect worker pool
  -> BagCorrelator
  -> SortReorderBuffer
  -> sorter thread
  -> end_sorter PLC
  -> JsonlResultRepository
```

`process_station_packet` 和 `process_defect_packet` 是两个关键阶段：

| 阶段 | 主要动作 | 为什么这样拆 |
| --- | --- | --- |
| station | presence、burst 采图、光源/曝光对齐、工位拨杆放行 | 采图完成就可以放行机构，不等待慢速缺陷推理 |
| defect | stage-1、stage-2、多光源融合、袋级关联 | 缺陷推理可并发、可乱序，但必须在末端分拣前完成 |

## PLC Laser Presence Gate

presence gate 现在由 PLC 激光到位消息驱动，只回答一个问题：当前工位有没有水样袋。C++ 不再为 presence 单独运行图像模型。

如果没有袋：

```text
control_action = no_bag
reason = plc_laser_no_bag
skip defect detection
```

如果有袋：

```text
plc_laser_presence:bag_present
-> capture_session
-> burst_start
-> burst_sync_valid
-> advance_command_dispatch
-> defect_enqueued
```

这样设计有三个好处：

- 空背景不进入 burst 和缺陷模型，减少无效采图和无效推理。
- 工位动作只在 PLC 激光确认有袋后执行。
- PLC presence 消息耗时被单独记录为 `presence_ms`，方便评估到位消息是否影响节拍。

生产 PLC 适配器应实现 `IPlcController::read_laser_presence(packet)`，把现场消息解码为 `PlcLaserPresence`。如果消息携带 PLC 生成的递增 BagID，填入 `bag_id` 后主流程会优先使用它；消息超时会记录 `status=timeout`、`reason=plc_laser_presence_timeout` 并跳过采图。mock 实现支持用 `FramePacket.metadata["plc.laser_present"]` 指定有袋/无袋，没有 metadata 时继续用文件名模拟。

## 多光源 Burst

当前生产默认 burst plan 为每个相机位置 3 帧：

```text
frame 0 -> L1_BACKLIGHT
frame 1 -> L2L3_DUAL_DARKFIELD
frame 2 -> L4_CROSS_POLARIZED
```

对应代码在 `make_production_burst_plan()` 中：

| 帧 | 曝光 | 脉冲 | settle | 用途 |
| --- | --- | --- | --- | --- |
| 0 | 100 us | 120 us | 500 us | 背光，增强透光异常、针孔、异物 |
| 1 | 200 us | 240 us | 500 us | 双暗场，增强折痕、划痕、细线边缘 |
| 2 | 600 us | 700 us | 800 us | 交叉偏振漫射，抑制反光和浅色伪缺陷 |

station 阶段会把 burst 结果写入 `FramePacket.metadata`：

```text
burst.capture_session_id
burst.plan_id
burst.image_count
burst.sync_valid
burst.light_ids
burst.images.N.light_id
burst.images.N.path
burst.images.N.camera_frame_id
burst.images.N.exposure_start_hw_ns
burst.images.N.exposure_end_hw_ns
burst.images.N.host_received_hw_ns
burst.images.N.jitter_us
burst.images.N.light_window_ok
```

defect worker 收到 packet 后，会从这些 metadata 恢复出多个 burst 子输入，逐路光源推理，再融合到同一个阶段结果。

## 硬触发与硬件适配

生产链路建议使用硬触发或接近硬触发的采图方式：

```text
camera.start_grabbing()
camera.arm_burst(session, plan)
plc.start_light_burst(session, plan)
频闪/IO 按 plan 输出光源和 camera trigger
camera frame callback 连续返回 frame0/frame1/frame2
CaptureGroup assembled
```

`mock_camera_driver` 包中的 `MockCameraBurstCapture` 会模拟这个过程。`camera_driver` 包中的真实相机实现负责：

- 打开相机、ROI、曝光、增益、触发模式配置。
- 相机提前进入 grabbing 状态。
- 每次 session 只注册期望帧数和 `capture_session_id`。
- 在 frame callback 中读取 frame id、chunk timestamp、曝光时间和图像 buffer。
- 将图像放入内存 ring buffer 或快速缓存，不在 callback 里做推理和慢速磁盘写入。

硬件 PLC/IO 接入时，建议让 `IPlcTransport` 或 `IPlcController` 的生产实现完成：

- 启动光源 burst 序列。
- 记录 light on、camera trigger、light off、burst done。
- 执行工位下拨杆 release、上拨杆 push、复位。
- 执行末端 `route_to_ok_bin` / `route_to_ng_bin`。
- 记录 ack、重试、超时和动作耗时。

## 时间同步和 jitter 校验

C++ 后端把光源、相机和工控机事件放到同一套硬件时间戳抽象里：

```cpp
struct HardwareTimestamp {
    long long ns = 0;
};
```

当前开源实现中 `UnifiedHardwareClock` 基于 `steady_clock` 模拟统一时钟。真实设备中建议绑定 PTP/IEEE1588、相机硬件时钟、PLC 高速 IO 时间戳或触发控制器全局 tick。

一次 burst 中会对齐两组事件：

| 来源 | 事件 |
| --- | --- |
| PLC/频闪 | `light_on_hw`、`camera_trigger_hw`、`light_off_hw` |
| 相机 | `exposure_start_hw`、`exposure_end_hw`、`host_received_hw` |

校验逻辑：

```text
light_on_before_exposure = light_on_hw <= exposure_start_hw
light_off_after_exposure = exposure_end_hw <= light_off_hw
trigger_to_exposure_jitter_us = exposure_start_hw - camera_trigger_hw
within_jitter_tolerance = abs(jitter) <= plan.jitter_tolerance_us
```

如果任意帧光源窗口不覆盖曝光，或 jitter 超阈值，则 `CaptureGroup.sync_valid=false`，主流程记录 `burst_sync_warning`，并走 `capture_invalid`。这让现场排查很直接：JSONL 的 `state_trace` 能看到是哪一帧、哪一路光源、哪个 session 出了时序问题。

## 两阶段检测和多光源融合

缺陷阶段由 `run_multi_light_detection()` 执行。它会对每个 burst 子输入调用 detector：

```text
stage1_running:inputs=3
stage1_light:L1_BACKLIGHT:boxes=0
stage1_light:L2L3_DUAL_DARKFIELD:boxes=1
stage1_light:L4_CROSS_POLARIZED:boxes=0
stage1_fused:boxes=1
```

决策规则：

| 情况 | 结果 |
| --- | --- |
| stage-1 有框 | 本面判定缺陷，`stage_source=stage1` |
| stage-1 无框且 `patch_enabled=true` | 运行 stage-2 |
| stage-2 有框 | 本面判定微缺陷，`stage_source=stage2` |
| 两阶段都无框 | 本面通过，等待袋级聚合 |

当前配置中的 `detector.primary`、`detector.patch` 可以分别接不同 ONNX 模型。开源 mock detector 根据文件名模拟缺陷，方便测试整个控制链路。ONNX detector 使用 OpenCV 做图像读取和 letterbox，ONNX Runtime 输出经过解码和 NMS 后转换为 `DetectionBox`。

## 袋体组包

`BagCaptureAssembler` 确保一个袋子不是“单面单张图”就进入最终判断。它等待配置中的全部相机：

```ini
[correlation]
expected_camera_ids = 1,2

[runtime]
expected_burst_images_per_camera = 3
bag_capture_timeout_ms = 1500
```

齐套条件：

```text
camera1 burst.image_count >= 3 且 sync_valid=true
camera2 burst.image_count >= 3 且 sync_valid=true
```

只有齐套后，才把 A/B 两个 `FramePacket` 投递给 defect worker。超时未齐套时生成 fail-safe 结果：

```text
control_action = reject
reason = image_lost_capture_timeout:missing_cameras=...
timed_out = true
```

## 袋级关联

`BagCorrelator` 聚合同一个 `bag_id` 下不同相机的局部判断：

| 情况 | 袋级动作 |
| --- | --- |
| 任意相机发现缺陷 | `reject`，原因 `aggregate_defect_detected` |
| 所有期望相机都通过 | `accept`，原因 `all_cameras_passed` |
| 只收到部分相机结果 | `await_peer_camera` |
| 等待对端超时 | 按 `timeout_action`，默认 `reject` |

`hold_non_defect_until_complete=true` 时，单面通过不会马上放行 OK，必须等另一面也通过。这是水样袋这类双面检测项目里非常重要的安全策略。

## 顺序分拣

缺陷 worker 可以并发，结果可能乱序完成。例如 Bag 101 比 Bag 100 先推理完。末端只有一个物理分拣窗口时，如果谁先推理完就谁先打拨杆，会打错袋。

`SortReorderBuffer` 的策略：

```text
1. station 捕获成功时 register_bag，记录物理进入顺序。
2. defect worker 乱序完成后 store_result。
3. collect_ready 只释放队首已有结果的 bag_id。
4. 队首超过 sort_result_timeout_ms 没结果，生成 fail-safe NG。
5. sorter_thread 调用 execute_sort_command 执行末端 PLC。
```

这将视觉吞吐和机械分拣顺序解耦：推理可以多线程，末端动作必须串行且有序。

## 多线程与队列

watch 模式线程模型：

| 线程 | 主要工作 |
| --- | --- |
| `poll_thread` | 轮询各相机 `watch_dir`，发现新图片后入 station 队列 |
| `worker_thread` | 等待文件稳定，运行 station 阶段，触发采图和工位动作 |
| `defect_threads` | 并行执行缺陷推理和袋级关联 |
| `sorter_thread` | 串行执行末端 OK/NG 分拣动作 |
| JSONL writer | 可选异步保存结果 |

队列实现使用 `deque`。队列满时，运行时队列会丢弃最旧输入，避免高频前端擦除导致尾延迟恶化。缺陷 worker 分片规则：

```text
worker_index = hash(bag_id) % defect_worker_count
```

这样同一袋子的任务会落到同一个 worker，不同袋子可以并发处理。

## 进程时间优化

项目内置的时间拆解不是装饰字段，而是用于现场调参：

| 字段 | 含义 |
| --- | --- |
| `queue_delay_ms` | 输入进入队列到被处理的等待时间 |
| `presence_inference_ms` | PLC 激光 presence 消息读取时间 |
| `advance_control_ms` | 工位放行动作耗时 |
| `stage1_inference_ms` | 整图缺陷检测耗时 |
| `stage2_inference_ms` | 微缺陷检测耗时 |
| `decision_ms` | 本地决策时间 |
| `correlation_ms` | 袋级关联时间 |
| `control_ms` | 末端分拣 PLC 动作耗时 |
| `total_ms` | 当前结果从进入处理到输出的总耗时 |

目前采取的优化措施：
- PLC 激光 presence gate 过滤无袋工位。
- station 和 defect 拆开，采图后先放行机构。
- stage-2 只在 stage-1 未发现缺陷时运行。
- defect worker pool 并发处理不同袋子。
- sorter thread 独立执行末端分拣。
- JSONL 可异步写盘，避免磁盘抖动影响实时线程。
- 文件输入会等待 size 和 mtime 稳定，避免读取半写入图片。
- `cooldown_ms` 防止同一相机过密重复触发。

调优建议：

- 单 GPU 推理时，不要盲目把 `defect_worker_count` 开很大。先从 1 或 2 开始，看 P95/P99、显存拷贝和 GPU 利用率。
- 如果 `queue_delay_ms` 持续升高，说明输入速度超过处理能力，优先检查模型耗时、图像分辨率和 worker 数。
- 如果 `advance_control_ms` 或 `control_ms` 高，问题通常在 PLC ack、通信超时或机械动作。
- 如果 `stage2_inference_ms` 占比高，可以提高 stage-1 召回、优化 patch detector、减少 patch 数或提升输入 ROI 策略。

## PLC Ack、重试和 fail-safe

`ReliablePlcController` 给每条 `ControlCommand` 提供 ack 超时和重试：

```ini
[plc]
enabled = true
ack_timeout_ms = 200
max_retries = 1
retry_interval_ms = 50
```

结果中会记录：

```text
plc_success
ack_attempts
ack_retry
execution_feedbacks[].detail
execution_feedbacks[].latency_ms
```

fail-safe 原则：

| 异常 | 默认处理 |
| --- | --- |
| 无袋 | `no_bag`，不做缺陷检测 |
| 采图时序无效 | `capture_invalid`，不进入正常 OK |
| A/B 面缺失 | timeout NG |
| 对端相机超时 | `timeout_action=reject` |
| 分拣队首结果超时 | fail-safe NG |
| PLC ack 超时/失败 | 结果标记 `plc_success=false`，看板显示 fault signal |

## 结果输出

默认结果文件：

```text
artifacts/cpp_backend/results.jsonl
```

每行是一条 `InspectionResult` JSON。典型字段：

```json
{
  "timestamp": "2026-05-01T12:00:00.123",
  "frame_id": "cam1-000000000001",
  "bag_id": "bag_500",
  "camera_id": 1,
  "status": "defect",
  "action": "reject",
  "reason": "aggregate_defect_detected",
  "is_defect": true,
  "finalized": true,
  "latency_ms": 12.345,
  "stage1_ms": 3.210,
  "stage2_ms": 4.567,
  "boxes": [],
  "control_commands": [],
  "execution_feedbacks": [],
  "state_trace": []
}
```

`state_trace` 是现场排查的重点，它会记录从 `received`、`presence_running`、`burst_alignment`、`defect_inputs` 到 `sorter_dispatch` 的完整状态轨迹。

## 构建和运行

基础构建：

```bash
cmake -S cpp_backend -B build/cpp_backend
cmake --build build/cpp_backend -j
```

处理当前目录里的样本后退出：

```bash
./build/cpp_backend/waterbag_cpp_service --config config/cpp_backend/demo.ini --once
```

持续轮询：

```bash
./build/cpp_backend/waterbag_cpp_service --config config/cpp_backend/demo.ini --watch
```

覆盖缺陷 worker 数：

```bash
./build/cpp_backend/waterbag_cpp_service \
  --config config/cpp_backend/demo.ini \
  --watch \
  --defect-workers 2
```

启用 ONNX Runtime：

```bash
cmake -S cpp_backend -B build/cpp_backend -DWATERBAG_ENABLE_ONNXRUNTIME=ON
cmake --build build/cpp_backend -j
```

启用后可配置：

```ini
[detector.primary]
backend = onnxruntime_cuda
model_path = artifacts/models/primary.onnx
use_cuda = true
cuda_device_id = 0
imgsz = 640
nms_iou_threshold = 0.45
```
