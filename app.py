# app.py (一次检测 + 网格二次检测，二次检测使用不同权重)
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO
from threading import Lock, Thread, Event
import os
import time
import base64
import cv2
import torch
import shutil
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pathlib import Path
from cryptography.fernet import Fernet
from pymodbus.client import ModbusSerialClient
import json
import threading
import webbrowser

# 导入数据库操作方法（确保 db.py 与 app.py 在同一工程路径下）
from db import insert_detection_result

# 只保留一次检测需要的工具
from filter_box import is_valid_defect
from check_box_repeater import check_box_repeat  # 重复缺陷判定（一次或二次的结果都可用这个）

app = Flask(__name__)
#将 Flask 应用包装成支持 WebSocket 的实时应用，实现客户端与服务器的实时双向通信
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ==============================
# 配置参数
# ==============================
PLC_CONFIG = {
    "port": "COM4",
    "baudrate": 115200,
    "parity": "N",
    "stopbits": 1,
    "bytesize": 8,
    "timeout": 0.1,
    "registers": {
        "cam1": 100,
        "cam2": 102,
        "cam3": 104,  # 用作“重复缺陷蜂鸣器”输出
    }
}

# 一次检测模型配置（解密加载）
MODEL_CONFIG = {
    "encrypted_path": r"D:\code\yolov5\runs\train6\exp\weights\best.enc",
    "key_path": r"D:\code\yolov5\runs\train6\exp\model.key",
    "yolov5_root": r"D:\code\yolov5"
}

CAMERA_CONFIG = {
    "folder1": r"D:\MVS\data\MV-CU120-10UC (DA5594458)",
    "folder2": r"D:\MVS\data\MV-CU120-10UC (DA4792752)",
    "backup": r"D:\MVS\data\backup",
    "cooldown": 1.0
}

# 二次“网格裁剪”检测参数（仅在一次检测正常时触发）
PATCH_CONFIG = {
    "num_patches_horizontal": 4,   # 横向块数
    "num_patches_vertical": 5,     # 纵向块数
    # "conf_thres": 0.25,
    "conf_thres": 0.2,
    "iou_thres": 0.3,
    "save_patch_vis": True,       # 如需保存每个小块的可视化，设 True
    "patch_vis_dir": "./patch_vis" # 保存目录
}

# 二次检测模型（新权重）配置 —— 使用与你一次检测不同的权重
# 直接 .pt 模型路径即可；如是加密 .enc，请把 is_encrypted 设 True 并提供 key_path
PATCH_MODEL_CONFIG = {
    "model_path": r"D:\code\yolov5\runs\train7\exp\weights\best.pt",  # ←← 改成新权重
    "key_path": r"D:\code\yolov5\runs\train7\exp\model.key",
    "yolov5_root": r"D:\code\yolov5"
}

# ==============================
# PLC 通信模块（单例）
# ==============================
class PLCController:
    _instance = None
    _lock = Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance.client = None
                cls._instance.connect()
        return cls._instance
    
    def connect(self):
        try:
            self.client = ModbusSerialClient(
                method='rtu',
                port=PLC_CONFIG['port'],
                baudrate=PLC_CONFIG['baudrate'],
                parity=PLC_CONFIG['parity'],
                stopbits=PLC_CONFIG['stopbits'],
                bytesize=PLC_CONFIG['bytesize'],
                timeout=PLC_CONFIG['timeout']
            )
            if self.client.connect():
                print("PLC连接成功")
                return True
            print("PLC连接失败：connect() 返回 False")
            return False
        except Exception as e:
            print(f"PLC连接异常: {str(e)}")
            return False

    def write_result(self, camera_id, result):
        """
        业务：1=异常，2=正常
        camera_id: 1/2 对应相机；3 用作重复缺陷蜂鸣
        """
        value = 1 if result else 2
        address = PLC_CONFIG['registers'][f'cam{camera_id}']
        try:
            t1_plc = time.time()
            rr = self.client.write_register(address, value)
            t2_plc = time.time()
            print(f"写寄存器耗时：{t2_plc - t1_plc:.2f}s")
            print("写入成功")
            return True
        except Exception as e:
            print(f"PLC通信异常: {str(e)}")
            self.connect()
            return False

from ultralytics import YOLO

