#在双摄像头系统（如你们的水质采样袋检测系统）中，检测第二个（反面）摄像头的检测结果与第一个是否相同
#如果是相同则证明一次检测中正反两面拍到了同一个污点，一个报产品不合格就行了

import os
import numpy as np

save_path = "last_result_second.txt"

def normalize_box_dict(box):
    x1, y1, x2, y2 = box['x1'], box['y1'], box['x2'], box['y2']
    conf = box.get('confidence', 1.0)
    return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2), conf]

def save_boxes(boxes, filepath=save_path):
    with open(filepath, 'w') as f:
        for box in boxes:
            norm = normalize_box_dict(box)
            f.write(' '.join([f"{v:.2f}" for v in norm]) + '\n')

def load_boxes(filepath=save_path):
    if not os.path.exists(filepath):
        return []
    with open(filepath, 'r') as f:
        lines = f.readlines()
    return [list(map(float, line.strip().split())) for line in lines]

def compute_iou(box1, box2):
    xA = max(box1[0], box2[0])
    yA = max(box1[1], box2[1])
    xB = min(box1[2], box2[2])
    yB = min(box1[3], box2[3])

    interArea = max(0, xB - xA) * max(0, yB - yA)
    area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
    area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
    return interArea / (area1 + area2 - interArea + 1e-6)

def check_box_repeat_second(boxes_dict_list, iou_thresh=0.5, verbose=True):
    last_boxes = load_boxes()
    any_match = False

    for i, cur_dict in enumerate(boxes_dict_list):
        cur = normalize_box_dict(cur_dict)
        matched = False
        for j, prev in enumerate(last_boxes):
            iou = compute_iou(cur[:4], prev[:4])
            if verbose:
                print(f"  IOU(Box {i} ↔ Last {j}) = {iou:.3f}")
            if iou >= iou_thresh:
                matched = True
                break
        if verbose:
            print(f"Box {i} → {'1' if matched else '2'}")
        if matched:
            any_match = True

    if any_match:
        if verbose:
            print("→ Second Result repeated (1)")
        return 1
    else:
        if verbose:
            print("→ New second result, saving... (2)")
        save_boxes(boxes_dict_list)
        return 2
