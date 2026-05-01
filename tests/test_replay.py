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
