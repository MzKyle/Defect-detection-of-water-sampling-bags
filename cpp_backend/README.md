# 水样袋缺陷检测 C++ 实时后端

本目录是水样袋缺陷检测项目中对实时性要求较高链路的 C++ 实现，主要覆盖相机接入、多光源 burst 采图、PLC 动作控制、状态编排和异步缺陷推理。

```text
工位线程：相机输入 -> PLC 激光到位消息 -> burst 抓拍锁存 -> PLC 拨杆动作 -> BagID 样本组装
推理线程：完整袋体样本 -> 缺陷检测 -> 袋级状态机 -> BagID 顺序重排
分拣线程：按袋序执行末端分拣 PLC -> 发布结果
日志线程：异步写入 JSONL 结果
```

Python 仍用于模型训练、数据标注、Web 看板、回放工具和 Demo；C++ 后端负责工业现场的实时链路，包括相机采集、低延迟推理适配、袋级状态融合、PLC 控制和长时间运行服务。

## 实时流程

```text
1. 轮询相机目录，或接收工业相机 SDK 回调。
2. 从 PLC 读取激光到位消息，判断当前工位是否有袋。
3. 如果没有袋子，跳过缺陷检测并记录 `no_bag`。
4. 下拨杆默认处于阻挡袋子的状态。如果某个相机工位检测到袋子，则锁存当前抓拍图像，然后执行工位动作序列：`camera1_bottom_lever:release_bag_after_capture`、`camera1_upper_lever:push_bag_after_capture`、`camera1_upper_lever:restore_after_push`、`camera1_bottom_lever:restore_blocking_position`。
5. station 结果进入 `BagCaptureAssembler`，只有同一个 `bag_id` 的 A 面 3 张和 B 面 3 张全部齐套，才把完整袋体样本送入缺陷检测队列。
6. 缺陷推理线程先执行 stage-1 整图缺陷检测。
7. 如果整图检测未发现明显缺陷，再执行 stage-2 patch 级细小缺陷检测。
8. defect worker 可以乱序完成推理，但最终结果必须进入 `SortReorderBuffer`。
9. 末端分拣结果进入独立 sorter 线程，按物理进入顺序释放 `bag_id`，再驱动 `end_sorter:route_to_ok_bin` 或 `end_sorter:route_to_ng_bin`。
10. 如果采图齐套或推理结果超时，默认输出 NG，符合 fail-safe 策略。
11. 将时序、PLC 反馈、PLC 激光 presence 结果和缺陷结果异步写入 JSONL。
```

## 模块结构

| 路径 | 作用 |
| --- | --- |
| `camera_driver/` | 海康 MVS 工业相机真实驱动、多光源 burst 采图和图像组包 |
| `mock_camera_driver/` | 本地 demo/tests 使用的相机 Mock 包 |
| `PLC_driver/` | PLC 激光到位消息、光源切换事件、拨杆动作和末端分拣动作 |
| `detect_orchestrator/` | 主流程编排、PLC presence gate、异步缺陷 worker 池、结果融合 |
| `detect_orchestrator/include/detect_orchestrator/schemas.hpp` | 跨模块共享的数据结构 |
| `detector.*` | 检测器接口、Mock 后端、ONNX Runtime CUDA 接入 |
| `correlator.*` | 双相机位置的袋级状态机 |
| `plc.*` | 带 ack 超时和重试机制的 PLC 控制封装 |
| `pipeline.*` | 实时检测流水线 |
| `runtime.*` | 相机目录轮询、工位队列、缺陷检测队列 |
| `storage.*` | 面向 Web/回放集成的 JSONL 结果落盘 |
| `config.*` | INI 配置加载 |
| `src/main.cpp` | 冒烟 Demo 程序 |
| `src/service_main.cpp` | 可配置的后端服务入口 |

CMake 当前暴露四个物理包，并且公共头文件入口与包名一致：

```text
camera_driver        -> target camera_driver        -> #include "camera_driver/..."
mock_camera_driver   -> target mock_camera_driver   -> #include "mock_camera_driver/..."
PLC_driver           -> target PLC_driver           -> #include "PLC_driver/..."
detect_orchestrator  -> target detect_orchestrator  -> #include "detect_orchestrator/..."
```

