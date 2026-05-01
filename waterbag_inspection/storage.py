from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .schemas import InspectionResult


class SQLiteDetectionRepository:
    def __init__(self, sqlite_path: str):
        self.sqlite_path = Path(sqlite_path)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.sqlite_path)

    def _ensure_columns(self, connection: sqlite3.Connection) -> None:
        existing = {
            row[1]
            for row in connection.execute("PRAGMA table_info(detection_results)").fetchall()
        }
        desired = {
            "frame_id": "TEXT",
            "bag_id": "TEXT",
            "enqueued_at": "TEXT",
            "decision_action": "TEXT",
            "decision_reason": "TEXT",
            "decision_finalized": "INTEGER",
            "bag_summary": "TEXT",
            "control_commands": "TEXT",
            "execution_feedbacks": "TEXT",
            "state_trace": "TEXT",
            "timing_breakdown": "TEXT",
        }
        for name, column_type in desired.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE detection_results ADD COLUMN {name} {column_type}")

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS detection_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    frame_id TEXT,
                    bag_id TEXT,
                    enqueued_at TEXT,
                    camera_id INTEGER NOT NULL,
                    camera_name TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    backup_path TEXT NOT NULL,
                    result_image_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    is_defect INTEGER NOT NULL,
                    repeated INTEGER NOT NULL,
                    plc_success INTEGER NOT NULL,
                    decision_action TEXT,
                    decision_reason TEXT,
                    decision_finalized INTEGER,
                    bag_summary TEXT,
                    stage1_boxes TEXT NOT NULL,
                    stage2_boxes TEXT NOT NULL,
                    final_boxes TEXT NOT NULL,
                    control_commands TEXT,
                    execution_feedbacks TEXT,
                    state_trace TEXT,
                    timing_breakdown TEXT,
                    latency_ms REAL NOT NULL
                )
                """
            )
            self._ensure_columns(connection)

    def save(self, result: InspectionResult) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO detection_results (
                    created_at,
                    frame_id,
                    bag_id,
                    enqueued_at,
                    camera_id,
                    camera_name,
                    source_path,
                    backup_path,
                    result_image_path,
                    status,
                    is_defect,
                    repeated,
                    plc_success,
                    decision_action,
                    decision_reason,
                    decision_finalized,
                    bag_summary,
                    stage1_boxes,
                    stage2_boxes,
                    final_boxes,
                    control_commands,
                    execution_feedbacks,
                    state_trace,
                    timing_breakdown,
                    latency_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.timestamp,
                    result.frame_id,
                    result.bag_id,
                    result.frame_packet.enqueued_at,
                    result.camera_id,
                    result.camera_name,
                    result.source_path,
                    result.backup_path,
                    result.result_image_path,
                    result.status_text,
                    int(result.is_defect),
                    int(result.repeated),
                    int(result.plc_success),
                    result.control_action,
                    result.decision_reason,
                    int(result.decision_result.finalized),
                    json.dumps(result.bag_summary.to_dict(), ensure_ascii=False),
                    json.dumps(result.stage1_boxes, ensure_ascii=False),
                    json.dumps(result.stage2_boxes, ensure_ascii=False),
                    json.dumps(result.final_boxes, ensure_ascii=False),
                    json.dumps([command.to_dict() for command in result.control_commands], ensure_ascii=False),
                    json.dumps([feedback.to_dict() for feedback in result.execution_feedbacks], ensure_ascii=False),
                    json.dumps([event.to_dict() for event in result.state_trace], ensure_ascii=False),
                    json.dumps(result.timing_breakdown.to_dict(), ensure_ascii=False),
                    result.latency_ms,
                ),
            )
            return int(cursor.lastrowid)

    def update_result_metrics(self, record_id: int, result: InspectionResult) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE detection_results
                SET state_trace = ?, timing_breakdown = ?, latency_ms = ?, plc_success = ?, execution_feedbacks = ?, control_commands = ?, bag_summary = ?, decision_finalized = ?
                WHERE id = ?
                """,
                (
                    json.dumps([event.to_dict() for event in result.state_trace], ensure_ascii=False),
                    json.dumps(result.timing_breakdown.to_dict(), ensure_ascii=False),
                    result.latency_ms,
                    int(result.plc_success),
                    json.dumps([feedback.to_dict() for feedback in result.execution_feedbacks], ensure_ascii=False),
                    json.dumps([command.to_dict() for command in result.control_commands], ensure_ascii=False),
                    json.dumps(result.bag_summary.to_dict(), ensure_ascii=False),
                    int(result.decision_result.finalized),
                    record_id,
                ),
            )

    def recent(self, limit: int = 20) -> list[dict[str, object]]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT created_at, frame_id, bag_id, camera_id, camera_name, status, repeated,
                       plc_success, decision_action, decision_reason, decision_finalized, latency_ms,
                       stage1_boxes, stage2_boxes, final_boxes, result_image_path, timing_breakdown, state_trace,
                       bag_summary, execution_feedbacks
                FROM detection_results
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        items = []
        for row in rows:
            stage1_boxes = json.loads(row["stage1_boxes"])
            stage2_boxes = json.loads(row["stage2_boxes"])
            final_boxes = json.loads(row["final_boxes"])
            visibility_assessments = [
                box.get("visibility_assessment")
                for box in final_boxes
                if isinstance(box, dict) and box.get("visibility_assessment")
            ]
            timing_breakdown = json.loads(row["timing_breakdown"] or "{}")
            state_trace = json.loads(row["state_trace"] or "[]")
            bag_summary = json.loads(row["bag_summary"] or "{}") if "bag_summary" in row.keys() else {}
            execution_feedbacks = json.loads(row["execution_feedbacks"] or "[]")
            ack_attempts = max((int(item.get("attempts", 1)) for item in execution_feedbacks), default=0)
            ack_retried = any(int(item.get("attempts", 1)) > 1 for item in execution_feedbacks)
            fault_signals = []
            if bag_summary.get("timed_out", False):
                fault_signals.append("timeout")
            if ack_retried:
                fault_signals.append("ack_retry")
            if bag_summary.get("stale_frame_ignored", False):
                fault_signals.append("stale_frame")
            if not bool(row["plc_success"]):
                fault_signals.append("plc_failure")
            items.append(
                {
                    "timestamp": row["created_at"],
                    "frame_id": row["frame_id"],
                    "bag_id": row["bag_id"],
                    "camera_id": row["camera_id"],
                    "camera_name": row["camera_name"],
                    "status": row["status"],
                    "repeated": bool(row["repeated"]),
                    "plc_success": bool(row["plc_success"]),
                    "decision_action": row["decision_action"],
                    "decision_reason": row["decision_reason"],
                    "decision_finalized": bool(row["decision_finalized"]) if row["decision_finalized"] is not None else True,
                    "timed_out": bool(bag_summary.get("timed_out", False)),
                    "stale_frame_ignored": bool(bag_summary.get("stale_frame_ignored", False)),
                    "ack_attempts": ack_attempts,
                    "ack_retried": ack_retried,
                    "fault_signals": fault_signals,
                    "latency_ms": row["latency_ms"],
                    "queue_delay_ms": timing_breakdown.get("queue_delay_ms", 0.0),
                    "correlation_ms": timing_breakdown.get("correlation_ms", 0.0),
                    "control_ms": timing_breakdown.get("control_ms", 0.0),
                    "stage1_count": len(stage1_boxes),
                    "stage2_count": len(stage2_boxes),
                    "final_count": len(final_boxes),
                    "visibility_assessment_count": len(visibility_assessments),
                    "visibility_recommendations": [
                        item.get("assessment", {}).get("recommended_action")
                        for item in visibility_assessments
                    ],
                    "state_count": len(state_trace),
                    "result_image_path": row["result_image_path"],
                }
            )
        return items

    def metrics(self, limit: int = 40) -> dict[str, object]:
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

        status_counts = {"normal": 0, "defect": 0, "pending": 0, "timeout": 0}
        for row in rows:
            status = row["status"]
            if status == "正常":
                status_counts["normal"] += 1
            elif status == "异常":
                status_counts["defect"] += 1
            elif status == "待确认":
                status_counts["pending"] += 1
            elif status == "超时":
                status_counts["timeout"] += 1

        ack_attempt_rows = [row["ack_attempts"] for row in rows if row["ack_attempts"] > 0]
        fault_rows = [
            row
            for row in rows
            if row["timed_out"] or row["ack_retried"] or row["stale_frame_ignored"] or not row["plc_success"]
        ][:8]

        return {
            "limit": limit,
            "total_events": total_events,
            "status_counts": status_counts,
            "defect_events": sum(1 for row in rows if row["status"] == "异常"),
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
