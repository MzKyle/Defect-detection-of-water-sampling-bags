# YOLOv5 Segment 文件夹详解

## 📁 **segment 文件夹概览**

`segment/` 文件夹是 YOLOv5 **实例分割** (Instance Segmentation) 专用模块，与检测任务不同，分割可以获得每个目标的精确**像素级边界**。

### **文件夹结构**
```
segment/
├── train.py           # 🔴 训练分割模型
├── val.py            # 🟡 验证分割模型
├── predict.py        # 🟢 推理（预测）分割
└── tutorial.ipynb    # 📘 Jupyter 教程
```

### **分割 vs 检测对比**

| 任务 | 输出 | 精度 | 应用场景 |
|------|------|------|---------|
| **检测** | 矩形框 (x,y,w,h) | 快速、粗糙 | 目标定位、通用检测 |
| **分割** | 像素级掩膜 (Mask) | 精确、细致 | 医学影像、自动驾驶、精准农业 |

**视觉对比**：
```
检测:  ┌──────────┐
       │  Object  │ ← 框不精确
       └──────────┘

分割:  ╭─────────╮
       │╱ Object ╲│ ← 精确边界
       ╰─────────╯
```

---

## 🔴 **train.py** - 分割模型训练脚本

### **文件属性**
| 属性 | 说明 |
|------|------|
| **功能** | 训练 YOLOv5 分割模型 |
| **行数** | 765 行 |
| **核心类** | `SegmentationModel` |
| **输入** | 分割数据集 (含掩膜标签) |
| **输出** | 训练模型 + 指标 (runs/train-seg/) |

### **主要功能**

#### **1. 数据加载**
```python
from utils.segment.dataloaders import create_dataloader

# 支持的格式：
# - 分割掩膜 (PNG, 二值化)
# - COCO 格式掩膜
# - 多边形标注
```

**数据集结构**：
```
dataset/
├── images/
│   ├── train/
│   │   ├── img1.jpg
│   │   └── img2.jpg
│   └── val/
└── labels/
    ├── train/
    │   ├── img1.png (掩膜)
    │   └── img2.png (掩膜)
    └── val/
```

#### **2. 模型构建**
```python
from models.yolo import SegmentationModel

# 支持模型尺寸
models:
  - yolov5n-seg (nano, 最快)
  - yolov5s-seg (small)
  - yolov5m-seg (medium, 推荐)
  - yolov5l-seg (large)
  - yolov5x-seg (extra-large, 最精确)
```

**分割模型架构对比检测**：
```
检测模型:
Backbone → Head → [Boxes, Confidence]

分割模型:
Backbone → Head → [Boxes, Confidence, Masks]
              ↓
          额外的掩膜解码器
```

#### **3. 损失函数**
```python
from utils.segment.loss import ComputeLoss

# 包含两部分损失：
# 1. 框损失 (Box Loss) - 边框定位精度
# 2. 掩膜损失 (Mask Loss) - 像素级分割精度
```

#### **4. 验证和评估**
```python
import segment.val as validate

# 自动调用 val.py
# 计算指标：
# - Mask mAP@0.5
# - Mask mAP@0.5:0.95
# - Box mAP
# - 像素精度 (Pixel Accuracy)
```

### **使用方式**

#### **基础训练**
```bash
# 用预训练权重训练 (推荐)
python segment/train.py \
    --data data/coco128-seg.yaml \
    --weights yolov5s-seg.pt \
    --img 640 \
    --epochs 100 \
    --batch-size 16 \
    --device 0
```

#### **从零开始训练**
```bash
# 不使用预训练权重
python segment/train.py \
    --data data/coco128-seg.yaml \
    --weights '' \
    --cfg models/segment/yolov5s-seg.yaml \
    --img 640 \
    --epochs 100
```

#### **单 GPU 训练**
```bash
python segment/train.py --data coco128-seg.yaml --weights yolov5s-seg.pt --img 640
```

#### **多 GPU 分布式训练** (DDP)
```bash
# 用4块 GPU 并行训练
python -m torch.distributed.run --nproc_per_node 4 --master_port 1 \
    segment/train.py \
    --data coco128-seg.yaml \
    --weights yolov5s-seg.pt \
    --img 640 \
    --device 0,1,2,3
```

