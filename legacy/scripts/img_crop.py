import os
import cv2
import numpy as np
from time import time
import argparse

# 裁剪目标水样检测袋的物体袋子范围，将四周背景（背光板）去除
# -----------------------------------------------------------
# 1. 处理单张图像
# -----------------------------------------------------------
def process_image(
        img_path: str,
        save_dir: str,
        roi_ratio: tuple = (0.15, 0.30),
        debug: bool = False
    ) -> None:
    """
    参数
    ----
    img_path : str   图像完整路径
    save_dir : str   处理后图像的保存文件夹
    roi_ratio: tuple ROI 的上下占比 (start_ratio, end_ratio)，默认 10%~30%
    debug    : bool  若为 True，会额外保存中间结果 (ROI、拟合直线)
    """
    img = cv2.imread(img_path)
    if img is None:
        print(f'[WARN] 无法读取 {img_path}')
        return

    h, w = img.shape[:2]
    roi_start = int(roi_ratio[0] * h)
    roi_end   = int(roi_ratio[1] * h)
    roi       = img[roi_start:roi_end, :]

    gray  = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blur  = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150, apertureSize=3)

    # —— 直线检测 —— #
    raw_lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=80,
        minLineLength=150,
        maxLineGap=15
    )

    horizontal = []
    if raw_lines is not None:
        for x1, y1, x2, y2 in raw_lines.reshape(-1, 4):
            if x2 == x1:         # 避免除零
                continue
            slope = (y2 - y1) / (x2 - x1)
            if abs(slope) < 0.1: # 近似水平
                y_mid  = (y1 + y2) / 2
                length = np.hypot(x2 - x1, y2 - y1)
                horizontal.append((y_mid, length, (x1, y1, x2, y2)))

    if not horizontal:
        print(f'[WARN] {os.path.basename(img_path)} 未检测到水平直线，可尝试调整 ROI 或阈值')
        return img[0:int(0.30 * h), :]

    # 选择 ROI 内“最靠下”的水平线（y 最大）
    horizontal.sort(key=lambda t: t[0], reverse=False)
    _, _, (x1, y1, x2, y2) = horizontal[0]
    y1_full, y2_full = y1 + roi_start, y2 + roi_start

    # 拟合直线方程
    k = (y2_full - y1_full) / (x2 - x1 + 1e-6)  # 加 1e-6 防零除
    b = y1_full - k * x1

    pt1 = (0,               int(b)) # 直线与左边界交点
    pt2 = (w - 1,           int(k * (w - 1) + b)) # 直线与右边界交点
    y_top = min(pt1[1], pt2[1])

    # —— 裁剪 & 保存 —— #
    upper_crop = img[0:y_top, :]

    os.makedirs(save_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(img_path))[0]
    out_path = os.path.join(save_dir, f'{base}_upper.jpg')
    cv2.imwrite(out_path, upper_crop)

    if debug:
        # 保存 ROI 与拟合直线可视化
        cv2.imwrite(os.path.join(save_dir, f'{base}_roi.jpg'), roi)
        vis = img.copy()
        cv2.line(vis, pt1, pt2, (0, 0, 255), 3)
        cv2.imwrite(os.path.join(save_dir, f'{base}_fitted.jpg'), vis)

    print(f'[OK] {base}: 直线 {pt1}->{pt2} | 裁剪高度 {y_top:4d}px | 保存 {out_path}')

    return upper_crop

if __name__ == '__main__':
    # process_image(img_path='D:\MVS\data\MV-CU120-10UC (DA4792752)\Image_20250613144310825.jpg', save_dir='./')
    process_image(img_path='D:\MVS\data\Image_20250613151825682.jpg', save_dir='./')

