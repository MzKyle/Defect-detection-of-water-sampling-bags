import os
import time
from pathlib import Path

import cv2
import numpy as np

from waterbag_inspection.config import load_settings
from waterbag_inspection.detectors import build_detector
from waterbag_inspection.pipeline import InspectionPipeline
from waterbag_inspection.plc import build_plc_controller
from waterbag_inspection.schemas import PipelineState
from waterbag_inspection.storage import SQLiteDetectionRepository


def test_pipeline_runs_stage2_and_persists_record(tmp_path):
    settings = load_settings("configs/demo.yaml")
    settings.runtime.backup_dir = str(tmp_path / "backups")
    settings.runtime.result_dir = str(tmp_path / "results")
    settings.runtime.upload_dir = str(tmp_path / "uploads")
    settings.storage.sqlite_path = str(tmp_path / "inspection.db")
    settings.repeat_detection.history_path = str(tmp_path / "repeat.json")
    settings.correlation.expected_camera_ids = [1]

    image_path = tmp_path / "bag_1001_cam1_micro_patch.jpg"
    image = np.full((480, 640, 3), 255, dtype=np.uint8)
    cv2.circle(image, (320, 180), 16, (0, 0, 0), -1)
    cv2.imwrite(str(image_path), image)

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

    result = pipeline.process_image(settings.cameras[0], str(image_path))

    assert result.status_text == "异常"
    assert result.control_action == "reject"
    assert len(result.stage1_boxes) == 0
    assert len(result.stage2_boxes) == 1
    assert Path(result.result_image_path).exists()
    assert result.timing_breakdown.stage2_inference_ms >= 0.0
    assert result.state_trace[0].state == PipelineState.RECEIVED
    assert result.state_trace[-1].state == PipelineState.PERSISTED
    assert len(repository.recent(limit=5)) == 1


def test_pipeline_waits_for_peer_camera_before_accept(tmp_path):
    settings = load_settings("configs/demo.yaml")
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

    image = np.full((480, 640, 3), 255, dtype=np.uint8)
    cam1_image = tmp_path / "bag_2001_cam1_good.jpg"
    cam2_image = tmp_path / "bag_2001_cam2_good.jpg"
    cv2.imwrite(str(cam1_image), image)
    cv2.imwrite(str(cam2_image), image)

    first_result = pipeline.process_image(settings.cameras[0], str(cam1_image))
    second_result = pipeline.process_image(settings.cameras[1], str(cam2_image))

    assert first_result.status_text == "待确认"
    assert first_result.decision_result.finalized is False
    assert first_result.control_commands == []
    assert first_result.bag_summary.missing_camera_ids == [2]

    assert second_result.status_text == "正常"
    assert second_result.decision_result.finalized is True
    assert second_result.control_action == "accept"
    assert len(second_result.control_commands) == 1
    assert second_result.control_commands[0].target == "bag_controller"
    assert second_result.bag_summary.observed_camera_ids == [1, 2]


def test_pipeline_rejects_immediately_when_any_camera_reports_defect(tmp_path):
    settings = load_settings("configs/demo.yaml")
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

    image_path = tmp_path / "bag_2002_cam1_defect_primary.jpg"
    image = np.full((480, 640, 3), 255, dtype=np.uint8)
    cv2.circle(image, (320, 200), 30, (0, 0, 0), -1)
    cv2.imwrite(str(image_path), image)

    result = pipeline.process_image(settings.cameras[0], str(image_path))

    assert result.status_text == "异常"
    assert result.decision_result.finalized is True
    assert result.control_action == "reject"
    assert len(result.control_commands) == 1
    assert result.bag_summary.complete is False
    assert result.bag_summary.missing_camera_ids == [2]


def test_pipeline_accepts_when_camera2_arrives_before_camera1(tmp_path):
    settings = load_settings("configs/demo.yaml")
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

    image = np.full((480, 640, 3), 255, dtype=np.uint8)
    cam2_image = tmp_path / "bag_2101_cam2_good.jpg"
    cam1_image = tmp_path / "bag_2101_cam1_good.jpg"
    cv2.imwrite(str(cam2_image), image)
    cv2.imwrite(str(cam1_image), image)

    first_result = pipeline.process_image(settings.cameras[1], str(cam2_image))
    second_result = pipeline.process_image(settings.cameras[0], str(cam1_image))

    assert first_result.status_text == "待确认"
    assert first_result.bag_summary.missing_camera_ids == [1]
    assert second_result.status_text == "正常"
    assert second_result.control_action == "accept"
    assert second_result.bag_summary.observed_camera_ids == [1, 2]


def test_pipeline_ignores_stale_same_camera_frame(tmp_path):
    settings = load_settings("configs/demo.yaml")
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

    image = np.full((480, 640, 3), 255, dtype=np.uint8)
    good_image = tmp_path / "bag_2201_cam1_good.jpg"
    stale_defect_image = tmp_path / "bag_2201_cam1_defect_primary_old.jpg"
    cv2.imwrite(str(good_image), image)
    cv2.imwrite(str(stale_defect_image), image)
    now = time.time()
    os.utime(good_image, (now, now))
    os.utime(stale_defect_image, (now - 5, now - 5))

    first_result = pipeline.process_image(settings.cameras[0], str(good_image))
    stale_result = pipeline.process_image(settings.cameras[0], str(stale_defect_image))

    assert first_result.status_text == "待确认"
    assert stale_result.status_text == "待确认"
    assert stale_result.control_commands == []
    assert stale_result.bag_summary.stale_frame_ignored is True
    assert "stale_frame_ignored" in stale_result.decision_reason


def test_pipeline_flushes_timeout_and_keeps_reject_after_late_peer_frame(tmp_path):
    settings = load_settings("configs/demo.yaml")
    settings.runtime.backup_dir = str(tmp_path / "backups")
    settings.runtime.result_dir = str(tmp_path / "results")
    settings.runtime.upload_dir = str(tmp_path / "uploads")
    settings.storage.sqlite_path = str(tmp_path / "inspection.db")
    settings.repeat_detection.history_path = str(tmp_path / "repeat.json")
    settings.correlation.pending_timeout_ms = 10

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

    image = np.full((480, 640, 3), 255, dtype=np.uint8)
    cam1_image = tmp_path / "bag_2301_cam1_good.jpg"
    cam2_image = tmp_path / "bag_2301_cam2_good.jpg"
    cv2.imwrite(str(cam1_image), image)
    cv2.imwrite(str(cam2_image), image)

    pending_result = pipeline.process_image(settings.cameras[0], str(cam1_image))
    time.sleep(0.03)
    timeout_results = pipeline.flush_timeouts()
    late_peer_result = pipeline.process_image(settings.cameras[1], str(cam2_image))

    assert pending_result.status_text == "待确认"
    assert len(timeout_results) == 1
    assert timeout_results[0].status_text == "超时"
    assert timeout_results[0].decision_result.timed_out is True
    assert timeout_results[0].control_action == "reject"
    assert len(timeout_results[0].control_commands) == 1

    assert late_peer_result.status_text == "超时"
    assert late_peer_result.control_commands == []
    assert late_peer_result.decision_result.timed_out is True
    assert "late_frame_after_timeout" in late_peer_result.decision_reason
