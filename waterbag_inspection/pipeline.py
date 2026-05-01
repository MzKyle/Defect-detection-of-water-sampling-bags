from __future__ import annotations

import os
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Mapping

import cv2
import numpy as np

from .artifacts import ArtifactWriter
from .config import (
    CameraConfig,
    CorrelationConfig,
    PatchConfig,
    RepeatConfig,
    RuntimeConfig,
)
from .correlation import BagCorrelator
from .detectors import BaseDetector, image_to_base64
from .plc import BasePLCController
from .policy import DefaultDecisionPolicy
from .repeater import RepeatDefectTracker
from .schemas import (
    FramePacket,
    InspectionResult,
    PerceptionResult,
    PipelineState,
    PipelineStateEvent,
    TimedOutBagContext,
    TimingBreakdown,
    build_frame_packet,
    now_iso,
)
from .storage import SQLiteDetectionRepository
from .visibility_matrix import (
    assess_best_visibility_evidence,
    estimate_visibility_evidence,
    load_visibility_matrix,
)


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def _iso_delta_ms(start_iso: str, end_iso: str | None) -> float:
    if end_iso is None:
        return 0.0
    started = datetime.fromisoformat(start_iso)
    ended = datetime.fromisoformat(end_iso)
    return (ended - started).total_seconds() * 1000.0


