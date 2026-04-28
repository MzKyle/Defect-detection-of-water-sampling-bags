import cv2

def is_valid_defect(x1, y1, x2, y2, conf, img_shape, area_thresh_px=3900, anomaly_thresh=0.32):
    """
    判断一个检测框是否为有效缺陷。

    参数:
    - x1, y1, x2, y2 : 框的左上和右下角像素坐标
    - conf           : 异常分数（越高越异常）
    - img_shape      : (height, width) 图像尺寸
    - area_thresh_px : 面积阈值（超过此面积且靠下会被判为误报）
    - anomaly_thresh : 异常分数阈值（低于此值说明正常）

    返回:
    - flag : bool，True 表示保留，False 表示过滤
    """
    h, w = img_shape
    area = (x2 - x1) * (y2 - y1)
    y_center = (y1 + y2) / 2
    in_bottom = y_center > 0.85 * h

    # 满足边缘、面积大、异常低 → 判定为误报
    if (in_bottom and area > area_thresh_px and anomaly_thresh < 0.4) or (in_bottom and conf < anomaly_thresh):
    # if(in_bottom and area > area_thresh_px and conf < anomaly_thresh):

        return False  # 过滤
    return True       # 保留

