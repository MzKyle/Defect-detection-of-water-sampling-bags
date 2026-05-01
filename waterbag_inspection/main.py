from __future__ import annotations

import atexit
import logging
import os
import webbrowser

from .config import load_settings
from .detectors import build_detector
from .pipeline import InspectionPipeline
from .plc import build_plc_controller
from .storage import SQLiteDetectionRepository


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def build_pipeline_components(config_path: str | None = None):
    settings = load_settings(config_path)
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
        multilight_config=settings.multilight,
    )
    return settings, repository, pipeline


def build_runtime(config_path: str | None = None, auto_start: bool | None = None):
    from .service import InspectionRuntime

    settings, repository, pipeline = build_pipeline_components(config_path)
    if auto_start is not None:
        settings.app.auto_start = auto_start
    runtime = InspectionRuntime(settings=settings, pipeline=pipeline, repository=repository)
    return settings, repository, pipeline, runtime


def serve(config_path: str | None = None, auto_start: bool | None = None) -> None:
    from .webapp import create_web_app

    configure_logging()
    settings, repository, _, runtime = build_runtime(config_path=config_path, auto_start=auto_start)
    app, socketio = create_web_app(settings=settings, runtime=runtime, repository=repository)

    if settings.app.auto_start:
        runtime.start()

    atexit.register(runtime.stop)

    if settings.app.open_browser:
        webbrowser.open(settings.app.browser_url)

    socketio.run(app, host=settings.app.host, port=settings.app.port, debug=False)


def main() -> None:
    serve()