# ==============================
# 一次检测模型类（旧权重，解密加载）
# ==============================
class YOLOModel:
    def __init__(self):
        self.model = None
        self.lock = Lock()
        self.load_model()
    
    def load_model(self):
        with self.lock:
            temp_file = None
            try:
                # 清理历史临时文件
                for old_file in Path(".").glob("temp_model_*.pt"):
                    try:
                        old_file.unlink(missing_ok=True)
                        print(f"清理旧临时文件: {old_file}")
                    except Exception as e:
                        print(f"清理旧文件失败: {str(e)}")

                print("[1/4] 正在加载模型密钥...")
                if not Path(MODEL_CONFIG['key_path']).exists():
                    raise FileNotFoundError(f"密钥文件不存在: {MODEL_CONFIG['key_path']}")
                with open(MODEL_CONFIG['key_path'], 'rb') as f:
                    key = f.read()
                print("✓ 密钥加载成功")

                print("[2/4] 正在解密模型...")
                if not Path(MODEL_CONFIG['encrypted_path']).exists():
                    raise FileNotFoundError(f"加密模型文件不存在: {MODEL_CONFIG['encrypted_path']}")
                with open(MODEL_CONFIG['encrypted_path'], 'rb') as f:
                    encrypted = f.read()
                decrypted = Fernet(key).decrypt(encrypted)
                print("✓ 模型解密成功")

                print("[3/4] 写入临时文件...")
                temp_file = Path(f"temp_model_{int(time.time())}.pt")
                temp_file.write_bytes(decrypted)
                print(f"✓ 临时文件已写入: {temp_file}")

                print("[4/4] 加载YOLO模型（一次检测）...")
                # 如需CPU/GPU兜底可改用 self.model.to(device)
                self.model = YOLO(temp_file).eval().cuda()
                if not hasattr(self.model, 'names'):
                    raise RuntimeError("加载的模型结构不完整，缺少names属性")
                print(f"✓ 模型加载成功（一次），检测类别: {self.model.names}")

            except Exception as e:
                self.model = None
                print(f"一次模型加载失败: {str(e)}")
                import traceback
                traceback.print_exc()
            finally:
                if temp_file and temp_file.exists():
                    try:
                        temp_file.unlink()
                        print(f"已清理临时文件: {temp_file}")
                    except Exception as e:
                        print(f"临时文件清理失败: {str(e)}")
                else:
                    print("无临时文件需要清理")

    def detect(self, img_path):
        """
        一次检测：执行检测并返回
        返回：绘制过的 ndarray、boxes 列表、is_defect 布尔
        """
        if self.model is None:
            return None, [], False

        base = os.path.splitext(os.path.basename(img_path))[0]

        img = cv2.imread(img_path)
        if img is None:
            return None, [], False
        
        img_h, img_w = img.shape[:2]

        results = self.model.predict(source=img, imgsz=640, conf=0.3, verbose=False)
        result = results[0]

        boxes = []
        is_defect = False

        # for box in result.boxes:
        #     conf = float(box.conf)
        #     cls_id = int(box.cls)
        #     x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
        #     print("候选缺陷坐标：", x1, y1, x2, y2, conf)
        #     not_hair = is_valid_defect(
        #         x1, y1, x2, y2, conf,
        #         img_shape=(img_h, img_w),
        #         area_thresh_px=3900,
        #         anomaly_thresh=0.32
        #     )
        #     if not not_hair:
        #         print(f"{base}是头发丝误检缺陷")

        #     # 屏蔽边缘黑色 bug（硬编码区域）
        #     tmp_result = True
        #     tmp_box_x1 = 1445
        #     tmp_box_x2 = 1520
        #     tmp_box_y1 = 1260
        #     tmp_box_y2 = 1375
        #     if x1 >= tmp_box_x1 and x2 <= tmp_box_x2 and y1 >= tmp_box_y1 and y2 <= tmp_box_y2:
        #         tmp_result = False
        #         print("玻璃上的边缘缺陷被检测并排除！！")
            
            # if conf >= 0.4 and not_hair and tmp_result:
            #     print(f"{base}通过过滤，判定为有效缺陷")
            #     is_defect = True
            #     boxes.append({
            #         "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            #         "label": self.model.names[cls_id],
            #         "confidence": conf
            #     })

        # 一次检测绘制（蓝色）
        img_box = img.copy()
        # 如果检测到任何目标，直接认为有缺陷
        if len(result.boxes) > 0:
            is_defect = True

        for box in result.boxes:
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            conf = float(box.conf[0])
            cls_id = int(box.cls[0])
            label = self.model.names[cls_id]

            cv2.rectangle(img_box, (x1, y1), (x2, y2), (255, 0, 0), 8)
            cv2.putText(img_box, f"{label}", (x1, max(y1 - 50, 0)),
                        cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(img_box, f"{conf:.2f}", (x1, y1),
                        cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 0, 0), 4, cv2.LINE_AA)

        os.makedirs('./detect_result', exist_ok=True)
        cv2.imwrite(f'./detect_result/{base}_detect.jpg', img_box)
        return img_box, boxes, is_defect

