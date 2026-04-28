from __future__ import annotations

import time
from pathlib import Path

from .config import Settings
from .pipeline import InspectionPipeline
from .schemas import InspectionResult, build_frame_packet, infer_bag_id, now_iso


IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp")


def collect_replay_packets(settings: Settings, source_root: str, limit: int | None = None):
    root = Path(source_root)
    entries: list[tuple[str, int, str, Path]] = []
    for camera in settings.cameras:
        candidate_dir = root / f"camera{camera.camera_id}"
        source_dir = candidate_dir if candidate_dir.exists() else Path(camera.watch_dir)
        for image_path in sorted(path for path in source_dir.iterdir() if path.suffix.lower() in IMAGE_EXTENSIONS):
            entries.append(
                (
                    infer_bag_id(str(image_path.resolve())),
                    camera.camera_id,
                    camera.name,
                    image_path,
                )
            )
    entries.sort(key=lambda item: (item[0], item[1], item[3].name))

    packets = []
    for bag_id, camera_id, camera_name, image_path in entries:
        packet = build_frame_packet(
            camera_id=camera_id,
            camera_name=camera_name,
            source_path=str(image_path.resolve()),
            replayed=True,
            bag_id=bag_id,
            metadata={"source": "replay", "repeat_scope": "replay"},
        )
        packet.file_ready_at = now_iso()
        packet.processing_started_at = now_iso()
        packet.source_mtime_ns = image_path.stat().st_mtime_ns
        packets.append(packet)
        if limit is not None and len(packets) >= limit:
            return packets
    return packets


def run_replay(
    *,
    settings: Settings,
    pipeline: InspectionPipeline,
    source_root: str,
    interval_ms: int = 0,
    limit: int | None = None,
) -> list[InspectionResult]:
    packets = collect_replay_packets(settings, source_root, limit=limit)
    results = []
    for index, packet in enumerate(packets):
        if index > 0 and interval_ms > 0:
            time.sleep(interval_ms / 1000.0)
        results.extend(pipeline.flush_timeouts())
        results.append(pipeline.process_packet(packet))
    results.extend(pipeline.flush_timeouts())
    return results
