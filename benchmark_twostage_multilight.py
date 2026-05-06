"""Benchmark the two-stage multi-light pipeline on manifest samples."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from waterbag_inspection.twostage_multilight import build_pipeline, discover_manifests


FIELDNAMES = [
    "sample_id",
    "coarse_detected",
    "coarse_num_boxes",
    "block_count",
    "final_num_boxes",
    "coarse_time_ms",
    "tiling_time_ms",
    "fine_inference_time_ms",
    "mapping_time_ms",
    "nms_time_ms",
    "total_time_ms",
    "precision",
    "recall",
    "map50",
    "map50_95",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark two-stage multi-light detection.")
    parser.add_argument("--coarse-model", required=True, help="Stage 1 YOLO checkpoint")
    parser.add_argument("--fine-model", required=True, help="Stage 2 multi-light checkpoint")
    parser.add_argument("--source", required=True, help="manifest file, manifest directory, or manifest list")
    parser.add_argument("--coarse-light", default="backlight", help="light used by Stage 1")
    parser.add_argument("--imgsz-coarse", type=int, default=640)
    parser.add_argument("--imgsz-fine", type=int, default=512)
    parser.add_argument("--block-size", type=int, default=512)
    parser.add_argument("--block-overlap", type=float, default=0.2)
    parser.add_argument("--block-stride", type=int)
    parser.add_argument("--min-block-size", type=int, default=1)
    parser.add_argument("--max-blocks", type=int)
    parser.add_argument("--no-pad-to-square", action="store_true")
    parser.add_argument("--device", default="0")
    parser.add_argument("--coarse-conf", type=float, default=0.25)
    parser.add_argument("--coarse-iou", type=float, default=0.7)
    parser.add_argument("--fine-conf", type=float, default=0.25)
    parser.add_argument("--nms-iou", type=float, default=0.45)
    parser.add_argument("--max-det", type=int, default=300)
    parser.add_argument("--fine-batch-size", type=int, default=0)
    parser.add_argument(
        "--fine-output-format",
        default="auto",
        choices=["auto", "yolo", "yolo_obj", "xyxy_conf_cls"],
    )
    parser.add_argument(
        "--metrics-json",
        help="optional sample_id keyed JSON with precision/recall/map50/map50_95 values",
    )
    parser.add_argument("--output", default="artifacts/twostage_multilight_benchmark.csv")
    parser.add_argument("--json-output", help="optional JSON benchmark output")
    return parser


def _load_metrics(path: str | None) -> dict[str, Mapping[str, float | None]]:
    if not path:
        return {}
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, Mapping):
        return {str(key): value for key, value in payload.items() if isinstance(value, Mapping)}
    raise ValueError("metrics JSON must be keyed by sample_id")


def _write_csv(rows: list[dict[str, Any]], output_path: str | Path) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(rows: list[dict[str, Any]], output_path: str | Path) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)


def _print_table(rows: list[dict[str, Any]]) -> None:
    headers = ["sample_id", "coarse_detected", "block_count", "final_num_boxes", "total_time_ms"]
    widths = {header: max(len(header), *(len(str(row.get(header, ""))) for row in rows)) for header in headers}
    print("  ".join(header.ljust(widths[header]) for header in headers))
    print("  ".join("-" * widths[header] for header in headers))
    for row in rows:
        print("  ".join(str(row.get(header, "")).ljust(widths[header]) for header in headers))


def main(argv: Sequence[str] | None = None) -> list[dict[str, Any]]:
    parser = build_parser()
    args = parser.parse_args(argv)

    manifests = discover_manifests(args.source)
    if not manifests:
        raise SystemExit(f"No manifests found under {args.source}")
    metrics_by_sample = _load_metrics(args.metrics_json)

    pipeline = build_pipeline(
        coarse_model=args.coarse_model,
        fine_model=args.fine_model,
        coarse_light=args.coarse_light,
        imgsz_coarse=args.imgsz_coarse,
        imgsz_fine=args.imgsz_fine,
        block_size=args.block_size,
        block_overlap=args.block_overlap,
        block_stride=args.block_stride,
        min_block_size=args.min_block_size,
        pad_to_square=not args.no_pad_to_square,
        max_blocks=args.max_blocks,
        device=args.device,
        coarse_conf=args.coarse_conf,
        coarse_iou=args.coarse_iou,
        fine_conf=args.fine_conf,
        nms_iou=args.nms_iou,
        max_detections=args.max_det,
        fine_batch_size=args.fine_batch_size,
        fine_output_format=args.fine_output_format,
    )

    rows: list[dict[str, Any]] = []
    for manifest in manifests:
        prediction = pipeline.predict_manifest(manifest)
        metrics = metrics_by_sample.get(prediction.sample_id)
        if metrics is None and isinstance(manifest.raw.get("metrics"), Mapping):
            metrics = manifest.raw["metrics"]
        rows.append(prediction.benchmark_row(metrics))

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
