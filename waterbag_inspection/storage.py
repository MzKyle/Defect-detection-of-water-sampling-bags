from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


STATUS_TEXT = {
    "ok": "OK",
    "defect": "NG",
    "timeout": "超时",
    "captured": "已采图",
    "no_bag": "无袋",
    "capture_invalid": "采图异常",
    "fault": "安全故障",
    "pending": "等待",
}

STATUS_ICON = {
    "ok": "✓",
    "defect": "✕",
    "timeout": "⏱",
    "captured": "●",
    "no_bag": "○",
    "capture_invalid": "!",
    "fault": "!",
    "pending": "…",
    "warning": "!",
    "failure": "✕",
    "offline": "○",
}


def _status_text(status: str) -> str:
    return STATUS_TEXT.get(status, status or "等待")


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


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y"}:
            return True
        if lowered in {"0", "false", "no", "n"}:
            return False
    return default


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_load(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _json_load_list(value: str) -> list[Any]:
    decoded = _json_load(value, [])
    return decoded if isinstance(decoded, list) else []


def _json_load_dict(value: str) -> dict[str, Any]:
    decoded = _json_load(value, {})
    return decoded if isinstance(decoded, dict) else {}


def _fault_signals(row: sqlite3.Row | dict[str, Any]) -> list[str]:
    signals: list[str] = []
    if _safe_bool(row["timed_out"]):
        signals.append("timeout")
    if _safe_bool(row["ack_retried"]):
        signals.append("ack_retry")
    if _safe_bool(row["stale_frame_ignored"]):
        signals.append("stale_frame")
    if not _safe_bool(row["plc_success"], True):
        signals.append("plc_failure")
    if str(row["status_code"]) == "capture_invalid":
        signals.append("capture_invalid")
    return signals


def _status_family(status_code: str, signals: Iterable[str] = ()) -> str:
    signal_set = set(signals)
    if status_code in {"defect", "timeout", "capture_invalid", "fault"} or "plc_failure" in signal_set:
        return "danger"
    if status_code == "ok":
        return "ok"
    if signal_set:
        return "warn"
    if status_code in {"pending", "captured", "no_bag"}:
        return "wait"
    return "neutral"


def _base_label(label: str) -> str:
    return str(label or "defect").split("@", maxsplit=1)[0]


def _side_label(camera_id: int) -> str:
    if camera_id == 1:
        return "A"
    if camera_id == 2:
        return "B"
    return f"C{camera_id}"


def _action_text(action: str) -> str:
    return {
        "accept": "顺序分拣 OK",
        "reject": "顺序分拣 NG",
        "await_peer_camera": "等待 A/B 齐套",
        "defect_queued": "已释放，等待算法",
        "capture_invalid": "采图无效",
        "no_bag": "无袋",
    }.get(action, action or "--")


def _reason_text(reason: str) -> str:
    return {
        "all_cameras_passed": "A/B 面均未检出缺陷",
        "aggregate_defect_detected": "袋级融合检出缺陷",
        "await_peer_camera": "等待另一侧图像",
        "capture_released_defect_async": "采图完成，已释放到检测队列",
        "stage1_detected_defect": "Stage 1 粗检检出缺陷",
        "stage2_detected_micro_defect": "Stage 2 精检检出细小缺陷",
        "stage2_clear": "Stage 2 精检通过",
        "stage1_clear": "Stage 1 粗检通过",
        "plc_laser_presence_timeout": "PLC 袋体到位信号超时",
        "plc_laser_presence_invalid": "PLC 袋体到位信号无效",
        "plc_laser_no_bag": "PLC 未检测到袋体",
        "burst_sync_or_jitter_invalid": "多光源采图同步或抖动异常",
        "sort_result_timeout_fail_safe_ng": "顺序分拣等待超时，故障保护 NG",
        "station_queue_saturated": "工位队列满，已停线",
        "defect_queue_saturated": "缺陷队列满，已停线",
        "sort_queue_saturated": "分拣队列满，已停线",
        "thread_exception": "关键线程异常，已停线",
        "plc_communication_lost": "PLC 心跳失联，已停线",
        "result_storage_failed": "结果审计写入失败，已停线",
        "bag_id_missing": "PLC BagID 缺失，已停线",
        "bag_id_regression": "PLC BagID 回退，已停线",
        "checkpoint_failed": "Presence checkpoint 失败，已停线",
    }.get(reason, reason or "--")


def _timing_from_payload(payload: dict[str, Any], key: str) -> float | None:
    if key not in payload:
        return None
    return _safe_float(payload.get(key))


def _latest_value(events: list[dict[str, Any]], key: str) -> Any:
    for event in reversed(events):
        value = event.get(key)
        if value not in (None, ""):
            return value
    return None


def _max_timing(events: list[dict[str, Any]], key: str) -> float | None:
    values = [event[key] for event in events if event.get(key) is not None]
    return max(values) if values else None


def _sum_timing(events: list[dict[str, Any]], key: str) -> float | None:
    values = [event[key] for event in events if event.get(key) is not None]
    return round(sum(values), 3) if values else None


def _trace_has(events: list[dict[str, Any]], needle: str) -> bool:
    return any(any(needle in item for item in event.get("state_trace", [])) for event in events)


def _trace_count(events: list[dict[str, Any]], needle: str) -> int:
    return sum(1 for event in events for item in event.get("state_trace", []) if needle in item)


def _first_reason(events: list[dict[str, Any]], candidates: Iterable[str]) -> str:
    candidate_set = set(candidates)
    for event in reversed(events):
        reason = str(event.get("decision_reason") or "")
        if reason in candidate_set:
            return _reason_text(reason)
    return ""


def _event_status_priority(event: dict[str, Any]) -> int:
    status = str(event.get("status_code") or "pending")
    if event.get("timed_out") or status == "timeout":
        return 60
    if status == "capture_invalid":
        return 55
    if status == "defect":
        return 50
    if status == "ok":
        return 45
    if event.get("decision_finalized"):
        return 40
    if status == "captured":
        return 30
    return 20


def _choose_focus_event(events: list[dict[str, Any]]) -> dict[str, Any]:
    return max(events, key=lambda event: (_event_status_priority(event), event["event_id"]))


def _sort_status(event: dict[str, Any]) -> dict[str, Any]:
    action = str(event.get("decision_action") or "")
    attempts = _safe_int(event.get("ack_attempts"))
    success = bool(event.get("plc_success", True))
    retried = bool(event.get("ack_retried"))
    finalized = bool(event.get("decision_finalized"))

    if not finalized or action in {"await_peer_camera", "defect_queued", ""}:
        return {
            "code": "waiting",
            "family": "wait",
            "icon": STATUS_ICON["pending"],
            "text": "等待分拣",
            "detail": _action_text(action),
            "attempts": attempts,
            "retried": retried,
            "success": success,
        }
    if not success:
        return {
            "code": "failed",
            "family": "danger",
            "icon": STATUS_ICON["failure"],
            "text": "PLC 执行失败",
            "detail": _action_text(action),
            "attempts": attempts,
            "retried": retried,
            "success": success,
        }
    if retried:
        return {
            "code": "retry_ok",
            "family": "warn",
            "icon": STATUS_ICON["warning"],
            "text": "PLC ACK 重试后确认",
            "detail": _action_text(action),
            "attempts": attempts,
            "retried": retried,
            "success": success,
        }
    return {
        "code": "acked",
        "family": "ok",
        "icon": STATUS_ICON["ok"],
        "text": "PLC ACK 已确认",
        "detail": _action_text(action),
        "attempts": attempts,
        "retried": retried,
        "success": success,
    }


def _stage(code: str, name: str, status: str, duration_ms: float | None, reason: str = "") -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "status": status,
        "icon": STATUS_ICON.get(status, STATUS_ICON["pending"]),
        "duration_ms": duration_ms,
        "reason": reason or "--",
    }


