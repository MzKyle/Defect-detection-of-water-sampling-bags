# 训练与评测

## 训练定位

当前项目建议将 YOLOv5 作为历史资产保留，把 YOLOv8 作为稳定 baseline，把 YOLO11 作为升级 candidate。是否升级不靠模型名字决定，而靠同一数据集上的指标和部署约束决定。

## 数据集结构

默认配置文件：

```text
data/waterbag.yaml
```

推荐数据集目录：

```text
datasets/waterbag/
├── images/
│   ├── train/
│   └── val/
└── labels/
    ├── train/
    └── val/
```

标签格式使用 Ultralytics YOLO 标准格式：

```text
class_id center_x center_y width height
```

坐标均为 0 到 1 的归一化值。

## 训练 YOLOv8 baseline

```bash
python train_v8.py \
  --data data/waterbag.yaml \
  --epochs 100 \
  --imgsz 640 \
  --batch 16 \
  --device 0
```

Makefile:

```bash
make train-yolov8 DATA=data/waterbag.yaml DEVICE=0
```

## 训练 YOLO11 candidate

```bash
python train_yolo11.py \
  --data data/waterbag.yaml \
  --epochs 100 \
  --imgsz 640 \
  --batch 16 \
  --device 0
```

Makefile:

```bash
make train-yolo11 DATA=data/waterbag.yaml DEVICE=0
```

## 通用训练入口

`train_v8.py` 和 `train_yolo11.py` 都复用 `train_ultralytics.py`。

常见参数：

| 参数 | 说明 |
| --- | --- |
| `--model` | 预训练模型或已有 checkpoint |
| `--data` | 数据集 YAML |
| `--epochs` | 训练轮数 |
| `--imgsz` | 输入尺寸 |
| `--batch` | batch size |
| `--device` | GPU id 或 `cpu` |
| `--patience` | early stopping patience |
| `--cache` | 缓存数据集图片 |
| `--resume` | 从 checkpoint 恢复训练 |
| `--extra KEY=VALUE` | 透传额外 Ultralytics 参数 |

示例：

```bash
python train_yolo11.py \
  --model yolo11n.pt \
  --data data/waterbag.yaml \
  --epochs 150 \
  --imgsz 640 \
  --batch 16 \
  --device 0 \
  --extra lr0=0.005 \
  --extra mosaic=0.8
```

## 模型对比

训练完成后使用同一验证集对比：

```bash
python benchmark_ultralytics_models.py \
  --models runs/train/yolov8_waterbag/weights/best.pt runs/train/yolo11_waterbag/weights/best.pt \
  --data data/waterbag.yaml \
  --device 0 \
  --output artifacts/model_benchmarks.csv \
  --json-output artifacts/model_benchmarks.json
```

输出字段：

| 字段 | 说明 |
| --- | --- |
| `model` | 模型名或权重路径 |
| `weights_mb` | 本地权重体积 |
| `precision` | 验证集 precision |
| `recall` | 验证集 recall |
| `map50` | mAP@0.5 |
| `map50_95` | mAP@0.5:0.95 |
| `preprocess_ms` | 预处理耗时 |
| `inference_ms` | 推理耗时 |
| `postprocess_ms` | 后处理耗时 |
| `total_ms` | 单图总耗时估计 |

## 进入生产配置

完成模型选择后，将目标权重写入：

```yaml
models:
  primary:
    backend: ultralytics
    weights_path: artifacts/models/yolo11_primary_best.pt
  patch:
    backend: ultralytics
    weights_path: artifacts/models/yolo11_patch_best.pt
```

如果要保护权重，可以使用 `encrypted_path + key_path`，运行时由 `UltralyticsDetector` 临时解密加载。
