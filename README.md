# Waterbag Inspection Demo

一个面向工业水样采集袋缺陷检测场景的开源演示项目。

这个仓库最初来自一套学生时期完成的产线视觉项目，后续逐步重构成了更适合继续维护、演示和工程化扩展的版本。当前版本的重点不是单独展示某个 YOLO 模型，而是把一条完整的工业视觉链路整理清楚：

- 双相机输入
- 一次整图检测 + 二次网格复检
- 多相机袋体级关联
- 重复缺陷识别
- PLC 控制命令下发与 Ack/重试
- Web 实时展示
- 结果留档、回放和故障注入
- YOLOv8 / YOLO11 训练与模型选型基准入口

如果你做的是机器人、视觉、工业软件、感知数据链路相关工作，这个仓库更适合被理解成一个“小型感知-决策-执行闭环系统”，而不只是一个目标检测训练脚本集合。

## 项目定位

这个仓库当前有三个明确目标：

1. 可演示  
   没有真实相机、真实 PLC、真实权重，也可以完整演示链路行为。

2. 可复现  
   入口、配置、目录结构、测试和最小依赖都尽量统一。

3. 可扩展  
   demo、回放、故障注入、真实部署共用同一套 pipeline 和数据模型。

## 核心特性

- `FramePacket -> PerceptionResult -> DecisionResult -> ControlCommand -> ExecutionFeedback` 标准化链路建模
- `BagSummary` 多相机袋体级结果聚合
- `pending_timeout_ms` 驱动的袋体等待超时淘汰
- 同机位乱序旧帧忽略，避免状态回滚
- PLC Ack/超时/重试机制
- `runtime / replay / manual` 来源隔离的重复缺陷状态
- Web 观测面板，展示 timeout / ack retry / stale frame / plc failure
- CLI 支持 `serve`、`seed-demo`、`inspect`、`replay`、`inject-faults`
- SQLite 留档、历史回放和故障注入

## 仓库说明

这个仓库看起来会比一般 demo 大一些，因为它保留了两部分内容：

### 1. 当前推荐维护的应用层代码

主代码集中在 [`waterbag_inspection/`](waterbag_inspection)：

- [`config.py`](waterbag_inspection/config.py)：YAML 配置加载
- [`schemas.py`](waterbag_inspection/schemas.py)：链路数据模型
- [`pipeline.py`](waterbag_inspection/pipeline.py)：检测与控制主流程
- [`correlation.py`](waterbag_inspection/correlation.py)：袋体级多相机关联
- [`policy.py`](waterbag_inspection/policy.py)：决策与控制策略
- [`plc.py`](waterbag_inspection/plc.py)：PLC/mock 执行层
- [`repeater.py`](waterbag_inspection/repeater.py)：重复缺陷判定
- [`replay.py`](waterbag_inspection/replay.py)：历史回放
- [`fault_injection.py`](waterbag_inspection/fault_injection.py)：故障注入
- [`storage.py`](waterbag_inspection/storage.py)：SQLite 留档与统计
- [`service.py`](waterbag_inspection/service.py)：目录监听与 worker
- [`webapp.py`](waterbag_inspection/webapp.py)：Flask + Socket.IO 页面/API
- [`cli.py`](waterbag_inspection/cli.py)：命令行入口

### 2. 保留的原始训练/推理资产

仓库中还保留了较多 YOLO 生态相关目录，例如：

- [`detect/`](detect)
- [`models/`](models)
- [`utils/`](utils)
- [`classify/`](classify)
- [`segment/`](segment)
- [`train_ultralytics.py`](train_ultralytics.py)
- [`train_v8.py`](train_v8.py)
- [`train_yolo11.py`](train_yolo11.py)
- [`benchmark_ultralytics_models.py`](benchmark_ultralytics_models.py)

其中 YOLOv5 相关目录主要用于保留原始训练、验证、导出和历史兼容能力；当前推荐用 Ultralytics 统一 API 训练 YOLOv8 / YOLO11，并通过 benchmark 结果做模型选型。它们不是当前 demo Web 链路的主要入口，但可以为真实部署权重提供来源。

### 3. 已归档的旧脚本

重构前的实验脚本和旧页面已经归档到 [`legacy/`](legacy)：

