from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CPP_CONFIG = ROOT_DIR / "config" / "cpp_backend" / "demo.ini"


def _resolve_path(value: str | None) -> str:
    if not value:
        return ""
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((ROOT_DIR / path).resolve())


def _camera_id_from_section(section: str) -> int:
    try:
        return int(section.split(".", 1)[1])
    except (IndexError, ValueError):
        return 0


@dataclass
class AppConfig:
    name: str = "Waterbag Inspection Dashboard"
    host: str = "0.0.0.0"
    port: int = 5000


@dataclass
class CameraConfig:
    camera_id: int
    name: str
    watch_dir: str


@dataclass
class DashboardSettings:
    app: AppConfig
    cameras: list[CameraConfig]
    result_jsonl: str
    sqlite_path: str
    upload_dir: str
    cpp_config_path: str

    @property
    def camera_map(self) -> dict[int, CameraConfig]:
        return {camera.camera_id: camera for camera in self.cameras}


def load_settings(config_path: str | None = None) -> DashboardSettings:
    source = Path(
        config_path
        or os.getenv("WATERBAG_CPP_CONFIG")
        or DEFAULT_CPP_CONFIG
    )
    if not source.is_absolute():
        source = (ROOT_DIR / source).resolve()

    parser = configparser.ConfigParser()
    parser.read(source, encoding="utf-8")

    app = AppConfig(
        name=os.getenv("WATERBAG_DASHBOARD_NAME", "Waterbag Inspection Dashboard"),
        host=os.getenv("WATERBAG_DASHBOARD_HOST", "0.0.0.0"),
        port=int(os.getenv("WATERBAG_DASHBOARD_PORT", "5000")),
    )

    cameras: list[CameraConfig] = []
    for section in sorted(parser.sections()):
        if not section.startswith("camera."):
            continue
        camera_id = parser.getint(section, "id", fallback=_camera_id_from_section(section))
        if camera_id <= 0:
            continue
        cameras.append(
            CameraConfig(
                camera_id=camera_id,
                name=parser.get(section, "name", fallback=f"Camera {camera_id}"),
                watch_dir=_resolve_path(parser.get(section, "watch_dir", fallback="")),
            )
        )
    if not cameras:
        cameras = [
            CameraConfig(1, "A-camera", _resolve_path("demo_data/camera1")),
            CameraConfig(2, "B-camera", _resolve_path("demo_data/camera2")),
        ]

    result_jsonl = _resolve_path(
        parser.get("storage", "result_jsonl", fallback="artifacts/cpp_backend/results.jsonl")
    )
    sqlite_path = _resolve_path(
        os.getenv("WATERBAG_DASHBOARD_DB", "artifacts/dashboard/inspection.db")
    )
    upload_dir = _resolve_path(
        os.getenv("WATERBAG_DASHBOARD_UPLOAD_DIR", "artifacts/uploads")
    )

    return DashboardSettings(
        app=app,
        cameras=sorted(cameras, key=lambda item: item.camera_id),
        result_jsonl=result_jsonl,
        sqlite_path=sqlite_path,
        upload_dir=upload_dir,
        cpp_config_path=str(source),
    )
