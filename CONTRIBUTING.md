# Contributing

感谢对 Waterbag Inspection 的关注。

当前工程边界：

- C++ 负责实时链路：采图、推理、PLC、分拣、JSONL 结果输出。
- Python 只负责 Web 看板、SQLite 结果库、训练、benchmark 和 ONNX 导出。
- 不再新增 Python 版实时 pipeline、PLC/mock 控制、回放或故障注入实现。

## 本地验证

构建和测试 C++ 后端：

```bash
cmake -S cpp_backend -B build/cpp_backend
cmake --build build/cpp_backend -j
ctest --test-dir build/cpp_backend --output-on-failure
```

检查 Python 看板和离线工具：

```bash
python -m compileall waterbag_inspection train_ultralytics.py train_v8.py train_yolo11.py benchmark_ultralytics_models.py export_ultralytics_onnx.py
python -m waterbag_inspection sync-results --config config/cpp_backend/demo.ini
```

启动看板：

```bash
python -m waterbag_inspection serve --config config/cpp_backend/demo.ini
```

## 常用命令

```bash
make build-cpp
make run-cpp-once
make run-cpp-watch
make serve-dashboard
make sync-results
make test
make python-check
```

## 贡献方向

- 改进 `cpp_backend/` 的实时链路、设备适配、线程模型和测试。
- 改进 Python 看板的查询、筛选、导出和 SQLite schema。
- 改进训练、ONNX 导出、模型 benchmark 和部署配置。
- 清理和更新文档，保持 C++ 实时链路为唯一执行主线。
- 优化前端，后续可能会美化前端界面或者做成Qt小软件
## PR 建议

- 标题直接说明改动目标。
- 一个 PR 尽量只解决一个主要问题。
- 涉及实时链路时说明对产线时序、PLC、超时和分拣顺序的影响。
- 涉及前端或数据库时说明 JSONL/SQLite schema 和 API 的兼容性。

提交到本仓库的贡献默认遵循 [`AGPL-3.0`](LICENSE) 许可证。
