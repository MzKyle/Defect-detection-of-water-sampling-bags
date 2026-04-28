import os
import shutil

#筛选裁剪与检测后的底部15%的图片
def extract_and_copy_images(a_folder, b_folder, c_folder):
    os.makedirs(c_folder, exist_ok=True)

    # 遍历 A 文件夹，查找 *_detect_bottom15.jpg 文件
    for fname in os.listdir(a_folder):
        if "_detect_bottom15.jpg" in fname:
            # 提取原始图像名
            base_name = fname.split("_detect_bottom15.jpg")[0] + ".jpg"
            src_path = os.path.join(b_folder, base_name)
            dst_path = os.path.join(c_folder, base_name)

            # 若存在于 B，则复制到 C
            if os.path.exists(src_path):
                shutil.copy(src_path, dst_path)
                print(f"[Copied] {base_name} → C")
            else:
                print(f"[Missing] {base_name} not found in B")

# 使用示例
if __name__ == '__main__':
    extract_and_copy_images(
        a_folder=r'D:\code\yolov5\cropped_results',  # 包含 *_detect_bottom15.jpg 的目录
        b_folder=r'D:\MVS\data\tmp',  # 原始图像所在目录
        c_folder=r'D:\MVS\data\tmp2'   # 输出目标目录
    )
