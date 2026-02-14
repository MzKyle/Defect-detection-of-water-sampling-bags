设备采用两个MV-CU120-10U型号的海康工业摄像头，镜头分别采用12mm与25mm，光源采用FG-ZK450400-W与FG-TH400300-W型背光板。以下是采集部分示意图，详细打光方案已整理到文件中。

<img width="307" height="403" alt="image" src="https://github.com/user-attachments/assets/e1713f61-29d6-43f1-b062-70347321d9f9" />

# YOLOv5 水袋检测项目
基于 YOLOv5 实现工业场景下水袋目标的检测与分析，支持目标裁剪、区域检测、特征可视化等功能。

## 项目结构
- `configs/`: 训练/检测配置文件
- `data/`: 数据集配置与示例
- `models/`: 模型定义
- `scripts/`: 自定义业务脚本
- `outputs/`: 结果输出目录

## 快速开始
### 1. 克隆仓库
```bash
git clone https://github.com/你的用户名/项目名.git
cd 项目名
```

### 2. 安装依赖
```bash
pip install -r requirements.txt
```

### 3. 下载数据集
- 数据集下载链接：[百度网盘链接]（提取码：xxx）
- 下载后解压到 `data/dataset/`，目录结构要求：
  ```
  data/dataset/
  ├── images/
  │   ├── train/
  │   └── val/
  └── labels/
      ├── train/
      └── val/
  ```

### 4. 运行检测
```bash
python detect.py --weights runs/train/exp/weights/best.pt --source data/samples/waterbag.jpg --save-txt --save-crop
```

## 训练说明
```bash
python train.py --cfg models/waterbag_model.yaml --data data/waterbag.yaml --epochs 100 --batch-size 16
```

## 结果说明
检测结果默认保存到 `outputs/detect/`，裁剪结果保存到 `outputs/cropped/`。
```

#### 3. 可选优化：
- **LICENSE**：添加 MIT 许可证（适合开源），在 GitHub 仓库页面可直接生成；
- **ISSUE_TEMPLATE**：在 `.github/ISSUE_TEMPLATE/` 下添加 bug 反馈、功能请求模板，方便协作；
- **可视化封面**：在 README 开头添加项目效果图（如检测结果对比图），更直观。

### 四、上传到 GitHub 的最终步骤
1. 本地整理好项目（删除冗余文件，配置好 `.gitignore`、README 等）；
2. 初始化 Git 仓库（若未初始化）：
   ```bash
   git init
   git add .
   git commit -m "init: YOLOv5 水袋检测项目，添加配置与说明"
   ```
3. 关联 GitHub 远程仓库并推送：
   ```bash
   git remote add origin https://github.com/你的用户名/项目名.git
   git push -u origin main
   ```

### 总结
1. **数据集**：优先用 DVC 管理，或提供外链+示例，绝对不上传原始大数据；
2. **结构**：精简冗余目录，统一输出/脚本目录，让结构清晰可追溯；
3. **规范**：必加 `.gitignore` 和详细 README，保证他人能一键复现，这是“好看”且实用的核心。
