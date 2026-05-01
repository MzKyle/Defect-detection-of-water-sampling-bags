# 故障注入流程

## 目标

故障注入用于验证异常链路是否可观测、可恢复、可留档。当前内置三类场景：

| 场景 | 验证点 |
| --- | --- |
| `timeout` | 单侧相机缺失时是否超时判退 |
| `ack-retry` | PLC 首次 Ack 失败后是否重试成功 |
| `out-of-order` | 同机位旧帧迟到时是否被忽略 |

## 运行全部场景

```bash
python -m waterbag_inspection inject-faults \
  --config config/demo.yaml \
  --scenario all \
  --output-root artifacts/fault_injection \
  --clean
```

Makefile:

```bash
make inject-faults
```

## 单独运行 timeout

```bash
python -m waterbag_inspection inject-faults \
  --config config/demo.yaml \
  --scenario timeout \
  --output-root artifacts/fault_injection \
  --clean
```

预期：

- 只生成 cam1 图片
- 第一条结果为 `await_peer_camera`
- 超过 `pending_timeout_ms` 后生成 `timeout-*` 结果
- `fault_signals` 包含 `timeout`

## 单独运行 ack-retry

```bash
python -m waterbag_inspection inject-faults \
  --config config/demo.yaml \
  --scenario ack-retry \
  --output-root artifacts/fault_injection \
  --clean
```

预期：

- mock PLC 首次失败
- 第二次尝试成功
- `ack_attempts > 1`
- `fault_signals` 包含 `ack_retry`

## 单独运行 out-of-order

```bash
python -m waterbag_inspection inject-faults \
  --config config/demo.yaml \
  --scenario out-of-order \
  --output-root artifacts/fault_injection \
  --clean
```

预期：

- 先处理新缺陷帧
- 后处理旧正常帧
- 旧帧不覆盖已有缺陷决策
- `stale_frame_ignored=true`

## 输出产物

```text
artifacts/fault_injection/
├── timeout/
├── ack_retry/
└── out_of_order/
```

每个场景目录包含：

- 注入图片
- `inspection.db`
- `backups/`
- `results/`
- `repeat_history.json`

## 面试说明角度

可以强调：

- 故障注入不是写死在 Web 页面，而是复用真实 pipeline
- 每个异常都有结构化指标和留档
- timeout / retry / stale frame 都是工业现场常见链路问题