`waterbag_cpp` 保留为 demo 和测试使用的聚合 target。burst 采图会记录相机曝光、PLC 光源事件、工控机接收事件在统一硬件时间轴上的时间戳；每张图进入缺陷队列前都会进行光源-相机对齐和 I/O jitter 检查。

## 海康 MVS 相机驱动

`camera_driver` 已拆成真实驱动包，依赖本机海康 MVS SDK：

```text
/opt/MVS/include/MvCameraControl.h
/opt/MVS/lib/64/libMvCameraControl.so
```

服务配置中 `camera_driver.backend = hikvision_mvs` 时，会枚举 MVS 设备，按 `[camera.N]` 的 `serial_number`、`device_user_id` 或 `device_index` 绑定相机。驱动启动后配置 `TriggerMode=On`、默认 `TriggerSource=Line0`、`TriggerActivation=RisingEdge`，并在设备支持时开启 `ChunkModeActive`、`ChunkSelector=Exposure/Timestamp` 和 GigE PTP/IEEE1588。相机线程只等待外部硬触发帧，不发软件触发；收到帧后用相机 device timestamp 映射到统一硬件时间轴，保存 burst 图像，再生成 `CaptureGroup`。

```ini
[camera_driver]
backend = hikvision_mvs
output_dir = artifacts/cpp_backend/captures
default_trigger_source = Line0
enable_ptp = true
enable_chunk_timestamp = true

[camera.1]
id = 1
serial_number = 12345678
# 或 device_user_id = A-camera
# 或 device_index = 0
```

Mock 已经移到 `mock_camera_driver`，demo/tests 显式链接该包；生产代码不要从 `camera_driver` 里引用 Mock 类型。

## ONNX Runtime CUDA

如果要让 C++ 推理走 ONNX Runtime 的 CUDA Execution Provider，构建时打开开关：

```bash
cmake -S cpp_backend -B build/cpp_backend -DWATERBAG_ENABLE_ONNXRUNTIME=ON
cmake --build build/cpp_backend -j
```

前提是系统里已经能找到 ONNX Runtime 的 C++ 头文件和库。当前实现会把 `backend = onnxruntime_cuda`，或者 `backend = onnxruntime` 且 `use_cuda = true`，解析为 GPU 推理后端。

示例配置：

```ini
[detector.primary]
backend = onnxruntime_cuda
model_path = artifacts/models/primary.onnx
use_cuda = true
cuda_device_id = 0
imgsz = 640
nms_iou_threshold = 0.45
```

ONNX 模型可以通过仓库根目录下的 [export_ultralytics_onnx.py](../export_ultralytics_onnx.py) 从 `.pt` checkpoint 导出。

## PLC 激光 Presence

presence gate 已切换为 PLC 激光检测，不再运行独立的图像 presence 模型。station 阶段会调用 `IPlcController::read_laser_presence(packet)`：

```text
PLC 激光消息 present=false -> no_bag，跳过 burst 和缺陷检测
PLC 激光消息 present=true  -> arm burst，采图并放行工位
PLC presence 消息超时      -> status=timeout，reason=plc_laser_presence_timeout
```

生产适配器应在 `PLC_driver` 中把现场 PLC 发来的到位位、消息序号和可选 BagID 解码为 `PlcLaserPresence`。如果 PLC 已经生成递增 BagID，可以填入 `PlcLaserPresence::bag_id`，主流程会用它覆盖文件名推导出的 `bag_id`。开源 `MockSemanticPlcController` 会优先读取 `FramePacket.metadata["plc.laser_present"]`，没有该字段时用文件名中的 `empty` / `no_bag` / `background` 来模拟无袋。

## 多光源 Burst 推理

当前生产 burst 计划为每个相机位置采集 3 张图：

```text
frame 0 -> L1_BACKLIGHT
frame 1 -> L2L3_DUAL_DARKFIELD
frame 2 -> L4_CROSS_POLARIZED
```

station 阶段完成时，`CaptureGroup.images` 会被写入 `FramePacket.metadata`，包括 `burst.image_count`、每张图的 `light_id`、图像路径、相机帧号和硬件时间戳。缺陷 worker 收到 packet 后，会恢复出 3 个 burst 子输入，分别运行 stage-1 整图检测；如果 stage-1 没有缺陷，再分别运行 stage-2 patch 检测。每路光源的结果最终融合成同一袋面的一次缺陷结论，并在 `state_trace` 中记录类似 `defect_inputs:3`、`stage1_light:L1_BACKLIGHT:boxes=0`、`stage2_fused:boxes=3` 的轨迹。

