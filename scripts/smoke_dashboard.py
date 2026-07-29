#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waterbag_inspection.main import build_dashboard
from waterbag_inspection.webapp import create_web_app


REQUIRED_ROUTES = {
    "/": {"GET"},
    "/api/status": {"GET"},
    "/api/results/recent": {"GET"},
    "/api/results/metrics": {"GET"},
    "/api/workbench": {"GET"},
    "/api/inspections/<inspection_id>": {"GET"},
    "/api/results/sync": {"POST"},
    "/api/source-image-event/<int:event_id>": {"GET"},
    "/api/source-image/<frame_id>": {"GET"},
    "/api/demo/upload": {"POST"},
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test the Flask dashboard surface.")
    parser.add_argument("--config", default="config/cpp_backend/demo.ini", help="C++ INI config path")
    return parser


def _route_methods(app) -> dict[str, set[str]]:
    return {
        rule.rule: {method for method in rule.methods if method not in {"HEAD", "OPTIONS"}}
        for rule in app.url_map.iter_rules()
    }


def main() -> int:
    args = build_parser().parse_args()
    with TemporaryDirectory() as tmpdir:
        os.environ["WATERBAG_DASHBOARD_DB"] = str(Path(tmpdir) / "inspection.db")
        settings, repository = build_dashboard(args.config)
        app = create_web_app(settings=settings, repository=repository)

        routes = _route_methods(app)
        missing = []
        for route, methods in REQUIRED_ROUTES.items():
            actual = routes.get(route, set())
            if not methods.issubset(actual):
                missing.append(f"{route} {sorted(methods)}")
        if missing:
            raise AssertionError(f"missing dashboard routes: {', '.join(missing)}")

        with app.test_client() as client:
            status = client.get("/api/status")
            assert status.status_code == 200, status.data
            assert status.get_json()["mode"] == "cpp_dashboard"

            recent = client.get("/api/results/recent?limit=1")
            assert recent.status_code == 200, recent.data
            assert isinstance(recent.get_json(), list)

            metrics = client.get("/api/results/metrics?limit=1")
            assert metrics.status_code == 200, metrics.data
            assert "total_events" in metrics.get_json()

            workbench = client.get("/api/workbench?limit=1")
            assert workbench.status_code == 200, workbench.data
            assert "history" in workbench.get_json()

            sync = client.post("/api/results/sync")
            assert sync.status_code == 200, sync.data
            assert "synced" in sync.get_json()

            upload = client.post("/api/demo/upload", data={})
            assert upload.status_code == 400, upload.data

    print(json.dumps({"dashboard_smoke": "ok"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
