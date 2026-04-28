from __future__ import annotations

import logging
import time

from .config import PLCConfig
from .schemas import ControlCommand, ExecutionFeedback, now_iso


LOGGER = logging.getLogger(__name__)


class BasePLCController:
    def execute(self, command: ControlCommand) -> ExecutionFeedback:
        raise NotImplementedError


class DisabledPLCController(BasePLCController):
    def execute(self, command: ControlCommand) -> ExecutionFeedback:
        timestamp = now_iso()
        return ExecutionFeedback(
            command_id=command.command_id,
            frame_id=command.frame_id,
            target=command.target,
            action=command.action,
            success=True,
            sent_at=timestamp,
            ack_at=timestamp,
            latency_ms=0.0,
            detail="plc_disabled",
            attempts=0,
            timed_out=False,
            ack_timeout_ms=0.0,
            attempt_details=["plc_disabled"],
        )


class BasePLCTransport:
    def send_once(self, command: ControlCommand) -> ExecutionFeedback:
        raise NotImplementedError


class MockPLCTransport(BasePLCTransport):
    def __init__(self, config: PLCConfig):
        self.config = config
        self.attempt_counters: dict[tuple[str, str, str], int] = {}

    def send_once(self, command: ControlCommand) -> ExecutionFeedback:
        start = time.perf_counter()
        sent_at = now_iso()
        key = (command.bag_id, command.target, command.action)
        attempt_index = self.attempt_counters.get(key, 0) + 1
        self.attempt_counters[key] = attempt_index

        LOGGER.info(
            "Mock PLC -> frame=%s target=%s action=%s",
            command.frame_id,
            command.target,
            command.action,
        )
        if self.config.mock_ack_latency_ms > 0:
            time.sleep(self.config.mock_ack_latency_ms / 1000.0)
        success = attempt_index > self.config.mock_fail_first_attempts
        return ExecutionFeedback(
            command_id=command.command_id,
            frame_id=command.frame_id,
            target=command.target,
            action=command.action,
            success=success,
            sent_at=sent_at,
            ack_at=now_iso(),
            latency_ms=(time.perf_counter() - start) * 1000.0,
            detail="mock_ack" if success else "mock_fail_before_retry",
        )


class ModbusPLCTransport(BasePLCTransport):
    def __init__(self, config: PLCConfig):
        self.config = config
        self.client = None
        self._connect()

    def _connect(self) -> None:
        from pymodbus.client import ModbusSerialClient

        self.client = ModbusSerialClient(
            method="rtu",
            port=self.config.port,
            baudrate=self.config.baudrate,
            parity=self.config.parity,
            stopbits=self.config.stopbits,
            bytesize=self.config.bytesize,
            timeout=self.config.timeout,
        )
        if not self.client.connect():
            raise ConnectionError("Failed to connect to Modbus PLC.")

    def _execute_write(self, command: ControlCommand, register_name: str, value: int) -> ExecutionFeedback:
        start = time.perf_counter()
        sent_at = now_iso()
        address = self.config.registers[register_name]
        try:
            self.client.write_register(address, value)
            latency_ms = (time.perf_counter() - start) * 1000.0
            return ExecutionFeedback(
                command_id=command.command_id,
                frame_id=command.frame_id,
                target=command.target,
                action=command.action,
                success=True,
                sent_at=sent_at,
                ack_at=now_iso(),
                latency_ms=latency_ms,
                detail=f"register={address},value={value}",
            )
        except Exception as exc:
            LOGGER.exception("PLC write failed: %s", exc)
            self._connect()
            return ExecutionFeedback(
                command_id=command.command_id,
                frame_id=command.frame_id,
                target=command.target,
                action=command.action,
                success=False,
                sent_at=sent_at,
                ack_at=now_iso(),
                latency_ms=(time.perf_counter() - start) * 1000.0,
                detail=f"write_failed:{exc}",
            )

    def send_once(self, command: ControlCommand) -> ExecutionFeedback:
        if command.target == "bag_controller":
            action_value = 1 if command.action == "reject" else 2
            register_name = "bag" if "bag" in self.config.registers else "cam1"
            return self._execute_write(command, register_name, action_value)

        if command.target == "repeat_alert" and command.action == "pulse":
            start = time.perf_counter()
            high = self._execute_write(command, "alert", 1)
            time.sleep(self.config.alert_pulse_seconds)
            low = self._execute_write(command, "alert", 2)
            success = high.success and low.success
            return ExecutionFeedback(
                command_id=command.command_id,
                frame_id=command.frame_id,
                target=command.target,
                action=command.action,
                success=success,
                sent_at=high.sent_at,
                ack_at=low.ack_at,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                detail=f"pulse_high={high.success},pulse_low={low.success}",
            )

        raise ValueError(f"Unsupported control command: {command.target}:{command.action}")


class ReliablePLCController(BasePLCController):
    def __init__(self, transport: BasePLCTransport, config: PLCConfig):
        self.transport = transport
        self.config = config

    def execute(self, command: ControlCommand) -> ExecutionFeedback:
        start = time.perf_counter()
        sent_at = now_iso()
        last_feedback: ExecutionFeedback | None = None
        attempt_details: list[str] = []
        max_attempts = max(1, self.config.max_retries + 1)

        for attempt in range(1, max_attempts + 1):
            feedback = self.transport.send_once(command)
            timed_out = feedback.latency_ms > self.config.ack_timeout_ms
            attempt_detail = f"attempt={attempt};success={feedback.success};timed_out={timed_out};detail={feedback.detail}"
            attempt_details.append(attempt_detail)
            last_feedback = feedback
            if feedback.success and not timed_out:
                return ExecutionFeedback(
                    command_id=feedback.command_id,
                    frame_id=feedback.frame_id,
                    target=feedback.target,
                    action=feedback.action,
                    success=True,
                    sent_at=sent_at,
                    ack_at=feedback.ack_at,
                    latency_ms=(time.perf_counter() - start) * 1000.0,
                    detail="ack_received",
                    attempts=attempt,
                    timed_out=False,
                    ack_timeout_ms=self.config.ack_timeout_ms,
                    attempt_details=attempt_details,
                )

            if attempt < max_attempts and self.config.retry_interval_ms > 0:
                time.sleep(self.config.retry_interval_ms / 1000.0)

        if last_feedback is None:
            raise RuntimeError("PLC transport produced no feedback.")

        return ExecutionFeedback(
            command_id=last_feedback.command_id,
            frame_id=last_feedback.frame_id,
            target=last_feedback.target,
            action=last_feedback.action,
            success=False,
            sent_at=sent_at,
            ack_at=last_feedback.ack_at,
            latency_ms=(time.perf_counter() - start) * 1000.0,
            detail="ack_timeout_or_retry_exhausted",
            attempts=max_attempts,
            timed_out=last_feedback.latency_ms > self.config.ack_timeout_ms,
            ack_timeout_ms=self.config.ack_timeout_ms,
            attempt_details=attempt_details,
        )


def build_plc_controller(config: PLCConfig) -> BasePLCController:
    if not config.enabled:
        return DisabledPLCController()
    if config.backend == "mock":
        return ReliablePLCController(MockPLCTransport(config), config)
    return ReliablePLCController(ModbusPLCTransport(config), config)
