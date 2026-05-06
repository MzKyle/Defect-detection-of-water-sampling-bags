# 运行与验证

面向想把项目跑起来、验证链路、定位问题和做性能调优的人。建议先用 mock 后端跑通，再接入真实相机、PLC 和 ONNX 模型。

## 一键命令

Makefile 中保留了常用入口：

```bash
make build-cpp
make test
make run-cpp-once
make sync-results
make serve-dashboard
```

如果你要看每一步发生了什么，可以使用下面的手动命令。

## C++ 构建

基础构建：

```bash
cmake -S cpp_backend -B build/cpp_backend
cmake --build build/cpp_backend -j
```

运行 C++ 测试：

```bash
ctest --test-dir build/cpp_backend --output-on-failure
```

测试覆盖的重点包括：

- PLC 激光 presence gate 会跳过无袋工位。
- PLC presence 有袋后先执行工位拨杆动作，再进入缺陷阶段。
- JSONL 包含 PLC presence message、PLC 动作、burst alignment 和状态轨迹字段。
- 异步 JSONL 写盘可用。
- burst alignment 使用统一硬件时间轴。
- station packet 会导出 3 张 burst 图像 metadata。
- defect 阶段会融合多光源结果。
- `BagCaptureAssembler` 会等待 A/B 面共 6 张图齐套。
- `SortReorderBuffer` 会按 BagID 物理顺序释放结果。
- INI 配置能正确加载 worker、burst、timeout 和 storage 参数。

## ONNX Runtime 构建

如果要加载 ONNX 模型：

```bash
cmake -S cpp_backend -B build/cpp_backend -DWATERBAG_ENABLE_ONNXRUNTIME=ON
cmake --build build/cpp_backend -j
```

需要系统能找到：

- ONNX Runtime C++ headers。
- ONNX Runtime library。
- OpenCV `core`、`imgproc`、`imgcodecs`。

如果依赖没有准备好，先用 mock 后端跑通链路，不要把硬件接入、模型接入和基础流程问题混在一起排查。

## Python 语法检查

```bash
python -m compileall \
  waterbag_inspection \
  train_ultralytics.py \
  train_v8.py \
  train_yolo11.py \
  benchmark_ultralytics_models.py \
  export_ultralytics_onnx.py
```

这一步只检查 Python 文件能否编译，不代表训练环境完整，也不代表 C++ ONNX 推理可用。

## Once 模式烟测

`--once` 会处理配置中相机目录里的已有图片，然后退出：

```bash
./build/cpp_backend/waterbag_cpp_service --config config/cpp_backend/demo.ini --once
```

同步结果：

```bash
python -m waterbag_inspection sync-results --config config/cpp_backend/demo.ini
```

查看最近结果：

```bash
python -m waterbag_inspection recent --config config/cpp_backend/demo.ini --limit 3
```

检查 JSONL：

```bash
tail -n 5 artifacts/cpp_backend/results.jsonl
```

你应该能看到类似字段：

```text
bag_id
camera_id
status
action
reason
latency_ms
stage1_ms
stage2_ms
control_commands
execution_feedbacks
state_trace
```

## Watch 模式烟测

启动 C++ 服务：

```bash
./build/cpp_backend/waterbag_cpp_service --config config/cpp_backend/demo.ini --watch
```

另一个终端启动看板：

```bash
python -m waterbag_inspection serve --config config/cpp_backend/demo.ini
```

打开：

```text
http://127.0.0.1:5000
```

将图片放入相机目录：

```text
demo_data/camera1
demo_data/camera2
```

或者用看板上传按钮复制到 watch 目录。注意：上传只是复制文件，处理动作仍由 C++ `--watch` 服务完成。

## 文件命名与 BagID

demo 中 `bag_id` 从文件名推导。建议让同一个袋子的 A/B 面文件使用相同前缀：

```text
bag_100_cam1_good.jpg
bag_100_cam2_good.jpg
bag_101_cam1_defect.jpg
bag_101_cam2_good.jpg
```

推导规则会去掉 `_cam`、`-cam`、`_camera`、`_front`、`_back`、`_a`、`_b` 等后缀。真实设备建议由 PLC 或主控生成递增 BagID。

## 端到端验证顺序

推荐按下面顺序验证，不要一次性把所有变量打开：

| 步骤 | 目标 | 命令/检查 |
| --- | --- | --- |
| 1 | 编译 C++ | `cmake` + `cmake --build` |
| 2 | 跑 C++ 单元测试 | `ctest --output-on-failure` |
| 3 | mock once | `waterbag_cpp_service --once` |
| 4 | 检查 JSONL | `tail results.jsonl` |
| 5 | 同步 SQLite | `sync-results` |
| 6 | 看板展示 | `serve` |
| 7 | watch 模式 | 放入新图片，确认 C++ 自动处理 |
| 8 | ONNX 接入 | 导出模型，打开 `WATERBAG_ENABLE_ONNXRUNTIME` |
| 9 | 真实相机/PLC 接入 | 替换接口实现，保留同一数据结构和结果输出 |

