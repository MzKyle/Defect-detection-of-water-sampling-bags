from __future__ import annotations

import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import Settings
from .multilight import (
    IMAGE_EXTENSIONS,
    is_image_path,
    is_manifest_path,
    load_multilight_manifest,
)
from .pipeline import InspectionPipeline
from .schemas import InspectionResult, build_frame_packet, now_iso
from .storage import SQLiteDetectionRepository


LOGGER = logging.getLogger(__name__)


class _ImageCreatedHandler(FileSystemEventHandler):
    def __init__(self, camera_id: int, submitter: Callable[[int, str], None]):
        self.camera_id = camera_id
        self.submitter = submitter

    def on_created(self, event) -> None:
        if event.is_directory:
            return
        path = event.src_path
        if not (
            path.lower().endswith(IMAGE_EXTENSIONS)
            or is_manifest_path(path)
        ):
            return
        self.submitter(self.camera_id, path)


class InspectionRuntime:
    def __init__(
        self,
        settings: Settings,
        pipeline: InspectionPipeline,
        repository: SQLiteDetectionRepository,
    ):
        self.settings = settings
        self.pipeline = pipeline
        self.repository = repository
        self.queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread: threading.Thread | None = None
        self.observers: list[Observer] = []
        self.listeners: list[Callable[[InspectionResult], None]] = []
        self.running = False
        self.last_processed: dict[int, float] = {}

        for camera in self.settings.cameras:
            Path(camera.watch_dir).mkdir(parents=True, exist_ok=True)

    def register_listener(self, callback: Callable[[InspectionResult], None]) -> None:
        self.listeners.append(callback)

    def _publish_results(self, results: list[InspectionResult]) -> None:
        for result in results:
            for listener in self.listeners:
                listener(result)

    def start(self) -> None:
        if self.running:
            return

        self.stop_event.clear()
        self.worker_thread = threading.Thread(
            target=self._worker_loop,
            name="inspection-worker",
            daemon=True,
        )
        self.worker_thread.start()

        for camera in self.settings.cameras:
            observer = Observer()
            observer.schedule(
                _ImageCreatedHandler(camera.camera_id, self.submit_path),
                camera.watch_dir,
                recursive=False,
            )
            observer.start()
            self.observers.append(observer)

        self.running = True
        LOGGER.info("Inspection runtime started with %s cameras.", len(self.settings.cameras))

    def stop(self) -> None:
        if not self.running:
            return

        self.stop_event.set()
        for observer in self.observers:
            observer.stop()
            observer.join()
        self.observers.clear()

        if self.worker_thread:
            self.worker_thread.join(timeout=2.0)
            self.worker_thread = None

        self.running = False
        self.pipeline.close()
        LOGGER.info("Inspection runtime stopped.")

    def submit_path(self, camera_id: int, path: str) -> None:
        if self.settings.multilight.enabled:
            if is_manifest_path(path, self.settings.multilight.manifest_suffixes):
                self.submit_multilight_manifest(camera_id, path)
            return

        if not is_image_path(path):
            return
        camera = self.settings.camera_map[camera_id]
        packet = build_frame_packet(
            camera_id=camera_id,
            camera_name=camera.name,
            source_path=path,
            metadata={"source": "runtime", "repeat_scope": "runtime"},
        )
        self.queue.put(packet)

    def submit_multilight_manifest(self, camera_id: int, manifest_path: str) -> None:
        payload = load_multilight_manifest(
            manifest_path,
            light_order=self.settings.multilight.light_order,
        )
        actual_camera_id = int(payload.get("camera_id") or camera_id)
        camera = self.settings.camera_map[actual_camera_id]
        packet = build_frame_packet(
            camera_id=actual_camera_id,
            camera_name=str(payload.get("camera_name") or camera.name),
            source_path=payload["manifest_path"],
            bag_id=payload.get("bag_id"),
            metadata={
                "source": "runtime_multilight_manifest",
                "repeat_scope": "runtime_multilight",
                "manifest_path": payload["manifest_path"],
                **(payload.get("metadata") or {}),
            },
            light_paths=payload["light_paths"],
        )
        if payload.get("frame_id"):
            packet.frame_id = str(payload["frame_id"])
        self.queue.put(packet)

    def _worker_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                packet = self.queue.get(timeout=self.settings.runtime.queue_poll_interval_seconds)
            except queue.Empty:
                self._publish_results(self.pipeline.flush_timeouts())
                continue

            try:
                ready_paths = [packet.source_path]
                if packet.is_multilight:
                    ready_paths.extend(packet.light_paths.values())
                if not all(self._wait_until_ready(path) for path in ready_paths):
                    LOGGER.warning("Skipped unstable file: %s", packet.source_path)
                    continue

                now = time.monotonic()
                last_time = self.last_processed.get(packet.camera_id, 0.0)
                if now - last_time < self.settings.runtime.cooldown_seconds:
                    continue

                packet.file_ready_at = now_iso()
                packet.processing_started_at = now_iso()
                packet.source_path = str(Path(packet.source_path).resolve())
                packet.light_paths = {
                    light_name: str(Path(path).resolve())
                    for light_name, path in packet.light_paths.items()
                }
                source_paths = [packet.source_path, *packet.light_paths.values()]
                packet.source_mtime_ns = max(
                    os.stat(path).st_mtime_ns for path in source_paths
                )

                result = self.pipeline.process_packet(packet)
                self.last_processed[packet.camera_id] = now
                self._publish_results([result])
                self._publish_results(self.pipeline.flush_timeouts())
            except Exception:
                LOGGER.exception("Failed to process image %s", packet.source_path)
            finally:
                self.queue.task_done()

    def _wait_until_ready(self, path: str) -> bool:
        deadline = time.monotonic() + self.settings.runtime.file_ready_timeout_seconds
        stable_since = None
        last_snapshot = None
        while time.monotonic() < deadline:
            if not os.path.exists(path):
                time.sleep(0.1)
                continue

            stat = os.stat(path)
            snapshot = (stat.st_size, stat.st_mtime_ns)
            if snapshot == last_snapshot:
                if stable_since is None:
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= self.settings.runtime.file_stable_seconds:
                    return True
            else:
                stable_since = None
                last_snapshot = snapshot
            time.sleep(0.1)
        return False