### **关键参数**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--data` | str | coco128-seg.yaml | 数据集配置文件 |
| `--weights` | str | yolov5s-seg.pt | 预训练权重 (空=从零训练) |
| `--cfg` | str | yolov5s-seg.yaml | 模型配置文件 |
| `--epochs` | int | 100 | 训练轮数 |
| `--batch-size` | int | 16 | 批次大小 |
| `--img` | int | 640 | 输入图像大小 |
| `--device` | str | 0 | GPU 设备 (0=首块) |
| `--lr0` | float | 0.01 | 初始学习率 |
| `--optimizer` | str | SGD | 优化器 (SGD/Adam/AdamW) |
| `--augment` | bool | True | 数据增强 |
| `--patience` | int | 100 | 早停耐心值 |

### **训练输出**

```
runs/train-seg/
├── exp/
│   ├── weights/
│   │   ├── best.pt            # 最佳分割模型
│   │   └── last.pt            # 最后一个 epoch
│   ├── results.csv            # 训练指标
│   ├── hyp.yaml               # 超参数
│   └── opt.yaml               # 训练选项
└── exp2/
    └── ...
```

### **性能指标**

| 指标 | 说明 | 范围 |
|------|------|------|
| **box/loss** | 框回归loss | ↓ 越小越好 |
| **mask/loss** | 掩膜分割loss | ↓ 越小越好 |
| **mask/mAP@0.5** | 掩膜精度(IoU=0.5) | 0-1 (↑) |
| **mask/mAP@0.5:0.95** | 掩膜精度(所有IoU) | 0-1 (↑) |
| **Pixel Accuracy** | 像素分类正确率 | 0-1 (↑) |

---

## 🟡 **val.py** - 分割模型验证脚本

### **文件属性**
| 属性 | 说明 |
|------|------|
| **功能** | 验证分割模型的性能 |
| **行数** | 523 行 |
| **输入** | 分割模型 + 测试数据集 |
| **输出** | mAP、IoU 等指标 |

### **主要功能**

#### **1. 数据加载**
```python
from utils.segment.dataloaders import create_dataloader

# 加载验证集，支持：
# - 单个数据集
# - 多个数据集混合
```

#### **2. 模型推理**
```python
# 前向传播：图像 → 检测框 + 掩膜
predictions = model(images)
# - boxes: (N, 4) - 边框坐标
# - masks: (N, H, W) - 掩膜
# - confidence: (N,) - 置信度
```

#### **3. 指标计算**
```python
from utils.segment.metrics import Metrics, ap_per_class_box_and_mask

# 计算掩膜 IoU
mask_iou = mask_iou(pred_masks, gt_masks)

# 计算框 mAP
box_map = ap_per_class_box_and_mask(...)

# 计算掩膜 mAP
mask_map = ap_per_class_box_and_mask(...)
```

#### **4. 结果保存**
```
验证输出：
├── confusion_matrix_mask.png      # 掩膜混淆矩阵
├── F1_mask_curve.png              # 掩膜 F1 曲线
├── PR_mask_curve.png              # 掩膜精度-召回曲线
├── val_batch_*.jpg                # 验证集可视化
└── predictions.json               # 预测结果 (COCO 格式)
```

### **使用方式**

#### **验证单个模型**
```bash
python segment/val.py \
    --weights runs/train-seg/exp/weights/best.pt \
    --data data/coco128-seg.yaml \
    --img 640 \
    --batch-size 16 \
    --device 0
```

#### **验证多个模型格式**
```bash
# PyTorch
python segment/val.py --weights yolov5s-seg.pt

# TorchScript
python segment/val.py --weights yolov5s-seg.torchscript

# ONNX
python segment/val.py --weights yolov5s-seg.onnx

# TensorRT
python segment/val.py --weights yolov5s-seg.engine

# TensorFlow Lite
python segment/val.py --weights yolov5s-seg.tflite
```

#### **生成 COCO 评估结果**
```bash
python segment/val.py \
    --weights yolov5s-seg.pt \
    --data coco.yaml \
    --save-json
```

### **关键参数**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--weights` | str | yolov5s-seg.pt | 模型权重 |
| `--data` | str | coco128.yaml | 验证数据集 |
| `--img` | int | 640 | 输入图像大小 |
| `--batch-size` | int | 32 | 批次大小 |
| `--conf-thres` | float | 0.001 | 置信度阈值 |
| `--iou-thres` | float | 0.6 | NMS IoU 阈值 |
| `--device` | str | 0 | GPU 设备 |
| `--save-json` | bool | False | 保存 COCO JSON 格式 |
| `--half` | bool | False | FP16 推理 |

### **输出指标解读**

