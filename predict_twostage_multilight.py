"""Run two-stage waterbag detection with Stage 2 multi-light block fusion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from waterbag_inspection.twostage_multilight import (
    build_pipeline,
    discover_manifests,
    save_prediction_json,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stage 1 coarse YOLO, then Stage 2 multi-light block detector only when Stage 1 is clear."
    )
    parser.add_argument("--coarse-model", required=True, help="Stage 1 YOLO checkpoint")
    parser.add_argument("--fine-model", required=True, help="Stage 2 multi-light checkpoint")
    parser.add_argument("--source", required=True, help="manifest file, manifest directory, or manifest list")
    parser.add_argument("--coarse-light", default="backlight", help="light used by Stage 1")
    parser.add_argument("--imgsz-coarse", type=int, default=640, help="Stage 1 image size")
    parser.add_argument("--imgsz-fine", type=int, default=512, help="Stage 2 model input size")
    parser.add_argument("--block-size", type=int, default=512, help="Stage 2 tile size in original pixels")
    parser.add_argument("--block-overlap", type=float, default=0.2, help="Stage 2 tile overlap ratio")
    parser.add_argument("--block-stride", type=int, help="explicit Stage 2 tile stride")
    parser.add_argument("--min-block-size", type=int, default=1, help="minimum accepted tile edge")
    parser.add_argument("--max-blocks", type=int, help="optional cap on Stage 2 blocks")
    parser.add_argument("--no-pad-to-square", action="store_true", help="stretch blocks instead of letterbox padding")
    parser.add_argument("--device", default="0", help="CUDA device id or cpu")
    parser.add_argument("--coarse-conf", type=float, default=0.25, help="Stage 1 confidence threshold")
    parser.add_argument("--coarse-iou", type=float, default=0.7, help="Stage 1 NMS IoU")
    parser.add_argument("--fine-conf", type=float, default=0.25, help="Stage 2 confidence threshold")
    parser.add_argument("--nms-iou", type=float, default=0.45, help="final class-aware NMS IoU")
    parser.add_argument("--max-det", type=int, default=300, help="maximum detections kept")
    parser.add_argument("--fine-batch-size", type=int, default=0, help="0 means run all blocks in one batch")
    parser.add_argument(
        "--fine-output-format",
        default="auto",
        choices=["auto", "yolo", "yolo_obj", "xyxy_conf_cls"],
        help="decode format for raw Stage 2 model outputs",
    )
    parser.add_argument("--save", action="store_true", help="save per-sample JSON predictions")
    parser.add_argument("--project", default="artifacts/twostage_multilight", help="prediction output directory")
    parser.add_argument("--json-output", help="optional aggregate JSON output path")
    return parser


def _prediction_summary(prediction) -> dict:
    row = prediction.to_dict()
    row.pop("block_metadata", None)
    return row


def main(argv: Sequence[str] | None = None) -> list[dict]:
    parser = build_parser()
    args = parser.parse_args(argv)

    manifests = discover_manifests(args.source)
    if not manifests:
        raise SystemExit(f"No manifests found under {args.source}")

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

    summaries = []
    for manifest in manifests:
        prediction = pipeline.predict_manifest(manifest)
        summaries.append(_prediction_summary(prediction))
        if args.save:
            save_prediction_json(prediction, args.project)

    if args.json_output:
        output = Path(args.json_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as handle:
            json.dump(summaries, handle, ensure_ascii=False, indent=2)

    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return summaries


if __name__ == "__main__":
    main()
