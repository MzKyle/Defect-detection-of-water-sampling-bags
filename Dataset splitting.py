import os
import shutil
import time

# 数据集拆分

# ==== 路径设置 ====
src_folder = r"D:\MVS\data\tmp"  # 原始图像文件夹
dst_folder_b = r"D:\MVS\data\MV-CU120-10UC (DA4792752)"  # 目标文件夹 B
dst_folder_c = r"D:\MVS\data\MV-CU120-10UC (DA5594458)"  # 目标文件夹 C

# ==== 创建目标文件夹（如果不存在）====
os.makedirs(dst_folder_b, exist_ok=True)
os.makedirs(dst_folder_c, exist_ok=True)

# ==== 获取图像列表 ====
image_extensions = ('.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff')
image_files = sorted([
    f for f in os.listdir(src_folder)
    if f.lower().endswith(image_extensions)
])

# ==== 执行交替复制 ====
for idx, fname in enumerate(image_files):
    src_path = os.path.join(src_folder, fname)
    dst_path = os.path.join(dst_folder_b if idx % 2 == 0 else dst_folder_c, fname)

    shutil.copy(src_path, dst_path)
    print(f"[{idx+1}/{len(image_files)}] Copied {fname} → {'b/' if idx % 2 == 0 else 'c/'}")

    time.sleep(0.5)  # 每次操作后等待 2 秒