- [`legacy/scripts/`](legacy/scripts)
- [`legacy/web_pages/`](legacy/web_pages)
- [`legacy/state/`](legacy/state)

如果你只是想运行或理解当前系统，请优先看 `waterbag_inspection/` 和 `configs/`，可以先忽略 `legacy/` 和原始 YOLO 目录。

## 快速开始

### 环境要求

- Python `>= 3.10`

### 安装最小演示依赖

```bash
pip install -r requirements-demo.txt
```

如果你希望做更完整的本地验证，包括真实模型、加密权重、Modbus 和训练相关依赖，可以安装：

```bash
pip install -r requirements.txt
```

### 启动 demo

```bash
python -m waterbag_inspection seed-demo --output-root demo_data --clean
python app.py
```

或者显式走 CLI：

```bash
python -m waterbag_inspection serve --config configs/demo.yaml
```

默认访问地址：

```text
http://127.0.0.1:5000
```

## 3 分钟演示流程

### 1. 生成演示样本

```bash
python -m waterbag_inspection seed-demo --output-root demo_data --clean
```

这会生成一批配对样本：

- `bag_0001_cam1_good.jpg` + `bag_0001_cam2_good.jpg`
  双相机都正常，第二张到达后整袋放行
- `bag_0002_cam1_defect_primary.jpg` + `bag_0002_cam2_good.jpg`
  一侧一次检测命中异常，整袋立即判退
- `bag_0003_cam1_defect_primary.jpg` + `bag_0003_cam2_good.jpg`
  复现重复缺陷场景
- `bag_0004_cam1_good.jpg` + `bag_0004_cam2_micro_patch.jpg`
  一次检测通过，二次网格复检命中微小缺陷

### 2. 启动 Web 服务

```bash
python app.py
```

### 3. 观察页面

页面除了显示最近图像和判定结果，还会展示链路异常观测信息：

- `Timeout Bags`
- `Ack Retries`
- `Stale Frames`
- `Ack Failures`
- `Avg Ack Attempts`
- `Avg Control Latency`
- `Recent Fault Signals`

## CLI 命令

### 启动服务

```bash
python -m waterbag_inspection serve --config configs/demo.yaml
```

### 单图烟测

```bash
python -m waterbag_inspection inspect \
  --config configs/demo.yaml \
  --camera-id 1 \
  --image demo_data/camera1/bag_0003_cam1_defect_primary.jpg \
  --reset-history
```

### 历史回放

```bash
python -m waterbag_inspection replay \
  --config configs/demo.yaml \
  --source-root demo_data \
  --limit 4 \
  --reset-history
```

### 故障注入

```bash
python -m waterbag_inspection inject-faults \
  --config configs/demo.yaml \
  --scenario all \
  --output-root artifacts/fault_injection \
  --clean
```

也可以只跑某一类故障：

```bash
python -m waterbag_inspection inject-faults --scenario timeout
python -m waterbag_inspection inject-faults --scenario ack-retry
python -m waterbag_inspection inject-faults --scenario out-of-order
```

三类故障对应：

- `timeout`
  只送入同袋体单侧相机数据，等待超时后输出 `超时 -> reject -> Ack`
- `ack-retry`
  mock PLC 首次 Ack 失败，验证重试路径
- `out-of-order`
  先送新帧、后送旧帧，验证旧帧被忽略而不会回滚袋体状态

## Makefile

常用命令已经封装：

```bash
make install-demo
make install-full
make seed-demo
make serve-demo
make replay-demo
make inject-faults
make inject-timeout
make inject-ack-retry
make inject-out-of-order
make train-yolov8
make train-yolo11
make benchmark-models
make smoke
make test
```

## 配置

### Demo 配置

默认使用 [`configs/demo.yaml`](configs/demo.yaml)。

特点：

- `mock` 检测器
- `mock` PLC
- 本地 `demo_data/camera1` / `demo_data/camera2`
- SQLite 留档
- 默认开启袋体级关联、超时淘汰和重复缺陷状态隔离

mock 检测规则：

- 文件名包含 `defect` / `ng` / `abnormal` / `anomaly`
  模拟一次检测命中
- 文件名包含 `patch` / `micro` / `tiny` / `pinhole`
  模拟二次网格检测命中