```
Class     Images  Instances       Box(P    R   mAP50  mAP50-95)  Mask(P    R   mAP50  mAP50-95)
all       5000     11239       0.945  0.918  0.948      0.725   0.957  0.922  0.957      0.742

说明：
- P (Precision): 预测正确率
- R (Recall): 检测完整性
- mAP50: 宽松评估 (IoU ≥ 0.5 即算对)
- mAP50-95: 严格评估 (IoU 从 0.5 到 0.95)
```

---

## 🟢 **predict.py** - 分割推理脚本

### **文件属性**
| 属性 | 说明 |
|------|------|
| **功能** | 用分割模型进行推理 |
| **行数** | 308 行 |
| **输入** | 图像/视频/网络流 |
| **输出** | 标注后的结果 (含掩膜) |

### **支持的输入源**

#### **图像输入**
```bash
# 单张图像
python segment/predict.py --weights yolov5s-seg.pt --source image.jpg

# 多张图像
python segment/predict.py --weights yolov5s-seg.pt --source images/

# 所有 JPG 图像
python segment/predict.py --weights yolov5s-seg.pt --source '*.jpg'
```

#### **视频输入**
```bash
# 视频文件
python segment/predict.py --weights yolov5s-seg.pt --source video.mp4

# 视频列表
python segment/predict.py --weights yolov5s-seg.pt --source videos.txt
```

#### **实时输入**
```bash
# 网络摄像头
python segment/predict.py --weights yolov5s-seg.pt --source 0

# 屏幕截图
python segment/predict.py --weights yolov5s-seg.pt --source screen

# YouTube 视频
python segment/predict.py --weights yolov5s-seg.pt --source 'https://youtu.be/xxx'

# RTSP 直播流
python segment/predict.py --weights yolov5s-seg.pt --source 'rtsp://example.com/stream'
```

### **支持的模型格式**

| 格式 | 后缀 | 说明 | 平台 |
|------|------|------|------|
| **PyTorch** | .pt | 标准训练格式 | 通用 |
| **TorchScript** | .torchscript | C++ 推理 | 高性能 |
| **ONNX** | .onnx | 模型标准格式 | 跨平台 |
| **OpenVINO** | openvino_model | Intel 优化 | CPU 优化 |
| **TensorRT** | .engine | NVIDIA 优化 | GPU 加速 |
| **CoreML** | .mlmodel | iOS 专用 | macOS/iOS |
| **TensorFlow** | SavedModel | TF 格式 | 服务器 |
| **TensorFlow Lite** | .tflite | 移动端 | 移动设备 |
| **Edge TPU** | edgetpu.tflite | 谷歌 TPU | 边缘设备 |
| **PaddlePaddle** | paddle_model | 国产框架 | 推理 |

### **主要功能**

#### **1. 分割推理**
```python
# 输入: 图像
# 处理过程:
#   1. 图像预处理 (缩放、归一化)
#   2. 模型推理 → 检测框 + 掩膜
#   3. 非最大抑制 (NMS) 去重
#   4. 掩膜后处理

# 输出：
# - 检测框 (x, y, w, h)
# - 置信度 (0-1)
# - 分割掩膜 (像素级)
```

#### **2. 掩膜处理**
```python
from utils.segment.general import process_mask, process_mask_native

# 掩膜大小调整
masks = process_mask(masks, shape=(640, 640))

# 原始分辨率掩膜
masks_native = process_mask_native(masks, shape=(1920, 1080))
```

#### **3. 可视化**
```python
from ultralytics.utils.plotting import Annotator

# 在图像上绘制：
# - 边框
# - 掩膜 (半透明彩色)
# - 类别标签
# - 置信度分数
```

### **使用方式**

#### **基础推理**
```bash
python segment/predict.py --weights yolov5s-seg.pt --source image.jpg
```

#### **高级推理**
```bash
python segment/predict.py \
    --weights yolov5s-seg.pt \
    --source videos/ \
    --img 1024 \
    --conf-thres 0.4 \
    --iou-thres 0.5 \
    --device 0 \
    --save-txt \
    --save-crop \
    --view-img \
    --line-thickness 2 \
    --hide-labels \
    --retina-masks
```

#### **批量处理**
```bash
# 处理整个文件夹
python segment/predict.py --weights yolov5s-seg.pt --source dataset/images/ --save-txt --save-crop

# 保存结果到特定路置
python segment/predict.py --weights yolov5s-seg.pt --source images/ --project results --name exp1
```