class InspectionPipeline:
    def __init__(
        self,
        runtime: RuntimeConfig,
        patch_config: PatchConfig,
        correlation_config,
        repeat_config: RepeatConfig,
        repository: SQLiteDetectionRepository,
        plc_controller: BasePLCController,
        primary_detector: BaseDetector,
        patch_detector: BaseDetector,
        multilight_config=None,
        artifact_writer: ArtifactWriter | None = None,
    ):
        self.runtime = runtime
        self.patch_config = patch_config
        self.correlation_config = correlation_config
        self.repeat_config = repeat_config
        self.repository = repository
        self.plc_controller = plc_controller
        self.primary_detector = primary_detector
        self.patch_detector = patch_detector
        self.artifact_writer = artifact_writer or ArtifactWriter(
            enabled=getattr(runtime, "async_artifact_writes", False),
            max_queue_size=getattr(runtime, "artifact_queue_size", 128),
            drop_when_full=getattr(runtime, "artifact_drop_when_full", True),
        )
        self.multilight_config = multilight_config
        self.visibility_matrix = None
        if (
            multilight_config is not None
            and getattr(multilight_config, "visibility_assessment_enabled", False)
        ):
            self.visibility_matrix = load_visibility_matrix(
                getattr(multilight_config, "visibility_matrix_path", None)
            )
        self.decision_policy = DefaultDecisionPolicy()
        self.bag_correlator = BagCorrelator(correlation_config)
        self.repeat_tracker = (
            RepeatDefectTracker(
                history_path=repeat_config.history_path,
                iou_threshold=repeat_config.iou_threshold,
                max_entries_per_camera=repeat_config.max_entries_per_camera,
                namespace=repeat_config.history_namespace,
            )
            if repeat_config.enabled
            else None
        )

        Path(self.runtime.backup_dir).mkdir(parents=True, exist_ok=True)
        Path(self.runtime.result_dir).mkdir(parents=True, exist_ok=True)
        Path(self.runtime.upload_dir).mkdir(parents=True, exist_ok=True)
        if self.patch_config.save_visualizations:
            Path(self.patch_config.visualization_dir).mkdir(parents=True, exist_ok=True)

    def close(self) -> bool:
        return self.artifact_writer.close(
            timeout=getattr(self.runtime, "artifact_flush_timeout_seconds", 2.0)
        )

    def _mark_state(
        self,
        trace: list[PipelineStateEvent],
        state: PipelineState,
        detail: str = "",
    ) -> None:
        trace.append(PipelineStateEvent(state=state, timestamp=now_iso(), detail=detail))

    def process_image(self, camera: CameraConfig, image_path: str) -> InspectionResult:
        source_path = str(Path(image_path).resolve())
        packet = build_frame_packet(
            camera_id=camera.camera_id,
            camera_name=camera.name,
            source_path=source_path,
            metadata={"source": "manual", "repeat_scope": "manual"},
        )
        packet.file_ready_at = now_iso()
        packet.processing_started_at = now_iso()
        if Path(source_path).exists():
            packet.source_mtime_ns = os.stat(source_path).st_mtime_ns
        return self.process_packet(packet)

    def process_multilight_images(
        self,
        camera: CameraConfig,
        light_paths: Mapping[str, str],
        *,
        bag_id: str | None = None,
        source_path: str | None = None,
        metadata: dict | None = None,
    ) -> InspectionResult:
        resolved_light_paths = {
            light_name: str(Path(path).resolve())
            for light_name, path in light_paths.items()
        }
        primary_source = source_path or next(iter(resolved_light_paths.values()))
        packet = build_frame_packet(
            camera_id=camera.camera_id,
            camera_name=camera.name,
            source_path=str(Path(primary_source).resolve()),
            bag_id=bag_id,
            metadata={
                "source": "manual_multilight",
                "repeat_scope": "manual_multilight",
                **(metadata or {}),
            },
            light_paths=resolved_light_paths,
        )
        packet.file_ready_at = now_iso()
        packet.processing_started_at = now_iso()
        existing_paths = [
            Path(path)
            for path in resolved_light_paths.values()
            if Path(path).exists()
        ]
        if existing_paths:
            packet.source_mtime_ns = max(path.stat().st_mtime_ns for path in existing_paths)
        return self.process_packet(packet)

    def _repeat_scope_for_packet(self, frame_packet: FramePacket) -> str | None:
        if not self.repeat_config.isolate_by_source:
            return None
        return str(
            frame_packet.metadata.get("repeat_scope")
            or frame_packet.metadata.get("source")
            or "default"
        )

    def _execute_commands(self, trace: list[PipelineStateEvent], commands: list) -> tuple[list, float]:
        control_started = time.perf_counter()
        execution_feedbacks = []
        for command in commands:
            self._mark_state(
                trace,
                PipelineState.COMMAND_DISPATCHED,
                f"{command.target}:{command.action}",
            )
            execution_feedbacks.append(self.plc_controller.execute(command))
        control_ms = _elapsed_ms(control_started)
        self._mark_state(
            trace,
            PipelineState.COMMAND_ACKED,
            f"success={all(feedback.success for feedback in execution_feedbacks)}",
        )
        return execution_feedbacks, control_ms

    def _persist_result(self, started: float, result: InspectionResult) -> InspectionResult:
        persist_started = time.perf_counter()
        self._mark_state(result.state_trace, PipelineState.PERSISTED, "repository_write")
        record_id = self.repository.save(result)
        result.timing_breakdown.persist_ms = _elapsed_ms(persist_started)
        result.timing_breakdown.total_ms = _elapsed_ms(started)
        self.repository.update_result_metrics(record_id, result)
        return result

    def _load_emit_image(self, source_path: str) -> np.ndarray:
        image = cv2.imread(source_path)
        if image is not None:
            return image
        return np.full((480, 640, 3), 245, dtype=np.uint8)

    def _backup_multilight_sources(
        self,
        frame_packet: FramePacket,
        backup_name: str,
    ) -> tuple[str, float]:
        backup_started = time.perf_counter()
        backup_root = Path(self.runtime.backup_dir) / f"{Path(backup_name).stem}_multilight"
        backup_root.mkdir(parents=True, exist_ok=True)
        for light_name, path in frame_packet.light_paths.items():
            source = Path(path)
            self.artifact_writer.copy_file(source, backup_root / f"{light_name}{source.suffix}")
        source_path = Path(frame_packet.source_path)
        if source_path.exists() and source_path.suffix.lower() == ".json":
            self.artifact_writer.copy_file(source_path, backup_root / "manifest.json")
        return str(backup_root.resolve()), _elapsed_ms(backup_started)

    def _assess_visibility_shadow(
        self,
        frame_packet: FramePacket,
        boxes: list,
    ) -> float:
        if self.visibility_matrix is None or not frame_packet.is_multilight or not boxes:
            return 0.0
        started = time.perf_counter()
        mode = getattr(self.multilight_config, "visibility_assessment_mode", "shadow")
        light_order = getattr(self.multilight_config, "light_order", self.visibility_matrix.light_order)
        for box in boxes:
            evidence = estimate_visibility_evidence(
                frame_packet.light_paths,
                box,
                light_order,
            )
            assessment = assess_best_visibility_evidence(
                self.visibility_matrix,
                box.label,
                evidence.light_scores,
                consistency_score=evidence.consistency_score,
                morphology_cues=evidence.morphology_cues,
                model_confidence=box.confidence,
            )
            box.visibility_assessment = {
                "mode": mode,
                "applied_to_decision": False,
                "assessment": assessment.to_dict(),
                "evidence": evidence.to_dict(),
            }
        return _elapsed_ms(started)

    def _build_timeout_result(self, timeout_context: TimedOutBagContext) -> InspectionResult:
        started = time.perf_counter()
        source_path = timeout_context.frame_packet.source_path
        source = Path(source_path)
        timeout_packet = deepcopy(timeout_context.frame_packet)
        timeout_packet.frame_id = f"timeout-{timeout_context.frame_packet.frame_id}"
        timeout_packet.received_at = now_iso()
        timeout_packet.enqueued_at = timeout_packet.received_at
        timeout_packet.file_ready_at = timeout_packet.received_at
        timeout_packet.processing_started_at = timeout_packet.received_at
        timeout_packet.metadata = {
            **timeout_packet.metadata,
            "source": "timeout_flush",
            "timeout_origin_frame_id": timeout_context.frame_packet.frame_id,
        }

        timings = TimingBreakdown()
        trace: list[PipelineStateEvent] = []
        self._mark_state(trace, PipelineState.RECEIVED, f"timeout_for={timeout_context.frame_packet.frame_id}")
        self._mark_state(trace, PipelineState.ENQUEUED, f"bag_id={timeout_packet.bag_id}")
        self._mark_state(trace, PipelineState.FILE_READY, timeout_packet.source_path)

        backup_path = ""
        if source.exists():
            backup_name = f"timeout_cam{timeout_packet.camera_id}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{source.suffix}"
            backup_started = time.perf_counter()
            backup_target = Path(self.runtime.backup_dir) / backup_name
            self.artifact_writer.copy_file(source, backup_target)
            timings.backup_ms = _elapsed_ms(backup_started)
            backup_path = str(backup_target.resolve())
            self._mark_state(trace, PipelineState.BACKED_UP, backup_path)

        decision_started = time.perf_counter()
        decision_result = self.decision_policy.decide(
            timeout_packet,
            timeout_context.stage1_result,
            timeout_context.stage2_result,
            timeout_context.bag_summary.aggregate_repeated,
            bag_summary=timeout_context.bag_summary,
        )
        timings.decision_ms = _elapsed_ms(decision_started)
        control_commands = self.decision_policy.build_commands(timeout_packet, decision_result, bag_summary=timeout_context.bag_summary)
        self._mark_state(
            trace,
            PipelineState.DECISION_READY,
            f"action={decision_result.control_action};reason={decision_result.reason}",
        )
        execution_feedbacks, timings.control_ms = self._execute_commands(trace, control_commands)

        emit_image = self._load_emit_image(source_path)
        result_name = f"timeout_cam{timeout_packet.camera_id}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_result.jpg"
        result_image_path = Path(self.runtime.result_dir) / result_name
        self.artifact_writer.write_image(result_image_path, emit_image)

        result = InspectionResult(
            frame_packet=timeout_packet,
            stage1_result=timeout_context.stage1_result,
            stage2_result=timeout_context.stage2_result,
            decision_result=decision_result,
            bag_summary=timeout_context.bag_summary,
            control_commands=control_commands,
            execution_feedbacks=execution_feedbacks,
            timing_breakdown=timings,
            state_trace=trace,
            backup_path=backup_path,
            result_image_path=str(result_image_path.resolve()),
            image_base64=image_to_base64(emit_image),
        )
        return self._persist_result(started, result)

    def flush_timeouts(self) -> list[InspectionResult]:
        return [self._build_timeout_result(context) for context in self.bag_correlator.collect_timeouts()]

    def process_packet(self, frame_packet: FramePacket) -> InspectionResult:
        started = time.perf_counter()
        timings = TimingBreakdown()
        trace: list[PipelineStateEvent] = []
        try:
            if frame_packet.processing_started_at is None:
                frame_packet.processing_started_at = now_iso()
            if frame_packet.file_ready_at is None:
                frame_packet.file_ready_at = frame_packet.processing_started_at

            timings.queue_delay_ms = _iso_delta_ms(frame_packet.enqueued_at, frame_packet.processing_started_at)

            self._mark_state(trace, PipelineState.RECEIVED, f"frame_id={frame_packet.frame_id}")
            self._mark_state(trace, PipelineState.ENQUEUED, f"bag_id={frame_packet.bag_id}")
            self._mark_state(trace, PipelineState.FILE_READY, frame_packet.source_path)
            if frame_packet.is_multilight:
                self._mark_state(
                    trace,
                    PipelineState.MULTILIGHT_READY,
                    f"lights={','.join(frame_packet.light_paths)}",
                )

            source = Path(frame_packet.source_path)
            backup_name = f"cam{frame_packet.camera_id}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{source.suffix}"

            if frame_packet.is_multilight:
                backup_path, timings.backup_ms = self._backup_multilight_sources(frame_packet, backup_name)
                self._mark_state(trace, PipelineState.BACKED_UP, backup_path)
            else:
                backup_started = time.perf_counter()
                backup_path = Path(self.runtime.backup_dir) / backup_name
                self.artifact_writer.copy_file(source, backup_path)
                timings.backup_ms = _elapsed_ms(backup_started)
                self._mark_state(trace, PipelineState.BACKED_UP, str(backup_path.resolve()))

            self._mark_state(trace, PipelineState.STAGE1_RUNNING)
            stage1_started = time.perf_counter()
            if frame_packet.is_multilight:
                stage1_image, stage1_boxes = self.primary_detector.detect_multilight(frame_packet.light_paths)
            else:
                stage1_image, stage1_boxes = self.primary_detector.detect(str(source))
            timings.stage1_inference_ms = _elapsed_ms(stage1_started)
            stage1_result = PerceptionResult(
                stage_name="stage1",
                detector_backend=type(self.primary_detector).__name__,
                boxes=stage1_boxes,
                inference_ms=timings.stage1_inference_ms,
                triggered=True,
            )
            self._mark_state(trace, PipelineState.STAGE1_DONE, f"boxes={len(stage1_boxes)}")

            should_run_stage2 = (
                self.patch_config.enabled
                and not stage1_result.is_defect
                and not frame_packet.is_multilight
            )
            stage2_result = PerceptionResult(
                stage_name="stage2",
                detector_backend=type(self.patch_detector).__name__,
                boxes=[],
                inference_ms=0.0,
                triggered=should_run_stage2,
            )
            emit_image = stage1_image

            if should_run_stage2:
                self._mark_state(trace, PipelineState.STAGE2_RUNNING)
                stage2_started = time.perf_counter()
                stage2_image, stage2_boxes = self.patch_detector.detect_patches(str(source), self.patch_config)
                timings.stage2_inference_ms = _elapsed_ms(stage2_started)
                stage2_result = PerceptionResult(
                    stage_name="stage2",
                    detector_backend=type(self.patch_detector).__name__,
                    boxes=stage2_boxes,
                    inference_ms=timings.stage2_inference_ms,
                    triggered=True,
                )
                if stage2_boxes:
                    emit_image = stage2_image
                self._mark_state(trace, PipelineState.STAGE2_DONE, f"boxes={len(stage2_boxes)}")

            visibility_boxes = stage2_result.boxes or stage1_result.boxes
            timings.visibility_assessment_ms = self._assess_visibility_shadow(
                frame_packet,
                visibility_boxes,
            )
            if timings.visibility_assessment_ms > 0:
                self._mark_state(
                    trace,
                    PipelineState.VISIBILITY_ASSESSED,
                    f"boxes={len(visibility_boxes)};mode=shadow",
                )

            decision_started = time.perf_counter()
            boxes_for_repeat = stage2_result.boxes or stage1_result.boxes
            repeated = (
                self.repeat_tracker.is_repeated(
                    frame_packet.camera_id,
                    [box.to_dict() for box in boxes_for_repeat],
                    scope=self._repeat_scope_for_packet(frame_packet),
                )
                if self.repeat_tracker
                else False
            )
            timings.decision_ms = _elapsed_ms(decision_started)
            correlation_started = time.perf_counter()
            local_decision = self.decision_policy.decide(frame_packet, stage1_result, stage2_result, repeated)
            bag_summary = self.bag_correlator.correlate(frame_packet, local_decision, stage1_result, stage2_result)
            timings.correlation_ms = _elapsed_ms(correlation_started)
            decision_result = self.decision_policy.decide(
                frame_packet,
                stage1_result,
                stage2_result,
                bag_summary.aggregate_repeated,
                bag_summary=bag_summary,
            )
            control_commands = self.decision_policy.build_commands(frame_packet, decision_result, bag_summary=bag_summary)
            self._mark_state(
                trace,
                PipelineState.DECISION_READY,
                f"action={decision_result.control_action};reason={decision_result.reason}",
            )

            execution_feedbacks, timings.control_ms = self._execute_commands(trace, control_commands)

            backup_stem = Path(backup_path).stem
            result_image_path = Path(self.runtime.result_dir) / f"{backup_stem}_result.jpg"
            self.artifact_writer.write_image(result_image_path, emit_image)

            result = InspectionResult(
                frame_packet=frame_packet,
                stage1_result=stage1_result,
                stage2_result=stage2_result,
                decision_result=decision_result,
                bag_summary=bag_summary,
                control_commands=control_commands,
                execution_feedbacks=execution_feedbacks,
                timing_breakdown=timings,
                state_trace=trace,
                backup_path=str(Path(backup_path).resolve()),
                result_image_path=str(result_image_path.resolve()),
                image_base64=image_to_base64(emit_image),
            )
            return self._persist_result(started, result)
        except Exception as exc:
            self._mark_state(trace, PipelineState.FAILED, str(exc))
            raise
