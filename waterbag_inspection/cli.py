from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .demo_assets import seed_demo_images
from .config import load_settings
from .multilight import load_multilight_manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Waterbag inspection demo toolkit")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="start the web demo service")
    serve_parser.add_argument("--config", help="path to YAML config file")
    serve_parser.add_argument(
        "--no-auto-start",
        action="store_true",
        help="start web service without enabling file observers immediately",
    )

    seed_parser = subparsers.add_parser("seed-demo", help="generate synthetic demo images")
    seed_parser.add_argument(
        "--output-root",
        default="demo_data",
        help="directory containing camera1 and camera2",
    )
    seed_parser.add_argument(
        "--clean",
        action="store_true",
        help="remove the output directory before generating",
    )

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="run the pipeline once on a single image",
    )
    inspect_parser.add_argument("--config", help="path to YAML config file")
    inspect_parser.add_argument(
        "--camera-id",
        type=int,
        default=1,
        help="camera id defined in config",
    )
    inspect_parser.add_argument("--image", required=True, help="image path to process")
    inspect_parser.add_argument("--reset-history", action="store_true", help="clear repeat history before running")

    multilight_parser = subparsers.add_parser(
        "inspect-multilight",
        help="run the pipeline once on a grouped backlight/darkfield/polarized sample",
    )
    multilight_parser.add_argument("--config", help="path to YAML config file")
    multilight_parser.add_argument(
        "--camera-id",
        type=int,
        default=1,
        help="camera id defined in config",
    )
    multilight_parser.add_argument("--bag-id", help="override bag id for the grouped sample")
    multilight_parser.add_argument("--manifest", help="multi-light JSON manifest path")
    multilight_parser.add_argument("--backlight", help="backlight image path")
    multilight_parser.add_argument("--darkfield", help="darkfield image path")
    multilight_parser.add_argument("--polarized", help="cross-polarized image path")
    multilight_parser.add_argument(
        "--reset-history",
        action="store_true",
        help="clear repeat history before running",
    )

    replay_parser = subparsers.add_parser(
        "replay",
        help="replay a directory of historical images through the pipeline",
    )
    replay_parser.add_argument("--config", help="path to YAML config file")
    replay_parser.add_argument(
        "--source-root",
        default="demo_data",
        help="root directory containing camera1/camera2",
    )
    replay_parser.add_argument(
        "--interval-ms",
        type=int,
        default=0,
        help="sleep interval between replayed frames",
    )
    replay_parser.add_argument("--limit", type=int, help="maximum number of frames to replay")
    replay_parser.add_argument("--reset-history", action="store_true", help="clear repeat history before replay")

    inject_parser = subparsers.add_parser(
        "inject-faults",
        help="run offline fault injection scenarios",
    )
    inject_parser.add_argument("--config", help="path to YAML config file")
    inject_parser.add_argument(
        "--scenario",
        default="all",
        choices=["all", "timeout", "ack-retry", "out-of-order"],
        help="fault injection scenario to run",
    )
    inject_parser.add_argument(
        "--output-root",
        default="artifacts/fault_injection",
        help="directory used to write injected files and scenario outputs",
    )
    inject_parser.add_argument(
        "--clean",
        action="store_true",
        help="remove the output directory before running",
    )

    return parser


def _maybe_reset_repeat_history(config: str | None) -> None:
    settings = load_settings(config)
    history_path = Path(settings.repeat_detection.history_path)
    if history_path.exists():
        history_path.unlink()


def _clean_output_root(output_root: str) -> None:
    root = Path(output_root)
    if root.exists():
        shutil.rmtree(root)


def _run_inspect(
    config: str | None,
    camera_id: int,
    image_path: str,
    reset_history: bool,
) -> None:
    from .main import build_pipeline_components, configure_logging

    configure_logging()
    if reset_history:
        _maybe_reset_repeat_history(config)
    settings, _, pipeline = build_pipeline_components(config_path=config)
    if camera_id not in settings.camera_map:
        raise SystemExit(
            f"Unknown camera_id={camera_id}. Available cameras: {sorted(settings.camera_map)}"
        )
    if not Path(image_path).exists():
        raise SystemExit(f"Image not found: {image_path}")
    camera = settings.camera_map[camera_id]
    result = pipeline.process_image(camera, image_path)
    print(json.dumps(result.to_summary_dict(), ensure_ascii=False, indent=2))


