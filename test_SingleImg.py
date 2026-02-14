import os
import base64
from io import BytesIO
from flask import Flask, render_template, request, jsonify
import torch
from PIL import Image
import cv2
import numpy as np
from pathlib import Path
from cryptography.fernet import Fernet

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'

# 加密模型加载函数
def load_encrypted_model(encrypted_path, key_path, device='cuda'):
    """解密并加载YOLOv5模型"""
    # 加载密钥
    with open(key_path, 'rb') as f:
        key = f.read()

    # 解密文件
    cipher = Fernet(key)
    with open(encrypted_path, 'rb') as f:
        encrypted_data = f.read()
    decrypted_data = cipher.decrypt(encrypted_data)

    # 创建临时文件加载模型
    temp_file = Path('temp.pt')
    with open(temp_file, 'wb') as f:
        f.write(decrypted_data)

    # 加载模型
    model = torch.hub.load("ultralytics/yolov5", 
                          "custom", 
                          path=str(temp_file),
                          force_reload=True,
                          trust_repo=True)

    temp_file.unlink()  # 删除临时文件
    return model.to(device)

# 初始化模型
model_path = "/home/kyle/ncf/yolov5/runs/train/exp4"
encrypted_path = os.path.join(model_path, "weights", "best.enc")
key_path = os.path.join(model_path, "model.key")
model = load_encrypted_model(encrypted_path, key_path)
model.eval()

def process_detection(img_path):
    """处理检测并返回结果"""
    # 执行推理
    results = model(img_path)

    # 获取检测结果
    detections = results.pandas().xyxy[0]
    has_high_confidence = any(detections['confidence'] >= 0.2)

    # 生成结果图像
    result_img = np.squeeze(results.render()) #result_img是numpy数组格式的图像数据,后续可以使用OpenCV或PIL进行处理和显示
    return result_img, has_high_confidence

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/detect', methods=['POST'])
def detect():
    if 'image' not in request.files:
        return jsonify({'error': 'No image uploaded'}), 400
    
    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400

    # 保存上传文件
    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(upload_path)

    try:
        # 处理检测
        result_img, has_issue = process_detection(upload_path)
        
        if result_img is not None:
            # 转换为PIL Image
            result_img_pil = Image.fromarray(result_img)
            
            # 转换为字节流
            buffered = BytesIO()
            result_img_pil.save(buffered, format="JPEG")
            
            # 获取base64编码
            result_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
            
            return jsonify({
                'result': result_base64,
                'status': '异常' if has_issue else '正常',
                'status_class': 'alert' if has_issue else 'normal'
            })
        return jsonify({'error': 'Detection failed'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        # 清理临时文件
        if os.path.exists(upload_path):
            os.remove(upload_path)

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    app.run(host='0.0.0.0', port=5000, debug=True)