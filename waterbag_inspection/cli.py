from __future__ import annotations

import argparse
import json

from .main import build_dashboard, serve


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Waterbag C++ result dashboard")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="start the dashboard")
    serve_parser.add_argument("--config", help="path to C++ INI config file")

    sync_parser = subparsers.add_parser("sync-results", help="sync C++ JSONL results into SQLite")
    sync_parser.add_argument("--config", help="path to C++ INI config file")

    recent_parser = subparsers.add_parser("recent", help="print recent dashboard rows")
    recent_parser.add_argument("--config", help="path to C++ INI config file")
    recent_parser.add_argument("--limit", type=int, default=20)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        serve(config_path=args.config)
        return

    settings, repository = build_dashboard(args.config)
    if args.command == "sync-results":
        payload = {
            "result_jsonl": settings.result_jsonl,
            "sqlite_path": settings.sqlite_path,
            "synced": repository.sync_from_jsonl(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.command == "recent":
        print(json.dumps(repository.recent(args.limit), ensure_ascii=False, indent=2))
        return

    parser.error(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
