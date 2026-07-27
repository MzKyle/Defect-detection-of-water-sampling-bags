# 硬件在环复现

本项目的核心 demo 目标是真实 C++ 硬件链路可复现：海康 MVS 相机负责 burst 采图，Modbus TCP PLC/IO 负责激光 presence、多光源/相机触发、工位放行和末端 OK/NG 分拣，C++ 服务输出 JSONL，Python Dashboard 只做观测。

mock 链路只用于 CI、无硬件开发和回归测试。硬件链路的最低验收不要求真实缺陷模型；可以先用 mock detector 证明采图、PLC 动作、ack、超时、顺序分拣和结果追溯闭环。

## 硬件拓扑

```text
PLC laser presence
-> C++ 轮询/读取 Modbus TCP presence
-> C++ arm Hikvision MVS camera burst
-> C++ 写入 burst plan 到 PLC holding registers
-> C++ 置位 start_burst coil
-> PLC/频闪器执行多光源和相机触发序列
-> MVS SDK callback 收到 3 帧并保存
-> C++ 校验 burst 齐套和同步状态
-> C++ 发送工位放行、末端 OK/NG 分拣命令
-> JSONL + SQLite + Dashboard 留痕
```

推荐接线职责：

| 部件 | 职责 |
| --- | --- |
| 海康 MVS 相机 | 外触发采图、chunk timestamp、图片保存 |
| PLC/IO | 激光到位、频闪/光源控制、相机触发输出、拨杆/分拣动作、ack/fault |
| 工控机 C++ 服务 | session 管理、Modbus 命令、相机 arm、组包、推理调度、结果输出 |
| Python Dashboard | 读取 JSONL/SQLite，不参与实时控制 |

## 参考配置

硬件配置样例：

```bash
config/cpp_backend/hardware_hik_mvs_modbus.ini
```

关键项：

```ini
[camera_driver]
backend = hikvision_mvs

[runtime]
input_mode = plc_presence
publish_no_bag_results = false

[plc]
backend = modbus_tcp

[plc.modbus_tcp]
host = 192.168.1.50
port = 502
unit_id = 1
```

`input_mode=plc_presence` 表示 C++ watch 模式由 PLC presence 触发 station packet，不再依赖 watch 目录中先出现图片。`watch_dir` 仍用于 mock/无硬件复现。

## Modbus Register Map

所有地址均为零基 Modbus 地址。默认实现使用功能码：

| 功能 | Function code |
| --- | --- |
| 读 presence bit | `0x02 Read Discrete Inputs` |
| 读 message/bag/ack/fault | `0x04 Read Input Registers` |
| 写动作线圈 | `0x05 Write Single Coil` |
| 写命令上下文和 burst plan | `0x06 Write Single Register` |

参考输入区：

| 配置键 | 默认地址 | 语义 |
| --- | --- | --- |
| `discrete_input_bag_present` | 0 | 激光到位，有袋为 1 |
| `input_register_message_id` | 0 | PLC presence 消息序号 |
| `input_register_bag_id_high` | 1 | PLC BagID 高 16 位 |
| `input_register_bag_id_low` | 2 | PLC BagID 低 16 位 |
| `input_register_ack_status` | 10 | 最近命令 ack，0 idle、1 success、2 failure |
| `input_register_fault_code` | 11 | PLC/IO 故障码 |

参考输出区：

| 配置键 | 默认地址 | 语义 |
| --- | --- | --- |
| `holding_register_command_id` | 0 | C++ 命令序号低 16 位 |
| `holding_register_bag_id_high` | 1 | 当前 BagID 高 16 位 |
| `holding_register_bag_id_low` | 2 | 当前 BagID 低 16 位 |
| `holding_register_action_code` | 5 | 动作码 |
| `holding_register_burst_frame_count` | 6 | burst 帧数 |
| `holding_register_burst_frame_base` | 20 | 每帧参数起始寄存器 |
| `burst_frame_register_stride` | 4 | 每帧参数跨度：light、exposure_us、settle_us、pulse_us |
| `coil_start_burst` | 0 | 启动完整多光源 burst |
| `coil_station_release` | 1 | 下挡/放行 |
| `coil_station_push` | 2 | 上拨/推袋 |
| `coil_station_restore` | 3 | 拨杆复位 |
| `coil_sort_ok` | 10 | 末端 OK 分拣 |
| `coil_sort_ng` | 11 | 末端 NG 分拣 |
| `coil_heartbeat` | 20 | 硬件预检心跳写入 |