### 生产配置模板

真实部署时可以参考 [`configs/production.example.yaml`](configs/production.example.yaml)。

需要按实际环境修改：

- 相机目录
- 模型权重路径或加密权重路径，可来自 YOLOv8 / YOLO11 训练结果
- PLC 串口参数
- 重复缺陷历史路径
- 可视化与结果输出目录

## 模型训练与选型

当前项目建议把 YOLOv5 作为 legacy baseline 保留，把 YOLOv8 作为稳定基线，把 YOLO11 作为升级候选。是否升级不靠“版本更新”本身决定，而靠同一数据集上的精度、漏检/误检、端侧时延和模型体积决定。

### 数据集配置

默认数据集配置在 [`data/waterbag.yaml`](data/waterbag.yaml)。推荐把真实数据放在：

```text
datasets/waterbag/
├── images/
│   ├── train/
│   └── val/
└── labels/
    ├── train/
    └── val/
```

如果你的数据集在其他位置，可以复制一份 YAML，或在命令中通过 `--data` 指向自己的配置文件。

### 训练 YOLOv8 基线

```bash
python train_v8.py --data data/waterbag.yaml --device 0
```

也可以通过 Makefile：

```bash
make train-yolov8 DATA=data/waterbag.yaml DEVICE=0
```

### 训练 YOLO11 候选模型

```bash
python train_yolo11.py --data data/waterbag.yaml --device 0
```

也可以通过 Makefile：

```bash
make train-yolo11 DATA=data/waterbag.yaml DEVICE=0
```

两个脚本都复用 [`train_ultralytics.py`](train_ultralytics.py)，常用参数包括：

```bash
python train_yolo11.py \
  --model yolo11n.pt \
  --data data/waterbag.yaml \
  --epochs 100 \
  --imgsz 640 \
  --batch 16 \
  --device 0 \
  --extra lr0=0.005
```

### 对比模型

训练完成后，用同一验证集比较 YOLOv8 / YOLO11：

```bash
python benchmark_ultralytics_models.py \
  --models runs/train/yolov8_waterbag/weights/best.pt runs/train/yolo11_waterbag/weights/best.pt \
  --data data/waterbag.yaml \
  --device 0 \
  --output artifacts/model_benchmarks.csv \
  --json-output artifacts/model_benchmarks.json
```

建议在 README 或简历项目说明中记录如下表格，而不是只写“升级到最新模型”：

| Model | mAP50-95 | mAP50 | Precision | Recall | Total ms/img | 结论 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| YOLOv8n | 待测 | 待测 | 待测 | 待测 | 待测 | 稳定基线 |
| YOLO11n | 待测 | 待测 | 待测 | 待测 | 待测 | 升级候选 |

更像工程决策的结论应该是：在产线节拍、可接受漏检率、GPU/CPU 资源和模型部署格式约束下，选择哪个模型进入 `configs/production.example.yaml`。

## Web API

```http
POST /api/control/start
POST /api/control/stop
GET  /api/status
GET  /api/results/recent?limit=12
GET  /api/results/metrics?limit=40
POST /api/demo/upload
```

说明：

- `/api/results/recent`
  返回最近结果列表，适合历史表格或外部看板接入
- `/api/results/metrics`
  返回 timeout / ack retry / stale frame / plc failure 等聚合统计
- `/api/demo/upload`
  上传图片到指定相机目录，复用与产线一致的文件监控链路

## 系统链路

这套 demo 的核心价值不是“调用了一个检测模型”，而是把一条工业视觉链路明确建模出来。

### 简历定位

如果把这个项目写进机器人链路工程师简历，建议突出：

- 工业视觉闭环：相机输入、检测、袋体级关联、决策、PLC 控制和 Ack 反馈
- 时序可靠性：乱序旧帧忽略、等待超时、Ack 重试和故障注入
- 可观测性：Web 面板、SQLite 留档、回放和异常指标
- 模型工程化：YOLOv5 legacy 资产保留，YOLOv8 / YOLO11 统一训练与 benchmark 选型

一句话版本：

