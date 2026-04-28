"""Shared Ultralytics training entrypoint for waterbag defect models."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Sequence


DEFAULT_DATA = "data/waterbag.yaml"


def _coerce_extra_value(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"none", "null"}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_extra_args(items: list[str]) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise argparse.ArgumentTypeError(
                f"Extra training option must use key=value format: {item}"
            )
        key, value = item.split("=", 1)
        if not key:
            raise argparse.ArgumentTypeError(f"Extra training option has empty key: {item}")
        extras[key] = _coerce_extra_value(value)
    return extras


def _default_run_name(model_name: str) -> str:
    stem = Path(model_name).stem.replace(".", "_")
    return f"{stem}_waterbag"


def build_parser(default_model: str, default_name: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train an Ultralytics YOLO model for waterbag defect detection."
    )
    parser.add_argument("--model", default=default_model, help="pretrained model or checkpoint path")
    parser.add_argument("--data", default=DEFAULT_DATA, help="Ultralytics dataset YAML path")
    parser.add_argument("--epochs", type=int, default=100, help="training epochs")
    parser.add_argument("--imgsz", type=int, default=640, help="input image size")
    parser.add_argument("--batch", type=int, default=16, help="batch size")
    parser.add_argument("--device", default="0", help="CUDA device id, comma list, or cpu")
    parser.add_argument("--workers", type=int, default=8, help="dataloader workers")
    parser.add_argument("--project", default="runs/train", help="output project directory")
    parser.add_argument(
        "--name",
        default=default_name,
        help="run name; defaults to '<model>_waterbag'",
    )
    parser.add_argument("--patience", type=int, default=30, help="early stopping patience")
    parser.add_argument("--seed", type=int, default=42, help="random seed")
    parser.add_argument("--optimizer", default="auto", help="Ultralytics optimizer setting")
    parser.add_argument("--close-mosaic", type=int, default=10, help="disable mosaic for final N epochs")
    parser.add_argument("--cache", action="store_true", help="cache images for faster training")
    parser.add_argument("--resume", action="store_true", help="resume the run represented by --model")
    parser.add_argument(
        "--exist-ok",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="reuse the output directory when it already exists",
    )
    parser.add_argument(
        "--plots",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="save training plots",
    )
    parser.add_argument(
        "--amp",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="use automatic mixed precision when supported",
    )
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="pass an additional Ultralytics train option, e.g. --extra lr0=0.005",
    )
    return parser


def train_from_args(args: argparse.Namespace):
    from ultralytics import YOLO

    run_name = args.name or _default_run_name(args.model)
    train_kwargs: dict[str, Any] = {
        "data": args.data,
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "device": args.device,
        "workers": args.workers,
        "project": args.project,
        "name": run_name,
        "exist_ok": args.exist_ok,
        "patience": args.patience,
        "seed": args.seed,
        "optimizer": args.optimizer,
        "close_mosaic": args.close_mosaic,
        "cache": args.cache,
        "resume": args.resume,
        "plots": args.plots,
        "amp": args.amp,
    }
    train_kwargs.update(_parse_extra_args(args.extra))

    model = YOLO(args.model)
    return model.train(**train_kwargs)


def main(
    argv: Sequence[str] | None = None,
    *,
    default_model: str = "yolov8n.pt",
    default_name: str | None = "yolov8_waterbag",
):
    parser = build_parser(default_model=default_model, default_name=default_name)
    args = parser.parse_args(argv)
    return train_from_args(args)


if __name__ == "__main__":
    main()
