#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test the installed dashboard package outside the repo.")
    parser.add_argument("--config", required=True, help="absolute or relative C++ INI config path")
    parser.add_argument("--cli", default="waterbag-inspection", help="installed console script")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config_path = Path(args.config).resolve()
    if not config_path.exists():
        raise AssertionError(f"config does not exist: {config_path}")

    with TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        env = os.environ.copy()
        env["WATERBAG_DASHBOARD_DB"] = str(tmp_path / "inspection.db")
        env["WATERBAG_DASHBOARD_UPLOAD_DIR"] = str(tmp_path / "uploads")

        os.chdir(tmp_path)
        subprocess.run(
            [args.cli, "sync-results", "--config", str(config_path)],
            check=True,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        from waterbag_inspection.main import build_dashboard
        from waterbag_inspection.webapp import create_web_app

        settings, repository = build_dashboard(str(config_path))
        app = create_web_app(settings=settings, repository=repository)
        with app.test_client() as client:
            home = client.get("/")
            if home.status_code != 200:
                raise AssertionError(f"home failed: {home.status_code}")
            status = client.get("/api/status")
            if status.status_code != 200:
                raise AssertionError(f"status failed: {status.status_code}")
            recent = client.get("/api/results/recent?limit=1")
            if recent.status_code != 200:
                raise AssertionError(f"recent failed: {recent.status_code}")

    print(json.dumps({"installed_dashboard_smoke": "ok"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
