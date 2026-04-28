"""Compare Ultralytics YOLO checkpoints for model-selection decisions."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Sequence


DEFAULT_MODELS = ["yolov8n.pt", "yolo11n.pt"]
DEFAULT_OUTPUT = "artifacts/model_benchmarks.csv"


def _safe_round(value: Any, digits: int = 4) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _model_size_mb(model_ref: str) -> float | None:
    path = Path(model_ref)
    if not path.exists() or not path.is_file():
        return None
    return round(path.stat().st_size / (1024 * 1024), 2)


def _extract_metrics(model_ref: str, results: Any) -> dict[str, Any]:
    box = getattr(results, "box", None)
    speed = getattr(results, "speed", {}) or {}
    preprocess_ms = speed.get("preprocess")
    inference_ms = speed.get("inference")
    postprocess_ms = speed.get("postprocess")
    total_ms = sum(value for value in [preprocess_ms, inference_ms, postprocess_ms] if value is not None)

    return {
        "model": model_ref,
        "weights_mb": _model_size_mb(model_ref),
        "precision": _safe_round(getattr(box, "mp", None)),
        "recall": _safe_round(getattr(box, "mr", None)),
        "map50": _safe_round(getattr(box, "map50", None)),
        "map50_95": _safe_round(getattr(box, "map", None)),
        "preprocess_ms": _safe_round(preprocess_ms, 2),
        "inference_ms": _safe_round(inference_ms, 2),
        "postprocess_ms": _safe_round(postprocess_ms, 2),
        "total_ms": _safe_round(total_ms, 2),
        "status": "ok",
        "error": "",
    }


def _benchmark_one(args: argparse.Namespace, model_ref: str) -> dict[str, Any]:
    from ultralytics import YOLO

    model = YOLO(model_ref)
    results = model.val(
        data=args.data,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        split=args.split,
        conf=args.conf,
        iou=args.iou,
        plots=args.plots,
        verbose=False,
    )
    return _extract_metrics(model_ref, results)


def _write_csv(rows: list[dict[str, Any]], output_path: str) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "model",
        "weights_mb",
        "precision",
        "recall",
        "map50",
        "map50_95",
        "preprocess_ms",
        "inference_ms",
        "postprocess_ms",
        "total_ms",
        "status",
        "error",
    ]
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(rows: list[dict[str, Any]], output_path: str) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)


def _print_table(rows: list[dict[str, Any]]) -> None:
    headers = ["model", "map50_95", "map50", "precision", "recall", "total_ms", "status"]
    widths = {
        header: max(len(header), *(len(str(row.get(header, ""))) for row in rows))
        for header in headers
    }
    print("  ".join(header.ljust(widths[header]) for header in headers))
    print("  ".join("-" * widths[header] for header in headers))
    for row in rows:
        print("  ".join(str(row.get(header, "")).ljust(widths[header]) for header in headers))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and compare YOLOv8/YOLO11 checkpoints on the same dataset."
    )
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS, help="model names or checkpoint paths")
    parser.add_argument("--data", default="data/waterbag.yaml", help="Ultralytics dataset YAML path")
    parser.add_argument("--imgsz", type=int, default=640, help="validation image size")
    parser.add_argument("--batch", type=int, default=1, help="validation batch size")
    parser.add_argument("--device", default="0", help="CUDA device id, comma list, or cpu")
    parser.add_argument("--split", default="val", help="dataset split to validate")
    parser.add_argument("--conf", type=float, default=0.001, help="validation confidence threshold")
    parser.add_argument("--iou", type=float, default=0.7, help="validation IoU threshold")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="CSV output path")
    parser.add_argument("--json-output", help="optional JSON output path")
    parser.add_argument("--plots", action="store_true", help="save Ultralytics validation plots")
    parser.add_argument("--hard-fail", action="store_true", help="fail on the first benchmark error")
    return parser


def main(argv: Sequence[str] | None = None) -> list[dict[str, Any]]:
    parser = build_parser()
    args = parser.parse_args(argv)

    rows: list[dict[str, Any]] = []
    for model_ref in args.models:
        try:
            rows.append(_benchmark_one(args, model_ref))
        except Exception as exc:
            if args.hard_fail:
                raise
            rows.append(
                {
                    "model": model_ref,
                    "weights_mb": _model_size_mb(model_ref),
                    "precision": None,
                    "recall": None,
                    "map50": None,
                    "map50_95": None,
                    "preprocess_ms": None,
                    "inference_ms": None,
                    "postprocess_ms": None,
                    "total_ms": None,
                    "status": "error",
                    "error": str(exc),
                }
            )

    _write_csv(rows, args.output)
    if args.json_output:
        _write_json(rows, args.json_output)
    _print_table(rows)
    print(f"\nCSV written to {args.output}")
    if args.json_output:
        print(f"JSON written to {args.json_output}")
    return rows


if __name__ == "__main__":
    main()
