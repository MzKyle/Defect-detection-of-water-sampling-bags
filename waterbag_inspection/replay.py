from __future__ import annotations

import time
from pathlib import Path

from .config import Settings
from .multilight import IMAGE_EXTENSIONS, is_manifest_path, load_multilight_manifest
from .pipeline import InspectionPipeline
from .schemas import InspectionResult, build_frame_packet, infer_bag_id, now_iso


def collect_replay_packets(settings: Settings, source_root: str, limit: int | None = None):
    root = Path(source_root)
    entries: list[tuple[str, int, str, Path, dict[str, str]]] = []
    for camera in settings.cameras:
        candidate_dir = root / f"camera{camera.camera_id}"
        source_dir = candidate_dir if candidate_dir.exists() else Path(camera.watch_dir)
        if settings.multilight.enabled:
            for manifest_path in sorted(
                path
                for path in source_dir.iterdir()
                if is_manifest_path(str(path), settings.multilight.manifest_suffixes)
            ):
                manifest = load_multilight_manifest(
                    str(manifest_path),
                    light_order=settings.multilight.light_order,
                )
                entries.append(
                    (
                        str(manifest.get("bag_id") or manifest_path.stem),
                        int(manifest.get("camera_id") or camera.camera_id),
                        str(manifest.get("camera_name") or camera.name),
                        manifest_path,
                        manifest["light_paths"],
                    )
                )
        else:
            for image_path in sorted(
                path
                for path in source_dir.iterdir()
                if path.suffix.lower() in IMAGE_EXTENSIONS
            ):
                entries.append(
                    (
                        infer_bag_id(str(image_path.resolve())),
                        camera.camera_id,
                        camera.name,
                        image_path,
                        {},
                    )
                )
    entries.sort(key=lambda item: (item[0], item[1], item[3].name))

    packets = []
    for bag_id, camera_id, camera_name, image_path, light_paths in entries:
        packet = build_frame_packet(
            camera_id=camera_id,
            camera_name=camera_name,
            source_path=str(image_path.resolve()),
            replayed=True,
            bag_id=bag_id,
            metadata={
                "source": "replay_multilight" if light_paths else "replay",
                "repeat_scope": "replay_multilight" if light_paths else "replay",
            },
            light_paths=light_paths,
        )
        packet.file_ready_at = now_iso()
        packet.processing_started_at = now_iso()
        source_paths = [image_path, *(Path(path) for path in light_paths.values())]
        packet.source_mtime_ns = max(path.stat().st_mtime_ns for path in source_paths)
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
