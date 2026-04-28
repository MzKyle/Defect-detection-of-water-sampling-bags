from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import CorrelationConfig
from .schemas import BagSummary, CameraBagObservation, DecisionResult, FramePacket, PerceptionResult, TimedOutBagContext


@dataclass
class _BagSession:
    bag_id: str
    correlation_key: str
    expected_camera_ids: list[int]
    observations: dict[int, CameraBagObservation] = field(default_factory=dict)
    command_issued: bool = False
    timed_out: bool = False
    final_action: str = "await_peer_camera"
    final_reason: str = "waiting_for_cameras"
    created_monotonic: float = field(default_factory=time.monotonic)
    last_updated_monotonic: float = field(default_factory=time.monotonic)
    last_frame_packet: FramePacket | None = None
    last_stage1_result: PerceptionResult | None = None
    last_stage2_result: PerceptionResult | None = None


class BagCorrelator:
    def __init__(self, config: CorrelationConfig):
        self.config = config
        self.sessions: dict[str, _BagSession] = {}

    def _cleanup_completed(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, session in self.sessions.items()
            if session.command_issued and (now - session.last_updated_monotonic) * 1000.0 >= self.config.finalized_retention_ms
        ]
        for key in expired:
            self.sessions.pop(key, None)

    def _build_summary(
        self,
        session: _BagSession,
        *,
        aggregate_action: str,
        aggregate_reason: str,
        decision_finalized: bool,
        new_command_required: bool,
        timed_out: bool,
        stale_frame_ignored: bool = False,
    ) -> BagSummary:
        observed_camera_ids = sorted(session.observations)
        missing_camera_ids = [camera_id for camera_id in session.expected_camera_ids if camera_id not in session.observations]
        return BagSummary(
            bag_id=session.bag_id,
            correlation_key=session.correlation_key,
            expected_camera_ids=session.expected_camera_ids,
            observed_camera_ids=observed_camera_ids,
            missing_camera_ids=missing_camera_ids,
            observations=[session.observations[camera_id] for camera_id in observed_camera_ids],
            complete=not missing_camera_ids,
            aggregate_defect=any(item.is_defect for item in session.observations.values()),
            aggregate_repeated=any(item.repeated for item in session.observations.values()),
            aggregate_action=aggregate_action,
            aggregate_reason=aggregate_reason,
            decision_finalized=decision_finalized,
            timed_out=timed_out,
            command_issued=session.command_issued,
            new_command_required=new_command_required,
            stale_frame_ignored=stale_frame_ignored,
        )

    @staticmethod
    def _is_stale_frame(existing: CameraBagObservation, frame_packet: FramePacket) -> bool:
        if existing.source_mtime_ns is not None and frame_packet.source_mtime_ns is not None:
            return frame_packet.source_mtime_ns < existing.source_mtime_ns
        return frame_packet.received_at < existing.timestamp

    def _build_timeout_reason(self, session: _BagSession) -> str:
        missing_camera_ids = [camera_id for camera_id in session.expected_camera_ids if camera_id not in session.observations]
        return f"peer_camera_timeout:{','.join(map(str, missing_camera_ids))}"

    def collect_timeouts(self) -> list[TimedOutBagContext]:
        if not self.config.enabled:
            return []

        self._cleanup_completed()
        now = time.monotonic()
        timed_out_contexts: list[TimedOutBagContext] = []

        for session in self.sessions.values():
            if session.command_issued or session.timed_out or not session.observations:
                continue
            if (now - session.last_updated_monotonic) * 1000.0 < self.config.pending_timeout_ms:
                continue
            if session.last_frame_packet is None or session.last_stage1_result is None or session.last_stage2_result is None:
                continue

            session.command_issued = True
            session.timed_out = True
            session.final_action = self.config.timeout_action
            session.final_reason = self._build_timeout_reason(session)
            session.last_updated_monotonic = now
            timed_out_contexts.append(
                TimedOutBagContext(
                    frame_packet=session.last_frame_packet,
                    stage1_result=session.last_stage1_result,
                    stage2_result=session.last_stage2_result,
                    bag_summary=self._build_summary(
                        session,
                        aggregate_action=session.final_action,
                        aggregate_reason=session.final_reason,
                        decision_finalized=True,
                        new_command_required=True,
                        timed_out=True,
                    ),
                )
            )

        return timed_out_contexts

    def correlate(
        self,
        frame_packet: FramePacket,
        decision: DecisionResult,
        stage1_result: PerceptionResult,
        stage2_result: PerceptionResult,
    ) -> BagSummary:
        if not self.config.enabled:
            observation = CameraBagObservation(
                camera_id=frame_packet.camera_id,
                camera_name=frame_packet.camera_name,
                frame_id=frame_packet.frame_id,
                is_defect=decision.is_defect,
                repeated=decision.repeated,
                stage_source=decision.stage_source,
                final_box_count=len(decision.final_boxes),
                timestamp=frame_packet.received_at,
                source_mtime_ns=frame_packet.source_mtime_ns,
            )
            return BagSummary(
                bag_id=frame_packet.bag_id,
                correlation_key=frame_packet.bag_id,
                expected_camera_ids=[frame_packet.camera_id],
                observed_camera_ids=[frame_packet.camera_id],
                missing_camera_ids=[],
                observations=[observation],
                complete=True,
                aggregate_defect=decision.is_defect,
                aggregate_repeated=decision.repeated,
                aggregate_action=decision.control_action,
                aggregate_reason=decision.reason,
                decision_finalized=True,
                timed_out=False,
                command_issued=True,
                new_command_required=True,
                stale_frame_ignored=False,
            )

        self._cleanup_completed()
        key = frame_packet.bag_id
        session = self.sessions.get(key)
        if session is None:
            session = _BagSession(
                bag_id=frame_packet.bag_id,
                correlation_key=key,
                expected_camera_ids=sorted(self.config.expected_camera_ids),
            )
            self.sessions[key] = session

        existing_observation = session.observations.get(frame_packet.camera_id)
        if existing_observation is not None and self._is_stale_frame(existing_observation, frame_packet):
            return self._build_summary(
                session,
                aggregate_action=session.final_action,
                aggregate_reason=f"{session.final_reason};stale_frame_ignored:camera{frame_packet.camera_id}",
                decision_finalized=session.command_issued or session.timed_out,
                new_command_required=False,
                timed_out=session.timed_out,
                stale_frame_ignored=True,
            )

        observation = CameraBagObservation(
            camera_id=frame_packet.camera_id,
            camera_name=frame_packet.camera_name,
            frame_id=frame_packet.frame_id,
            is_defect=decision.is_defect,
            repeated=decision.repeated,
            stage_source=decision.stage_source,
            final_box_count=len(decision.final_boxes),
            timestamp=frame_packet.received_at,
            source_mtime_ns=frame_packet.source_mtime_ns,
        )
        session.observations[frame_packet.camera_id] = observation
        session.last_frame_packet = frame_packet
        session.last_stage1_result = stage1_result
        session.last_stage2_result = stage2_result
        session.last_updated_monotonic = time.monotonic()

        any_defect = any(item.is_defect for item in session.observations.values())
        new_command_required = False

        if session.timed_out:
            return self._build_summary(
                session,
                aggregate_action=session.final_action,
                aggregate_reason=f"{session.final_reason};late_frame_after_timeout:camera{frame_packet.camera_id}",
                decision_finalized=True,
                new_command_required=False,
                timed_out=True,
            )

        if any_defect:
            aggregate_action = "reject"
            aggregate_reason = "aggregate_defect_detected"
            decision_finalized = True
            if (not session.command_issued) or session.final_action != aggregate_action:
                session.command_issued = True
                new_command_required = True
            session.final_action = aggregate_action
            session.final_reason = aggregate_reason
        elif not [camera_id for camera_id in session.expected_camera_ids if camera_id not in session.observations] or not self.config.hold_non_defect_until_complete:
            complete = not [camera_id for camera_id in session.expected_camera_ids if camera_id not in session.observations]
            aggregate_action = "accept"
            aggregate_reason = "all_cameras_passed" if complete else "single_camera_accept"
            decision_finalized = True
            if (not session.command_issued) or session.final_action != aggregate_action:
                session.command_issued = True
                new_command_required = True
            session.final_action = aggregate_action
            session.final_reason = aggregate_reason
        else:
            missing_camera_ids = [camera_id for camera_id in session.expected_camera_ids if camera_id not in session.observations]
            aggregate_action = "await_peer_camera"
            aggregate_reason = f"waiting_for_cameras:{','.join(map(str, missing_camera_ids))}"
            decision_finalized = False
            session.final_action = aggregate_action
            session.final_reason = aggregate_reason

        return self._build_summary(
            session,
            aggregate_action=aggregate_action,
            aggregate_reason=aggregate_reason,
            decision_finalized=decision_finalized,
            new_command_required=new_command_required,
            timed_out=False,
        )
