from __future__ import annotations

import time
from copy import deepcopy
from pathlib import Path

import cv2
import numpy as np

from .config import Settings, load_settings
from .detectors import build_detector
from .pipeline import InspectionPipeline
from .plc import build_plc_controller
from .storage import SQLiteDetectionRepository


def _write_demo_frame(path: Path, title: str, defect: bool = False) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.full((480, 640, 3), 248, dtype=np.uint8)
    cv2.rectangle(image, (80, 70), (560, 410), (255, 255, 255), thickness=-1)
    cv2.rectangle(image, (80, 70), (560, 410), (215, 221, 224), thickness=5)
    cv2.putText(image, title, (95, 430), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (92, 98, 101), 2)
    if defect:
        cv2.circle(image, (330, 220), 28, (50, 50, 50), thickness=-1)
        cv2.circle(image, (350, 205), 8, (90, 90, 90), thickness=-1)
    cv2.imwrite(str(path), image)
    return str(path)


def _prepare_settings(config_path: str | None, scenario_root: Path, scenario_name: str) -> Settings:
    settings = deepcopy(load_settings(config_path))
    settings.runtime.backup_dir = str((scenario_root / "backups").resolve())
    settings.runtime.result_dir = str((scenario_root / "results").resolve())
    settings.runtime.upload_dir = str((scenario_root / "uploads").resolve())
    settings.storage.sqlite_path = str((scenario_root / "inspection.db").resolve())
    settings.repeat_detection.history_path = str((scenario_root / "repeat_history.json").resolve())
    settings.repeat_detection.history_namespace = f"{settings.repeat_detection.history_namespace}:{scenario_name}"

    for camera in settings.cameras:
        camera.watch_dir = str((scenario_root / f"camera{camera.camera_id}").resolve())
        Path(camera.watch_dir).mkdir(parents=True, exist_ok=True)

    return settings


def _build_pipeline(settings: Settings) -> tuple[SQLiteDetectionRepository, InspectionPipeline]:
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
    return repository, pipeline


def _scenario_timeout(config_path: str | None, scenario_root: Path) -> dict[str, object]:
    settings = _prepare_settings(config_path, scenario_root, "timeout")
    settings.correlation.pending_timeout_ms = 30
    settings.plc.enabled = True
    settings.plc.backend = "mock"
    repository, pipeline = _build_pipeline(settings)

    image_path = Path(settings.cameras[0].watch_dir) / "bag_fault_timeout_0001_cam1_good.jpg"
    written = _write_demo_frame(image_path, "TIMEOUT CAM1 GOOD", defect=False)

    pending_result = pipeline.process_image(settings.cameras[0], written)
    time.sleep(0.06)
    timeout_results = pipeline.flush_timeouts()
    scenario_results = [pending_result, *timeout_results]

    return {
        "name": "timeout",
        "output_root": str(scenario_root.resolve()),
        "generated_files": [written],
        "results": [result.to_summary_dict() for result in scenario_results],
        "metrics": repository.metrics(limit=20),
    }


def _scenario_ack_retry(config_path: str | None, scenario_root: Path) -> dict[str, object]:
    settings = _prepare_settings(config_path, scenario_root, "ack_retry")
    settings.correlation.expected_camera_ids = [1]
    settings.plc.enabled = True
    settings.plc.backend = "mock"
    settings.plc.max_retries = 2
    settings.plc.retry_interval_ms = 0
    settings.plc.mock_fail_first_attempts = 1
    settings.plc.ack_timeout_ms = 200
    repository, pipeline = _build_pipeline(settings)

    image_path = Path(settings.cameras[0].watch_dir) / "bag_fault_retry_0001_cam1_defect_primary.jpg"
    written = _write_demo_frame(image_path, "ACK RETRY DEFECT", defect=True)
    result = pipeline.process_image(settings.cameras[0], written)

    return {
        "name": "ack-retry",
        "output_root": str(scenario_root.resolve()),
        "generated_files": [written],
        "results": [result.to_summary_dict()],
        "metrics": repository.metrics(limit=20),
    }


def _scenario_out_of_order(config_path: str | None, scenario_root: Path) -> dict[str, object]:
    settings = _prepare_settings(config_path, scenario_root, "out_of_order")
    settings.correlation.expected_camera_ids = [1]
    settings.plc.enabled = False
    repository, pipeline = _build_pipeline(settings)

    new_path = Path(settings.cameras[0].watch_dir) / "bag_fault_reorder_0001_cam1_defect_primary_new.jpg"
    old_path = Path(settings.cameras[0].watch_dir) / "bag_fault_reorder_0001_cam1_good_old.jpg"
    written_new = _write_demo_frame(new_path, "NEW DEFECT", defect=True)
    written_old = _write_demo_frame(old_path, "OLD GOOD", defect=False)

    now = time.time()
    Path(written_new).touch()
    Path(written_old).touch()
    time.sleep(0.01)
    Path(written_new).touch()
    old_ts = now - 10
    Path(written_old).touch()
    import os
    os.utime(written_new, (now, now))
    os.utime(written_old, (old_ts, old_ts))

    first_result = pipeline.process_image(settings.cameras[0], written_new)
    stale_result = pipeline.process_image(settings.cameras[0], written_old)

    return {
        "name": "out-of-order",
        "output_root": str(scenario_root.resolve()),
        "generated_files": [written_new, written_old],
        "results": [first_result.to_summary_dict(), stale_result.to_summary_dict()],
        "metrics": repository.metrics(limit=20),
    }


def run_fault_injections(
    *,
    config_path: str | None = None,
    scenario: str = "all",
    output_root: str = "artifacts/fault_injection",
) -> dict[str, object]:
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    scenario_builders = {
        "timeout": _scenario_timeout,
        "ack-retry": _scenario_ack_retry,
        "out-of-order": _scenario_out_of_order,
    }
    selected = list(scenario_builders) if scenario == "all" else [scenario]
    reports = []

    for name in selected:
        scenario_root = root / name.replace("-", "_")
        scenario_root.mkdir(parents=True, exist_ok=True)
        reports.append(scenario_builders[name](config_path, scenario_root))

    return {
        "output_root": str(root),
        "scenario_count": len(reports),
        "scenarios": reports,
    }