# ==============================
# 二次检测模型类（新权重：pt 或 enc）
# ==============================

class YOLOPatchModel:
    def __init__(self):
        self.model = None
        self.lock = Lock()
        self.load_model()

    def load_model(self):
        with self.lock:
            try:
                model_path = PATCH_MODEL_CONFIG["model_path"]
                if not model_path or not Path(model_path).exists():
                    raise FileNotFoundError(f"二次检测权重不存在: {model_path}")

                print("✓ 直接加载二次检测权重：", model_path)
                self.model = YOLO(model_path).eval().cuda()
                if not hasattr(self.model, 'names'):
                    raise RuntimeError("二次模型结构不完整，缺少names属性")
                print(f"✓ 二次模型加载成功，类别: {self.model.names}")

            except Exception as e:
                self.model = None
                print(f"二次模型加载失败: {str(e)}")
                import traceback
                traceback.print_exc()

    def detect_patches(self, img_path,
                       num_patches_horizontal,
                       num_patches_vertical,
                       conf_thres,
                       iou_thres,
                       save_patch_vis,
                       patch_vis_dir):
        """
        网格裁剪二次检测（使用新权重）。
        返回：img_annot（原图上叠加红框的ndarray）、boxes（全局坐标框列表）
        """
        if self.model is None:
            return None, []

        img = cv2.imread(img_path)
        if img is None:
            return None, []

        H, W = img.shape[:2]
        base_pw = W // num_patches_horizontal
        base_ph = H // num_patches_vertical

        if save_patch_vis:
            os.makedirs(patch_vis_dir, exist_ok=True)

        all_boxes = []
        print(num_patches_vertical,num_patches_horizontal)
        for r in range(num_patches_vertical):
            for c in range(num_patches_horizontal):
                left   = c * base_pw
                upper  = r * base_ph
                right  = (c + 1) * base_pw if c < num_patches_horizontal - 1 else W
                lower  = (r + 1) * base_ph if r < num_patches_vertical   - 1 else H

                patch = img[upper:lower, left:right]

                cv2.imwrite("tmp.png", patch)

                results = self.model.predict(
                    source=patch,
                    conf=conf_thres,
                    iou=iou_thres,
                    verbose=False
                )
                res = results[0]
                if res.boxes is None or len(res.boxes) == 0:
                    continue

                for b in res.boxes:
                    x1, y1, x2, y2 = b.xyxy[0].tolist()
                    cls_id = int(b.cls.item()) if b.cls is not None else -1
                    score  = float(b.conf.item()) if b.conf is not None else 0.0

                    gx1, gy1 = int(x1 + left), int(y1 + upper)
                    gx2, gy2 = int(x2 + left), int(y2 + upper)

                    all_boxes.append({
                        "x1": gx1, "y1": gy1, "x2": gx2, "y2": gy2,
                        "label": self.model.names[cls_id] if hasattr(self.model, "names") else str(cls_id),
                        "confidence": score
                    })

                    # 红框绘制
                    cv2.rectangle(img, (gx1, gy1), (gx2, gy2), (0, 0, 255), 6)
                    cv2.putText(img, f"{all_boxes[-1]['label']}", (gx1, max(gy1 - 40, 0)),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 255), 3, cv2.LINE_AA)
                    cv2.putText(img, f"{score:.2f}", (gx1, gy1),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 255), 3, cv2.LINE_AA)

                if save_patch_vis:
                    pv = patch.copy()
                    for b in res.boxes:
                        x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
                        cv2.rectangle(pv, (x1, y1), (x2, y2), (0, 0, 255), 2)
                    cv2.imwrite(os.path.join(patch_vis_dir, f"{Path(img_path).stem}_r{r}_c{c}.jpg"), pv)

        return img, all_boxes


