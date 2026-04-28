"""Train a YOLO11 baseline for the waterbag defect dataset."""

from __future__ import annotations

from train_ultralytics import main


if __name__ == "__main__":
    main(default_model="yolo11n.pt", default_name="yolo11_waterbag")