## 性能压测

实时性能不要只看平均值，要看尾延迟和机械窗口。

重点字段：

| 字段 | 说明 |
| --- | --- |
| `queue_delay_ms` | 输入排队是否积压 |
| `presence_ms` | PLC 激光到位消息是否足够快 |
| `stage1_ms` / `stage2_ms` | 模型耗时 |
| `advance_control_ms` | 工位拨杆动作耗时 |
| `control_ms` | 末端分拣动作耗时 |
| `latency_ms` | 当前结果总耗时 |
| `ack_attempts` / `ack_retry` | PLC 通信是否稳定 |
| `timed_out` | 是否触发 fail-safe |

建议做三组压测：

1. **mock 链路压测**：不接真实模型和硬件，验证线程、队列、JSONL、看板无明显问题。
2. **模型压测**：接 ONNX，固定图片输入，评估 stage-1/stage-2 的 P50/P95/P99。
3. **硬件联调压测**：接相机和 PLC，评估光源 jitter、曝光窗口、ack、机械动作和末端分拣窗口。

## defect_worker_count 调优

运行时可覆盖 worker 数：

```bash
./build/cpp_backend/waterbag_cpp_service \
  --config config/cpp_backend/demo.ini \
  --watch \
  --defect-workers 2
```

调优建议：

- CPU 或轻量模型可以适当增加 worker。
- 单 GPU 模型不一定适合高 worker，可能被显存拷贝、同步和上下文调度拖慢。
- 同一袋子通过 `hash(bag_id)` 固定到一个 worker，不同袋子并发。
- 最终末端分拣不在 defect worker 中执行，而是在 sorter thread 中串行执行。

判断标准：

| 现象 | 可能原因 |
| --- | --- |
| `queue_delay_ms` 升高 | worker 不足或模型太慢 |
| GPU 利用率低但延迟高 | 数据预处理、图像读取或同步开销 |
| `stage2_ms` 高 | patch 模型或二阶段触发过多 |
| `sort_result_timeout` 增多 | 推理 P99 超过机械等待窗口 |

## 硬触发验证

真实硬件接入后，建议先验证采图时序，而不是先看模型精度。

检查项：

- 相机是否提前进入 grabbing/armed 状态。
- `start_light_burst` 是否一次完成整个光源序列。
- 每帧 `frame_index` 和 `light_id` 是否一致。
- `light_on_hw <= exposure_start_hw`。
- `exposure_end_hw <= light_off_hw`。
- `trigger_to_exposure_jitter_us` 是否在 `jitter_tolerance_us` 内。
- 拨杆放行是否发生在最后一帧曝光完成之后。

如果出现 `capture_invalid`，先排查光源、触发和时间戳，不要直接调模型阈值。

## 常见问题

| 现象 | 检查项 |
| --- | --- |
| 看板没有数据 | C++ 是否已写入 `artifacts/cpp_backend/results.jsonl` |
| JSONL 存在但 SQLite 没数据 | 执行 `python -m waterbag_inspection sync-results --config config/cpp_backend/demo.ini` |
| 图片无法显示 | JSONL 中 `source_path` 是否仍存在；相对路径默认按仓库根目录解析 |
| 上传后无检测结果 | C++ 服务是否以 `--watch` 运行 |
| 一直 `no_bag` | PLC 激光 presence 消息是否为 false，或 mock 文件名是否包含 `empty` / `no_bag` / `background` |
| 一直 `await_peer_camera` | 另一个相机同 `bag_id` 的图片是否缺失 |
| 出现 timeout NG | 检查 `bag_capture_timeout_ms`、`pending_timeout_ms`、`sort_result_timeout_ms` 和输入节拍 |
| `capture_invalid` | 检查 burst 图像数、光源窗口、jitter 和 `sync_valid` |
| PLC 显示失败 | 检查 `ack_timeout_ms`、`max_retries`、通信链路和机械动作反馈 |
| PLC presence 超时 | 检查 `presence_message_timeout_ms`、激光传感器、PLC 到工控机消息链路和 station 节拍 |
| C++ ONNX 构建失败 | 是否安装 ONNX Runtime C++ headers/libs 和 OpenCV |
| C++ ONNX 运行失败 | `model_path` 是否存在，输出格式是否和 detector 解码逻辑匹配 |

## 发布前检查

开源发布或现场版本交付前，建议至少确认：

- docs 站说明和当前代码边界一致。
- C++ mock 测试全部通过。
- `--once` 能产生 JSONL。
- Python 能同步并展示 SQLite 数据。
- ONNX 模型若启用，C++ 后端能加载并跑通样本。
- 真实硬件接入分支中，mock 和生产适配器边界清楚。
- fail-safe 路径不会默认 OK。
