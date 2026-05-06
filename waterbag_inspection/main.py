from __future__ import annotations

import logging
import os

from .config import load_settings
from .storage import SQLiteDetectionRepository


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def build_dashboard(config_path: str | None = None):
    settings = load_settings(config_path)
    repository = SQLiteDetectionRepository(settings.sqlite_path, settings.result_jsonl)
    repository.sync_from_jsonl()
    return settings, repository


def serve(config_path: str | None = None) -> None:
    from .webapp import create_web_app

    configure_logging()
    settings, repository = build_dashboard(config_path)
    app = create_web_app(settings=settings, repository=repository)
    app.run(host=settings.app.host, port=settings.app.port, debug=False)


def main() -> None:
    serve()
