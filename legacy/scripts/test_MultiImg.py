from PIL import Image, ImageDraw, ImageFont
import pandas as pd
import torch
from weight_crypto import WeightEncryptor
from pathlib import Path
import io
import os
from models.common import AutoShape
from utils.general import set_logging
from cryptography.fernet import Fernet

def load_encrypted_model(encrypted_path, key_path, device='cuda'):
    """解密并加载符合YOLOv5官方接口的模型"""
    # 加载密钥
    with open(key_path, 'rb') as f:
        key = f.read()

    # 解密文件
    cipher = Fernet(key)
    with open(encrypted_path, 'rb') as f:
        encrypted_data = f.read()
    decrypted_data = cipher.decrypt(encrypted_data)

    # 创建临时文件加载
    temp_file = Path('temp.pt')
    with open(temp_file, 'wb') as f:
        f.write(decrypted_data)

    # 按官方方式加载
    set_logging()  # 初始化YOLOv5日志系统
    model = torch.hub.load("ultralytics/yolov5",
                          "custom",
                          path=str(temp_file),
                          force_reload=False,
                          trust_repo=True)
    temp_file.unlink()  # 删除临时文件
    return model.to(device)

    
model_path = r"D:\code\yolov5\runs\train\exp2"
encrypted_path = os.path.join(model_path, "weights", "best.enc")
key_path = os.path.join(model_path, "model.key")
# 加载模型
model = load_encrypted_model(encrypted_path, key_path)

# # 加载自定义训练好的模型
# model = torch.hub.load("ultralytics/yolov5", "custom", path="/home/ysj/ncf/yolov5/runs/train/exp7/weights/best.pt")

# 输入图片文件夹路径
img_dir = "D:\MVS\copy"
save_dir = "D:\MVS\copy_res"  # 结果保存目录
os.makedirs(save_dir, exist_ok=True)  # 创建保存目录

# 结果记录文件路径
result_csv = os.path.join(save_dir, "detection_results.csv")

# 获取文件夹中所有图片的路径
img_paths = [os.path.join(img_dir, f) for f in os.listdir(img_dir) if f.endswith(('.jpg', '.jpeg', '.png'))]

# 初始化结果记录列表
results_list = []

# 定义字体（需要安装字体文件）
try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size=150)
except:
    font = ImageFont.load_default()

# 遍历文件夹中的每张图片
for img_path in img_paths:
    img_name = os.path.basename(img_path)
    print(f"正在处理图片: {img_name}")

    # 模型推理
    results = model(img_path)
    detections = results.pandas().xyxy[0]
    
    # 处理检测结果
    has_high_confidence = any(detections['confidence'] >= 0.2)
    max_confidence = detections['confidence'].max() if not detections.empty else 0.0
    
    # 生成结果记录
    results_list.append({
        "filename": img_name,
        "has_abnormality": has_high_confidence,
        "max_confidence": max_confidence,
        "detection_count": len(detections)
    })

    # 标注图像
    annotated_img = results.render()[0]
    annotated_img = Image.fromarray(annotated_img)
    
    if has_high_confidence:
        print(f"图片 {img_name} 有异常")
        draw = ImageDraw.Draw(annotated_img)
        text = "Unqualified"
        text_width, text_height = draw.textbbox((0, 0), text, font=font)[2:]
        draw.text(
            ((annotated_img.width - text_width) / 2, 20),
            text,
            fill="red",
            font=font,
        )

    # 保存标注后的图像
    # save_path = os.path.join(save_dir, img_name)
    # annotated_img.save(save_path)
    # print(f"图像已保存到 {save_path}")

    # 可视化显示（需要时取消注释）
    # annotated_img.show()

# 保存结果到CSV文件
pd.DataFrame(results_list).to_csv(result_csv, index=False)
print(f"检测结果已保存到 {result_csv}")
