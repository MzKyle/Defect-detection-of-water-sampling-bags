from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np


LOGGER = logging.getLogger(__name__)


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


@dataclass(frozen=True)
class ArtifactJob:
    description: str
    action: Callable[[], None]


class ArtifactWriter:
    """Writes inspection artifacts without forcing the inference path to wait for disk IO."""

    _SENTINEL = object()

    def __init__(
        self,
        *,
        enabled: bool = False,
        max_queue_size: int = 128,
        drop_when_full: bool = True,
    ):
        self.enabled = enabled
        self.max_queue_size = max(1, int(max_queue_size))
        self.drop_when_full = drop_when_full
        self.errors: list[str] = []
        self.dropped_jobs = 0
        self._queue: queue.Queue[ArtifactJob | object] = queue.Queue(maxsize=self.max_queue_size)
        self._thread: threading.Thread | None = None
        self._closed = False
        if self.enabled:
            self.start()

    @property
    def pending_jobs(self) -> int:
        return int(getattr(self._queue, "unfinished_tasks", 0))

    def start(self) -> None:
        if not self.enabled:
            return
        if self._closed:
            self._queue = queue.Queue(maxsize=self.max_queue_size)
            self._thread = None
            self._closed = False
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="artifact-writer",
            daemon=True,
        )
        self._thread.start()

    def close(self, timeout: float | None = 2.0) -> bool:
        if not self.enabled:
            return True
        if self._closed:
            return self.pending_jobs == 0 and (
                self._thread is None or not self._thread.is_alive()
            )
        flushed = self.flush(timeout=timeout)
        self._closed = True
        try:
            self._queue.put(self._SENTINEL, timeout=max(timeout or 0.1, 0.1))
        except queue.Full:
            LOGGER.warning("Artifact writer queue did not accept shutdown sentinel.")
            return False
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            return flushed and not self._thread.is_alive()
        return flushed

    def flush(self, timeout: float | None = None) -> bool:
        if not self.enabled:
            return True
        if timeout is None:
            self._queue.join()
            return True
        deadline = time.monotonic() + max(timeout, 0.0)
        while time.monotonic() <= deadline:
            if self.pending_jobs == 0:
                return True
            time.sleep(0.01)
        return self.pending_jobs == 0

    def copy_file(self, source: str | Path, target: str | Path) -> float:
        source_path = Path(source)
        target_path = Path(target)

        def write() -> None:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)

        return self._submit(f"copy:{source_path}->{target_path}", write)

    def write_image(self, target: str | Path, image: np.ndarray) -> float:
        target_path = Path(target)
        image_to_write = image.copy() if self.enabled else image

        def write() -> None:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(target_path), image_to_write):
                raise OSError(f"Failed to write image artifact: {target_path}")

        return self._submit(f"image:{target_path}", write)

    def _submit(self, description: str, action: Callable[[], None]) -> float:
        started = time.perf_counter()
        if not self.enabled:
            action()
            return _elapsed_ms(started)
        self.start()
        job = ArtifactJob(description=description, action=action)
        if self.drop_when_full:
            try:
                self._queue.put_nowait(job)
            except queue.Full:
                self.dropped_jobs += 1
                LOGGER.warning("Dropped artifact job because queue is full: %s", description)
        else:
            self._queue.put(job)
        return _elapsed_ms(started)

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is self._SENTINEL:
                    return
                assert isinstance(item, ArtifactJob)
                item.action()
            except Exception as exc:
                description = getattr(item, "description", "artifact")
                message = f"{description}: {exc}"
                self.errors.append(message)
                LOGGER.exception("Artifact write failed: %s", description)
            finally:
                self._queue.task_done()
