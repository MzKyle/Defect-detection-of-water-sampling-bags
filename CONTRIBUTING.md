# Contributing

感谢你对 Waterbag Inspection 的关注。

这个仓库欢迎围绕工业视觉链路、文档、测试、演示体验和模型工程化的改进。为了让协作更顺畅，建议先阅读下面这些说明。

## 贡献前建议先看

- [README.md](README.md)
- [docs/README.md](docs/README.md)
- [docs/architecture/README.md](docs/architecture/README.md)
- [cpp_backend/README.md](cpp_backend/README.md)
- [docs/workflow/fault-injection.md](docs/workflow/fault-injection.md)

## 本地开发

### 1. 克隆仓库

```bash
git clone <your-fork-or-repo-url>
cd Defect-detection-of-water-sampling-bags
```

### 2. 安装依赖

最小演示依赖：

```bash
pip install -r requirements-demo.txt
```

完整依赖：

```bash
pip install -r requirements.txt
```

如果你要跑测试，建议再安装开发依赖：

```bash
pip install -r requirements-dev.txt
```

## 常用开发命令

```bash
make seed-demo
make serve-demo
make replay-demo
make inject-faults
make serve-docs
make test
```

如果你正在改 C++ 实时后端，也建议额外跑一遍：

```bash
cmake -S cpp_backend -B build/cpp_backend
cmake --build build/cpp_backend -j
ctest --test-dir build/cpp_backend --output-on-failure
```

也可以直接使用 CLI：

```bash
python -m waterbag_inspection serve --config config/demo.yaml
python -m waterbag_inspection replay --config config/demo.yaml --source-root demo_data --reset-history
python -m waterbag_inspection inject-faults --config config/demo.yaml --scenario all --output-root artifacts/fault_injection --clean
```

## 我们最欢迎的贡献方向

- 改进 `docs/` 中的架构、流程、算法说明
- 增加更细粒度的故障注入场景和测试覆盖
- 增强 Web 看板的观测、筛选和导出能力
- 优化 replay、timeout、Ack retry、异常帧处理链路
- 增加真实模型接入、导出和 benchmark 结果
- 改进 demo 配置、启动体验和目录结构

## 提交代码前请检查

### 测试

```bash
python -m pytest -q tests
```

如果改动涉及 `cpp_backend/`，请再确认 C++ 构建和测试通过。

### 最小功能验证

至少建议验证下面其中一项：

- `python -m waterbag_inspection inspect --config config/demo.yaml --camera-id 1 --image demo_data/camera1/bag_0001_cam1_good.jpg --reset-history`
- `python -m waterbag_inspection replay --config config/demo.yaml --source-root demo_data --reset-history`
- `python -m waterbag_inspection inject-faults --config config/demo.yaml --scenario all --output-root artifacts/fault_injection --clean`

### 文档改动

如果你修改了 README、docs、配置示例或命令入口，建议同步检查：

- 链接是否仍然有效
- 命令是否仍然可运行
- `docs/` 与 README 表述是否一致

## Pull Request 建议

- PR 标题尽量直接描述改动目标
- 一个 PR 尽量只解决一个主要问题
- 如果改动了链路行为，请在描述中说明影响范围
- 如果改动了配置、命令或文档，请附上更新后的使用方式
- 如果改动和故障注入、timeout、Ack retry、乱序帧有关，建议附上验证结果

## Issue 建议

提 Issue 时，尽量包含：

- 使用的配置文件
- 运行命令
- 期望结果和实际结果
- 关键日志或报错信息
- 如果和回放或故障注入有关，附上输入样本命名方式

## 代码与文档风格

- 尽量保持现有目录结构和模块边界
- 新增功能优先复用现有 `pipeline`、`replay`、`fault_injection` 和 `docs/` 结构
- 配置项尽量放到 YAML 或 dataclass 配置中，不要直接写死
- 如果是文档改动，优先写清楚“为什么这样设计”和“怎么验证”

## 许可证说明

提交到本仓库的贡献将默认遵循本项目的 [`AGPL-3.0`](LICENSE) 许可证。
