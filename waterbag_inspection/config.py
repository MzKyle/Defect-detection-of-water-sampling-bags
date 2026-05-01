from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "config" / "demo.yaml"


def _resolve_path(value: str | None) -> str | None:
    if not value:
        return value
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((ROOT_DIR / path).resolve())


@dataclass
class AppConfig:
    name: str = "Waterbag Inspection Demo"
    host: str = "0.0.0.0"
    port: int = 5000
    auto_start: bool = True
    open_browser: bool = False
    browser_url: str = "http://127.0.0.1:5000"


@dataclass
class CameraConfig:
    camera_id: int
    name: str
    watch_dir: str


@dataclass
class ModelConfig:
    backend: str = "mock"
    weights_path: str | None = None
    encrypted_path: str | None = None
    key_path: str | None = None
    device: str = "cpu"
    imgsz: int = 640
    conf_thres: float = 0.3
    iou_thres: float = 0.3


@dataclass
class PatchConfig:
    enabled: bool = True
    horizontal: int = 4
    vertical: int = 5
    conf_thres: float = 0.2
    iou_thres: float = 0.3
    save_visualizations: bool = False
    visualization_dir: str = "artifacts/patch_vis"


@dataclass
class StorageConfig:
    backend: str = "sqlite"
    sqlite_path: str = "artifacts/inspection.db"


@dataclass
class PLCConfig:
    backend: str = "mock"
    enabled: bool = False
    port: str = "COM4"
    baudrate: int = 115200
    parity: str = "N"
    stopbits: int = 1
    bytesize: int = 8
    timeout: float = 0.1
    registers: dict[str, int] = field(
        default_factory=lambda: {"cam1": 100, "cam2": 102, "alert": 104, "bag": 106}
    )
    alert_pulse_seconds: float = 0.2
    ack_timeout_ms: float = 200.0
    max_retries: int = 1
    retry_interval_ms: float = 50.0
    mock_ack_latency_ms: float = 0.0
    mock_fail_first_attempts: int = 0


@dataclass
class CorrelationConfig:
    enabled: bool = True
    expected_camera_ids: list[int] = field(default_factory=list)
    hold_non_defect_until_complete: bool = True
    pending_timeout_ms: float = 1500.0
    timeout_action: str = "reject"
    finalized_retention_ms: float = 5000.0


@dataclass
class RepeatConfig:
    enabled: bool = True
    history_path: str = "artifacts/repeat_history.json"
    history_namespace: str = "default"
    isolate_by_source: bool = True
    iou_threshold: float = 0.5
    max_entries_per_camera: int = 50


@dataclass
class RuntimeConfig:
    backup_dir: str = "artifacts/backups"
    result_dir: str = "artifacts/results"
    upload_dir: str = "artifacts/uploads"
    cooldown_seconds: float = 0.5
    file_ready_timeout_seconds: float = 5.0
    file_stable_seconds: float = 0.3
    queue_poll_interval_seconds: float = 0.2


@dataclass
class Settings:
    app: AppConfig
    cameras: list[CameraConfig]
    primary_model: ModelConfig
    patch_model: ModelConfig
    patch_detection: PatchConfig
    storage: StorageConfig
    plc: PLCConfig
    correlation: CorrelationConfig
    repeat_detection: RepeatConfig
    runtime: RuntimeConfig

    @property
    def camera_map(self) -> dict[int, CameraConfig]:
        return {camera.camera_id: camera for camera in self.cameras}


def _camera_from_dict(data: dict[str, Any]) -> CameraConfig:
    return CameraConfig(
        camera_id=int(data["id"]),
        name=str(data["name"]),
        watch_dir=_resolve_path(str(data["watch_dir"])) or "",
    )


def _model_from_dict(data: dict[str, Any]) -> ModelConfig:
    return ModelConfig(
        backend=str(data.get("backend", "mock")),
        weights_path=_resolve_path(data.get("weights_path")),
        encrypted_path=_resolve_path(data.get("encrypted_path")),
        key_path=_resolve_path(data.get("key_path")),
        device=str(data.get("device", "cpu")),
        imgsz=int(data.get("imgsz", 640)),
        conf_thres=float(data.get("conf_thres", 0.3)),
        iou_thres=float(data.get("iou_thres", 0.3)),
    )


def load_settings(config_path: str | None = None) -> Settings:
    source = Path(
        config_path
        or os.getenv("WATERBAG_CONFIG")
        or DEFAULT_CONFIG_PATH
    )
    with source.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    app = AppConfig(**(raw.get("app") or {}))
    cameras = [_camera_from_dict(item) for item in raw.get("cameras", [])]
    if not cameras:
        raise ValueError("At least one camera must be configured.")

    primary_model = _model_from_dict(raw.get("models", {}).get("primary", {}))
    patch_model = _model_from_dict(raw.get("models", {}).get("patch", {}))

    patch_detection = PatchConfig(**(raw.get("patch_detection") or {}))
    patch_detection.visualization_dir = (
        _resolve_path(patch_detection.visualization_dir) or patch_detection.visualization_dir
    )

    storage = StorageConfig(**(raw.get("storage") or {}))
    storage.sqlite_path = _resolve_path(storage.sqlite_path) or storage.sqlite_path

    plc = PLCConfig(**(raw.get("plc") or {}))
    correlation = CorrelationConfig(**(raw.get("correlation") or {}))
    if not correlation.expected_camera_ids:
        correlation.expected_camera_ids = [camera.camera_id for camera in cameras]

    repeat_detection = RepeatConfig(**(raw.get("repeat_detection") or {}))
    repeat_detection.history_path = (
        _resolve_path(repeat_detection.history_path) or repeat_detection.history_path
    )

    runtime = RuntimeConfig(**(raw.get("runtime") or {}))
    runtime.backup_dir = _resolve_path(runtime.backup_dir) or runtime.backup_dir
    runtime.result_dir = _resolve_path(runtime.result_dir) or runtime.result_dir
    runtime.upload_dir = _resolve_path(runtime.upload_dir) or runtime.upload_dir

    return Settings(
        app=app,
        cameras=cameras,
        primary_model=primary_model,
        patch_model=patch_model,
        patch_detection=patch_detection,
        storage=storage,
        plc=plc,
        correlation=correlation,
        repeat_detection=repeat_detection,
        runtime=runtime,
    )
