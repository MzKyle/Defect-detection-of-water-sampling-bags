# Waterbag Inspection

> 面向水样检测袋缺陷检测的工程评审型研究 demo
>
> 海康 MVS / Modbus TCP PLC-IO / C++ 实时后端 / 多光源 burst / Python 观测与模型工具层

[![C++17](https://img.shields.io/badge/C%2B%2B-17-00599C?logo=cplusplus&logoColor=white)](cpp_backend/README.md)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-A42E2B.svg)](LICENSE)



Waterbag Inspection 把真实相机、PLC/IO、多光源 burst、袋级组包、缺陷检测、顺序分拣和结果追溯串成一条可复现的 C++ 硬件链路。

主路径是海康 MVS 相机 + Modbus TCP PLC/IO + C++ 服务 + JSONL + SQLite + Dashboard。mock 相机和 mock PLC 只保留给 CI、无硬件开发和回归测试；真实硬件复现不要求先接入真实缺陷模型，可以用 mock detector 验证采图、触发、ack、分拣和结果追溯闭环。

## 项目背景

水样袋通常是白色、半透明、低对比度的，缺陷可能是针孔、毛发、黑点、异物、压痕、折痕、污染或封边异常（详细可以看项目附加目录中的分类图片）。单张普通正面光图片很容易遇到两个问题：缺陷太浅看不见，或者折痕和反光太像缺陷。
>人工做水袋缺陷检测时是在大背光灯下用手调换不同角度来找缺陷，这中多角度观察微小缺陷的能力对受硬件限制只能平放检测的机器来说是个很大的挑战

因此项目的核心思路和难点不在“模型”，而是把成像和控制先做好：

```text
PLC 激光 presence gate
-> 多光源 burst 采图
-> A/B 面和多光源按 bag_id 齐套
-> stage-1 整图粗检
-> stage-2 微缺陷/patch 精检
-> 袋级融合得到 OK / NG
-> 按物理 BagID 顺序驱动末端分拣
-> JSONL + SQLite + Web 看板留痕
```

## 系统架构

```mermaid
flowchart TB
    subgraph HW["现场硬件与传感<br>On-site Hardware & Sensing"]
        BAG["水样袋到位<br>Water Bag In Position"]
        PLCIO["PLC 激光到位 / 频闪 / 末端分拣 IO<br>PLC Laser/Strobe/Sorting IO"]
        CAMHW["工业相机 / 多光源<br>Industrial Camera & Multi-light Source"]
    end

    subgraph CPP["C++ 实时执行层<br>C++ Real-time Execution Layer"]
        CAM["camera_driver<br>相机驱动"]
        PLC["PLC_driver<br>PLC驱动"]
        WATCH["watch_dir input<br>无硬件样本输入"]
        ORCH["detect_orchestrator<br>检测编排器"]
        ASM["BagCaptureAssembler<br>水样袋采集组装器"]
        DET["defect worker pool<br>缺陷检测线程池"]
        COR["BagCorrelator<br>水样袋关联器"]
        SORTBUF["SortReorderBuffer<br>分拣重排序缓冲区"]
        SORTER["sorter thread<br>分拣执行线程"]
        JSONL["JsonlResultRepository<br>JSONL结果仓库"]
    end

    subgraph PY["Python 观测、demo 辅助与模型工具层<br>Python Observation, Demo Helper & Model Layer"]
        SYNC["JSONL -> SQLite<br>数据同步"]
        WEB["Flask Dashboard / API<br>可视化看板接口"]
        UPLOAD["demo upload -> C++ watch_dir<br>无硬件样本投料"]
        TRAIN["Ultralytics 训练 / benchmark / ONNX 导出<br>模型训练导出"]
        MODEL["ONNX weights<br>ONNX模型权重"]
    end

    %% 硬件数据流
    BAG --> PLCIO
    PLCIO --> PLC
    CAMHW --> CAM

    %% 核心检测流程
    CAM --> ORCH
    PLC --> ORCH
    WATCH --> ORCH
    ORCH --> ASM
    ASM --> DET
    DET --> COR
    COR --> SORTBUF
    SORTBUF --> SORTER

    %% 分拣闭环（核心逻辑）
    SORTER -.分拣指令.-> PLC
    SORTER --> JSONL

    %% 数据可视化
    JSONL --> SYNC
    SYNC --> WEB
    UPLOAD -.手动样本复制.-> WATCH

    %% 模型支撑
    TRAIN --> MODEL
    MODEL --> ORCH
```

实时链路：

```text
PLC 激光 presence gate
-> 相机 arm burst / PLC start_light_burst
-> 多光源 burst 采图和时序校验
-> A/B 面按 bag_id 齐套
-> stage-1 整图检测
-> stage-2 微缺陷 / patch 检测
-> 袋级融合得到 OK / NG
-> SortReorderBuffer 按物理 BagID 顺序释放
-> sorter thread 下发 end_sorter OK / NG
-> JSONL 结果写盘
```

### 组件职责

- `camera_driver` 只负责相机采图、burst session 和 `CaptureGroup` 组包，不决定 OK/NG。
- `PLC_driver` 只负责激光到位消息、光源 burst、工位拨杆和末端分拣动作，不做视觉判断。
- `detect_orchestrator` 负责 presence gate、袋级状态机、推理调度、结果融合和顺序分拣编排。
- Python 同步 C++ 输出的 JSONL 到 SQLite 并提供 Dashboard；上传入口只用于无硬件 demo 投料，不执行 Python 检测、PLC 控制或分拣。
- 训练和导出的模型通过 ONNX / Ultralytics 进入 C++ 实时后端。

## 项目代码文件

- C++ 实时后端：相机输入、PLC、burst、推理调度、袋级状态机、顺序分拣。
- Python 观测与 demo 辅助层：同步 C++ JSONL 到 SQLite，提供 Dashboard、查询接口和手动样本上传到 C++ `watch_dir`。
- Python 模型工具：YOLO 训练、benchmark 和 ONNX 导出。
- 文档：从架构、配置、运行到模型工具都有详细讲解。
- demo 数据：可直接用于本地复现和烟测。

## 硬件复现

真实硬件链路：

```bash
make build-cpp
make hardware-check
make run-hardware-watch
```

另一个终端启动看板：

```bash
python -m waterbag_inspection sync-results --config config/cpp_backend/hardware_hik_mvs_modbus.ini
python -m waterbag_inspection serve --config config/cpp_backend/hardware_hik_mvs_modbus.ini
```

硬件配置样例在 `config/cpp_backend/hardware_hik_mvs_modbus.ini`，Modbus register map 和接线/排障说明见 [硬件在环复现](docs/hardware/README.md)。

## Mock 回归

无硬件环境可以跑 mock 链路：

```bash
make build-cpp
make test
make run-cpp-once
make sync-results
make serve-dashboard
```

手动命令：

```bash
cmake -S cpp_backend -B build/cpp_backend
cmake --build build/cpp_backend -j
ctest --test-dir build/cpp_backend --output-on-failure
./build/cpp_backend/waterbag_cpp_service --config config/cpp_backend/demo.ini --once
python -m waterbag_inspection sync-results --config config/cpp_backend/demo.ini
python -m waterbag_inspection serve --config config/cpp_backend/demo.ini
```

默认看板地址是 `http://127.0.0.1:5000`。

## 核心特性

- C++ 是唯一实时链路，避免 Python Web 和产线控制互相干扰。
- Python 上传按钮只是把无硬件样本复制到 C++ `watch_dir`，真实检测、状态机和分拣仍由 C++ `--watch` 服务完成。
- 真实硬件主路径支持海康 MVS burst 采图和 Modbus TCP PLC/IO 控制。
- mock 相机和 mock PLC 用于 CI、无硬件开发和回归测试。
- PLC 激光 presence 负责有无袋判断，减少空背景采图和无效推理。
- 两阶段检测把整图粗检和微缺陷补检拆开，适合水样袋这种低对比度场景，并提高效率和准确度。
- 袋级状态机和顺序分拣按物理 BagID 组织，避免并发推理乱序打错袋。
- JSONL + SQLite + Dashboard 提供完整结果记录，便于追踪每一次判定和时序问题。
- ONNX Runtime、Ultralytics 训练和模型导出都保留在仓库里，方便从研究过渡到部署。

## 进一步阅读

如果想了解这套系统为什么这样拆，建议按下面顺序看：

1. [工程文档总览](docs/README.md)
2. [整体架构](docs/architecture/README.md)
3. [C++ 后端](cpp_backend/README.md)
4. [硬件在环复现](docs/hardware/README.md)
5. [配置说明](docs/configuration/README.md)
6. [运行与验证](docs/operations/README.md)
7. [模型工具](docs/model-tools/README.md)
8. [前端与数据库](docs/frontend/README.md)

## 仓库结构

| 路径 | 说明 |
| --- | --- |
| `cpp_backend/` | C++ 实时执行链路、PLC、相机驱动和测试 |
| `waterbag_inspection/` | Python 看板、SQLite 同步、demo 上传辅助和 CLI |
| `config/` | 运行配置、demo 配置和训练数据配置 |
| `docs/` | 站点文档与分主题说明 |
| `demo_data/` | 本地复现用的相机样本目录 |
| `artifacts/` | 运行结果、导出模型和看板数据 |
| `train_*.py` | YOLO 训练入口 |
| `benchmark_ultralytics_models.py` | 模型评测和延迟对比 |
| `export_ultralytics_onnx.py` | 导出 C++ 可加载的 ONNX 模型 |

## 许可证

本仓库使用 [AGPL-3.0](LICENSE) 许可证。
