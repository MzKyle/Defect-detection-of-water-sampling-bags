from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4
import re


def now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def infer_bag_id(source_path: str) -> str:
    stem = Path(source_path).stem
    patterns = [
        r"^(?P<bag>.+?)(?:[_-]cam(?:era)?\d+)(?:[_-].*)?$",
        r"^(?P<bag>.+?)(?:[_-](?:front|back|a|b))(?:[_-].*)?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, stem, re.IGNORECASE)
        if match:
            return match.group("bag")
    return stem


def build_frame_packet(
    *,
    camera_id: int,
    camera_name: str,
    source_path: str,
    replayed: bool = False,
    bag_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    light_paths: dict[str, str] | None = None,
) -> "FramePacket":
    timestamp = now_iso()
    return FramePacket(
        frame_id=f"cam{camera_id}-{uuid4().hex[:12]}",
        bag_id=bag_id or infer_bag_id(source_path),
        camera_id=camera_id,
        camera_name=camera_name,
        source_path=source_path,
        source_name=Path(source_path).name,
        received_at=timestamp,
        enqueued_at=timestamp,
        replayed=replayed,
        metadata=metadata or {},
        light_paths=light_paths or {},
    )


@dataclass
class DetectionBox:
    x1: int
    y1: int
    x2: int
    y2: int
    label: str
    confidence: float
    visibility_assessment: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "x1": self.x1,
            "y1": self.y1,
            "x2": self.x2,
            "y2": self.y2,
            "label": self.label,
            "confidence": self.confidence,
        }
        if self.visibility_assessment is not None:
            payload["visibility_assessment"] = self.visibility_assessment
        return payload


class PipelineState(str, Enum):
    RECEIVED = "received"
    ENQUEUED = "enqueued"
    FILE_READY = "file_ready"
    MULTILIGHT_READY = "multilight_ready"
    BACKED_UP = "backed_up"
    STAGE1_RUNNING = "stage1_running"
    STAGE1_DONE = "stage1_done"
    STAGE2_RUNNING = "stage2_running"
    STAGE2_DONE = "stage2_done"
    VISIBILITY_ASSESSED = "visibility_assessed"
    DECISION_READY = "decision_ready"
    COMMAND_DISPATCHED = "command_dispatched"
    COMMAND_ACKED = "command_acked"
    PERSISTED = "persisted"
    FAILED = "failed"


@dataclass
class PipelineStateEvent:
    state: PipelineState
    timestamp: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "state": self.state.value,
            "timestamp": self.timestamp,
            "detail": self.detail,
        }


@dataclass
class FramePacket:
    frame_id: str
    bag_id: str
    camera_id: int
    camera_name: str
    source_path: str
    source_name: str
    received_at: str
    enqueued_at: str
    replayed: bool = False
    file_ready_at: str | None = None
    processing_started_at: str | None = None
    source_mtime_ns: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    light_paths: dict[str, str] = field(default_factory=dict)

    @property
    def is_multilight(self) -> bool:
        return bool(self.light_paths)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "bag_id": self.bag_id,
            "camera_id": self.camera_id,
            "camera_name": self.camera_name,
            "source_path": self.source_path,
            "source_name": self.source_name,
            "received_at": self.received_at,
            "enqueued_at": self.enqueued_at,
            "replayed": self.replayed,
            "file_ready_at": self.file_ready_at,
            "processing_started_at": self.processing_started_at,
            "source_mtime_ns": self.source_mtime_ns,
            "metadata": self.metadata,
            "light_paths": self.light_paths,
        }


@dataclass
class PerceptionResult:
    stage_name: str
    detector_backend: str
    boxes: list[DetectionBox]
    inference_ms: float
    triggered: bool

    @property
    def is_defect(self) -> bool:
        return bool(self.boxes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_name": self.stage_name,
            "detector_backend": self.detector_backend,
            "boxes": [box.to_dict() for box in self.boxes],
            "inference_ms": self.inference_ms,
            "triggered": self.triggered,
            "is_defect": self.is_defect,
        }


