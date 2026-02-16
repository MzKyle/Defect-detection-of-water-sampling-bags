# 工业水样采集袋缺陷检测系统
aaaaaaaaaa
## 项目概述

这是一个基于YOLOv5/YOLOv8的工业水样采集袋缺陷检测系统，专门用于检测水样检测袋的正反面缺陷。系统集成了文件监控、Web界面、实时通信和PLC控制等功能，实现了完整的工业自动化检测流程。本项目旨在利用视觉自动检测装置代替人工检测水样采集袋的缺陷问题。

设备采用两个MV-CU120-10U型号的海康工业摄像头，光源采用FG-ZK450400-W与FG-TH400300-W型背光板。以下是采集部分示意图，打光方案已整理到项目附加文件中。

<img width="300" height="403" alt="5f7b2509a5016620ea8dca1875969b8d" src="https://github.com/user-attachments/assets/9d035640-ed79-42a2-97fd-8c3322cd202d" />


## 系统架构

```
├── 文件监控系统 (Watchdog)
├── Flask Web前端
├── WebSocket实时通信
├── YOLOv5双模型检测
├── 海康威视双摄像头
└── PLC通信模块
```


## 项目文件结构

```
yolov5/
├── app.py                 # 主应用程序（核心检测系统）
|—— db.py                    # 数据库操作模块
|—— filter_box.py              # 缺陷筛选模块
|—— check_box_repeater.py      # 判断是否与上一次检测的袋子的结果重复，是否清洁玻璃
|——check_box_repeater_second.py  # 判断正反面是不是同一个缺陷
├── crypt/
│   ├── legacy_encrypt.py      # 模型加密脚本，多个历史模型序加密时用
│   ├── weight_crypto.py       # 模型加密脚本，训练完成后自动加密处理
|—— Dataset splitting.py        # 数据集划分脚本，将数据集划分为训练集和验证集
|—— Dataset splitting_2.py      # 数据集划分脚本，将分块后的小图片数据集划分为训练集和验证集
│—— detect/                    #yolov5训练验证检测模型
│   ├── detect.py
|   |——train.py
|   |——val.py
|   |——hyp.scratch.yaml
|——hubconf.py                  # yolo模型配置文件
|—— train_v8.py                 #yolov8训练验证检测模型
|—— export.py                  # yolo模型导出脚本
|—— hubconf.py                 # yolo模型配置文件
|—— utils.py                  # 工具函数模块
├── test_SingleImg.py          # 单图检测测试
├── test_MultiImg.py           # 批量检测测试
├── data/waterbag.yaml         # 数据集配置文件
├── runs/train/                # 训练结果目录
│   ├── exp6/weights/          # 一次检测模型权重
│   └── exp7/weights/          # 二次检测模型权重
├── templates/                 # Web界面模板
│   └── index.html            # 主控制界面
└── detect_result/            # 检测结果保存目录
```

## 安装和配置

### 环境要求
```bash
# 核心依赖
Python 3.8+
PyTorch 1.7+
Flask 2.0+
OpenCV 4.0+

# 完整依赖列表见 requirements.txt
```

### 安装步骤

1. **克隆项目**
```bash
git clone <repository-url>
cd Defect_detection
```

2. **安装依赖**
```bash
pip install -r requirements.txt
```

3. **配置摄像头路径**
修改 `app.py` 中的摄像头文件夹配置：
```python
CAMERA_CONFIG = {
    "folder1": r"D:\MVS\data\MV-CU120-10UC (DA5594458)",
    "folder2": r"D:\MVS\data\MV-CU120-10UC (DA4792752)",
    # ... 其他配置
}
```

4. **配置模型路径**
```python
MODEL_CONFIG = {
    "encrypted_path": r"path\to\best.enc",
    "key_path": r"path\to\model.key"
}
```

5. **配置PLC通信**
```python
PLC_CONFIG = {
    "port": "COM4",
    "baudrate": 115200,
    # ... 其他串口配置
}
```
6.**重新训练模型**

给出训练命令示例，根据需要调整参数
```bash
python train.py \
  --weights '' \                # 无初始权重
  --cfg models/yolov5s.yaml \   # 模型配置文件（根据你的模型选择）
  --hyp Defect_detection/detect/hyp.scratch.yaml \  # 指定你的超参数文件路径
  --data Defect_detection/detect/data.yaml \        # 数据集配置文件（需提前准备）
  --epochs 100 \                # 训练轮数
  --batch-size 32 \             # 批次大小
  --device 0 \                  # GPU编号（CPU用cpu）
  --project Defect_detection/runs/train \           # 训练结果保存路径
  --name zero_train_hyp \       # 实验名称
  --exist-ok                    # 允许覆盖已有实验目录
```

yolov8去改train_v8.py中的参数然后运行
```
yolo detect train \
  model=yolov8s.yaml \          # 模型配置（从0训练需指定yaml，而非预训练权重）
  data=Defect_detection/detect/data.yaml \          # 数据集配置
  hyp=Defect_detection/detect/hyp.scratch.yaml \    # 超参数文件路径
  epochs=100 \
  batch=32 \
  device=0 \
  pretrained=False \            # 核心：禁用预训练（从0训练）
  project=Defect_detection/runs/train \
  name=zero_train_hyp
```



## 使用说明

### 启动系统

1. **命令行启动**
```bash
python app.py
```