# ==============================
# 共享模型（避免多相机重复加载占显存）
# ==============================
_primary_model = None
_patch_model = None
_primary_lock = Lock()
_patch_lock = Lock()

def get_primary_model(): #单例模式获取检测模型
    global _primary_model
    with _primary_lock:
        if _primary_model is None:
            _primary_model = YOLOModel()
        return _primary_model

def get_patch_model():
    global _patch_model
    with _patch_lock:
        if _patch_model is None:
            _patch_model = YOLOPatchModel()
        return _patch_model

# ==============================
# 文件监控处理
# ==============================
class CameraHandler(FileSystemEventHandler):
    def __init__(self, camera_id):
        self.camera_id = camera_id
        self.last_processed = 0
        self.alert = 3  # cam3 用于重复缺陷蜂鸣器
        self.plc = PLCController()
        # 共享单例模型，避免重复加载
        self.model = get_primary_model()
        self.patch_model = get_patch_model()
        self.processing = False

    def on_created(self, event):
        if self.processing or event.is_directory or not event.src_path.endswith('.jpg'):
            return
        if time.time() - self.last_processed < CAMERA_CONFIG['cooldown']:       #防止短时间内重复处理
            return
        self.processing = True
        try:
            img_path = self.get_latest_image()
            if img_path:
                self.process_image(img_path)
        finally:
            self.processing = False
            self.last_processed = time.time()

    def get_latest_image(self):
        try:
            folder = CAMERA_CONFIG[f'folder{self.camera_id}']
            files = [f for f in os.listdir(folder) if f.endswith('.jpg')]
            if not files:
                return None
            latest = max(files, key=lambda f: os.path.getctime(os.path.join(folder, f)))
            return os.path.join(folder, latest)
        except Exception as e:
            print(f"获取图片失败: {str(e)}")
            return None

    def process_image(self, img_path):
        """
        单图处理流程：
        1. 备份图片；
        2. 一次检测（旧权重）；
        3. 如一次为正常 -> 网格二次检测（新权重）；
        4. 重复缺陷判定（优先二次结果）→ 蜂鸣器脉冲；
        5. 入库（两阶段结果）；
        6. 写 PLC 状态 & 前端推送。
        """
        t1 = time.time()

        # 1) 备份
        backup_dir = CAMERA_CONFIG['backup']
        os.makedirs(backup_dir, exist_ok=True)
        filename = os.path.basename(img_path)
        backup_path = os.path.join(backup_dir, filename)
        shutil.copy(img_path, backup_path)

        # 2) 一次检测（旧权重）
        img_stage1, boxes_stage1, is_defect = self.model.detect(img_path)
        print("全局瑕疵检测结果（一次）：", is_defect)

        # 3) 二次检测（新权重，仅一次为正常时触发）
        boxes_stage2 = []
        img_for_emit = img_stage1  # 默认推送一次检测的图
        if not is_defect:
            t_patch_start = time.time()
            img_stage2, boxes_stage2 = self.patch_model.detect_patches(
                img_path,
                num_patches_horizontal=PATCH_CONFIG["num_patches_horizontal"],
                num_patches_vertical=PATCH_CONFIG["num_patches_vertical"],
                conf_thres=PATCH_CONFIG["conf_thres"],
                iou_thres=PATCH_CONFIG["iou_thres"],
                save_patch_vis=PATCH_CONFIG["save_patch_vis"],
                patch_vis_dir=PATCH_CONFIG["patch_vis_dir"]
            )
            if img_stage2 is not None and len(boxes_stage2) > 0:
                is_defect = True
                img_for_emit = img_stage2
                print(f"二次网格检测（新权重）发现异常，共 {len(boxes_stage2)} 个框，耗时 {time.time()-t_patch_start:.2f}s")
            else:
                print(f"二次网格检测未发现异常，耗时 {time.time()-t_patch_start:.2f}s")

        # 4) 重复缺陷判定：优先二次结果，否则用一次，最后检查是否为重复缺陷，重复可能是玻璃板上的污点或划痕等非生产缺陷
        boxes_for_repeat = boxes_stage2 if len(boxes_stage2) > 0 else boxes_stage1
        repeat_final = check_box_repeat(boxes_for_repeat)
        print("是否为重复缺陷：", repeat_final)

        # 蜂鸣器：cam3 写 1→0（仅在重复时）
        if repeat_final == 1:
            self.plc.write_result(self.alert, True)   # 1 = 异常
            time.sleep(0.2)
            self.plc.write_result(self.alert, False)  # 2 = 正常（复位）

        # 5) 入库：两阶段都保存
        yolo_result = json.dumps({"stage1": boxes_stage1, "stage2": boxes_stage2}, ensure_ascii=False)
        status_text = "异常" if is_defect else "正常"
        insert_detection_result(backup_path, yolo_result, status_text)
        t2 = time.time()
        print("单张图片检测完成，并已写入数据库备份，耗时%.2f 秒\n" % (t2 - t1))

        # 6) 写 PLC & 前端
        success = self.plc.write_result(self.camera_id, is_defect)
        _, buffer = cv2.imencode('.jpg', img_for_emit)
        img_base64 = base64.b64encode(buffer).decode('utf-8')

        socketio.emit('update', {
            "camera": self.camera_id,
            "image": img_base64,
            "boxes": boxes_for_repeat,  # 前端可展示“最终判定用”的框
            "status": status_text,
            "plc_status": success,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        })

        t3 = time.time()
        print("单张图片处理到备份到返回给PLC总耗时%.2f 秒\n" % (t3 - t1))
        # 可选：删除原图
        # os.remove(img_path)

