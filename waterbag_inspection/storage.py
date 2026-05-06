from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


STATUS_TEXT = {
    "ok": "正常",
    "defect": "异常",
    "timeout": "超时",
    "captured": "已采图",
    "no_bag": "无袋",
    "capture_invalid": "采图异常",
    "pending": "待确认",
}


def _status_text(status: str) -> str:
    return STATUS_TEXT.get(status, status or "待确认")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_load_list(value: str) -> list[Any]:
    try:
        decoded = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return decoded if isinstance(decoded, list) else []


def _fault_signals(row: sqlite3.Row | dict[str, Any]) -> list[str]:
    signals: list[str] = []
    if bool(row["timed_out"]):
        signals.append("timeout")
    if bool(row["ack_retried"]):
        signals.append("ack_retry")
    if bool(row["stale_frame_ignored"]):
        signals.append("stale_frame")
    if not bool(row["plc_success"]):
        signals.append("plc_failure")
    return signals


class SQLiteDetectionRepository:
    def __init__(self, sqlite_path: str, result_jsonl: str | None = None):
        self.sqlite_path = Path(sqlite_path)
        self.result_jsonl = Path(result_jsonl) if result_jsonl else None
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.sqlite_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS detection_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    frame_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    bag_id TEXT NOT NULL,
                    camera_id INTEGER NOT NULL,
                    camera_name TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    status_code TEXT NOT NULL,
                    is_defect INTEGER NOT NULL,
                    repeated INTEGER NOT NULL,
                    plc_success INTEGER NOT NULL,
                    decision_action TEXT NOT NULL,
                    decision_reason TEXT NOT NULL,
                    decision_finalized INTEGER NOT NULL,
                    timed_out INTEGER NOT NULL,
                    stale_frame_ignored INTEGER NOT NULL,
                    ack_attempts INTEGER NOT NULL,
                    ack_retried INTEGER NOT NULL,
                    latency_ms REAL NOT NULL,
                    advance_control_ms REAL NOT NULL,
                    control_ms REAL NOT NULL,
                    stage1_ms REAL NOT NULL,
                    stage2_ms REAL NOT NULL,
                    final_boxes TEXT NOT NULL,
                    control_commands TEXT NOT NULL,
                    execution_feedbacks TEXT NOT NULL,
                    state_trace TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                )
                """
            )
            self._ensure_detection_columns(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_state (
                    path TEXT PRIMARY KEY,
                    offset INTEGER NOT NULL,
                    size INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_detection_created_at ON detection_results(created_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_detection_bag_id ON detection_results(bag_id)")

    def _ensure_detection_columns(self, connection: sqlite3.Connection) -> None:
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(detection_results)").fetchall()
        }
        desired = {
            "frame_id": "TEXT NOT NULL DEFAULT ''",
            "created_at": "TEXT NOT NULL DEFAULT ''",
            "bag_id": "TEXT NOT NULL DEFAULT ''",
            "camera_id": "INTEGER NOT NULL DEFAULT 0",
            "camera_name": "TEXT NOT NULL DEFAULT ''",
            "source_path": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT '待确认'",
            "status_code": "TEXT NOT NULL DEFAULT 'pending'",
            "is_defect": "INTEGER NOT NULL DEFAULT 0",
            "repeated": "INTEGER NOT NULL DEFAULT 0",
            "plc_success": "INTEGER NOT NULL DEFAULT 1",
            "decision_action": "TEXT NOT NULL DEFAULT ''",
            "decision_reason": "TEXT NOT NULL DEFAULT ''",
            "decision_finalized": "INTEGER NOT NULL DEFAULT 1",
            "timed_out": "INTEGER NOT NULL DEFAULT 0",
            "stale_frame_ignored": "INTEGER NOT NULL DEFAULT 0",
            "ack_attempts": "INTEGER NOT NULL DEFAULT 0",
            "ack_retried": "INTEGER NOT NULL DEFAULT 0",
            "latency_ms": "REAL NOT NULL DEFAULT 0",
            "advance_control_ms": "REAL NOT NULL DEFAULT 0",
            "control_ms": "REAL NOT NULL DEFAULT 0",
            "stage1_ms": "REAL NOT NULL DEFAULT 0",
            "stage2_ms": "REAL NOT NULL DEFAULT 0",
            "final_boxes": "TEXT NOT NULL DEFAULT '[]'",
            "control_commands": "TEXT NOT NULL DEFAULT '[]'",
            "execution_feedbacks": "TEXT NOT NULL DEFAULT '[]'",
            "state_trace": "TEXT NOT NULL DEFAULT '[]'",
            "raw_json": "TEXT NOT NULL DEFAULT '{}'",
            "imported_at": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in desired.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE detection_results ADD COLUMN {name} {definition}")

    def _state_for_path(self, connection: sqlite3.Connection, path: Path) -> tuple[int, int]:
        row = connection.execute(
            "SELECT offset, size FROM import_state WHERE path = ?",
            (str(path),),
        ).fetchone()
        if row is None:
            return 0, 0
        return int(row["offset"]), int(row["size"])

    def _save_state(self, connection: sqlite3.Connection, path: Path, offset: int, size: int) -> None:
        connection.execute(
            """
            INSERT INTO import_state(path, offset, size, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                offset = excluded.offset,
                size = excluded.size,
                updated_at = excluded.updated_at
            """,
            (str(path), offset, size, datetime.now().isoformat(timespec="seconds")),
        )

    def _upsert_payload(self, connection: sqlite3.Connection, payload: dict[str, Any], fallback_id: str) -> None:
        frame_id = str(payload.get("frame_id") or fallback_id)
        feedbacks = payload.get("execution_feedbacks") or []
        commands = payload.get("control_commands") or []
        boxes = payload.get("boxes") or []
        state_trace = payload.get("state_trace") or []
        ack_attempts = _safe_int(
            payload.get("ack_attempts"),
            sum(_safe_int(item.get("attempts"), 1) for item in feedbacks if isinstance(item, dict)),
        )
        ack_retried = bool(payload.get("ack_retry")) or any(
            _safe_int(item.get("attempts"), 1) > 1
            for item in feedbacks
            if isinstance(item, dict)
        )
        status_code = str(payload.get("status") or "pending")

        values = {
            "frame_id": frame_id,
            "created_at": str(payload.get("timestamp") or ""),
            "bag_id": str(payload.get("bag_id") or ""),
            "camera_id": _safe_int(payload.get("camera_id")),
            "camera_name": str(payload.get("camera_name") or ""),
            "source_path": str(payload.get("source_path") or ""),
            "status": _status_text(status_code),
            "status_code": status_code,
            "is_defect": int(bool(payload.get("is_defect"))),
            "repeated": int(bool(payload.get("repeated", False))),
            "plc_success": int(bool(payload.get("plc_success", True))),
            "decision_action": str(payload.get("action") or ""),
            "decision_reason": str(payload.get("reason") or ""),
            "decision_finalized": int(bool(payload.get("finalized", True))),
            "timed_out": int(bool(payload.get("timed_out", False))),
            "stale_frame_ignored": int(bool(payload.get("stale_frame_ignored", False))),
            "ack_attempts": ack_attempts,
            "ack_retried": int(ack_retried),
            "latency_ms": _safe_float(payload.get("latency_ms")),
            "advance_control_ms": _safe_float(payload.get("advance_control_ms")),
            "control_ms": _safe_float(payload.get("control_ms")),
            "stage1_ms": _safe_float(payload.get("stage1_ms")),
            "stage2_ms": _safe_float(payload.get("stage2_ms")),
            "final_boxes": _json_dump(boxes),
            "control_commands": _json_dump(commands),
            "execution_feedbacks": _json_dump(feedbacks),
            "state_trace": _json_dump(state_trace),
            "raw_json": _json_dump(payload),
            "imported_at": datetime.now().isoformat(timespec="seconds"),
        }

        columns = tuple(values)
        placeholders = ",".join("?" for _ in columns)
        updates = ",".join(f"{column}=excluded.{column}" for column in columns if column != "frame_id")
        connection.execute(
            f"""
            INSERT INTO detection_results({','.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(frame_id) DO UPDATE SET {updates}
            """,
            tuple(values[column] for column in columns),
        )

    def sync_from_jsonl(self, result_jsonl: str | None = None) -> int:
        path = Path(result_jsonl) if result_jsonl else self.result_jsonl
        if path is None or not path.exists():
            return 0

        path = path.resolve()
        size = path.stat().st_size
        processed = 0
        with self._connect() as connection:
            offset, previous_size = self._state_for_path(connection, path)
            if size < previous_size:
                offset = 0

            with path.open("r", encoding="utf-8") as handle:
                handle.seek(offset)
                while True:
                    line_start = handle.tell()
                    line = handle.readline()
                    if not line:
                        break
                    line_end = handle.tell()
                    if not line.endswith("\n"):
                        handle.seek(line_start)
                        break
                    stripped = line.strip()
                    if not stripped:
                        offset = line_end
                        continue
                    try:
                        payload = json.loads(stripped)
                    except json.JSONDecodeError:
                        offset = line_end
                        continue
                    if isinstance(payload, dict):
                        self._upsert_payload(connection, payload, f"jsonl-{line_end}")
                        processed += 1
                    offset = line_end

            self._save_state(connection, path, offset, size)
        return processed

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        self.sync_from_jsonl()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM detection_results
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            final_boxes = _json_load_list(row["final_boxes"])
            state_trace = _json_load_list(row["state_trace"])
            source_path = row["source_path"]
            items.append(
                {
                    "timestamp": row["created_at"],
                    "frame_id": row["frame_id"],
                    "bag_id": row["bag_id"],
                    "camera_id": row["camera_id"],
                    "camera_name": row["camera_name"],
                    "source_path": source_path,
                    "image_url": f"/api/source-image/{row['frame_id']}" if source_path else "",
                    "status": row["status"],
                    "status_code": row["status_code"],
                    "repeated": bool(row["repeated"]),
                    "plc_success": bool(row["plc_success"]),
                    "decision_action": row["decision_action"],
                    "decision_reason": row["decision_reason"],
                    "decision_finalized": bool(row["decision_finalized"]),
                    "timed_out": bool(row["timed_out"]),
                    "stale_frame_ignored": bool(row["stale_frame_ignored"]),
                    "ack_attempts": row["ack_attempts"],
                    "ack_retried": bool(row["ack_retried"]),
                    "fault_signals": _fault_signals(row),
                    "latency_ms": row["latency_ms"],
                    "advance_control_ms": row["advance_control_ms"],
                    "control_ms": row["control_ms"],
                    "stage1_ms": row["stage1_ms"],
                    "stage2_ms": row["stage2_ms"],
                    "final_count": len(final_boxes),
                    "state_count": len(state_trace),
                }
            )
        return items

    def metrics(self, limit: int = 80) -> dict[str, Any]:
        rows = self.recent(limit)
        total_events = len(rows)
        if total_events == 0:
            return {
                "limit": limit,
                "total_events": 0,
                "status_counts": {"normal": 0, "defect": 0, "pending": 0, "timeout": 0},
                "defect_events": 0,
                "repeat_events": 0,
                "timeout_events": 0,
                "ack_retry_events": 0,
                "stale_frame_events": 0,
                "ack_failure_events": 0,
                "avg_latency_ms": 0.0,
                "avg_control_ms": 0.0,
                "avg_ack_attempts": 0.0,
                "max_ack_attempts": 0,
                "fault_rows": [],
            }

        ack_attempt_rows = [row["ack_attempts"] for row in rows if row["ack_attempts"] > 0]
        fault_rows = [
            row
            for row in rows
            if row["timed_out"] or row["ack_retried"] or row["stale_frame_ignored"] or not row["plc_success"]
        ][:8]
        return {
            "limit": limit,
            "total_events": total_events,
            "status_counts": {
                "normal": sum(1 for row in rows if row["status_code"] == "ok"),
                "defect": sum(1 for row in rows if row["status_code"] == "defect"),
                "pending": sum(1 for row in rows if row["status_code"] in {"pending", "captured"}),
                "timeout": sum(1 for row in rows if row["status_code"] == "timeout"),
            },
            "defect_events": sum(1 for row in rows if row["status_code"] == "defect"),
            "repeat_events": sum(1 for row in rows if row["repeated"]),
            "timeout_events": sum(1 for row in rows if row["timed_out"]),
            "ack_retry_events": sum(1 for row in rows if row["ack_retried"]),
            "stale_frame_events": sum(1 for row in rows if row["stale_frame_ignored"]),
            "ack_failure_events": sum(1 for row in rows if not row["plc_success"]),
            "avg_latency_ms": round(sum(float(row["latency_ms"]) for row in rows) / total_events, 1),
            "avg_control_ms": round(sum(float(row["control_ms"]) for row in rows) / total_events, 1),
            "avg_ack_attempts": round(sum(ack_attempt_rows) / len(ack_attempt_rows), 2) if ack_attempt_rows else 0.0,
            "max_ack_attempts": max(ack_attempt_rows, default=0),
            "fault_rows": fault_rows,
        }

    def source_path_for_frame(self, frame_id: str) -> str | None:
        self.sync_from_jsonl()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT source_path FROM detection_results WHERE frame_id = ?",
                (frame_id,),
            ).fetchone()
        return str(row["source_path"]) if row and row["source_path"] else None
