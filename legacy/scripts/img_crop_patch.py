import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from ultralytics import YOLO

#分块检测水样采集袋中的污点，避免极小的污点难以检测出来，降低假真率
#设计思路：
#1.网格分割：将输入图像分割成指定数量的小块（默认横向4块，纵向7块）
#2.批量检测：对每个小块使用YOLO模型进行目标检测
#3.坐标映射：将小块中的检测结果映射回原始图像坐标系
#4.结果标注：在原始图像上标注检测结果（边界框、类别、置信度）
#5.可视化保存：保存标注后的图像和可选的小块检测可视化结果
#6.异常检测：识别并报告图像中的异常目标

# --------------------
# 可配置参数
# --------------------
input_dir  = r'D:\\code\\data0909'   # 输入图像文件夹
output_dir = r'D:\\code\\result'   # 输出结果文件夹（标注图与日志）
model_path = r'D:\\code\\yolov5\\runs\\train6\\exp\\weights\\best.pt'  # YOLO模型权重路径

# 网格裁剪数（横向x纵向）
num_patches_horizontal = 4
num_patches_vertical   = 7

# YOLO 推理阈值/设备
conf_thres = 0.25   #置信度阈值
iou_thres  = 0.45   # IoU阈值
device     = 0  # 0=CUDA:0；无GPU可设为 'cpu'

# 是否保存每个小块检测可视化（可选）
save_patch_vis = False
patch_vis_dir  = os.path.join(output_dir, "patch_vis")

# --------------------
# 准备环境
# --------------------
os.makedirs(output_dir, exist_ok=True)
if save_patch_vis:
    os.makedirs(patch_vis_dir, exist_ok=True)

# 加载模型
model = YOLO(model_path)

# 字体（标注用）
def get_font(size=18):
    try:
        return ImageFont.truetype("arial.ttf", size=size)
    except:
        return ImageFont.load_default()

font = get_font(18)

# --------------------
# 主流程
# --------------------
image_files = [f for f in sorted(os.listdir(input_dir)) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]

for idx, filename in enumerate(image_files, start=1):
    image_path = os.path.join(input_dir, filename)
    try:
        image = Image.open(image_path).convert("RGB")
    except Exception as e:
        print(f"[跳过] 无法打开图像 {filename} ：{e}")
        continue

    W, H = image.size
    base_pw = W // num_patches_horizontal  # 小块宽度
    base_ph = H // num_patches_vertical    # 小块高度

    draw = ImageDraw.Draw(image)
    total_detections = 0
    any_anomaly = False

    for r in range(num_patches_vertical):
        for c in range(num_patches_horizontal):
            # 计算该 patch 的区域；最后一行/列补齐到边界，避免漏检边缘
            left   = c * base_pw
            upper  = r * base_ph
            right  = (c + 1) * base_pw if c < num_patches_horizontal - 1 else W
            lower  = (r + 1) * base_ph if r < num_patches_vertical   - 1 else H

            patch = image.crop((left, upper, right, lower))

            # YOLO 推理
            results = model.predict(
                source=patch,
                conf=conf_thres,
                iou=iou_thres,
                device=device,
                verbose=False
            )

            res = results[0]
            boxes = res.boxes #从 Results 对象中获取边界框信息
            if boxes is None or len(boxes) == 0:
                continue

            any_anomaly = True #标记当前图像中检测到了异常（

            # 将 patch 中的检测框映射回原图坐标，并在原图上标注
            for b in boxes:
                x1, y1, x2, y2 = b.xyxy[0].tolist()
                cls_id = int(b.cls.item()) if b.cls is not None else -1
                score  = float(b.conf.item()) if b.conf is not None else 0.0

                gx1, gy1, gx2, gy2 = x1 + left, y1 + upper, x2 + left, y2 + upper

                draw.rectangle([gx1, gy1, gx2, gy2], outline=(255, 0, 0), width=3)
                label = f"{model.names.get(cls_id, str(cls_id)) if hasattr(model, 'names') else cls_id} {score:.2f}"
                tw, th = draw.textbbox((0, 0), label, font=font)[2:]
                draw.rectangle([gx1, gy1 - th - 4, gx1 + tw + 4, gy1], fill=(255, 0, 0))
                draw.text((gx1 + 2, gy1 - th - 2), label, fill=(255, 255, 255), font=font)

                total_detections += 1

                # 控制台输出：原图名 + patch 行列 + 全局坐标 + 类别/分数
                print(
                    f"[检测] {filename} | patch(r={r}, c={c}) | "
                    f"box=({gx1:.1f},{gy1:.1f},{gx2:.1f},{gy2:.1f}) | "
                    f"class={model.names.get(cls_id, str(cls_id)) if hasattr(model, 'names') else cls_id} "
                    f"score={score:.2f}"
                )

            # 可选：保存 patch 可视化
            if save_patch_vis:
                pv = patch.copy()
                d = ImageDraw.Draw(pv)
                for b in boxes:
                    x1, y1, x2, y2 = b.xyxy[0].tolist()
                    cls_id = int(b.cls.item()) if b.cls is not None else -1
                    score  = float(b.conf.item()) if b.conf is not None else 0.0
                    d.rectangle([x1, y1, x2, y2], outline=(255, 0, 0), width=2)
                    lbl = f"{model.names.get(cls_id, str(cls_id)) if hasattr(model, 'names') else cls_id} {score:.2f}"
                    tw, th = d.textbbox((0, 0), lbl, font=font)[2:]
                    d.rectangle([x1, y1 - th - 4, x1 + tw + 4, y1], fill=(255, 0, 0))
                    d.text((x1 + 2, y1 - th - 2), lbl, fill=(255, 255, 255), font=font)

                pv.save(os.path.join(patch_vis_dir, f"{Path(filename).stem}_r{r}_c{c}.jpg"))

    # 保存原图标注或给出正常提示
    if any_anomaly:
        out_path = os.path.join(output_dir, f"{Path(filename).stem}_annotated.jpg")
        image.save(out_path, quality=95)
        print(f"[异常] {filename} -> 已保存标注图：{out_path}；总检测框数：{total_detections}")
    else:
        print(f"[正常] {filename} 无异常检测结果。")

print("全部图片处理完成。")
