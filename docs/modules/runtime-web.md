# service / webapp - 运行时与 Web

## InspectionRuntime

**路径**: `waterbag_inspection/service.py`

`InspectionRuntime` 负责在线运行时的输入编排：

- 为每个相机目录创建 `watchdog.Observer`
- 监听 `.jpg/.jpeg/.png/.bmp` 新文件
- 构造 `FramePacket` 并放入队列
- worker 等待文件大小和 mtime 稳定
- 通过 cooldown 控制同相机处理频率
- 调用 `InspectionPipeline.process_packet()`
- 发布结果给已注册 listener
- worker 空闲时定期调用 `pipeline.flush_timeouts()`
- 停止时关闭 `pipeline`，等待异步落盘队列在配置时间内排空

当前 runtime 仍兼容相机软件落盘到 watch 目录的模式，因此会等待输入文件稳定后再推理。为了进一步做到“相机图像 -> 内存 ring buffer -> 推理”，需要相机 SDK 直接把三光源图像组交给 `FramePacket` 或后续的采集队列；本次优化先把备份图和结果图从主推理链路里移到异步落盘。

## 建议流水线职责

| 环节 | 当前落点 | 说明 |
| --- | --- | --- |
| 采集 | `watchdog` / 后续相机 SDK | 当前监听文件，SDK 接入后可直接写入内存队列 |
| 预处理 | detector / multilight backend | 单图或三光源组包后进入模型输入 |
| 推理 | `primary_detector`, `patch_detector` | 多光源模式一次模型调用处理三张图 |
| 后处理 | `InspectionPipeline` | NMS 后结果、可见性矩阵影子评估、袋级判定 |
| PLC | `plc_controller.execute()` | 只在最终决策后下发控制 |
| 日志落盘 | `ArtifactWriter` | 备份图和结果图异步保存，不阻塞分拣主链路 |

## 文件稳定性检查

相机落盘图片时，文件可能尚未写完。`_wait_until_ready()` 会在 `file_ready_timeout_seconds` 内轮询：

| 条件 | 说明 |
| --- | --- |
| 文件存在 | 路径已经可访问 |
| `st_size` 不变 | 文件大小稳定 |
| `st_mtime_ns` 不变 | 修改时间稳定 |
| 持续 `file_stable_seconds` | 认为可读 |

如果超时仍不稳定，runtime 会跳过该文件。

## Web App

**路径**: `waterbag_inspection/webapp.py`

Flask 负责页面和 HTTP API，Socket.IO 负责实时推送。

```mermaid
graph LR
    Runtime["InspectionRuntime"] --> Listener["publish(result)"]
    Listener --> Socket["Socket.IO inspection_update"]
    Repository["SQLiteDetectionRepository"] --> API["/api/results/*"]
    Browser["Web Browser"] --> API
    Browser <--> Socket
```

## 实时事件

事件名：

```text
inspection_update
```

事件 payload 包含：

| 字段 | 说明 |
| --- | --- |
| `frame_id` | 帧 ID |
| `bag_id` | 袋体 ID |
| `status` | 正常 / 异常 / 待确认 / 超时 |
| `image` | 结果图 base64 |
| `bag_summary` | 多相机聚合结果 |
| `state_trace` | 状态轨迹 |
| `timing_breakdown` | 耗时拆解 |
| `fault_signals` | timeout / ack_retry / stale_frame / plc_failure |

## HTTP API

Web API 详见 [接口参考](../interfaces/web-api.md)。

## 运行时控制

| 操作 | 入口 |
| --- | --- |
| 启动 runtime | `POST /api/control/start` |
| 停止 runtime | `POST /api/control/stop` |
| 查询状态 | `GET /api/status` |
| 上传图片 | `POST /api/demo/upload` |
| 查询历史 | `GET /api/results/recent` |
| 查询指标 | `GET /api/results/metrics` |