def _build_stages(events: list[dict[str, Any]], focus: dict[str, Any], expected_camera_ids: set[int]) -> list[dict[str, Any]]:
    faces_seen = {int(event["camera_id"]) for event in events if event.get("source_path")}
    missing = sorted(expected_camera_ids - faces_seen)

    presence_status = "ok" if any(event.get("bag_present") for event in events) else "wait"
    presence_reason = "PLC 袋体到位"
    if any(event.get("presence_timed_out") for event in events):
        presence_status = "failure"
        presence_reason = "PLC 袋体到位信号超时"
    elif any(event.get("presence_message_valid") is False for event in events):
        presence_status = "failure"
        presence_reason = "PLC 袋体到位信号无效"
    elif str(focus.get("decision_action")) == "no_bag":
        presence_status = "wait"
        presence_reason = "未检测到袋体"

    capture_status = "ok" if _trace_has(events, "burst_sync_valid") or any(event.get("burst_sync_valid") for event in events) else "wait"
    capture_reason = "多光源采图同步正常" if capture_status == "ok" else "等待采图结果"
    if _trace_has(events, "burst_sync_warning"):
        capture_status = "warning"
        capture_reason = "多光源同步警告"
    if str(focus.get("status_code")) == "capture_invalid" or _trace_has(events, "burst_group_missing"):
        capture_status = "failure"
        capture_reason = "采图同步或抖动异常"

    pair_status = "ok" if expected_camera_ids and expected_camera_ids.issubset(faces_seen) else "wait"
    pair_reason = "A/B 面齐套" if pair_status == "ok" else f"缺少相机 {','.join(map(str, missing)) or '--'}"
    if _trace_has(events, "capture_reorder_timeout"):
        pair_status = "failure"
        pair_reason = _first_reason(events, {"image_lost_capture_timeout"}) or "A/B 面齐套超时"

    stage1_boxes = _trace_count(events, "stage1_light:")
    stage1_status = "ok" if _trace_has(events, "stage1_fused") else "wait"
    stage1_reason = "Stage 1 粗检完成" if stage1_status == "ok" else "等待 Stage 1"
    if any(event.get("stage_source") == "stage1" and event.get("is_defect") for event in events):
        stage1_status = "warning"
        stage1_reason = f"粗检检出缺陷，光源结果 {stage1_boxes} 条"

    stage2_ran = _trace_has(events, "stage2_running")
    stage2_status = "ok" if stage2_ran else "wait"
    stage2_reason = "Stage 2 精检完成" if stage2_ran else "策略跳过"
    if any(event.get("stage_source") == "stage2" and event.get("is_defect") for event in events):
        stage2_status = "warning"
        stage2_reason = "精检检出细小缺陷"

    fusion_status = "ok" if _trace_has(events, "decision_ready") else "wait"
    fusion_reason = _reason_text(str(focus.get("decision_reason") or ""))
    if focus.get("timed_out"):
        fusion_status = "failure"
    elif str(focus.get("status_code")) == "defect":
        fusion_status = "warning"

    reorder_status = "ok" if _trace_has(events, "sort_reorder_release") else "wait"
    reorder_reason = "顺序分拣释放" if reorder_status == "ok" else "等待排序释放"
    if _trace_has(events, "sort_reorder_timeout"):
        reorder_status = "failure"
        reorder_reason = "顺序分拣等待超时"

    sort = _sort_status(focus)
    ack_status = {
        "ok": "ok",
        "warn": "warning",
        "danger": "failure",
        "wait": "wait",
    }.get(sort["family"], "wait")

    return [
        _stage("presence", "袋体到位", presence_status, _max_timing(events, "presence_ms"), presence_reason),
        _stage("capture", "多光源采图", capture_status, _max_timing(events, "capture_ms"), capture_reason),
        _stage("pairing", "A/B 面齐套", pair_status, _max_timing(events, "bag_pairing_ms"), pair_reason),
        _stage("stage1", "Stage 1 粗检", stage1_status, _max_timing(events, "stage1_ms"), stage1_reason),
        _stage("stage2", "Stage 2 精检", stage2_status, _max_timing(events, "stage2_ms"), stage2_reason),
        _stage("fusion", "袋级融合", fusion_status, _max_timing(events, "decision_ms"), fusion_reason),
        _stage("reorder", "顺序分拣", reorder_status, _max_timing(events, "reorder_wait_ms"), reorder_reason),
        _stage("ack", "PLC ACK", ack_status, focus.get("control_ms"), sort["text"]),
    ]


