import json

import cv2
import numpy as np

from waterbag_inspection.config import load_settings
from waterbag_inspection.demo_assets import seed_demo_images
from waterbag_inspection.detectors import build_detector
from waterbag_inspection.pipeline import InspectionPipeline
from waterbag_inspection.plc import build_plc_controller
from waterbag_inspection.replay import run_replay
from waterbag_inspection.storage import SQLiteDetectionRepository


def test_replay_runs_multiple_frames(tmp_path):
    source_root = tmp_path / "replay_data"
    seed_demo_images(str(source_root), clean=True)

    settings = load_settings("config/demo.yaml")
    settings.runtime.backup_dir = str(tmp_path / "backups")
    settings.runtime.result_dir = str(tmp_path / "results")
    settings.runtime.upload_dir = str(tmp_path / "uploads")
    settings.storage.sqlite_path = str(tmp_path / "inspection.db")
    settings.repeat_detection.history_path = str(tmp_path / "repeat.json")

    repository = SQLiteDetectionRepository(settings.storage.sqlite_path)
    pipeline = InspectionPipeline(
        runtime=settings.runtime,
        patch_config=settings.patch_detection,
        correlation_config=settings.correlation,
        repeat_config=settings.repeat_detection,
        repository=repository,
        plc_controller=build_plc_controller(settings.plc),
        primary_detector=build_detector(settings.primary_model),
        patch_detector=build_detector(settings.patch_model),
    )

    results = run_replay(
        settings=settings,
        pipeline=pipeline,
        source_root=str(source_root),
        interval_ms=0,
        limit=4,
    )

    assert len(results) == 4
    assert any(result.is_defect for result in results)
    assert any(result.status_text == "待确认" for result in results)
    assert any(result.status_text == "正常" for result in results)
    assert len(repository.recent(limit=10)) == 4


def test_replay_runs_multilight_manifests(tmp_path):
    source_root = tmp_path / "replay_multilight"
    camera_dir = source_root / "camera1"
    camera_dir.mkdir(parents=True)
    image = np.full((160, 240, 3), 255, dtype=np.uint8)
    light_entries = {}
    for light_name in ("backlight", "darkfield", "polarized"):
        marker = "_defect" if light_name == "polarized" else ""
        path = camera_dir / f"bag_3001_cam1_{light_name}{marker}.jpg"
        cv2.imwrite(str(path), image)
        light_entries[light_name] = path.name
    manifest_path = camera_dir / "bag_3001_cam1.json"
    manifest_path.write_text(
        json.dumps(
            {
                "bag_id": "bag_3001",
                "camera_id": 1,
                "lights": light_entries,
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings("config/demo.yaml")
    settings.multilight.enabled = True
    settings.correlation.expected_camera_ids = [1]
    settings.runtime.backup_dir = str(tmp_path / "backups")
    settings.runtime.result_dir = str(tmp_path / "results")
    settings.runtime.upload_dir = str(tmp_path / "uploads")
    settings.storage.sqlite_path = str(tmp_path / "inspection.db")
    settings.repeat_detection.history_path = str(tmp_path / "repeat.json")

    repository = SQLiteDetectionRepository(settings.storage.sqlite_path)
    pipeline = InspectionPipeline(
        runtime=settings.runtime,
        patch_config=settings.patch_detection,
        correlation_config=settings.correlation,
        repeat_config=settings.repeat_detection,
        repository=repository,
        plc_controller=build_plc_controller(settings.plc),
        primary_detector=build_detector(settings.primary_model),
        patch_detector=build_detector(settings.patch_model),
    )

    results = run_replay(
        settings=settings,
        pipeline=pipeline,
        source_root=str(source_root),
        interval_ms=0,
    )

    assert len(results) == 1
    assert results[0].frame_packet.is_multilight is True
    assert results[0].is_defect is True
    assert len(repository.recent(limit=10)) == 1
