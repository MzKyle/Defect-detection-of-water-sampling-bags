from __future__ import annotations

from uuid import uuid4

from .schemas import BagSummary, ControlCommand, DecisionResult, FramePacket, PerceptionResult, now_iso


class DefaultDecisionPolicy:
    def decide(
        self,
        frame_packet: FramePacket,
        stage1_result: PerceptionResult,
        stage2_result: PerceptionResult,
        repeated: bool,
        bag_summary: BagSummary | None = None,
    ) -> DecisionResult:
        if bag_summary is not None:
            stage_source = "aggregate"
            if stage1_result.is_defect:
                stage_source = "stage1"
            elif stage2_result.is_defect:
                stage_source = "stage2"
            return DecisionResult(
                is_defect=bag_summary.aggregate_defect,
                repeated=bag_summary.aggregate_repeated,
                should_run_stage2=stage2_result.triggered,
                finalized=bag_summary.decision_finalized,
                timed_out=bag_summary.timed_out,
                stage_source=stage_source,
                control_action=bag_summary.aggregate_action,
                reason=bag_summary.aggregate_reason,
                final_boxes=stage2_result.boxes or stage1_result.boxes,
            )

        should_run_stage2 = stage2_result.triggered
        if stage1_result.is_defect:
            return DecisionResult(
                is_defect=True,
                repeated=repeated,
                should_run_stage2=should_run_stage2,
                finalized=True,
                timed_out=False,
                stage_source="stage1",
                control_action="reject",
                reason="stage1_detected_defect",
                final_boxes=stage1_result.boxes,
            )

        if stage2_result.is_defect:
            return DecisionResult(
                is_defect=True,
                repeated=repeated,
                should_run_stage2=should_run_stage2,
                finalized=True,
                timed_out=False,
                stage_source="stage2",
                control_action="reject",
                reason="stage2_detected_micro_defect",
                final_boxes=stage2_result.boxes,
            )

        return DecisionResult(
            is_defect=False,
            repeated=repeated,
            should_run_stage2=should_run_stage2,
            finalized=True,
            timed_out=False,
            stage_source="none",
            control_action="accept",
            reason="no_defect_detected",
            final_boxes=[],
        )

    def build_commands(self, frame_packet: FramePacket, decision: DecisionResult, bag_summary: BagSummary | None = None) -> list[ControlCommand]:
        if not decision.finalized:
            return []
        if bag_summary is not None and not bag_summary.new_command_required:
            return []

        created_at = now_iso()
        commands = [
            ControlCommand(
                command_id=f"cmd-{uuid4().hex[:12]}",
                frame_id=frame_packet.frame_id,
                bag_id=frame_packet.bag_id,
                target="bag_controller",
                action=decision.control_action,
                created_at=created_at,
                payload={
                    "reason": decision.reason,
                    "stage_source": decision.stage_source,
                    "observed_camera_ids": bag_summary.observed_camera_ids if bag_summary else [frame_packet.camera_id],
                },
            )
        ]

        if decision.repeated:
            commands.append(
                ControlCommand(
                    command_id=f"cmd-{uuid4().hex[:12]}",
                    frame_id=frame_packet.frame_id,
                    bag_id=frame_packet.bag_id,
                    target="repeat_alert",
                    action="pulse",
                    created_at=created_at,
                    payload={
                        "reason": "repeat_defect_suspected_fixture_contamination",
                        "stage_source": decision.stage_source,
                    },
                )
            )

        return commands