def _compact_raw(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


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
            self._init_legacy_schema(connection)
            self._init_event_schema(connection)
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
            connection.execute("CREATE INDEX IF NOT EXISTS idx_event_created_at ON detection_events(created_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_event_bag_id ON detection_events(bag_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_event_frame_id ON detection_events(frame_id)")
            self._migrate_legacy_results(connection)
            self._reset_jsonl_offset_when_events_are_missing(connection)

    def _init_legacy_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS detection_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                frame_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                bag_id TEXT NOT NULL,
                camera_id INTEGER NOT NULL,
                camera_name TEXT NOT NULL,
                camera_backend TEXT NOT NULL,
                plc_backend TEXT NOT NULL,
                plc_message_id TEXT NOT NULL,
                plc_bag_id TEXT NOT NULL,
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
                burst_sync_valid INTEGER NOT NULL,
                hardware_check_status TEXT NOT NULL,
                final_boxes TEXT NOT NULL,
                control_commands TEXT NOT NULL,
                execution_feedbacks TEXT NOT NULL,
                state_trace TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                imported_at TEXT NOT NULL
            )
            """
        )
        self._ensure_columns(connection, "detection_results", self._legacy_columns())

    def _init_event_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS detection_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                frame_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                bag_id TEXT NOT NULL,
                camera_id INTEGER NOT NULL,
                camera_name TEXT NOT NULL,
                camera_backend TEXT NOT NULL,
                plc_backend TEXT NOT NULL,
                plc_message_id TEXT NOT NULL,
                plc_bag_id TEXT NOT NULL,
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
                queue_delay_ms REAL,
                presence_ms REAL,
                capture_ms REAL,
                bag_pairing_ms REAL,
                latency_ms REAL,
                bag_latency_ms REAL,
                advance_control_ms REAL,
                control_ms REAL,
                stage1_ms REAL,
                stage2_ms REAL,
                decision_ms REAL,
                correlation_ms REAL,
                reorder_wait_ms REAL,
                burst_sync_valid INTEGER NOT NULL,
                hardware_check_status TEXT NOT NULL,
                bag_present INTEGER NOT NULL,
                presence_message_valid INTEGER NOT NULL,
                presence_timed_out INTEGER NOT NULL,
                stage_source TEXT NOT NULL,
                final_boxes TEXT NOT NULL,
                control_commands TEXT NOT NULL,
                execution_feedbacks TEXT NOT NULL,
                state_trace TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                imported_at TEXT NOT NULL
            )
            """
        )
        self._ensure_columns(connection, "detection_events", self._event_columns())

    def _legacy_columns(self) -> dict[str, str]:
        return {
            "frame_id": "TEXT NOT NULL DEFAULT ''",
            "created_at": "TEXT NOT NULL DEFAULT ''",
            "bag_id": "TEXT NOT NULL DEFAULT ''",
            "camera_id": "INTEGER NOT NULL DEFAULT 0",
            "camera_name": "TEXT NOT NULL DEFAULT ''",
            "camera_backend": "TEXT NOT NULL DEFAULT ''",
            "plc_backend": "TEXT NOT NULL DEFAULT ''",
            "plc_message_id": "TEXT NOT NULL DEFAULT ''",
            "plc_bag_id": "TEXT NOT NULL DEFAULT ''",
            "source_path": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT '等待'",
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
            "burst_sync_valid": "INTEGER NOT NULL DEFAULT 0",
            "hardware_check_status": "TEXT NOT NULL DEFAULT ''",
            "final_boxes": "TEXT NOT NULL DEFAULT '[]'",
            "control_commands": "TEXT NOT NULL DEFAULT '[]'",
            "execution_feedbacks": "TEXT NOT NULL DEFAULT '[]'",
            "state_trace": "TEXT NOT NULL DEFAULT '[]'",
            "raw_json": "TEXT NOT NULL DEFAULT '{}'",
            "imported_at": "TEXT NOT NULL DEFAULT ''",
        }

    def _event_columns(self) -> dict[str, str]:
        columns = self._legacy_columns()
        columns.update(
            {
                "event_key": "TEXT NOT NULL DEFAULT ''",
                "queue_delay_ms": "REAL",
                "presence_ms": "REAL",
                "capture_ms": "REAL",
                "bag_pairing_ms": "REAL",
                "bag_latency_ms": "REAL",
                "decision_ms": "REAL",
                "correlation_ms": "REAL",
                "reorder_wait_ms": "REAL",
                "bag_present": "INTEGER NOT NULL DEFAULT 0",
                "presence_message_valid": "INTEGER NOT NULL DEFAULT 1",
                "presence_timed_out": "INTEGER NOT NULL DEFAULT 0",
                "stage_source": "TEXT NOT NULL DEFAULT ''",
            }
        )
        for nullable in {
            "latency_ms",
            "advance_control_ms",
            "control_ms",
            "stage1_ms",
            "stage2_ms",
        }:
            columns[nullable] = "REAL"
        return columns

    def _ensure_columns(self, connection: sqlite3.Connection, table: str, desired: dict[str, str]) -> None:
        existing = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, definition in desired.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _migrate_legacy_results(self, connection: sqlite3.Connection) -> None:
        event_count = connection.execute("SELECT COUNT(*) AS count FROM detection_events").fetchone()["count"]
        if event_count:
            return

        rows = connection.execute("SELECT * FROM detection_results ORDER BY id ASC").fetchall()
        for row in rows:
            payload = _json_load_dict(row["raw_json"])
            if not payload:
                payload = {
                    "timestamp": row["created_at"],
                    "frame_id": row["frame_id"],
                    "bag_id": row["bag_id"],
                    "camera_id": row["camera_id"],
                    "camera_name": row["camera_name"],
                    "camera_backend": row["camera_backend"],
                    "plc_backend": row["plc_backend"],
                    "plc_message_id": row["plc_message_id"],
                    "plc_bag_id": row["plc_bag_id"],
                    "source_path": row["source_path"],
                    "status": row["status_code"],
                    "action": row["decision_action"],
                    "reason": row["decision_reason"],
                    "is_defect": bool(row["is_defect"]),
                    "finalized": bool(row["decision_finalized"]),
                    "timed_out": bool(row["timed_out"]),
                    "stale_frame_ignored": bool(row["stale_frame_ignored"]),
                    "plc_success": bool(row["plc_success"]),
                    "ack_attempts": row["ack_attempts"],
                    "ack_retry": bool(row["ack_retried"]),
                    "latency_ms": row["latency_ms"],
                    "advance_control_ms": row["advance_control_ms"],
                    "control_ms": row["control_ms"],
                    "stage1_ms": row["stage1_ms"],
                    "stage2_ms": row["stage2_ms"],
                    "burst_sync_valid": bool(row["burst_sync_valid"]),
                    "hardware_check_status": row["hardware_check_status"],
                    "boxes": _json_load_list(row["final_boxes"]),
                    "control_commands": _json_load_list(row["control_commands"]),
                    "execution_feedbacks": _json_load_list(row["execution_feedbacks"]),
                    "state_trace": _json_load_list(row["state_trace"]),
                }
            self._insert_payload(connection, payload, f"legacy:{row['id']}")

    def _reset_jsonl_offset_when_events_are_missing(self, connection: sqlite3.Connection) -> None:
        if self.result_jsonl is None:
            return
        path = self.result_jsonl
        if not path.exists():
            return
        resolved = str(path.resolve())
        imported = connection.execute(
            "SELECT COUNT(*) AS count FROM detection_events WHERE event_key LIKE ?",
            (f"{resolved}:%",),
        ).fetchone()["count"]
        if imported:
            return
        connection.execute("DELETE FROM import_state WHERE path = ?", (resolved,))

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

    def _values_from_payload(self, payload: dict[str, Any], fallback_key: str) -> dict[str, Any]:
        frame_id = str(payload.get("frame_id") or fallback_key)
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
        bag_present = _safe_bool(payload.get("bag_present"), bool(payload.get("presence_result")))
        presence_valid = _safe_bool(payload.get("presence_message_valid"), True)
        presence_timed_out = _safe_bool(payload.get("presence_timed_out"), False)

        return {
            "event_key": fallback_key,
            "frame_id": frame_id,
            "created_at": str(payload.get("timestamp") or ""),
            "bag_id": str(payload.get("bag_id") or ""),
            "camera_id": _safe_int(payload.get("camera_id")),
            "camera_name": str(payload.get("camera_name") or ""),
            "camera_backend": str(payload.get("camera_backend") or ""),
            "plc_backend": str(payload.get("plc_backend") or ""),
            "plc_message_id": str(payload.get("plc_message_id") or payload.get("presence_message_id") or ""),
            "plc_bag_id": str(payload.get("plc_bag_id") or ""),
            "source_path": str(payload.get("source_path") or ""),
            "status": _status_text(status_code),
            "status_code": status_code,
            "is_defect": int(bool(payload.get("is_defect"))),
            "repeated": int(bool(payload.get("repeated", False))),
            "plc_success": int(_safe_bool(payload.get("plc_success"), True)),
            "decision_action": str(payload.get("action") or ""),
            "decision_reason": str(payload.get("reason") or ""),
            "decision_finalized": int(_safe_bool(payload.get("finalized"), True)),
            "timed_out": int(_safe_bool(payload.get("timed_out"), False)),
            "stale_frame_ignored": int(_safe_bool(payload.get("stale_frame_ignored"), False)),
            "ack_attempts": ack_attempts,
            "ack_retried": int(ack_retried),
            "queue_delay_ms": _timing_from_payload(payload, "queue_delay_ms"),
            "presence_ms": _timing_from_payload(payload, "presence_ms"),
            "capture_ms": _timing_from_payload(payload, "capture_ms"),
            "bag_pairing_ms": _timing_from_payload(payload, "bag_pairing_ms"),
            "latency_ms": _timing_from_payload(payload, "latency_ms"),
            "bag_latency_ms": _timing_from_payload(payload, "bag_latency_ms"),
            "advance_control_ms": _timing_from_payload(payload, "advance_control_ms"),
            "control_ms": _timing_from_payload(payload, "control_ms"),
            "stage1_ms": _timing_from_payload(payload, "stage1_ms"),
            "stage2_ms": _timing_from_payload(payload, "stage2_ms"),
            "decision_ms": _timing_from_payload(payload, "decision_ms"),
            "correlation_ms": _timing_from_payload(payload, "correlation_ms"),
            "reorder_wait_ms": _timing_from_payload(payload, "reorder_wait_ms"),
            "burst_sync_valid": int(_safe_bool(payload.get("burst_sync_valid"), False)),
            "hardware_check_status": str(payload.get("hardware_check_status") or ""),
            "bag_present": int(bag_present),
            "presence_message_valid": int(presence_valid),
            "presence_timed_out": int(presence_timed_out),
            "stage_source": str(payload.get("stage_source") or ""),
            "final_boxes": _json_dump(boxes),
            "control_commands": _json_dump(commands),
            "execution_feedbacks": _json_dump(feedbacks),
            "state_trace": _json_dump(state_trace),
            "raw_json": _json_dump(payload),
            "imported_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _insert_payload(self, connection: sqlite3.Connection, payload: dict[str, Any], fallback_key: str) -> None:
        values = self._values_from_payload(payload, fallback_key)
        columns = tuple(values)
        placeholders = ",".join("?" for _ in columns)
        updates = ",".join(f"{column}=excluded.{column}" for column in columns if column != "event_key")
        connection.execute(
            f"""
            INSERT INTO detection_events({','.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(event_key) DO UPDATE SET {updates}
            """,
            tuple(values[column] for column in columns),
        )

        legacy_columns = tuple(column for column in self._legacy_columns() if column in values)
        legacy_placeholders = ",".join("?" for _ in legacy_columns)
        legacy_updates = ",".join(f"{column}=excluded.{column}" for column in legacy_columns if column != "frame_id")
        legacy_values = {
            column: (0 if values[column] is None and column.endswith("_ms") else values[column])
            for column in legacy_columns
        }
        connection.execute(
            f"""
            INSERT INTO detection_results({','.join(legacy_columns)})
            VALUES ({legacy_placeholders})
            ON CONFLICT(frame_id) DO UPDATE SET {legacy_updates}
            """,
            tuple(legacy_values[column] for column in legacy_columns),
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
                        self._insert_payload(connection, payload, f"{path}:{line_start}")
                        processed += 1
                    offset = line_end

            self._save_state(connection, path, offset, size)
        return processed

    def _event_rows(self, limit: int = 800) -> list[sqlite3.Row]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM detection_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return list(reversed(rows))

    def _event_item(self, row: sqlite3.Row) -> dict[str, Any]:
        raw_json = _json_load_dict(row["raw_json"])
        final_boxes = _json_load_list(row["final_boxes"])
        state_trace = _json_load_list(row["state_trace"])
        source_path = row["source_path"]
        status_code = str(row["status_code"] or "pending")
        signals = _fault_signals(row)
        return {
            "event_id": row["id"],
            "event_key": row["event_key"],
            "timestamp": row["created_at"],
            "frame_id": row["frame_id"],
            "bag_id": row["bag_id"],
            "camera_id": row["camera_id"],
            "side": _side_label(row["camera_id"]),
            "camera_name": row["camera_name"],
            "camera_backend": row["camera_backend"],
            "plc_backend": row["plc_backend"],
            "plc_message_id": row["plc_message_id"],
            "plc_bag_id": row["plc_bag_id"],
            "source_path": source_path,
            "image_url": f"/api/source-image-event/{row['id']}" if source_path else "",
            "status": row["status"],
            "status_code": status_code,
            "status_icon": STATUS_ICON.get(status_code, STATUS_ICON["pending"]),
            "status_family": _status_family(status_code, signals),
            "repeated": bool(row["repeated"]),
            "plc_success": bool(row["plc_success"]),
            "decision_action": row["decision_action"],
            "decision_action_text": _action_text(row["decision_action"]),
            "decision_reason": row["decision_reason"],
            "decision_reason_text": _reason_text(row["decision_reason"]),
            "decision_finalized": bool(row["decision_finalized"]),
            "timed_out": bool(row["timed_out"]),
            "stale_frame_ignored": bool(row["stale_frame_ignored"]),
            "ack_attempts": row["ack_attempts"],
            "ack_retried": bool(row["ack_retried"]),
            "fault_signals": signals,
            "queue_delay_ms": row["queue_delay_ms"],
            "presence_ms": row["presence_ms"],
            "capture_ms": row["capture_ms"],
            "bag_pairing_ms": row["bag_pairing_ms"],
            "latency_ms": row["latency_ms"],
            "bag_latency_ms": row["bag_latency_ms"],
            "advance_control_ms": row["advance_control_ms"],
            "control_ms": row["control_ms"],
            "stage1_ms": row["stage1_ms"],
            "stage2_ms": row["stage2_ms"],
            "decision_ms": row["decision_ms"],
            "correlation_ms": row["correlation_ms"],
            "reorder_wait_ms": row["reorder_wait_ms"],
            "burst_sync_valid": bool(row["burst_sync_valid"]),
            "hardware_check_status": row["hardware_check_status"],
            "bag_present": bool(row["bag_present"]),
            "presence_message_valid": bool(row["presence_message_valid"]),
            "presence_timed_out": bool(row["presence_timed_out"]),
            "stage_source": row["stage_source"],
            "boxes": final_boxes,
            "final_count": len(final_boxes),
            "control_commands": _json_load_list(row["control_commands"]),
            "execution_feedbacks": _json_load_list(row["execution_feedbacks"]),
            "state_trace": state_trace,
            "state_count": len(state_trace),
            "raw_json": raw_json,
            "raw_json_pretty": _compact_raw(raw_json),
        }

    def _inspection_groups(self, event_limit: int = 1600) -> list[list[dict[str, Any]]]:
        rows = self._event_rows(event_limit)
        groups: list[list[dict[str, Any]]] = []
        open_by_bag: dict[str, list[dict[str, Any]]] = {}

        for row in rows:
            event = self._event_item(row)
            bag_key = event["bag_id"] or event["frame_id"] or str(event["event_id"])
            current = open_by_bag.get(bag_key)
            current_finalized = bool(current and any(item["decision_finalized"] for item in current))

            if current is None or current_finalized:
                current = []
                groups.append(current)
                open_by_bag[bag_key] = current
            current.append(event)
        return groups

    def _inspection_item(self, events: list[dict[str, Any]], expected_camera_ids: set[int]) -> dict[str, Any]:
        focus = _choose_focus_event(events)
        signals = sorted({signal for event in events for signal in event["fault_signals"]})
        all_boxes = [box for event in events for box in event.get("boxes", []) if isinstance(box, dict)]
        counts: dict[str, int] = {}
        for box in all_boxes:
            label = _base_label(str(box.get("label") or "defect"))
            counts[label] = counts.get(label, 0) + 1

        latest_by_camera: dict[int, dict[str, Any]] = {}
        for event in events:
            if event.get("source_path"):
                latest_by_camera[int(event["camera_id"])] = event

        faces = []
        for camera_id in sorted(expected_camera_ids | set(latest_by_camera)):
            event = latest_by_camera.get(camera_id)
            if event is None:
                faces.append(
                    {
                        "camera_id": camera_id,
                        "side": _side_label(camera_id),
                        "camera_name": f"{_side_label(camera_id)} 面",
                        "status_code": "offline",
                        "status": "无数据",
                        "status_family": "neutral",
                        "status_icon": STATUS_ICON["offline"],
                        "image_url": "",
                        "boxes": [],
                        "final_count": 0,
                        "stage_source": "",
                        "timestamp": "",
                        "frame_id": "",
                    }
                )
                continue
            faces.append(event)

        status_code = str(focus.get("status_code") or "pending")
        if any(event["timed_out"] for event in events):
            status_code = "timeout"
        elif any(event["status_code"] == "defect" for event in events):
            status_code = "defect"
        elif any(event["status_code"] == "capture_invalid" for event in events):
            status_code = "capture_invalid"
        elif not focus.get("decision_finalized") and status_code not in {"no_bag", "captured"}:
            status_code = "pending"

        family = _status_family(status_code, signals)
        stages = _build_stages(events, focus, expected_camera_ids)
        sort = _sort_status(focus)
        bag_latency = focus.get("bag_latency_ms")
        if bag_latency is None:
            bag_latency = None

        return {
            "inspection_id": f"insp-{events[0]['event_id']:08d}",
            "event_ids": [event["event_id"] for event in events],
            "created_at": events[0]["timestamp"],
            "updated_at": events[-1]["timestamp"],
            "bag_id": focus.get("bag_id") or events[0].get("bag_id") or "--",
            "status_code": status_code,
            "status": _status_text(status_code),
            "status_icon": STATUS_ICON.get(status_code, STATUS_ICON["pending"]),
            "status_family": family,
            "finalized": any(event["decision_finalized"] for event in events),
            "decision_action": focus.get("decision_action") or "",
            "decision_action_text": _action_text(str(focus.get("decision_action") or "")),
            "decision_reason": focus.get("decision_reason") or "",
            "decision_reason_text": _reason_text(str(focus.get("decision_reason") or "")),
            "plc": sort,
            "bag_latency_ms": bag_latency,
            "event_latency_ms": focus.get("latency_ms"),
            "control_ms": focus.get("control_ms"),
            "defect_count": len(all_boxes),
            "defect_classes": counts,
            "faces": faces,
            "stages": stages,
            "signals": signals,
            "timings": {
                "queue_delay_ms": _max_timing(events, "queue_delay_ms"),
                "presence_ms": _max_timing(events, "presence_ms"),
                "capture_ms": _max_timing(events, "capture_ms"),
                "bag_pairing_ms": _max_timing(events, "bag_pairing_ms"),
                "stage1_ms": _max_timing(events, "stage1_ms"),
                "stage2_ms": _max_timing(events, "stage2_ms"),
                "decision_ms": _max_timing(events, "decision_ms"),
                "correlation_ms": _max_timing(events, "correlation_ms"),
                "reorder_wait_ms": _max_timing(events, "reorder_wait_ms"),
                "advance_control_ms": _sum_timing(events, "advance_control_ms"),
                "control_ms": focus.get("control_ms"),
                "bag_latency_ms": bag_latency,
            },
            "events": events,
        }

    def _camera_items(
        self,
        events: list[dict[str, Any]],
        camera_configs: Iterable[Any] | None,
    ) -> list[dict[str, Any]]:
        latest_by_camera: dict[int, dict[str, Any]] = {}
        for event in events:
            if event.get("camera_id"):
                latest_by_camera[int(event["camera_id"])] = event

        config_items = []
        for camera in camera_configs or []:
            config_items.append(
                {
                    "camera_id": int(getattr(camera, "camera_id", 0)),
                    "camera_name": str(getattr(camera, "name", "")),
                    "watch_dir": str(getattr(camera, "watch_dir", "")),
                }
            )
        if not config_items:
            config_items = [
                {"camera_id": camera_id, "camera_name": f"{_side_label(camera_id)}-camera", "watch_dir": ""}
                for camera_id in sorted(latest_by_camera)
            ]

        cameras = []
        for item in config_items:
            camera_id = item["camera_id"]
            event = latest_by_camera.get(camera_id)
            if event is None:
                cameras.append(
                    {
                        **item,
                        "side": _side_label(camera_id),
                        "status": "无数据",
                        "status_code": "offline",
                        "status_family": "neutral",
                        "status_icon": STATUS_ICON["offline"],
                        "last_seen": "",
                        "last_bag_id": "--",
                        "frame_id": "",
                        "image_url": "",
                        "final_count": 0,
                        "detail": "等待 JSONL 结果",
                    }
                )
                continue
            status_code = event["status_code"]
            family = "danger" if status_code == "capture_invalid" else ("warn" if event["fault_signals"] else "ok")
            cameras.append(
                {
                    **item,
                    "side": event["side"],
                    "status": "采图异常" if status_code == "capture_invalid" else "最近采图正常",
                    "status_code": status_code,
                    "status_family": family,
                    "status_icon": STATUS_ICON["capture_invalid"] if status_code == "capture_invalid" else STATUS_ICON["ok"],
                    "last_seen": event["timestamp"],
                    "last_bag_id": event["bag_id"],
                    "frame_id": event["frame_id"],
                    "image_url": event["image_url"],
                    "final_count": event["final_count"],
                    "detail": event["decision_reason_text"],
                }
            )
        return cameras

    def _metrics_from_inspections(self, inspections: list[dict[str, Any]], limit: int) -> dict[str, Any]:
        total = len(inspections)
        terminal = [item for item in inspections if item["status_code"] in {"ok", "defect"}]
        latency_values = [item["bag_latency_ms"] for item in inspections if item.get("bag_latency_ms") is not None]
        event_latency_values = [item["event_latency_ms"] for item in inspections if item.get("event_latency_ms") is not None]
        control_values = [item["control_ms"] for item in inspections if item.get("control_ms") is not None]
        fault_items = [
            item
            for item in inspections
            if item["signals"] or item["status_code"] in {"defect", "timeout", "capture_invalid"} or item["plc"]["family"] in {"warn", "danger"}
        ][:10]
        ok_count = sum(1 for item in inspections if item["status_code"] == "ok")
        ng_count = sum(1 for item in inspections if item["status_code"] == "defect")
        timeout_count = sum(1 for item in inspections if item["status_code"] == "timeout")
        retry_count = sum(1 for item in inspections if item["plc"]["retried"])
        return {
            "limit": limit,
            "total_inspections": total,
            "recent_count": total,
            "ok_count": ok_count,
            "ng_count": ng_count,
            "waiting_count": sum(1 for item in inspections if item["status_code"] in {"pending", "captured", "no_bag"}),
            "timeout_count": timeout_count,
            "defect_rate": round(ng_count / len(terminal), 4) if terminal else 0.0,
            "ack_retry_count": retry_count,
            "avg_detection_latency_ms": round(sum(latency_values) / len(latency_values), 1) if latency_values else None,
            "avg_event_latency_ms": round(sum(event_latency_values) / len(event_latency_values), 1) if event_latency_values else None,
            "avg_control_ms": round(sum(control_values) / len(control_values), 1) if control_values else None,
            "faults": [
                {
                    "inspection_id": item["inspection_id"],
                    "bag_id": item["bag_id"],
                    "timestamp": item["updated_at"],
                    "status": item["status"],
                    "status_family": item["status_family"],
                    "message": item["decision_reason_text"],
                    "signals": item["signals"],
                }
                for item in fault_items
            ],
            "status_counts": {
                "normal": ok_count,
                "defect": ng_count,
                "pending": sum(1 for item in inspections if item["status_code"] in {"pending", "captured"}),
                "timeout": timeout_count,
            },
            "total_events": sum(len(item["event_ids"]) for item in inspections),
            "defect_events": ng_count,
            "timeout_events": timeout_count,
            "ack_retry_events": retry_count,
            "ack_failure_events": sum(1 for item in inspections if not item["plc"]["success"]),
            "avg_latency_ms": round(sum(event_latency_values) / len(event_latency_values), 1) if event_latency_values else 0.0,
            "avg_ack_attempts": 0.0,
            "max_ack_attempts": max((item["plc"]["attempts"] for item in inspections), default=0),
            "fault_rows": fault_items,
        }

    def inspections(self, limit: int = 80, event_limit: int | None = None, camera_configs: Iterable[Any] | None = None) -> list[dict[str, Any]]:
        self.sync_from_jsonl()
        expected_camera_ids = {int(getattr(camera, "camera_id", 0)) for camera in camera_configs or [] if getattr(camera, "camera_id", 0)}
        if not expected_camera_ids:
            expected_camera_ids = {1, 2}
        groups = self._inspection_groups(event_limit or max(limit * 12, 400))
        inspections = [self._inspection_item(group, expected_camera_ids) for group in groups if group]
        inspections.sort(key=lambda item: max(item["event_ids"]), reverse=True)
        return inspections[:limit]

    def workbench(self, limit: int = 80, camera_configs: Iterable[Any] | None = None) -> dict[str, Any]:
        self.sync_from_jsonl()
        expected_camera_ids = {int(getattr(camera, "camera_id", 0)) for camera in camera_configs or [] if getattr(camera, "camera_id", 0)}
        if not expected_camera_ids:
            expected_camera_ids = {1, 2}
        event_limit = max(limit * 12, 400)
        groups = self._inspection_groups(event_limit)
        inspections = [self._inspection_item(group, expected_camera_ids) for group in groups if group]
        inspections.sort(key=lambda item: max(item["event_ids"]), reverse=True)
        inspections = inspections[:limit]
        event_items = [event for group in groups for event in group]
        return {
            "current": inspections[0] if inspections else None,
            "history": inspections,
            "cameras": self._camera_items(event_items, camera_configs),
            "metrics": self._metrics_from_inspections(inspections, limit),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def inspection_detail(self, inspection_id: str, camera_configs: Iterable[Any] | None = None) -> dict[str, Any] | None:
        self.sync_from_jsonl()
        expected_camera_ids = {int(getattr(camera, "camera_id", 0)) for camera in camera_configs or [] if getattr(camera, "camera_id", 0)}
        if not expected_camera_ids:
            expected_camera_ids = {1, 2}
        groups = self._inspection_groups(5000)
        for group in groups:
            item = self._inspection_item(group, expected_camera_ids)
            if item["inspection_id"] == inspection_id:
                return item
        return None

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        self.sync_from_jsonl()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM detection_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._event_item(row) for row in rows]

    def metrics(self, limit: int = 80) -> dict[str, Any]:
        items = self.inspections(limit)
        return self._metrics_from_inspections(items, limit)

    def source_path_for_event(self, event_id: int) -> str | None:
        self.sync_from_jsonl()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT source_path FROM detection_events WHERE id = ?",
                (event_id,),
            ).fetchone()
        return str(row["source_path"]) if row and row["source_path"] else None

    def source_path_for_frame(self, frame_id: str) -> str | None:
        self.sync_from_jsonl()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT source_path
                FROM detection_events
                WHERE frame_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (frame_id,),
            ).fetchone()
        return str(row["source_path"]) if row and row["source_path"] else None
