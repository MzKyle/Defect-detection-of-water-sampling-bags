#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


EXPECTED = {
    "bag_0001": ("accept", "all_cameras_passed"),
    "bag_0002": ("reject", "aggregate_defect_detected"),
    "bag_0003": ("accept", "all_cameras_passed"),
    "bag_0004": ("reject", "aggregate_defect_detected"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assert the deterministic mock once result contract.")
    parser.add_argument("--jsonl", default="build/verify/cpp_backend/results.jsonl", help="result JSONL path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    path = Path(args.jsonl)
    if not path.exists():
        raise AssertionError(f"result JSONL does not exist: {path}")

    terminal: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            bag_id = event.get("bag_id")
            if event.get("finalized") is True and bag_id:
                terminal[str(bag_id)] = event

    missing = sorted(set(EXPECTED) - set(terminal))
    unexpected = sorted(set(terminal) - set(EXPECTED))
    if missing or unexpected:
        raise AssertionError(f"unexpected terminal bags; missing={missing}, unexpected={unexpected}")

    for bag_id, (action, reason) in EXPECTED.items():
        event = terminal[bag_id]
        actual = (event.get("action"), event.get("reason"))
        if actual != (action, reason):
            raise AssertionError(f"{bag_id} expected {(action, reason)}, got {actual}")
        if event.get("plc_success") is not True:
            raise AssertionError(f"{bag_id} expected successful PLC feedback")

    print(json.dumps({"mock_terminal_bags": len(terminal), "status": "ok"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
