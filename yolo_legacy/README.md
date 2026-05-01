# YOLO Legacy 归档

本目录集中存放从原始 YOLOv5 工程保留下来的上游代码和示例配置。它们用于保留项目演化轨迹、参考旧训练/导出方式，不是当前水样袋检测系统的主入口。

当前推荐维护的业务链路在：

- `waterbag_inspection/`：Python demo、Web、回放、故障注入和 Ultralytics 推理适配
- `cpp_backend/`：C++ 实时链路、相机 burst、PLC 控制和缺陷推理编排
- `train_ultralytics.py`、`train_v8.py`、`train_yolo11.py`：当前保留的 YOLOv8 / YOLO11 训练入口
- `config/waterbag.yaml`：当前水样袋数据集配置

本目录主要包含：

| 路径 | 内容 |
| --- | --- |
| `detect/` | YOLOv5 检测、训练、验证脚本 |
| `models/` | YOLOv5 模型结构和模型 YAML |
| `utils/` | YOLOv5 工具函数 |
| `classify/` | YOLOv5 分类模块示例 |
| `segment/` | YOLOv5 分割模块示例 |
| `data/` | COCO、VOC、ImageNet 等通用示例数据集配置 |
| `export.py`、`benchmarks.py`、`hubconf.py` | YOLOv5 导出、benchmark 和 PyTorch Hub 入口 |

如果后续不再需要保留 YOLOv5 历史资产，可以整体删除本目录，不影响当前 demo、测试、Web 链路和 C++ 后端。
