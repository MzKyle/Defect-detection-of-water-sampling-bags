from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


DEFAULT_LIGHT_ORDER = ("backlight", "darkfield", "polarized")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp")
MANIFEST_EXTENSIONS = (".json", ".manifest")


def _resolve_manifest_path(value: str, base_dir: Path) -> str:
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return str(path.resolve())


def normalize_light_paths(
    lights: Mapping[str, Any] | Sequence[Any],
    *,
    light_order: Sequence[str] = DEFAULT_LIGHT_ORDER,
    base_dir: Path | None = None,
) -> dict[str, str]:
    """Normalize manifest light entries to {light_name: absolute_path}."""
    root = base_dir or Path.cwd()
    if isinstance(lights, Mapping):
        raw = {}
        for name, item in lights.items():
            raw[str(name)] = item.get("path") if isinstance(item, Mapping) else item
    else:
        raw = {}
        for item in lights:
            if not isinstance(item, Mapping):
                raise ValueError("List-style light entries must be objects.")
            name = item.get("light_name") or item.get("name") or item.get("light_id")
            path = item.get("path") or item.get("source_path")
            if not name or not path:
                raise ValueError("Each light entry must include light_name and path.")
            raw[str(name)] = path

    missing = [name for name in light_order if name not in raw]
    if missing:
        raise ValueError(f"Missing multi-light image(s): {', '.join(missing)}")

    return {
        name: _resolve_manifest_path(str(raw[name]), root)
        for name in light_order
    }


def load_multilight_manifest(
    manifest_path: str,
    *,
    light_order: Sequence[str] = DEFAULT_LIGHT_ORDER,
) -> dict[str, Any]:
    """Load a burst manifest describing one camera-side multi-light sample."""
    path = Path(manifest_path).resolve()
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    lights = payload.get("lights") or payload.get("light_paths")
    if not lights:
        raise ValueError(f"Multi-light manifest has no lights: {manifest_path}")

    light_paths = normalize_light_paths(
        lights,
        light_order=light_order,
        base_dir=path.parent,
    )
    return {
        "manifest_path": str(path),
        "bag_id": payload.get("bag_id"),
        "camera_id": payload.get("camera_id"),
        "camera_name": payload.get("camera_name"),
        "frame_id": payload.get("frame_id"),
        "light_paths": light_paths,
        "metadata": {
            key: value
            for key, value in payload.items()
            if key not in {"lights", "light_paths"}
        },
    }


def is_image_path(path: str) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def is_manifest_path(path: str, suffixes: Sequence[str] = MANIFEST_EXTENSIONS) -> bool:
    name = Path(path).name.lower()
    return any(name.endswith(suffix.lower()) for suffix in suffixes)
