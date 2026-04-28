from waterbag_inspection.config import PLCConfig
from waterbag_inspection.plc import build_plc_controller
from waterbag_inspection.schemas import ControlCommand


def test_mock_plc_retries_and_eventually_succeeds():
    controller = build_plc_controller(
        PLCConfig(
            backend="mock",
            enabled=True,
            ack_timeout_ms=100,
            max_retries=1,
            retry_interval_ms=0,
            mock_ack_latency_ms=0,
            mock_fail_first_attempts=1,
        )
    )
    command = ControlCommand(
        command_id="cmd-1",
        frame_id="frame-1",
        bag_id="bag-1",
        target="bag_controller",
        action="reject",
        created_at="2026-04-28T10:00:00.000",
    )

    feedback = controller.execute(command)

    assert feedback.success is True
    assert feedback.attempts == 2
    assert feedback.timed_out is False
    assert len(feedback.attempt_details) == 2


def test_mock_plc_marks_timeout_after_retry_exhausted():
    controller = build_plc_controller(
        PLCConfig(
            backend="mock",
            enabled=True,
            ack_timeout_ms=5,
            max_retries=1,
            retry_interval_ms=0,
            mock_ack_latency_ms=20,
            mock_fail_first_attempts=0,
        )
    )
    command = ControlCommand(
        command_id="cmd-2",
        frame_id="frame-2",
        bag_id="bag-2",
        target="bag_controller",
        action="accept",
        created_at="2026-04-28T10:00:00.000",
    )

    feedback = controller.execute(command)

    assert feedback.success is False
    assert feedback.attempts == 2
    assert feedback.timed_out is True
    assert feedback.detail == "ack_timeout_or_retry_exhausted"
