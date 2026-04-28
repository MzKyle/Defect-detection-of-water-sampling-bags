# 环境依赖

## Python 版本

项目当前要求：

| 项目 | 要求 |
| --- | --- |
| Python | `>= 3.10` |
| 操作系统 | Linux / Windows 均可，生产模板中保留 Windows 相机目录示例 |
| GPU | 非必需；真实 YOLO 推理或训练建议使用 NVIDIA GPU |

## 推荐环境方式

建议使用虚拟环境隔离依赖，避免训练依赖影响系统 Python：

```bash
cd Defect-detection-of-water-sampling-bags
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Windows PowerShell:

```powershell
cd Defect-detection-of-water-sampling-bags
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

## 最小演示依赖

只运行 Web demo、mock 检测器、mock PLC 和离线故障注入时，安装：

```bash
python -m pip install -r requirements-demo.txt
```

最小依赖包含：

| 依赖 | 用途 |
| --- | --- |
| `Flask` | HTTP 服务 |
| `Flask-SocketIO` | 实时推送 |
| `PyYAML` | 读取 YAML 配置 |
| `watchdog` | 文件目录监听 |
| `opencv-python-headless` | 图像读取、绘制、demo 图生成 |
| `numpy` | 图像数组处理 |

## 完整开发 / 生产验证依赖

如果要接入真实模型、训练 YOLO、加密权重或 Modbus PLC：

```bash
python -m pip install -r requirements.txt
```

完整依赖在最小依赖基础上增加：

| 依赖 | 用途 |
| --- | --- |
| `ultralytics` | YOLOv8 / YOLO11 训练、验证、推理 |
| `torch`, `torchvision` | 深度学习训练与推理 |
| `cryptography` | 加密模型权重解密 |
| `pymodbus` | Modbus RTU PLC 通信 |
| `pandas`, `matplotlib`, `seaborn`, `scipy` | 训练评估和可视化 |

## 依赖版本说明

`requirements-demo.txt` 和 `requirements.txt` 将 `numpy` 限制在 `1.26.x`，并限制了 OpenCV 上限。这是为了兼容常见的 `scipy` / `matplotlib` 组合，避免安装 OpenCV 新版本时把 `numpy` 拉到 `2.x` 引发全局环境冲突。

## 验证安装

```bash
python --version
python -m pip check
python -m pytest -q tests
```

期望结果：

```text
No broken requirements found.
15 passed
```

## 常见问题

| 现象 | 可能原因 | 处理方式 |
| --- | --- | --- |
| `ModuleNotFoundError: flask_socketio` | 未安装最小依赖 | 执行 `python -m pip install -r requirements-demo.txt` |
| OpenCV 读图失败 | 图片路径不存在或格式不支持 | 检查相机目录、文件扩展名和权限 |
| YOLO 加载失败 | 未安装完整依赖或权重路径错误 | 安装 `requirements.txt` 并检查 `configs/production.example.yaml` |
| Modbus 连接失败 | 串口、波特率或 PLC 未就绪 | 核对 `plc` 配置和现场接线 |
