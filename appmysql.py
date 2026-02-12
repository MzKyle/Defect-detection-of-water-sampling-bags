import os
from flask import Flask, render_template, request, jsonify
import torch
from PIL import Image
from datetime import datetime
import cv2
import numpy as np
import base64
from pathlib import Path
from io import BytesIO
from models.common import AutoShape
from utils.general import set_logging
from cryptography.fernet import Fernet
import mysql.connector  # 用于连接MySQL数据库

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['RESULT_FOLDER'] = 'static/results'

# 数据库连接函数
def get_db_connection():
    conn = mysql.connector.connect(
        host='localhost',
        user='root',         # 替换为你的MySQL用户名
        password='123456', # 替换为你的MySQL密码
        database='waterbag'  # 替换为你的数据库名
    )
    return conn

# 加载加密模型函数（保持原有逻辑）
def load_encrypted_model(encrypted_path, key_path, device='cuda'):
    with open(key_path, 'rb') as f:
        key = f.read()
    
    cipher = Fernet(key)
    with open(encrypted_path, 'rb') as f:
        encrypted_data = f.read()
    decrypted_data = cipher.decrypt(encrypted_data)
    
    temp_file = Path('temp.pt')
    with open(temp_file, 'wb') as f:
        f.write(decrypted_data)
    
    set_logging()  # 初始化YOLOv5日志系统
    model = torch.hub.load("ultralytics/yolov5", 
                           "custom", 
                           path=str(temp_file),
                           force_reload=False,
                           trust_repo=True)
    temp_file.unlink()  # 删除临时文件
    return model.to(device)

# 配置模型路径及加载模型
model_path = r"D:\code\yolov5\runs\train\exp2"
encrypted_path = os.path.join(model_path, "weights", "best.enc")
key_path = os.path.join(model_path, "model.key")
model = load_encrypted_model(encrypted_path, key_path)

# 检测处理函数
def process_detection(img_path):
    img = Image.open(img_path)
    results = model(img)
    
    try:
        detections = results.pandas().xyxy[0]
        # 如果有检测结果，且任一目标的置信度大于等于0.2，则认为检测为异常
        has_high_confidence = any(detections['confidence'] >= 0.2)
        result_img = np.squeeze(results.render())
        
        # 如果检测为异常，则获取坐标信息；否则为正常，不保存坐标
        coordinates = None
        if has_high_confidence:
            coordinates = detections[['xmin', 'ymin', 'xmax', 'ymax']].values.tolist()

        return result_img, has_high_confidence, len(detections), coordinates
    except Exception as e:
        print(f"处理检测结果时出错: {str(e)}")
        return None, False, 0, None


@app.route('/')
def index():
    return render_template('indexmysql.html')

@app.route('/detect', methods=['POST'])
def detect():
    if 'image' not in request.files:
        return jsonify({'error': 'No image uploaded'}), 400

    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400

    # 获取前端上传时附带的side参数，必须为'front'或'back'
    side = request.form.get('side')
    if side not in ['front', 'back']:
        return jsonify({'error': 'Invalid side parameter'}), 400

    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(upload_path)

    try:
        result_img, has_high_confidence, detections_count, coordinates = process_detection(upload_path)
        
        if result_img is not None:
            # 根据当前时间生成唯一文件名
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            filename = f"{side}_{timestamp}.jpg"
            
            # 根据side参数选择保存的文件夹
            if side == 'front':
                save_dir = r"D:\MySQL\jieguo1"
            else:
                save_dir = r"D:\MySQL\jieguo2"
            os.makedirs(save_dir, exist_ok=True)
            save_path = os.path.join(save_dir, filename)
            
            # 保存检测结果图（使用cv2写入）
            cv2.imwrite(save_path, result_img)

            # 将检测结果和坐标存入MySQL数据库
            try:
                conn = get_db_connection()
                cursor = conn.cursor()

                # 将坐标数据转为JSON格式，如果没有坐标则为NULL
                coordinates_json = None if not coordinates else str(coordinates)
                
                sql = """
                    INSERT INTO detection_results (side, image_path, status, coordinates, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                """
                status = '异常' if has_high_confidence else '正常'
                cursor.execute(sql, (side, save_path, status, coordinates_json, datetime.now()))
                conn.commit()
            except Exception as db_e:
                print(f"数据库插入错误: {db_e}")
            finally:
                cursor.close()
                conn.close()

            # 将检测结果图转为Base64编码返回前端
            result_img_pil = Image.fromarray(result_img)
            buffered = BytesIO()
            result_img_pil.save(buffered, format="JPEG")
            result_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
            
            return jsonify({
                'result': result_base64,
                'status': '异常' if has_high_confidence else '正常',
                'status_class': 'alert' if has_high_confidence else 'normal',
                'detection_count': detections_count
            })
        return jsonify({'error': 'Detection failed'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if os.path.exists(upload_path):
            os.remove(upload_path)

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    app.run(host='0.0.0.0', port=5000, debug=True)
