# train_yolov8.py
from ultralytics import YOLO

def train_yolov8():
    # 加载模型
    model = YOLO('yolov8n.pt')  # 使用预训练权重

    # 训练配置
    results = model.train(
        data='data/waterbag.yaml',
        epochs=100,
        imgsz=640,
        batch=16,
        save=True,
        device=0,  # GPU设备
        workers=8,
        project='runs/train',
        name='yolov8_waterbag',
        exist_ok=True
    )

    return results

if __name__ == '__main__':
    train_yolov8()