from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from .config import ROOT_DIR, DashboardSettings
from .storage import SQLiteDetectionRepository


def create_web_app(settings: DashboardSettings, repository: SQLiteDetectionRepository) -> Flask:
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parent.parent / "templates"))

    @app.route("/")
    def index():
        return render_template(
            "index.html",
            app_name=settings.app.name,
            cameras=settings.cameras,
            config_name=Path(settings.cpp_config_path).name,
        )

    @app.route("/api/status")
    def status():
        repository.sync_from_jsonl()
        result_path = Path(settings.result_jsonl)
        return jsonify(
            {
                "mode": "cpp_dashboard",
                "cpp_config": settings.cpp_config_path,
                "config_name": Path(settings.cpp_config_path).name,
                "result_jsonl": settings.result_jsonl,
                "result_jsonl_exists": result_path.exists(),
                "database": settings.sqlite_path,
                "cameras": [
                    {
                        "id": camera.camera_id,
                        "name": camera.name,
                        "watch_dir": camera.watch_dir,
                    }
                    for camera in settings.cameras
                ],
            }
        )

    @app.route("/api/results/recent")
    def recent_results():
        limit = min(int(request.args.get("limit", 20)), 200)
        return jsonify(repository.recent(limit))

    @app.route("/api/results/metrics")
    def metrics_results():
        limit = min(int(request.args.get("limit", 80)), 1000)
        return jsonify(repository.metrics(limit))

    @app.route("/api/results/sync", methods=["POST"])
    def sync_results():
        return jsonify({"synced": repository.sync_from_jsonl()})

    @app.route("/api/source-image/<frame_id>")
    def source_image(frame_id: str):
        source_path = repository.source_path_for_frame(frame_id)
        if not source_path:
            return jsonify({"error": "frame not found"}), 404
        path = Path(source_path)
        if not path.is_absolute():
            path = ROOT_DIR / path
        if not path.exists() or not path.is_file():
            return jsonify({"error": "source image not found"}), 404
        return send_file(path)

    @app.route("/api/demo/upload", methods=["POST"])
    def demo_upload():
        file = request.files.get("image")
        camera_id = int(request.form.get("camera_id", "1"))

        if file is None or not file.filename:
            return jsonify({"error": "missing image"}), 400
        if camera_id not in settings.camera_map:
            return jsonify({"error": "invalid camera_id"}), 400

        suffix = Path(file.filename).suffix or ".jpg"
        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{Path(file.filename).stem}{suffix}"
        upload_path = Path(settings.upload_dir) / filename
        upload_path.parent.mkdir(parents=True, exist_ok=True)
        file.save(upload_path)

        camera_dir = Path(settings.camera_map[camera_id].watch_dir)
        camera_dir.mkdir(parents=True, exist_ok=True)
        target_path = camera_dir / filename
        shutil.copy2(upload_path, target_path)

        return jsonify(
            {
                "status": "copied_to_cpp_watch_dir",
                "filename": filename,
                "camera_id": camera_id,
                "target_path": str(target_path),
            }
        )

    return app
