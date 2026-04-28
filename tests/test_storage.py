from waterbag_inspection.schemas import (
    BagSummary,
    CameraBagObservation,
    ControlCommand,
    DecisionResult,
    DetectionBox,
    ExecutionFeedback,
    FramePacket,
    InspectionResult,
    PerceptionResult,
    PipelineState,
    PipelineStateEvent,
    TimingBreakdown,
)
from waterbag_inspection.storage import SQLiteDetectionRepository


def test_sqlite_repository_persists_recent_records(tmp_path):
    repository = SQLiteDetectionRepository(str(tmp_path / "inspection.db"))
    result = InspectionResult(
        frame_packet=FramePacket(
            frame_id="frame-1",
            bag_id="bag-1",
            camera_id=1,
            camera_name="A面相机",
            source_path="/tmp/source.jpg",
            source_name="source.jpg",
            received_at="2026-04-28T10:00:00.000",
            enqueued_at="2026-04-28T10:00:00.000",
        ),
        stage1_result=PerceptionResult(
            stage_name="stage1",
            detector_backend="MockDetector",
            boxes=[DetectionBox(x1=1, y1=2, x2=3, y2=4, label="anomaly", confidence=0.9)],
            inference_ms=8.2,
            triggered=True,
        ),
        stage2_result=PerceptionResult(
            stage_name="stage2",
            detector_backend="MockDetector",
            boxes=[],
            inference_ms=0.0,
            triggered=False,
        ),
        decision_result=DecisionResult(
            is_defect=True,
            repeated=False,
            should_run_stage2=False,
            finalized=True,
            timed_out=False,
            stage_source="stage1",
            control_action="reject",
            reason="stage1_detected_defect",
            final_boxes=[DetectionBox(x1=1, y1=2, x2=3, y2=4, label="anomaly", confidence=0.9)],
        ),
        bag_summary=BagSummary(
            bag_id="bag-1",
            correlation_key="bag-1",
            expected_camera_ids=[1, 2],
            observed_camera_ids=[1],
            missing_camera_ids=[2],
            observations=[
                CameraBagObservation(
                    camera_id=1,
                    camera_name="A面相机",
                    frame_id="frame-1",
                    is_defect=True,
                    repeated=False,
                    stage_source="stage1",
                    final_box_count=1,
                    timestamp="2026-04-28T10:00:00.000",
                )
            ],
            complete=False,
            aggregate_defect=True,
            aggregate_repeated=False,
            aggregate_action="reject",
            aggregate_reason="aggregate_defect_detected",
            decision_finalized=True,
            timed_out=False,
            command_issued=True,
            new_command_required=True,
        ),
        control_commands=[
            ControlCommand(
                command_id="cmd-1",
                frame_id="frame-1",
                bag_id="bag-1",
                target="bag_controller",
                action="reject",
                created_at="2026-04-28T10:00:00.100",
            )
        ],
        execution_feedbacks=[
            ExecutionFeedback(
                command_id="cmd-1",
                frame_id="frame-1",
                target="bag_controller",
                action="reject",
                success=True,
                sent_at="2026-04-28T10:00:00.100",
                ack_at="2026-04-28T10:00:00.100",
                latency_ms=0.2,
                detail="mock_ack",
            )
        ],
        timing_breakdown=TimingBreakdown(total_ms=18.4),
        state_trace=[
            PipelineStateEvent(state=PipelineState.RECEIVED, timestamp="2026-04-28T10:00:00.000"),
            PipelineStateEvent(state=PipelineState.PERSISTED, timestamp="2026-04-28T10:00:00.120"),
        ],
        backup_path="/tmp/backup.jpg",
        result_image_path="/tmp/result.jpg",
        image_base64="",
    )

    repository.save(result)
    recent = repository.recent(limit=5)

    assert len(recent) == 1
    assert recent[0]["status"] == "异常"
    assert recent[0]["stage1_count"] == 1
    assert recent[0]["decision_action"] == "reject"
    assert recent[0]["bag_id"] == "bag-1"
    assert recent[0]["decision_finalized"] is True
    assert recent[0]["timed_out"] is False