@dataclass
class DecisionResult:
    is_defect: bool
    repeated: bool
    should_run_stage2: bool
    finalized: bool
    timed_out: bool
    stage_source: str
    control_action: str
    reason: str
    final_boxes: list[DetectionBox]

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_defect": self.is_defect,
            "repeated": self.repeated,
            "should_run_stage2": self.should_run_stage2,
            "finalized": self.finalized,
            "timed_out": self.timed_out,
            "stage_source": self.stage_source,
            "control_action": self.control_action,
            "reason": self.reason,
            "final_boxes": [box.to_dict() for box in self.final_boxes],
        }


@dataclass
class ControlCommand:
    command_id: str
    frame_id: str
    bag_id: str
    target: str
    action: str
    created_at: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "frame_id": self.frame_id,
            "bag_id": self.bag_id,
            "target": self.target,
            "action": self.action,
            "created_at": self.created_at,
            "payload": self.payload,
        }


@dataclass
class ExecutionFeedback:
    command_id: str
    frame_id: str
    target: str
    action: str
    success: bool
    sent_at: str
    ack_at: str
    latency_ms: float
    detail: str
    attempts: int = 1
    timed_out: bool = False
    ack_timeout_ms: float = 0.0
    attempt_details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "frame_id": self.frame_id,
            "target": self.target,
            "action": self.action,
            "success": self.success,
            "sent_at": self.sent_at,
            "ack_at": self.ack_at,
            "latency_ms": self.latency_ms,
            "detail": self.detail,
            "attempts": self.attempts,
            "timed_out": self.timed_out,
            "ack_timeout_ms": self.ack_timeout_ms,
            "attempt_details": self.attempt_details,
        }


@dataclass
class CameraBagObservation:
    camera_id: int
    camera_name: str
    frame_id: str
    is_defect: bool
    repeated: bool
    stage_source: str
    final_box_count: int
    timestamp: str
    source_mtime_ns: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "camera_name": self.camera_name,
            "frame_id": self.frame_id,
            "is_defect": self.is_defect,
            "repeated": self.repeated,
            "stage_source": self.stage_source,
            "final_box_count": self.final_box_count,
            "timestamp": self.timestamp,
            "source_mtime_ns": self.source_mtime_ns,
        }


@dataclass
class BagSummary:
    bag_id: str
    correlation_key: str
    expected_camera_ids: list[int]
    observed_camera_ids: list[int]
    missing_camera_ids: list[int]
    observations: list[CameraBagObservation]
    complete: bool
    aggregate_defect: bool
    aggregate_repeated: bool
    aggregate_action: str
    aggregate_reason: str
    decision_finalized: bool
    timed_out: bool
    command_issued: bool
    new_command_required: bool
    stale_frame_ignored: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "bag_id": self.bag_id,
            "correlation_key": self.correlation_key,
            "expected_camera_ids": self.expected_camera_ids,
            "observed_camera_ids": self.observed_camera_ids,
            "missing_camera_ids": self.missing_camera_ids,
            "observations": [observation.to_dict() for observation in self.observations],
            "complete": self.complete,
            "aggregate_defect": self.aggregate_defect,
            "aggregate_repeated": self.aggregate_repeated,
            "aggregate_action": self.aggregate_action,
            "aggregate_reason": self.aggregate_reason,
            "decision_finalized": self.decision_finalized,
            "timed_out": self.timed_out,
            "command_issued": self.command_issued,
            "new_command_required": self.new_command_required,
            "stale_frame_ignored": self.stale_frame_ignored,
        }


@dataclass
class TimedOutBagContext:
    frame_packet: FramePacket
    stage1_result: PerceptionResult
    stage2_result: PerceptionResult
    bag_summary: BagSummary


@dataclass
class TimingBreakdown:
    queue_delay_ms: float = 0.0
    backup_ms: float = 0.0
    stage1_inference_ms: float = 0.0
    stage2_inference_ms: float = 0.0
    visibility_assessment_ms: float = 0.0
    decision_ms: float = 0.0
    correlation_ms: float = 0.0
    control_ms: float = 0.0
    persist_ms: float = 0.0
    total_ms: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "queue_delay_ms": self.queue_delay_ms,
            "backup_ms": self.backup_ms,
            "stage1_inference_ms": self.stage1_inference_ms,
            "stage2_inference_ms": self.stage2_inference_ms,
            "visibility_assessment_ms": self.visibility_assessment_ms,
            "decision_ms": self.decision_ms,
            "correlation_ms": self.correlation_ms,
            "control_ms": self.control_ms,
            "persist_ms": self.persist_ms,
            "total_ms": self.total_ms,
        }


