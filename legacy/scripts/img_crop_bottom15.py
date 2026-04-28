import os
import cv2

def crop_bottom_15_percent(img_path: str, save_dir: str) -> None:
    """
    从图像中裁剪下部15%的区域并保存。

    参数:
    img_path : str  图像路径
    save_dir : str  保存目录
    """
    img = cv2.imread(img_path)
    if img is None:
        print(f'[WARN] 无法读取 {img_path}')
        return

    h, w = img.shape[:2]
    roi_start = int(0.85 * h)
    cropped = img[roi_start:, :]  # 下 15%

    os.makedirs(save_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(img_path))[0]
    out_path = os.path.join(save_dir, f'{base}_bottom15.jpg')
    cv2.imwrite(out_path, cropped)

    print(f'[OK] 裁剪图像下15% → {out_path}')


def process_folder(input_dir: str, save_dir: str) -> None:
    """
    批量处理文件夹中的所有图像。

    参数:
    input_dir : str  输入文件夹路径
    save_dir  : str  输出文件夹路径
    """
    supported_exts = ('.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff')
    image_files = [f for f in os.listdir(input_dir) if f.lower().endswith(supported_exts)]

    for fname in image_files:
        img_path = os.path.join(input_dir, fname)
        crop_bottom_15_percent(img_path, save_dir)


if __name__ == '__main__':
    process_folder(
        input_dir=r'D:\MVS\data\test_image',   # 输入文件夹
        save_dir=r'./cropped_results'          # 输出文件夹
    )
