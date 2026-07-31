#!/usr/bin/env python3
from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


SAMPLES = [
    ("bag_0001", "ok"),
    ("bag_0002", "defect"),
    ("bag_0003", "ok"),
    ("bag_0004", "micro"),
]


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _png_bytes(width: int, height: int, seed: int) -> bytes:
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row.extend(
                (
                    (x * 5 + seed * 17) % 256,
                    (y * 7 + seed * 29) % 256,
                    ((x + y) * 3 + seed * 43) % 256,
                )
            )
        rows.append(bytes(row))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(b"".join(rows), level=9))
        + _png_chunk(b"IEND", b"")
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate deterministic mock demo images.")
    parser.add_argument("--output", default="build/generated_demo", help="output directory")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output

    written = []
    for camera_id, camera_dir_name in ((1, "camera1"), (2, "camera2")):
        camera_dir = output / camera_dir_name
        camera_dir.mkdir(parents=True, exist_ok=True)
        for bag_index, (bag_id, marker) in enumerate(SAMPLES, start=1):
            filename = f"{bag_id}_camera{camera_id}_{marker}.png"
            path = camera_dir / filename
            path.write_bytes(_png_bytes(96, 64, seed=bag_index * 10 + camera_id))
            written.append(path)

    print(f"generated {len(written)} demo PNGs under {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
