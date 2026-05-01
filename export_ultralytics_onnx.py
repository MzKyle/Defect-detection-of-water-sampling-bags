"""Export an Ultralytics YOLO .pt checkpoint to ONNX."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a PyTorch checkpoint to ONNX for C++ ONNX Runtime inference."
    )
    parser.add_argument("--weights", required=True, help="path to a .pt checkpoint")
    parser.add_argument(
        "--output",
        help="optional ONNX output path; defaults to the same stem as --weights",
    )
    parser.add_argument("--imgsz", type=int, default=640, help="export image size")
    parser.add_argument("--device", default="0", help="device to use while exporting")
    parser.add_argument("--opset", type=int, default=17, help="ONNX opset version")
    parser.add_argument(
        "--dynamic",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="export with dynamic input shapes",
    )
    parser.add_argument(
        "--simplify",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="try to simplify the exported ONNX graph",
    )
    parser.add_argument(
        "--half",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="export with half precision when the backend supports it",
    )
    parser.add_argument(
        "--nms",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="export with built-in NMS when supported by the model family",
    )
    return parser


def export_onnx(weights: Path, output: Path, **export_kwargs) -> Path:
    from ultralytics import YOLO

    model = YOLO(str(weights))
    exported = model.export(format="onnx", **export_kwargs)
    exported_path = Path(str(exported))

    if exported_path.resolve() != output.resolve():
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            output.unlink()
        shutil.move(str(exported_path), str(output))
        exported_path = output

    return exported_path


def main(argv: Sequence[str] | None = None) -> Path:
    parser = build_parser()
    args = parser.parse_args(argv)

    weights = Path(args.weights).expanduser().resolve()
    if not weights.exists():
        raise FileNotFoundError(f"weights not found: {weights}")

    output = Path(args.output).expanduser().resolve() if args.output else weights.with_suffix(".onnx")
    exported = export_onnx(
        weights,
        output,
        imgsz=args.imgsz,
        device=args.device,
        opset=args.opset,
        dynamic=args.dynamic,
        simplify=args.simplify,
        half=args.half,
        nms=args.nms,
    )
    print(f"exported {weights.name} -> {exported}")
    return exported


if __name__ == "__main__":
    main()