> 构建水样采集袋工业视觉检测闭环系统，完成双相机图像接入、YOLO 缺陷检测、袋体级多相机关联、PLC 指令下发与 Ack 重试、历史回放和故障注入验证，并对 YOLOv8 / YOLO11 进行精度与端侧时延对比以支撑部署选型。

### 数据模型

- `FramePacket`
  传感器输入数据包
- `PerceptionResult`
  算法感知结果
- `DecisionResult`
  决策层输出
- `ControlCommand`
  发送给执行层的控制命令
- `ExecutionFeedback`
  PLC/mock 返回的 Ack 与执行反馈
- `BagSummary`
  同一袋体跨相机聚合结果
- `TimingBreakdown`
  队列、推理、决策、控制、持久化时延
- `PipelineStateEvent`
  显式状态机轨迹

### 多相机袋体级关联

当前实现不把双相机当成两条完全独立的检测流，而是围绕 `bag_id` 进行聚合：

- 正常袋体默认等待所有预期相机都到齐后再放行
- 任一相机命中异常时整袋立即 `reject`
- 若长时间等不到另一侧相机，会按 `pending_timeout_ms` 输出独立 timeout 结果
- 若同机位旧帧乱序迟到，会被识别并忽略

### 控制 Ack / 超时 / 重试

PLC 层支持：

- `ack_timeout_ms`
- `max_retries`
- `retry_interval_ms`
- `mock_ack_latency_ms`
- `mock_fail_first_attempts`

因此可以稳定演示：

- 正常 Ack
- Ack 超时
- Ack 重试成功
- 重试耗尽失败

### 重复缺陷状态隔离

重复缺陷判定虽然仍是轻量持久化方案，但已经支持：

- `history_namespace`
- 按 `runtime / replay / manual` 来源隔离

这样回放、在线监控和单图排查共用一个历史文件时，不会互相污染状态。

## 当前推荐目录结构

如果你是第一次阅读这个仓库，建议按这个顺序看：

```text
.
├── app.py
├── benchmark_ultralytics_models.py
├── configs/
│   ├── demo.yaml
│   └── production.example.yaml
├── train_ultralytics.py
├── train_v8.py
├── train_yolo11.py
├── waterbag_inspection/
│   ├── cli.py
│   ├── config.py
│   ├── schemas.py
│   ├── pipeline.py
│   ├── correlation.py
│   ├── policy.py
│   ├── plc.py
│   ├── replay.py
│   ├── repeater.py
│   ├── fault_injection.py
│   ├── service.py
│   ├── storage.py
│   └── webapp.py
├── templates/
│   └── index.html
├── tests/
├── legacy/
└── demo_data/
```

## 开发与测试

安装开发依赖：

```bash
pip install -r requirements-dev.txt
```

运行测试：

```bash
python -m pytest -q tests
```

当前测试覆盖的重点包括：

- 二阶段检测主流程
- 多相机关联
- 等待超时淘汰
- Ack 重试
- 乱序旧帧忽略
- replay
- 故障注入
- SQLite 留档

## 限制与说明

- 仓库默认不附带真实生产权重
- `configs/production.example.yaml` 只是部署模板，不代表开箱即用
- 当前 demo 更强调链路与工程结构；模型 benchmark 脚本已提供，但真实指标需要接入你的生产数据集和权重后生成
- 仓库保留了较多原始 YOLO 代码与历史脚本，是为了兼容原始项目演化过程

## 路线图

后续值得继续推进的方向包括：

- 将 `bag_id` 从文件名推断升级为显式产线触发 ID
- 增加更细粒度的相机掉线、网络延迟、PLC 故障注入
- 将重复缺陷状态从 JSON 文件迁移到数据库或缓存
- 增加趋势统计、检索、过滤和报表导出
- 引入真实回放数据集做非 mock 回归验证
- 增加 ONNX / TensorRT 导出和部署时延 benchmark

## 许可证

本仓库使用 [`LICENSE`](LICENSE) 中提供的 `AGPL-3.0` 许可证。

如果你计划将本项目用于网络服务、闭源系统或商业场景，请务必先确认许可证要求。

## 致谢

- 感谢原始 YOLOv5/YOLO 生态为训练、推理和模型导出提供的基础能力
- 感谢这个项目早期在真实工业场景中的探索，它为后续工程化重构提供了足够真实的问题来源