# ==============================
# 系统服务
# ==============================
class InspectionSystem:
    def __init__(self):
        self.observers = []
        self.running = Event()
        self.init_folders()
        self.started_flag = Event()
    
    def init_folders(self):
        for d in [CAMERA_CONFIG['folder1'], CAMERA_CONFIG['folder2'], CAMERA_CONFIG['backup']]:
            os.makedirs(d, exist_ok=True)
        os.makedirs('./detect_result', exist_ok=True)
        if PATCH_CONFIG["save_patch_vis"]:
            os.makedirs(PATCH_CONFIG["patch_vis_dir"], exist_ok=True)
    
    def start(self):
        handler1 = CameraHandler(1)
        handler2 = CameraHandler(2)
        
        self.observers = [
            (Observer(), CAMERA_CONFIG['folder1'], handler1),
            (Observer(), CAMERA_CONFIG['folder2'], handler2)
        ]
        
        for observer, path, handler in self.observers:
            observer.schedule(handler, path, recursive=False)
            observer.start()
        
        print("系统启动成功")
        self.started_flag.set()
        self.running.wait()
    
    def stop(self):
        self.running.set()
        for observer, _, _ in self.observers:
            observer.stop()
            observer.join()
            self.started_flag.clear()
        print("系统已停止")

system = InspectionSystem()

def auto_start_system(retry_interval=2):
    while True:
        system.running.clear()
        system.started_flag.clear()
        t = Thread(target=system.start)
        t.start()
        for _ in range(10):
            if system.started_flag.is_set():     # ← 用 system，而不是 self
                print("[自动控制] 系统已检测到成功启动！")
                return
            time.sleep(1)
        print("[自动控制] 启动超时，发送停止并重启")
        system.stop()
        t.join(timeout=5)
        time.sleep(retry_interval)

# ==============================
# Flask 路由
# ==============================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/control/start')
def start_control():
    Thread(target=auto_start_system).start()
    return jsonify({"status": "auto_starting"})

@app.route('/control/stop')
def stop_control():
    system.stop()
    return jsonify({"status": "stopped"})

def open_chrome():
    chrome_path = r"C:\\Users\Administrator\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe"
    url = "http://127.0.0.1:5000/"
    webbrowser.register('chrome', None, webbrowser.BackgroundBrowser(chrome_path))
    time.sleep(1.5)
    webbrowser.get('chrome').open(url)

if __name__ == '__main__':
    threading.Thread(target=open_chrome).start()
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