动作码：

| 动作码 | C++ action |
| --- | --- |
| 1 | `start_light_burst` |
| 10 | `release_bag_after_capture` |
| 11 | `push_bag_after_capture` |
| 12 | `restore_after_push` |
| 13 | `restore_blocking_position` |
| 20 | `route_to_ok_bin` |
| 21 | `route_to_ng_bin` |

当前 Modbus 后端会写入 burst plan 并等待 ack。PLC 侧应在命令完成后把 `input_register_ack_status` 置为 success/failure；如果需要区分命令，可同时检查 `holding_register_command_id` 和 `holding_register_action_code`。

Modbus 后端会按 `camera_id + message_id + bag_id` 去重 presence，避免 PLC presence bit 在多个轮询周期保持 true 时重复触发同一袋 burst。PLC 侧应保证新袋到位时递增 `message_id` 或 `bag_id`。

## 运行

构建并执行硬件预检：

```bash
make hardware-check
```

预检会打开配置中的相机 backend，并对 PLC 执行 presence 读取、message/fault register 读取和 heartbeat coil 写入。输出是 JSON：

```json
{"hardware_check_status":"ok","camera":{"success":true},"plc":{"success":true}}
```

启动真实硬件 watch：

```bash
make run-hardware-watch
```

另一个终端启动看板：

```bash
python -m waterbag_inspection sync-results --config config/cpp_backend/hardware_hik_mvs_modbus.ini
python -m waterbag_inspection serve --config config/cpp_backend/hardware_hik_mvs_modbus.ini
```

## 验收标准

- `make hardware-check` 返回 `hardware_check_status=ok`。
- `make run-hardware-watch` 下，PLC presence 有袋后触发 MVS burst，`artifacts/cpp_backend/captures/` 出现每面 3 帧图片。
- JSONL 中可看到 `camera_backend=hikvision_mvs`、`plc_backend=modbus_tcp`、`plc_message_id`、`plc_bag_id`、`burst_sync_valid`、`ack_attempts`。
- Dashboard 最近结果表能显示真实图片、后端、袋号、动作、ack、耗时。
- A/B 面缺失、burst 不齐、ack 失败或超时默认走 fail-safe NG。

## 排障优先级

| 现象 | 优先检查 |
| --- | --- |
| `hardware_check_status=failed` | 相机 SDK 路径、相机序列号、PLC IP/端口/unit_id、防火墙 |
| 一直无结果 | `runtime.input_mode` 是否为 `plc_presence`，PLC presence bit 是否置位 |
| 一直 `no_bag` | `discrete_input_bag_present` 地址或 PLC 逻辑 |
| 有袋但无图片 | 相机是否已 arm，PLC 是否真的输出相机 trigger，触发源是否为 `Line0` |
| `capture_invalid` | burst 帧数、MVS frame timeout、光源/触发时序、chunk timestamp |
| ack 超时 | `input_register_ack_status` 是否按约定返回，`ack_timeout_ms` 是否过短 |
| 分拣乱序 | PLC BagID 是否单调且 A/B 面一致，`pending_timeout_ms` 和 `sort_result_timeout_ms` 是否匹配机械窗口 |

## 当前边界

- Modbus 后端实现真实命令和 ack 闭环，但 PLC 侧 ladder/程序需要按参考 register map 响应。
- `read_burst_events` 当前根据 C++ 发出 start burst 的统一时钟生成计划事件；如果现场 PLC/触发控制器能提供实际硬件时间戳，可以在同一接口下扩展读取真实事件寄存器。
- 真实检测模型不是硬件链路的阻塞条件；ONNX Runtime 可作为独立验收路径接入。