2. **自动功能**
- 系统启动后自动打开浏览器 (http://127.0.0.1:5000)
- 自动开始监控摄像头文件夹
- 实时检测并显示结果

### Web界面操作

1. **主控制面板**
- 查看实时检测状态
- 启动/停止检测系统
- 查看历史检测记录

2. **实时监控**
- 摄像头1/2检测状态
- 缺陷统计信息
- 系统运行日志

### 检测流程

1. **图像采集**: 摄像头实时采集水样袋图像
2. **文件监控**: 系统监控新图像文件创建
3. **一次检测**: 快速判断是否有缺陷
4. **二次检测**: 对疑似缺陷进行精细分析
5. **结果输出**: 通过PLC控制生产线
6. **数据记录**: 保存检测结果到数据库


## 核心功能

### 1. 双摄像头检测系统
- **摄像头1**: 检测水样采集袋正面
- **摄像头2**: 检测水样采集袋反面
- **数据流**: 实时图像采集 → 文件系统监控 → 自动检测

### 2. 双重模型检测策略
- **一次检测模型**: 快速粗检测，筛选正常/异常样本，直接完整的水样采集袋进行yolo模型检测（此次模型权重针对完整图片进行）。
- **二次检测模型**: 精细网格检测，将一次检测为正常的水样采集袋图分割为小图，二次yolo模型检测（此次模型权重针对小块图片训练得到）。
- **检测流程**: 大图检测 → 异常样本直接警报 → 正常样本分割小图再检测，二次确认是否真的没有问题。
>假阳性（False Positive）是指系统错误地将正常的水样采集袋判断为异常，而假阴性（False Negative）是指系统错误地将异常的水样采集袋判断为正常。因为水样采集袋瑕疵的概率在3%以内，考虑各方面需求针对本项目而言，假阴性的危害远大于假阳性，因此对正常样例采用严格审查。

### 3. 实时Web监控界面
- **框架**: Flask + SocketIO
- **功能**: 实时显示检测结果、系统状态、历史记录
- **通信**: WebSocket实现实时数据推送

### 4. PLC工业控制集成
- **通信协议**: Modbus RTU
- **控制功能**: 生产线启停、报警输出、状态反馈
- **端口**: COM4, 115200波特率


## 核心组件详解

### 1. 文件监控系统 (Watchdog)

**功能特点**:
- 实时监控两个摄像头文件夹
- 文件创建事件触发检测
- 冷却时间机制防止重复处理
- 自动备份处理过的图像

### 2. Flask Web前端

**Web功能**:
- 系统启动/停止控制
- 实时检测结果显示
- 历史记录查询
- 系统状态监控

### 3. WebSocket实时通信

**实时功能**:
- 检测结果即时推送
- 系统状态实时更新
- 异常报警通知
- 用户操作反馈

### 4. 双重模型检测引擎

#### 一次检测（整图快速筛选）
模型1用整图训练，快速筛选出异常样本的能力，但可能存在有极小的污点发现不了的情况

#### 二次检测（小块精细分析）
模型2用小块图训练，针对一次检测为正常的样例进行更细致的检查，进一步确认是否真的没有问题。减少假阳例的情况。

### 5. PLC通信模块



**PLC控制功能**:
- 摄像头1检测结果输出 (寄存器100)
- 摄像头2检测结果输出 (寄存器102)  
- 重复缺陷报警输出 (寄存器104)
- 生产线控制信号




## 技术特点

### 1. 高性能检测
- **双重模型策略**: 兼顾检测速度和精度
- **GPU加速**: 利用CUDA进行快速推理
- **并行处理**: 多线程处理不同摄像头数据

### 2. 工业级可靠性
- **自动恢复机制**: 系统异常时自动重启
- **错误处理**: 完善的异常捕获和处理
- **日志记录**: 详细的运行日志记录

### 3. 安全保护
- **模型加密**: 使用Fernet加密保护模型权重
- **访问控制**: Web界面权限管理
- **数据备份**: 自动备份重要数据

### 4. 可扩展性
- **模块化设计**: 各功能模块独立，易于维护
- **配置驱动**: 通过配置文件调整系统参数
- **接口标准化**: 便于集成其他工业设备

## 故障排除

### 常见问题

1. **摄像头连接失败**
   - 检查摄像头电源和网络连接
   - 确认MVS软件正常运行

2. **PLC通信异常**
   - 检查COM端口配置
   - 确认波特率等参数匹配
   - 检查PLC设备状态

3. **模型加载失败**
   - 确认模型文件路径正确
   - 检查密钥文件完整性
   - 验证CUDA环境配置

### 日志查看
系统运行日志保存在 `logs/` 目录下，包含详细的运行信息。

## 开发指南

### 代码结构说明

- **app.py**: 主应用程序，包含所有核心功能
- **模型管理**: 使用单例模式确保模型只加载一次
- **线程安全**: 使用锁机制保证多线程安全
- **事件驱动**: 基于文件系统事件的自动检测触发

### 扩展开发

1. **添加新摄像头**
   - 在CAMERA_CONFIG中添加新配置
   - 创建对应的CameraHandler实例
   - 更新Observer监控列表

2. **修改检测算法**
   - 继承YOLOModel类实现自定义检测逻辑
   - 修改detect和detect_patches方法
   - 更新模型配置文件

3. **集成新设备**
   - 实现新的设备控制类
   - 在PLCController中添加对应方法
   - 更新Web界面显示

## 许可证

本项目基于AGPL-3.0许可证开源。

## 联系我们

如有问题或建议，请联系2972689924@qq.com。

---

**注意**: 本系统为工业级应用，请在专业人员指导下部署和使用。
