#include "detect_orchestrator/pipeline.hpp"

#include <algorithm>
#include <cctype>
#include <sstream>

namespace waterbag {
namespace {

std::string metadata_key(std::size_t index, const std::string& field) {
    return "burst.images." + std::to_string(index) + "." + field;
}

std::string metadata_value(const FramePacket& packet, const std::string& key, const std::string& fallback = "") {
    const auto found = packet.metadata.find(key);
    return found == packet.metadata.end() ? fallback : found->second;
}

std::optional<std::size_t> metadata_size(const FramePacket& packet, const std::string& key) {
    const auto found = packet.metadata.find(key);
    if (found == packet.metadata.end()) {
        return std::nullopt;
    }
    try {
        return static_cast<std::size_t>(std::stoull(found->second));
    } catch (...) {
        return std::nullopt;
    }
}

std::string side_label_for_camera(int camera_id) {
    return camera_id == 1 ? "A" : "B";
}

std::string encoder_position_for_packet(const FramePacket& packet) {
    const auto found = packet.metadata.find("encoder_position");
    if (found != packet.metadata.end()) {
        return found->second;
    }

    std::string digits;
    for (char ch : packet.bag_id) {
        if (std::isdigit(static_cast<unsigned char>(ch))) {
            digits.push_back(ch);
        }
    }
    return digits.empty() ? "0" : digits;
}

void filter_by_confidence(std::vector<DetectionBox>& boxes, double threshold) {
    boxes.erase(
        std::remove_if(boxes.begin(), boxes.end(), [threshold](const DetectionBox& box) {
            return box.confidence < threshold;
        }),
        boxes.end());
}

void attach_burst_metadata(FramePacket& packet, const CaptureGroup& group) {
    const auto side = side_label_for_camera(packet.camera_id);
    const auto encoder_position = encoder_position_for_packet(packet);

    packet.metadata["bag.id"] = packet.bag_id;
    packet.metadata["bag.id_source"] = "plc_or_filename";
    packet.metadata["side"] = side;
    packet.metadata["camera.id"] = std::to_string(packet.camera_id);
    packet.metadata["encoder_position"] = encoder_position;
    packet.metadata["burst.capture_session_id"] = group.capture_session_id;
    packet.metadata["burst.plan_id"] = group.burst_plan.plan_id;
    packet.metadata["burst.image_count"] = std::to_string(group.images.size());
    packet.metadata["burst.sync_valid"] = group.sync_valid ? "true" : "false";
    packet.metadata["burst.side_id"] = group.side_id;

    std::ostringstream lights;
    for (std::size_t i = 0; i < group.images.size(); ++i) {
        const auto& image = group.images[i];
        if (i > 0) {
            lights << ",";
        }
        lights << to_string(image.light_id);
        const auto frame_index = std::to_string(image.frame_index);
        const auto light_id = to_string(image.light_id);
        const auto trigger_hw_ns = image.exposure_start_hw.ns;
        const auto burst_frame_id = packet.frame_id + "-" + side + "-" + light_id;

        packet.metadata[metadata_key(i, "bag_id")] = packet.bag_id;
        packet.metadata[metadata_key(i, "side")] = side;
        packet.metadata[metadata_key(i, "light")] = light_id;
        packet.metadata[metadata_key(i, "camera_id")] = std::to_string(packet.camera_id);
        packet.metadata[metadata_key(i, "frame_id")] = burst_frame_id;
        packet.metadata[metadata_key(i, "trigger_hw_ns")] = std::to_string(trigger_hw_ns);
        packet.metadata[metadata_key(i, "encoder_position")] = encoder_position;
        packet.metadata[metadata_key(i, "frame_index")] = std::to_string(image.frame_index);
        packet.metadata[metadata_key(i, "light_id")] = light_id;
        packet.metadata[metadata_key(i, "path")] = image.image_path.string();
        packet.metadata[metadata_key(i, "camera_frame_id")] = std::to_string(image.camera_frame_id);
        packet.metadata[metadata_key(i, "exposure_start_hw_ns")] = std::to_string(image.exposure_start_hw.ns);
        packet.metadata[metadata_key(i, "exposure_end_hw_ns")] = std::to_string(image.exposure_end_hw.ns);
        packet.metadata[metadata_key(i, "host_received_hw_ns")] = std::to_string(image.host_received_hw.ns);
    }
    packet.metadata["burst.light_ids"] = lights.str();

    for (const auto& alignment : group.alignments) {
        for (std::size_t i = 0; i < group.images.size(); ++i) {
            if (group.images[i].frame_index != alignment.frame_index) {
                continue;
            }
            packet.metadata[metadata_key(i, "jitter_us")] = std::to_string(alignment.trigger_to_exposure_jitter_us);
            packet.metadata[metadata_key(i, "trigger_hw_ns")] = std::to_string(
                group.images[i].exposure_start_hw.ns - alignment.trigger_to_exposure_jitter_us * 1000LL);
            packet.metadata[metadata_key(i, "light_window_ok")] =
                alignment.light_on_before_exposure && alignment.light_off_after_exposure ? "true" : "false";
            packet.metadata[metadata_key(i, "within_jitter_tolerance")] =
                alignment.within_jitter_tolerance ? "true" : "false";
        }
    }
}

std::vector<FramePacket> build_burst_detection_packets(const FramePacket& packet) {
    const auto image_count = metadata_size(packet, "burst.image_count");
    if (!image_count || *image_count == 0) {
        return {packet};
    }

    std::vector<FramePacket> packets;
    packets.reserve(*image_count);
    for (std::size_t i = 0; i < *image_count; ++i) {
        const auto path = metadata_value(packet, metadata_key(i, "path"));
        if (path.empty()) {
            continue;
        }

        FramePacket burst_packet = packet;
        burst_packet.frame_id = metadata_value(
            packet,
            metadata_key(i, "frame_id"),
            packet.frame_id + "-burst" + metadata_value(packet, metadata_key(i, "frame_index"), std::to_string(i)));
        burst_packet.source_path = path;
        burst_packet.source_name = std::filesystem::path(path).filename().string();
        burst_packet.source = packet.source + ":burst_image";
        burst_packet.metadata["parent_frame_id"] = packet.frame_id;
        burst_packet.metadata["side"] = metadata_value(packet, metadata_key(i, "side"));
        burst_packet.metadata["burst.light_id"] = metadata_value(packet, metadata_key(i, "light_id"));
        burst_packet.metadata["burst.light"] = metadata_value(packet, metadata_key(i, "light"));
        burst_packet.metadata["burst.frame_index"] = metadata_value(packet, metadata_key(i, "frame_index"), std::to_string(i));
        burst_packet.metadata["burst.camera_frame_id"] = metadata_value(packet, metadata_key(i, "camera_frame_id"));
        burst_packet.metadata["trigger_hw_ns"] = metadata_value(packet, metadata_key(i, "trigger_hw_ns"));
        burst_packet.metadata["encoder_position"] = metadata_value(packet, metadata_key(i, "encoder_position"));
        burst_packet.metadata["burst.input_index"] = std::to_string(i);
        packets.push_back(std::move(burst_packet));
    }

    return packets.empty() ? std::vector<FramePacket>{packet} : packets;
}

PerceptionResult run_multi_light_detection(
    const std::vector<FramePacket>& packets,
    IDetector& detector,
    DetectionStage stage,
    double threshold,
    std::vector<std::string>& trace) {
    PerceptionResult fused;
    fused.stage_name = to_string(stage);
    fused.detector_backend = detector.backend_name();
    fused.triggered = true;

    const auto stage_name = to_string(stage);
    trace.push_back(stage_name + "_running:inputs=" + std::to_string(packets.size()));
    for (const auto& input : packets) {
        auto partial = detector.detect(input, stage);
        filter_by_confidence(partial.boxes, threshold);
        fused.inference_ms += partial.inference_ms;

        const auto light_id = metadata_value(input, "burst.light_id", "single_frame");
        trace.push_back(stage_name + "_light:" + light_id + ":boxes=" + std::to_string(partial.boxes.size()));
        for (auto box : partial.boxes) {
            if (light_id != "single_frame" && box.label.find('@') == std::string::npos) {
                box.label += "@" + light_id;
            }
            fused.boxes.push_back(std::move(box));
        }
    }

    if (packets.size() > 1) {
        fused.detector_backend += "+multi_light_fusion";
    }
    trace.push_back(stage_name + "_fused:boxes=" + std::to_string(fused.boxes.size()));
    return fused;
}

}  // namespace

InspectionPipeline::InspectionPipeline(
    DetectionConfig detection_config,
    CorrelationConfig correlation_config,
    std::shared_ptr<ICameraBurstCapture> burst_capture,
    std::shared_ptr<IPlcController> plc_controller,
    std::shared_ptr<IDetector> primary_detector,
    std::shared_ptr<IDetector> patch_detector)
    : detection_config_(detection_config),
      correlator_(std::move(correlation_config)),
      burst_plan_(make_production_burst_plan()),
      burst_capture_(std::move(burst_capture)),
      plc_controller_(std::move(plc_controller)),
      primary_detector_(std::move(primary_detector)),
      patch_detector_(std::move(patch_detector)) {}

InspectionResult InspectionPipeline::process_packet(FramePacket packet) {
    auto station_result = process_station_packet(packet);
    if (station_result.decision_result.control_action != "defect_queued") {
        return station_result;
    }

    auto defect_result = process_defect_packet(station_result.frame_packet);
    defect_result.presence_result = station_result.presence_result;
    defect_result.timing.queue_delay_ms = station_result.timing.queue_delay_ms;
    defect_result.timing.presence_inference_ms = station_result.timing.presence_inference_ms;
    defect_result.timing.advance_control_ms = station_result.timing.advance_control_ms;
    defect_result.timing.total_ms += station_result.timing.total_ms;
    defect_result.control_commands.insert(
        defect_result.control_commands.begin(),
        station_result.control_commands.begin(),
        station_result.control_commands.end());
    defect_result.execution_feedbacks.insert(
        defect_result.execution_feedbacks.begin(),
        station_result.execution_feedbacks.begin(),
        station_result.execution_feedbacks.end());
    defect_result.state_trace.insert(
        defect_result.state_trace.begin(),
        station_result.state_trace.begin(),
        station_result.state_trace.end());
    return defect_result;
}

InspectionResult InspectionPipeline::process_station_packet(FramePacket packet) {
    const auto started = Clock::now();
    InspectionResult result;
    result.frame_packet = packet;
    result.state_trace.push_back("received:" + packet.frame_id);

    const auto now = SystemClock::now();
    result.timing.queue_delay_ms = std::chrono::duration<double, std::milli>(now - packet.enqueued_at).count();

    result.presence_result.stage_name = "presence";
    result.presence_result.detector_backend = "plc_laser";
    result.presence_result.triggered = detection_config_.presence_enabled;
    if (detection_config_.presence_enabled) {
        result.state_trace.push_back("plc_laser_presence_waiting");
        const auto presence = plc_controller_->read_laser_presence(packet);
        result.timing.presence_inference_ms = presence.latency_ms;
        packet.metadata["presence.source"] = "plc_laser";
        packet.metadata["presence.station_id"] = presence.station_id;
        packet.metadata["presence.message_id"] = presence.message_id;
        packet.metadata["presence.detail"] = presence.detail;
        packet.metadata["presence.message_valid"] = presence.message_valid ? "true" : "false";
        packet.metadata["presence.timed_out"] = presence.timed_out ? "true" : "false";
        packet.metadata["presence.bag_present"] = presence.bag_present ? "true" : "false";
        if (!presence.bag_id.empty()) {
            packet.bag_id = presence.bag_id;
            packet.metadata["bag.id"] = presence.bag_id;
            packet.metadata["bag.id_source"] = "plc_laser_presence";
        }
        result.frame_packet = packet;
        result.presence_result.inference_ms = presence.latency_ms;
        if (presence.message_valid && !presence.timed_out && presence.bag_present) {
            result.presence_result.boxes.push_back(DetectionBox{0, 0, 0, 0, "plc_laser_presence", 1.0});
        }
        result.state_trace.push_back(
            std::string("plc_laser_presence:") +
            (presence.bag_present ? "bag_present" : "no_bag") +
            ":valid=" + (presence.message_valid ? "true" : "false") +
            ":timeout=" + (presence.timed_out ? "true" : "false") +
            ":" + presence.detail);
    }

    if (detection_config_.presence_enabled && !result.presence_result.is_defect()) {
        const auto timed_out = metadata_value(packet, "presence.timed_out") == "true";
        const auto message_valid = metadata_value(packet, "presence.message_valid", "true") == "true";
        result.decision_result.finalized = false;
        result.decision_result.timed_out = timed_out;
        result.decision_result.control_action = "no_bag";
        result.decision_result.reason = timed_out
            ? "plc_laser_presence_timeout"
            : (message_valid ? "plc_laser_no_bag" : "plc_laser_presence_invalid");
        result.bag_summary.bag_id = packet.bag_id;
        result.bag_summary.timed_out = timed_out;
        result.bag_summary.aggregate_action = "no_bag";
        result.bag_summary.aggregate_reason = result.decision_result.reason;
        result.state_trace.push_back("skip_defect_detection:" + result.decision_result.reason);
        result.timing.total_ms = elapsed_ms(started);
        return result;
    }

    const auto session = make_capture_session(packet);
    result.state_trace.push_back("capture_session:" + session.capture_session_id + ":clock=" + session.hardware_clock_source);
    burst_capture_->arm_burst(session, burst_plan_);
    const auto burst_ack = plc_controller_->start_light_burst(session, burst_plan_);
    result.state_trace.push_back("burst_start:" + burst_plan_.plan_id + ":" + burst_ack.detail);

    auto capture_group = burst_capture_->poll_completed_group(session.capture_session_id);
    if (capture_group) {
        const auto plc_events = plc_controller_->read_burst_events(session.capture_session_id);
        capture_group->alignments = align_camera_and_plc_events(*capture_group, plc_events);
        capture_group->sync_valid = std::all_of(
            capture_group->alignments.begin(),
            capture_group->alignments.end(),
            [](const auto& alignment) {
                return alignment.light_on_before_exposure &&
                    alignment.light_off_after_exposure &&
                    alignment.within_jitter_tolerance;
            });
        for (const auto& trace : capture_group_trace(*capture_group)) {
            result.state_trace.push_back(trace);
        }
        result.state_trace.push_back(capture_group->sync_valid ? "burst_sync_valid" : "burst_sync_warning");
        if (capture_group->sync_valid) {
            attach_burst_metadata(packet, *capture_group);
            result.frame_packet = packet;
            result.state_trace.push_back("burst_detection_inputs:" + std::to_string(capture_group->images.size()));
        }
    } else {
        result.state_trace.push_back("burst_group_missing:" + session.capture_session_id);
    }

    if (!capture_group || !capture_group->sync_valid) {
        result.decision_result.finalized = false;
        result.decision_result.control_action = "capture_invalid";
        result.decision_result.reason = "burst_sync_or_jitter_invalid";
        result.bag_summary.bag_id = packet.bag_id;
        result.bag_summary.aggregate_action = "capture_invalid";
        result.bag_summary.aggregate_reason = "burst_sync_or_jitter_invalid";
        result.state_trace.push_back("skip_defect_detection:capture_invalid");
        result.timing.total_ms = elapsed_ms(started);
        return result;
    }

    result.state_trace.push_back("capture_latched:" + packet.source_path.string());
    const auto advance_commands = build_advance_commands(packet, result.presence_result);
    const auto advance_feedbacks = plc_controller_->release_station_after_capture(session);
    if (!advance_commands.empty() || !advance_feedbacks.empty()) {
        const auto advance_started = Clock::now();
        for (const auto& command : advance_commands) {
            result.state_trace.push_back("advance_command_dispatch:" + command.action);
            result.control_commands.push_back(command);
        }
        result.execution_feedbacks.insert(result.execution_feedbacks.end(), advance_feedbacks.begin(), advance_feedbacks.end());
        result.timing.advance_control_ms = elapsed_ms(advance_started);
        result.state_trace.push_back("advance_command_done");
    }

    result.decision_result.finalized = false;
    result.decision_result.control_action = "defect_queued";
    result.decision_result.reason = "capture_released_defect_async";
    result.bag_summary.bag_id = packet.bag_id;
    result.bag_summary.aggregate_action = "defect_queued";
    result.bag_summary.aggregate_reason = "capture_released_defect_async";
    result.state_trace.push_back("defect_enqueued");
    result.timing.total_ms = elapsed_ms(started);
    return result;
}

InspectionResult InspectionPipeline::process_defect_packet(FramePacket packet) {
    const auto started = Clock::now();
    InspectionResult result;
    result.frame_packet = packet;
    result.state_trace.push_back("defect_worker_received:" + packet.frame_id);

    const auto detection_packets = build_burst_detection_packets(packet);
    result.state_trace.push_back("defect_inputs:" + std::to_string(detection_packets.size()));

    result.stage1_result = run_multi_light_detection(
        detection_packets,
        *primary_detector_,
        DetectionStage::Stage1,
        detection_config_.primary_conf_threshold,
        result.state_trace);
    result.timing.stage1_inference_ms = result.stage1_result.inference_ms;

    const bool should_run_stage2 = detection_config_.patch_enabled && !result.stage1_result.is_defect();
    result.stage2_result.stage_name = "stage2";
    result.stage2_result.detector_backend = patch_detector_->backend_name();
    result.stage2_result.triggered = should_run_stage2;

    if (should_run_stage2) {
        result.stage2_result = run_multi_light_detection(
            detection_packets,
            *patch_detector_,
            DetectionStage::Stage2,
            detection_config_.patch_conf_threshold,
            result.state_trace);
        result.timing.stage2_inference_ms = result.stage2_result.inference_ms;
    }

    const auto decision_started = Clock::now();
    const auto local_decision = build_local_decision(packet, result.stage1_result, result.stage2_result, should_run_stage2);
    result.timing.decision_ms = elapsed_ms(decision_started);

    {
        std::lock_guard<std::mutex> lock(correlation_mutex_);
        const auto correlation_started = Clock::now();
        result.bag_summary = correlator_.update(packet, local_decision, result.stage1_result, result.stage2_result);
        result.timing.correlation_ms = elapsed_ms(correlation_started);
        result.decision_result = build_final_decision(local_decision, result.bag_summary);
        result.state_trace.push_back("decision_ready:" + result.decision_result.control_action + ":" + result.decision_result.reason);

        if (result.bag_summary.new_command_required) {
            result.state_trace.push_back("sort_result_waiting_reorder:" + packet.bag_id);
        }
    }

    result.timing.total_ms = elapsed_ms(started);
    return result;
}

InspectionResult InspectionPipeline::execute_sort_command(InspectionResult result) {
    if (!result.decision_result.finalized) {
        return result;
    }

    const auto control_started = Clock::now();
    const auto decision_commands = build_commands(result.frame_packet, result.decision_result);
    result.state_trace.push_back("sorter_dispatch:" + result.decision_result.control_action + ":" + result.decision_result.reason);
    for (const auto& command : decision_commands) {
        result.state_trace.push_back("command_dispatch:" + command.action);
        result.control_commands.push_back(command);
    }
    if (result.decision_result.control_action == "accept") {
        result.execution_feedbacks.push_back(plc_controller_->route_to_ok_bin(result.frame_packet));
    } else if (result.decision_result.control_action == "reject") {
        result.execution_feedbacks.push_back(plc_controller_->route_to_ng_bin(result.frame_packet));
    }
    result.timing.control_ms += elapsed_ms(control_started);
    result.state_trace.push_back("command_done");
    return result;
}

std::vector<InspectionResult> InspectionPipeline::flush_timeouts() {
    std::lock_guard<std::mutex> lock(correlation_mutex_);
    std::vector<InspectionResult> results;
    for (const auto& context : correlator_.collect_timeouts()) {
        results.push_back(build_timeout_result(context));
    }
    return results;
}

DecisionResult InspectionPipeline::build_local_decision(
    const FramePacket& packet,
    const PerceptionResult& stage1_result,
    const PerceptionResult& stage2_result,
    bool should_run_stage2) const {
    (void)packet;
    DecisionResult decision;
    decision.should_run_stage2 = should_run_stage2;

    if (stage1_result.is_defect()) {
        decision.is_defect = true;
        decision.stage_source = "stage1";
        decision.control_action = "reject";
        decision.reason = "stage1_detected_defect";
        decision.final_boxes = stage1_result.boxes;
        return decision;
    }

    if (stage2_result.is_defect()) {
        decision.is_defect = true;
        decision.stage_source = "stage2";
        decision.control_action = "reject";
        decision.reason = "stage2_detected_micro_defect";
        decision.final_boxes = stage2_result.boxes;
        return decision;
    }

    decision.stage_source = "none";
    decision.control_action = "accept";
    decision.reason = should_run_stage2 ? "stage2_clear" : "stage1_clear";
    return decision;
}

DecisionResult InspectionPipeline::build_final_decision(const DecisionResult& local_decision, const BagSummary& summary) const {
    DecisionResult decision = local_decision;
    decision.finalized = summary.finalized;
    decision.timed_out = summary.timed_out;
    decision.control_action = summary.aggregate_action;
    decision.reason = summary.aggregate_reason;
    decision.is_defect = summary.aggregate_defect;
    decision.repeated = summary.aggregate_repeated;
    return decision;
}

std::vector<ControlCommand> InspectionPipeline::build_commands(const FramePacket& packet, const DecisionResult& decision) const {
    std::vector<ControlCommand> commands;
    if (!decision.finalized || decision.control_action == "await_peer_camera") {
        return commands;
    }

    ControlCommand command;
    command.command_id = make_command_id();
    command.frame_id = packet.frame_id;
    command.bag_id = packet.bag_id;
    command.target = "end_sorter";
    if (decision.control_action == "accept") {
        command.action = "route_to_ok_bin";
    } else if (decision.control_action == "reject") {
        command.action = "route_to_ng_bin";
    } else {
        command.action = decision.control_action;
    }
    commands.push_back(command);
    return commands;
}

std::vector<ControlCommand> InspectionPipeline::build_commands_from_feedbacks(
    const std::vector<ExecutionFeedback>& feedbacks,
    const FramePacket& packet) const {
    std::vector<ControlCommand> commands;
    for (const auto& feedback : feedbacks) {
        ControlCommand command;
        command.command_id = feedback.command_id;
        command.frame_id = packet.frame_id;
        command.bag_id = packet.bag_id;
        command.target = feedback.target;
        command.action = feedback.action;
        commands.push_back(command);
    }
    return commands;
}

std::vector<ControlCommand> InspectionPipeline::build_advance_commands(
    const FramePacket& packet,
    const PerceptionResult& presence_result) const {
    std::vector<ControlCommand> commands;
    if (!detection_config_.presence_enabled || !detection_config_.advance_on_presence || !presence_result.is_defect()) {
        return commands;
    }
    if (detection_config_.advance_trigger_camera_id > 0 && packet.camera_id != detection_config_.advance_trigger_camera_id) {
        return commands;
    }

    ControlCommand bottom_lever;
    bottom_lever.command_id = make_command_id();
    bottom_lever.frame_id = packet.frame_id;
    bottom_lever.bag_id = packet.bag_id;
    bottom_lever.target = "camera" + std::to_string(packet.camera_id) + "_bottom_lever";
    bottom_lever.action = "release_bag_after_capture";
    commands.push_back(bottom_lever);

    ControlCommand top_lever;
    top_lever.command_id = make_command_id();
    top_lever.frame_id = packet.frame_id;
    top_lever.bag_id = packet.bag_id;
    top_lever.target = "camera" + std::to_string(packet.camera_id) + "_upper_lever";
    top_lever.action = "push_bag_after_capture";
    commands.push_back(top_lever);

    ControlCommand top_restore;
    top_restore.command_id = make_command_id();
    top_restore.frame_id = packet.frame_id;
    top_restore.bag_id = packet.bag_id;
    top_restore.target = "camera" + std::to_string(packet.camera_id) + "_upper_lever";
    top_restore.action = "restore_after_push";
    commands.push_back(top_restore);

    ControlCommand bottom_restore;
    bottom_restore.command_id = make_command_id();
    bottom_restore.frame_id = packet.frame_id;
    bottom_restore.bag_id = packet.bag_id;
    bottom_restore.target = "camera" + std::to_string(packet.camera_id) + "_bottom_lever";
    bottom_restore.action = "restore_blocking_position";
    commands.push_back(bottom_restore);
    return commands;
}

InspectionResult InspectionPipeline::build_timeout_result(const TimedOutBagContext& context) {
    auto packet = context.packet;
    packet.frame_id = "timeout-" + packet.frame_id;
    packet.received_at = SystemClock::now();
    packet.enqueued_at = packet.received_at;
    packet.source = "timeout_flush";

    const auto started = Clock::now();
    InspectionResult result;
    result.frame_packet = packet;
    result.stage1_result = context.stage1_result;
    result.stage2_result = context.stage2_result;
    result.bag_summary = context.summary;
    result.state_trace.push_back("timeout_flush:" + context.summary.aggregate_reason);

    DecisionResult local_decision = build_local_decision(
        packet,
        context.stage1_result,
        context.stage2_result,
        context.stage2_result.triggered);
    result.decision_result = build_final_decision(local_decision, context.summary);

    if (result.decision_result.finalized) {
        result.state_trace.push_back("sort_result_waiting_reorder:" + packet.bag_id);
    }
    result.timing.total_ms = elapsed_ms(started);
    return result;
}

}  // namespace waterbag