@dataclass
class InspectionResult:
    frame_packet: FramePacket
    stage1_result: PerceptionResult
    stage2_result: PerceptionResult
    decision_result: DecisionResult
    bag_summary: BagSummary
    control_commands: list[ControlCommand]
    execution_feedbacks: list[ExecutionFeedback]
    timing_breakdown: TimingBreakdown
    state_trace: list[PipelineStateEvent]
    backup_path: str
    result_image_path: str
    image_base64: str

    @property
    def camera_id(self) -> int:
        return self.frame_packet.camera_id

    @property
    def camera_name(self) -> str:
        return self.frame_packet.camera_name

    @property
    def frame_id(self) -> str:
        return self.frame_packet.frame_id

    @property
    def bag_id(self) -> str:
        return self.frame_packet.bag_id

    @property
    def source_path(self) -> str:
        return self.frame_packet.source_path

    @property
    def stage1_boxes(self) -> list[dict[str, Any]]:
        return [box.to_dict() for box in self.stage1_result.boxes]

    @property
    def stage2_boxes(self) -> list[dict[str, Any]]:
        return [box.to_dict() for box in self.stage2_result.boxes]

    @property
    def final_boxes(self) -> list[dict[str, Any]]:
        return [box.to_dict() for box in self.decision_result.final_boxes]

    @property
    def visibility_assessments(self) -> list[dict[str, Any]]:
        return [
            box.visibility_assessment
            for box in self.decision_result.final_boxes
            if box.visibility_assessment is not None
        ]

    @property
    def is_defect(self) -> bool:
        return self.decision_result.is_defect

    @property
    def repeated(self) -> bool:
        return self.decision_result.repeated

    @property
    def plc_success(self) -> bool:
        return all(feedback.success for feedback in self.execution_feedbacks) if self.execution_feedbacks else True

    @property
    def latency_ms(self) -> float:
        return self.timing_breakdown.total_ms

    @property
    def timestamp(self) -> str:
        return self.frame_packet.received_at

    @property
    def status_text(self) -> str:
        if not self.decision_result.finalized:
            return "待确认"
        if self.decision_result.timed_out:
            return "超时"
        return "异常" if self.is_defect else "正常"

    @property
    def control_action(self) -> str:
        return self.decision_result.control_action

    @property
    def decision_reason(self) -> str:
        return self.decision_result.reason

    @property
    def timed_out(self) -> bool:
        return self.decision_result.timed_out

    @property
    def stale_frame_ignored(self) -> bool:
        return self.bag_summary.stale_frame_ignored

    @property
    def ack_attempts(self) -> int:
        return max((feedback.attempts for feedback in self.execution_feedbacks), default=0)

    @property
    def ack_retried(self) -> bool:
        return any(feedback.attempts > 1 for feedback in self.execution_feedbacks)

    @property
    def fault_signals(self) -> list[str]:
        signals: list[str] = []
        if self.timed_out:
            signals.append("timeout")
        if self.ack_retried:
            signals.append("ack_retry")
        if self.stale_frame_ignored:
            signals.append("stale_frame")
        if not self.plc_success:
            signals.append("plc_failure")
        return signals

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "bag_id": self.bag_id,
            "camera_id": self.camera_id,
            "camera_name": self.camera_name,
            "status": self.status_text,
            "repeated": self.repeated,
            "plc_success": self.plc_success,
            "control_action": self.control_action,
            "decision_reason": self.decision_reason,
            "decision_finalized": self.decision_result.finalized,
            "timed_out": self.timed_out,
            "observed_cameras": self.bag_summary.observed_camera_ids,
            "missing_cameras": self.bag_summary.missing_camera_ids,
            "stale_frame_ignored": self.stale_frame_ignored,
            "ack_attempts": self.ack_attempts,
            "ack_retried": self.ack_retried,
            "fault_signals": self.fault_signals,
            "latency_ms": round(self.latency_ms, 1),
            "stage1_count": len(self.stage1_result.boxes),
            "stage2_count": len(self.stage2_result.boxes),
            "visibility_assessments": self.visibility_assessments,
            "timing_breakdown": self.timing_breakdown.to_dict(),
            "result_image_path": self.result_image_path,
            "backup_path": self.backup_path,
            "light_paths": self.frame_packet.light_paths,
        }
