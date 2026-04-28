from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO

from .config import Settings
from .schemas import InspectionResult
from .service import InspectionRuntime
from .storage import SQLiteDetectionRepository


LOGGER = logging.getLogger(__name__)


def create_web_app(settings: Settings, runtime: InspectionRuntime, repository: SQLiteDetectionRepository):
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parent.parent / "templates"))
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

    def publish(result: InspectionResult) -> None:
        summary = result.to_summary_dict()
        socketio.emit(
            "inspection_update",
            {
                **summary,
                "image": result.image_base64,
                "bag_summary": result.bag_summary.to_dict(),
                "state_trace": [event.to_dict() for event in result.state_trace],
                "final_count": len(result.final_boxes),
            },
        )

    runtime.register_listener(publish)

    @app.route("/")
    def index():
        return render_template(
            "index.html",
            app_name=settings.app.name,
            cameras=settings.cameras,
            config_name=request.args.get("config", "demo"),
        )

    @app.route("/api/control/start", methods=["POST"])
    def start_runtime():
        runtime.start()
        return jsonify({"status": "running"})

    @app.route("/api/control/stop", methods=["POST"])
    def stop_runtime():
        runtime.stop()
        return jsonify({"status": "stopped"})

    @app.route("/api/status")
    def status():
        return jsonify(
            {
                "running": runtime.running,
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
        limit = min(int(request.args.get("limit", 20)), 50)
        return jsonify(repository.recent(limit))

    @app.route("/api/results/metrics")
    def metrics_results():
        limit = min(int(request.args.get("limit", 40)), 200)
        return jsonify(repository.metrics(limit))

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
        upload_path = Path(settings.runtime.upload_dir) / filename
        upload_path.parent.mkdir(parents=True, exist_ok=True)
        file.save(upload_path)

        camera_dir = Path(settings.camera_map[camera_id].watch_dir)
        camera_dir.mkdir(parents=True, exist_ok=True)
        target_path = camera_dir / filename
        shutil.copy2(upload_path, target_path)

        if not runtime.running:
            runtime.submit_path(camera_id, str(target_path))
        return jsonify({"status": "queued", "filename": filename, "camera_id": camera_id})

    return app, socketio