### **关键参数**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--weights` | str | yolov5s-seg.pt | 模型权重 |
| `--source` | str | data/images | 输入源 |
| `--img` | int | 640 | 推理大小 |
| `--conf-thres` | float | 0.25 | 置信度阈值 |
| `--iou-thres` | float | 0.45 | NMS IoU 阈值 |
| `--max-det` | int | 1000 | 最大检测数 |
| `--device` | str | '' | GPU 设备 ('' = auto) |
| `--view-img` | bool | False | 显示结果 |
| `--save-txt` | bool | False | 保存为 txt |
| `--save-crop` | bool | False | 保存裁剪目标 |
| `--nosave` | bool | False | 不保存结果 |
| `--line-thickness` | int | 3 | 边框粗细 |
| `--hide-labels` | bool | False | 隐藏标签 |
| `--hide-conf` | bool | False | 隐藏置信度 |
| `--half` | bool | False | FP16 加速 |
| `--retina-masks` | bool | False | 高分辨率掩膜 |
| `--vid-stride` | int | 1 | 视频帧率步长 |

### **推理输出**

```
runs/predict-seg/
├── exp/
│   ├── image1.jpg         # 标注后的图像
│   ├── image2.jpg
│   ├── labels/
│   │   ├── image1.txt     # 检测框和掩膜坐标
│   │   └── image2.txt
│   ├── crops/
│   │   ├── image1/
│   │   │   ├── obj_0.jpg  # 裁剪的目标
│   │   │   └── obj_1.jpg
│   │   └── image2/
│   └── masks/
│       ├── image1.png     # PNG 掩膜
│       └── image2.png
└── exp2/
    └── ...
```

### **输出结果示例**

**检测框格式** (labels/image.txt)：
```
0 0.5 0.5 0.3 0.4       # 类别 中心_x 中心_y 宽 高
1 0.2 0.3 0.4 0.5
```

**掩膜文件** (masks/image.png)：
- 单通道灰度图
- 像素值 = 实例 ID (0=背景, 1=第一对象, 2=第二对象...)

---

## 📘 **tutorial.ipynb** - Jupyter 交互式教程

### **文件属性**
| 属性 | 说明 |
|------|------|
| **格式** | Jupyter Notebook (.ipynb) |
| **功能** | 交互式学习分割任务 |
| **内容** | 代码示例 + 讲解 + 可视化 |

### **通常包含的章节**

#### **1. 环境设置**
```python
# 导入必要库
import torch
import cv2
from pathlib import Path
from IPython.display import Image, display

# 检查 GPU
print(torch.cuda.is_available())
```

#### **2. 数据探索**
```python
# 可视化数据集样本
# 显示图像和对应的掩膜
# 统计类别分布
```

#### **3. 模型训练**
```python
# 运行 train.py
# !python segment/train.py --data coco128-seg.yaml --weights yolov5s-seg.pt
```

#### **4. 模型验证**
```python
# 运行 val.py
# !python segment/val.py --weights runs/train-seg/exp/weights/best.pt
```

#### **5. 推理演示**
```python
# 加载模型
model = torch.hub.load('ultralytics/yolov5', 'yolov5s-seg')

# 推理
results = model(image)

# 可视化结果
results.show()
```

#### **6. 结果展示**
```python
# 绘制检测框和掩膜
# 导出结果
# 性能分析
```

### **如何使用**

#### **在本地运行**
```bash
jupyter notebook segment/tutorial.ipynb
```

#### **在在线平台运行**
- Google Colab
- Kaggle Notebooks
- Azure Notebooks

#### **关键优势**
- ✅ 交互式执行
- ✅ 实时显示结果
- ✅ 便于学习和调试
- ✅ 支持 GPU 加速

---

## 🚀 **完整工作流程**

### **场景 1：从零开始训练分割模型**

```bash
# 第1步：准备数据集 (COCO 格式)
bash data/scripts/get_coco.sh --val --segments

# 第2步：查看教程
jupyter notebook segment/tutorial.ipynb

# 第3步：训练分割模型
python segment/train.py \
    --data data/coco128-seg.yaml \
    --weights yolov5s-seg.pt \
    --img 640 \
    --epochs 100 \
    --batch-size 16 \
    --device 0

# 第4步：验证模型
python segment/val.py \
    --weights runs/train-seg/exp/weights/best.pt \
    --data data/coco128-seg.yaml \
    --img 640

# 第5步：推理测试
python segment/predict.py \
    --weights runs/train-seg/exp/weights/best.pt \
    --source test_images/ \
    --save-txt --save-crop
```

### **场景 2：用预训练模型快速推理**

```bash
# 直接用官方预训练模型推理
python segment/predict.py \
    --weights yolov5m-seg.pt \
    --source image.jpg \
    --view-img
```

### **场景 3：微调预训练模型**

