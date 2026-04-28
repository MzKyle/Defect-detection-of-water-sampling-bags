"""Train a YOLOv8 baseline for the waterbag defect dataset."""

from __future__ import annotations

from train_ultralytics import main


if __name__ == "__main__":
    main(default_model="yolov8n.pt", default_name="yolov8_waterbag")
