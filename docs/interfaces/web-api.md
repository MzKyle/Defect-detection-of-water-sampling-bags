# Web API

服务默认地址：

```text
http://127.0.0.1:5000
```

## 页面

### `GET /`

返回 Web 看板页面。

## 控制接口

### `POST /api/control/start`

启动 runtime，开始监听相机目录。

响应：

```json
{
  "status": "running"
}
```

### `POST /api/control/stop`

停止 runtime。

响应：

```json
{
  "status": "stopped"
}
```

### `GET /api/status`

查询运行状态和相机配置。

响应示例：

```json
{
  "running": true,
  "cameras": [
    {
      "id": 1,
      "name": "A面相机",
      "watch_dir": "/path/to/demo_data/camera1"
    }
  ]
}
```

## 结果接口

### `GET /api/results/recent?limit=20`

查询最近检测结果。

参数：

| 参数 | 默认 | 最大值 | 说明 |
| --- | --- | --- | --- |
| `limit` | `20` | `50` | 返回条数 |

核心字段：

| 字段 | 说明 |
| --- | --- |
| `timestamp` | 检测时间 |
| `frame_id` | 帧 ID |
| `bag_id` | 袋体 ID |
| `camera_id` | 相机 ID |
| `status` | 正常 / 异常 / 待确认 / 超时 |
| `decision_action` | accept / reject / await_peer_camera |
| `decision_reason` | 决策原因 |
| `latency_ms` | 总耗时 |
| `fault_signals` | 故障信号列表 |

### `GET /api/results/metrics?limit=40`

查询聚合指标。

参数：

| 参数 | 默认 | 最大值 | 说明 |
| --- | --- | --- | --- |
| `limit` | `40` | `200` | 统计最近多少条结果 |

响应字段：

| 字段 | 说明 |
| --- | --- |
| `total_events` | 总事件数 |
| `status_counts` | 各状态数量 |
| `defect_events` | 异常事件数 |
| `repeat_events` | 重复缺陷事件数 |
| `timeout_events` | 超时事件数 |
| `ack_retry_events` | Ack 重试事件数 |
| `stale_frame_events` | 旧帧忽略事件数 |
| `ack_failure_events` | PLC 失败事件数 |
| `avg_latency_ms` | 平均总耗时 |
| `avg_control_ms` | 平均控制耗时 |
| `avg_ack_attempts` | 平均 Ack 尝试次数 |
| `fault_rows` | 最近故障行 |

## 上传接口

### `POST /api/demo/upload`

上传图片到指定相机目录，复用在线文件监控链路。

请求：

```text
multipart/form-data
image: <file>
camera_id: 1
```

响应：

```json
{
  "status": "queued",
  "filename": "20260428_120000_000000_sample.jpg",
  "camera_id": 1
}
```

错误：

| 状态码 | 原因 |
| --- | --- |
| `400` | 缺少图片 |
| `400` | camera_id 不存在 |

## Socket.IO 事件

### `inspection_update`

实时检测结果推送。

主要字段：

| 字段 | 说明 |
| --- | --- |
| `image` | base64 结果图 |
| `bag_summary` | 袋体级聚合结果 |
| `state_trace` | 状态轨迹 |
| `final_count` | 最终缺陷框数量 |
| `timing_breakdown` | 耗时拆解 |
| `fault_signals` | 故障信号 |