def _run_inspect_multilight(
    config: str | None,
    camera_id: int,
    bag_id: str | None,
    manifest_path: str | None,
    light_args: dict[str, str | None],
    reset_history: bool,
) -> None:
    from .main import build_pipeline_components, configure_logging

    configure_logging()
    if reset_history:
        _maybe_reset_repeat_history(config)
    settings, _, pipeline = build_pipeline_components(config_path=config)

    source_path = None
    metadata = {"source": "cli_multilight"}
    if manifest_path:
        manifest = load_multilight_manifest(
            manifest_path,
            light_order=settings.multilight.light_order,
        )
        camera_id = int(manifest.get("camera_id") or camera_id)
        bag_id = bag_id or manifest.get("bag_id")
        light_paths = manifest["light_paths"]
        source_path = manifest["manifest_path"]
        metadata.update(manifest.get("metadata") or {})
    else:
        light_paths = {name: path for name, path in light_args.items() if path}
        missing = [
            name for name in settings.multilight.light_order if name not in light_paths
        ]
        if missing:
            raise SystemExit(f"Missing light image argument(s): {', '.join(missing)}")

    if camera_id not in settings.camera_map:
        raise SystemExit(
            f"Unknown camera_id={camera_id}. Available cameras: {sorted(settings.camera_map)}"
        )
    for light_name, image_path in light_paths.items():
        if not Path(image_path).exists():
            raise SystemExit(f"{light_name} image not found: {image_path}")

    camera = settings.camera_map[camera_id]
    result = pipeline.process_multilight_images(
        camera,
        light_paths,
        bag_id=bag_id,
        source_path=source_path,
        metadata=metadata,
    )
    print(json.dumps(result.to_summary_dict(), ensure_ascii=False, indent=2))


def _run_replay(
    config: str | None,
    source_root: str,
    interval_ms: int,
    limit: int | None,
    reset_history: bool,
) -> None:
    from .main import build_pipeline_components, configure_logging
    from .replay import run_replay

    configure_logging()
    if reset_history:
        _maybe_reset_repeat_history(config)
    settings, _, pipeline = build_pipeline_components(config_path=config)
    results = run_replay(
        settings=settings,
        pipeline=pipeline,
        source_root=source_root,
        interval_ms=interval_ms,
        limit=limit,
    )
    payload = {
        "source_root": str(Path(source_root).resolve()),
        "processed_frames": len(results),
        "defect_frames": sum(1 for result in results if result.is_defect),
        "repeat_frames": sum(1 for result in results if result.repeated),
        "avg_total_latency_ms": (
            round(
                sum(result.latency_ms for result in results) / len(results),
                1,
            )
            if results
            else 0.0
        ),
        "frames": [result.to_summary_dict() for result in results],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _run_fault_injections(
    config: str | None,
    scenario: str,
    output_root: str,
    clean: bool,
) -> None:
    from .fault_injection import run_fault_injections
    from .main import configure_logging

    configure_logging()
    if clean:
        _clean_output_root(output_root)
    payload = run_fault_injections(
        config_path=config,
        scenario=scenario,
        output_root=output_root,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        from .main import serve

        serve(config_path=args.config, auto_start=False if args.no_auto_start else None)
        return

    if args.command == "seed-demo":
        outputs = seed_demo_images(args.output_root, clean=args.clean)
        payload = {
            "output_root": str(Path(args.output_root).resolve()),
            "generated_files": outputs,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.command == "inspect":
        _run_inspect(args.config, args.camera_id, args.image, args.reset_history)
        return

    if args.command == "inspect-multilight":
        _run_inspect_multilight(
            args.config,
            args.camera_id,
            args.bag_id,
            args.manifest,
            {
                "backlight": args.backlight,
                "darkfield": args.darkfield,
                "polarized": args.polarized,
            },
            args.reset_history,
        )
        return

    if args.command == "replay":
        _run_replay(
            args.config,
            args.source_root,
            args.interval_ms,
            args.limit,
            args.reset_history,
        )
        return

    if args.command == "inject-faults":
        _run_fault_injections(args.config, args.scenario, args.output_root, args.clean)
        return

    parser.error(f"Unsupported command: {args.command}")
