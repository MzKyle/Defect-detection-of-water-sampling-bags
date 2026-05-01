# YOLOv8 / YOLO11 选型

## 为什么不只写“升级到最新模型”

工业项目里，模型升级需要回答三个问题：

1. 精度是否提升，尤其是微小缺陷召回？
2. 端侧延迟是否满足产线节拍？
3. 部署复杂度和稳定性是否可接受？

因此项目将 YOLOv5 legacy 资产归档到 `yolo_legacy/`，同时保留 YOLOv8 / YOLO11 统一训练与 benchmark 入口，让模型选择可以被复现。

## 推荐实验矩阵

| 模型 | 角色 | 说明 |
| --- | --- | --- |
| YOLOv5 | legacy baseline | 保留原项目演化痕迹，不作为新主线 |
| YOLOv8n | stable baseline | 稳定、常见、部署资料多 |
| YOLO11n | upgrade candidate | 新主线候选，适合与 YOLOv8n 对比 |

## 训练命令

YOLOv8:

```bash
python train_v8.py --data config/waterbag.yaml --device 0
```

YOLO11:

```bash
python train_yolo11.py --data config/waterbag.yaml --device 0
```

## 对比命令

```bash
python benchmark_ultralytics_models.py \
  --models runs/train/yolov8_waterbag/weights/best.pt runs/train/yolo11_waterbag/weights/best.pt \
  --data config/waterbag.yaml \
  --device 0 \
  --output artifacts/model_benchmarks.csv \
  --json-output artifacts/model_benchmarks.json
```

## 推荐记录表

| Model | mAP50-95 | mAP50 | Precision | Recall | Total ms/img | Weights MB | 结论 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| YOLOv8n | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 稳定基线 |
| YOLO11n | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 升级候选 |

## 简历表达建议

不要只写：

```text
使用 YOLOv8/YOLO11 完成缺陷检测。
```

更推荐：

```text
构建可替换检测后端，保留 YOLOv5 legacy 资产，引入 YOLOv8 / YOLO11 训练与 benchmark 流程，对 mAP、召回率、模型体积和单图推理时延进行对比，以产线节拍和漏检约束支撑部署模型选型。
```

这类表述更符合机器人链路工程师的能力画像：不是追模型名字，而是能把模型纳入工程链路并做可解释的部署取舍。