## BagID 驱动与顺序分拣

当前 C++ 实时链路不采用“谁先推理完谁先分拣”的策略，而是以 `bag_id` 作为唯一业务主键。生产接入时推荐由 PLC 递增编号生成 `bag_id`；demo 环境中可以继续从文件名推导。

每张 burst 图像都会携带：

```text
bag_id
side = A / B
light = Backlight / Darkfield / CrossPolar
camera_id
frame_id
trigger_hw_ns
encoder_position
```

一个完整袋体样本为：

```text
BagID N:
  A_Backlight
  A_Darkfield
  A_CrossPolar
  B_Backlight
  B_Darkfield
  B_CrossPolar
```

`BagCaptureAssembler` 负责等待双相机双面样本齐套。只要缺任意相机或任意一面的 burst 不完整，就不会进入正常袋级 OK 判定；超过 `bag_capture_timeout_ms` 后生成 fail-safe NG 结果。

`SortReorderBuffer` 负责末端分拣顺序。袋体进入时先登记物理顺序，推理结果可以被不同 worker 乱序写回，但只有队首 `bag_id` 已有结果时才释放给末端拨杆。如果队首结果超过 `sort_result_timeout_ms` 仍未到达，系统默认 NG 或异常通道，避免后续袋子顶上来导致单个分拣拨杆打错袋子。

缺陷检测队列使用分片 worker 池，默认线程数为 4；队列内部使用 `deque`，满载时丢弃最旧输入，避免高频 `erase(begin)` 造成尾延迟抖动：

```ini
[runtime]
defect_worker_count = 4
expected_burst_images_per_camera = 3
bag_capture_timeout_ms = 1500
sort_result_timeout_ms = 1500
```

每个袋子通过 `hash(bag_id) % defect_worker_count` 固定分配到某个推理线程，因此同一袋子的多张图一定在同一个线程内顺序处理，不同袋子可以并行推理。后续前端设置页可以修改该配置，服务启动时也可以通过参数覆盖：

```bash
./build/cpp_backend/waterbag_cpp_service --config config/cpp_backend/demo.ini --watch --defect-workers 4
```

如果主推理后端跑单 GPU，`defect_worker_count = 4` 不一定更快，可能因为 GPU 上下文、显存拷贝和 host/device 同步导致 P99 延迟更差。现场建议从 1 或 2 开始压测，再按吞吐、P95/P99 和 PLC 分拣窗口调整。末端分拣 PLC 已经由 sorter 线程执行，不会再占用 defect worker。

JSONL 结果可以异步落盘：

```ini
[storage]
result_jsonl = artifacts/cpp_backend/results.jsonl
async_result_writes = true
result_queue_capacity = 512
drop_results_when_full = true
```

`drop_results_when_full = true` 会优先保障实时分拣，磁盘抖动时允许丢弃最旧日志；如必须完整留档，可改为 `false`，但队列满时保存路径会反压。

## 构建与运行

```bash
cmake -S cpp_backend -B build/cpp_backend
cmake --build build/cpp_backend -j
./build/cpp_backend/waterbag_cpp_demo
./build/cpp_backend/waterbag_cpp_service --config config/cpp_backend/demo.ini --once
ctest --test-dir build/cpp_backend --output-on-failure
```

`--once` 会处理配置目录中已有的相机图像后退出。`--watch` 会持续运行轮询服务，直到 Ctrl+C 停止：

```bash
./build/cpp_backend/waterbag_cpp_service --config config/cpp_backend/demo.ini --watch
```

检测结果会追加写入：

```text
artifacts/cpp_backend/results.jsonl
```

## 后续集成点

- 将 `OnnxRuntimeDetector` 进一步扩展为 TensorRT 或 OpenVINO C++ 推理适配器。
- 将目录轮询替换为工业相机 SDK 回调。
- 将 `MockPlcTransport` 替换为真实的 Modbus/TCP 或 Modbus/RTU 通信。
- 通过 HTTP、gRPC、ZeroMQ 将 `InspectionResult` 发布给 Python Web，或将该库封装成 ROS2 节点。