```bash
# 第1步：用新数据集微调
python segment/train.py \
    --weights yolov5s-seg.pt \
    --data data/custom-seg.yaml \
    --img 640 \
    --epochs 50 \
    --batch-size 8 \
    --project runs \
    --name finetune

# 第2步：评估
python segment/val.py \
    --weights runs/finetune/exp/weights/best.pt \
    --data data/custom-seg.yaml

# 第3步：推理
python segment/predict.py \
    --weights runs/finetune/exp/weights/best.pt \
    --source custom_images/
```

---

## 📊 **分割精度指标详解**

### **掩膜 IoU (Intersection over Union)**

```
        交集面积
IoU = ─────────────────
      并集面积
      
IoU ∈ [0, 1]
1.0 = 完美分割
0.5 = 50% 重叠
0.0 = 无重叠
```

### **平均精度 (mAP)**

| 指标 | 说明 | 用途 |
|------|------|------|
| **mAP@0.5** | IoU=0.5 的平均精度 | 宽松评估，快速检查 |
| **mAP@0.75** | IoU=0.75 的平均精度 | 中等难度 |
| **mAP@0.5:0.95** | IoU 从 0.5 到 0.95 的平均 | 标准评估，COCO 用 |

### **像素精度 (Pixel Accuracy)**

```
       分割正确的像素数
PA = ──────────────────
     总像素数
```

---

## 🔗 **与其他模块的关系**

```
segment/ 分割模块
    ├─ train.py
    │   └─ 导入: models/yolo.py (SegmentationModel)
    │   └─ 导入: utils/segment/dataloaders.py
    │   └─ 导入: utils/segment/loss.py
    │
    ├─ val.py
    │   └─ 导入: utils/segment/metrics.py
    │   └─ 导入: utils/segment/plots.py
    │
    ├─ predict.py
    │   └─ 导入: utils/segment/general.py (mask 处理)
    │
    └─ tutorial.ipynb
        └─ 调用上述三个脚本

models/ 模型模块
    ├─ segment/yolov5*-seg.yaml (分割模型配置)
    └─ yolo.py (SegmentationModel 类)

utils/ 工具模块
    └─ segment/
        ├─ dataloaders.py (分割数据加载)
        ├─ loss.py (分割损失函数)
        ├─ metrics.py (分割评估指标)
        ├─ general.py (掩膜处理)
        └─ plots.py (分割可视化)
```

---

## 💡 **快速参考命令**

### **训练**
```bash
# 快速开始
python segment/train.py --data coco128-seg.yaml --weights yolov5s-seg.pt --epochs 10

# 完整配置
python segment/train.py --cfg models/segment/yolov5m-seg.yaml --data coco.yaml --weights yolov5m-seg.pt --img 640 --epochs 100 --batch-size 16 --device 0
```

### **验证**
```bash
# 快速验证
python segment/val.py --weights yolov5s-seg.pt --data coco128-seg.yaml

# 完整验证
python segment/val.py --weights runs/train-seg/exp/weights/best.pt --data coco.yaml --img 1024 --batch-size 32 --save-json
```

### **推理**
```bash
# 快速推理
python segment/predict.py --weights yolov5s-seg.pt --source image.jpg

# 完整推理
python segment/predict.py --weights yolov5s-seg.pt --source videos/ --img 1024 --conf-thres 0.5 --save-txt --save-crop --retina-masks
```

---

## ✅ **检查清单**

### **训练前检查**
- [ ] 数据集已下载且格式正确
- [ ] 掩膜标签已准备 (PNG 格式)
- [ ] GPU 可用且内存充足
- [ ] 预训练权重已下载

### **训练中检查**
- [ ] 损失函数持续下降
- [ ] 验证精度持续上升
- [ ] 没有 OOM (内存溢出) 错误
- [ ] 结果保存到 runs/ 目录

### **推理前检查**
- [ ] 模型权重文件存在
- [ ] 输入图像格式支持 (JPG, PNG 等)
- [ ] GPU 显存充足
- [ ] 输出目录可写

---

## 📝 **最佳实践**

| 方面 | 建议 |
|------|------|
| **数据集大小** | 至少 500 张分割标注图像 |
| **标签格式** | 使用 COCO 或 YOLO 格式 |
| **模型选择** | 开始用 yolov5s，精度不够再用 yolov5m |
| **输入分辨率** | 640 (标准), 1024 (高精度), 320 (快速) |
| **批次大小** | 16-32 (根据 GPU 内存调整) |
| **学习率** | 0.01 (从预训练) 或 0.001 (微调) |
| **数据增强** | 使用默认增强策略 |
| **早停** | patience=100 防止过拟合 |

