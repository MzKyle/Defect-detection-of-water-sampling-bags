#include "PLC_driver/plc_controller.hpp"

#include <algorithm>
#include <cstdlib>

namespace waterbag {
namespace {

}  // namespace

MockSemanticPlcController::MockSemanticPlcController(PlcConfig config)
    : config_(config),
      reliable_(config, std::make_unique<MockPlcTransport>(config)) {}

PlcAck MockSemanticPlcController::start_light_burst(const CaptureSession& session, const BurstPlan& plan) {
    const auto started = Clock::now();
    const auto base_time = SystemClock::now();
    const auto base_hw_ns = session.burst_start_hw.ns > 0 ? session.burst_start_hw.ns : UnifiedHardwareClock::now_ns();
    std::vector<PlcBurstEvent> events;

    for (const auto& frame_plan : plan.frames) {
        const auto light_on = base_time + std::chrono::microseconds(frame_plan.frame_index * 1000);
        const auto camera_trigger = light_on + std::chrono::microseconds(frame_plan.settle_us);
        const auto light_off = camera_trigger + std::chrono::microseconds(frame_plan.light_pulse_us);
        const auto light_on_hw = HardwareTimestamp{base_hw_ns + frame_plan.frame_index * 1000LL * 1000LL};
        const auto camera_trigger_hw = HardwareTimestamp{light_on_hw.ns + frame_plan.settle_us * 1000LL};
        const auto light_off_hw = HardwareTimestamp{camera_trigger_hw.ns + frame_plan.light_pulse_us * 1000LL};

        PlcBurstEvent event;
        event.capture_session_id = session.capture_session_id;
        event.plan_id = plan.plan_id;
        event.frame_index = frame_plan.frame_index;
        event.light_id = frame_plan.light_id;
        event.light_on_hw = light_on_hw;
        event.camera_trigger_hw = camera_trigger_hw;
        event.light_off_hw = light_off_hw;
        event.light_on = light_on;
        event.camera_trigger = camera_trigger;
        event.light_off = light_off;
        events.push_back(event);
    }

    burst_events_[session.capture_session_id] = events;
    return PlcAck{true, "mock_light_burst_started", elapsed_ms(started)};
}

std::vector<PlcBurstEvent> MockSemanticPlcController::read_burst_events(const std::string& capture_session_id) {
    const auto found = burst_events_.find(capture_session_id);
    if (found == burst_events_.end()) {
        return {};
    }
    return found->second;
}

std::vector<ExecutionFeedback> MockSemanticPlcController::release_station_after_capture(const CaptureSession& session) {
    const auto& packet = session.packet;
    const auto station = "camera" + std::to_string(session.camera_id);
    std::vector<ExecutionFeedback> feedbacks;
    feedbacks.push_back(execute_semantic_command(packet, station + "_bottom_lever", "release_bag_after_capture"));
    feedbacks.push_back(execute_semantic_command(packet, station + "_upper_lever", "push_bag_after_capture"));
    feedbacks.push_back(execute_semantic_command(packet, station + "_upper_lever", "restore_after_push"));
    feedbacks.push_back(execute_semantic_command(packet, station + "_bottom_lever", "restore_blocking_position"));
    return feedbacks;
}

ExecutionFeedback MockSemanticPlcController::route_to_ok_bin(const FramePacket& packet) {
    return execute_semantic_command(packet, "end_sorter", "route_to_ok_bin");
}

ExecutionFeedback MockSemanticPlcController::route_to_ng_bin(const FramePacket& packet) {
    return execute_semantic_command(packet, "end_sorter", "route_to_ng_bin");
}

ExecutionFeedback MockSemanticPlcController::execute_semantic_command(
    const FramePacket& packet,
    const std::string& target,
    const std::string& action) {
    ControlCommand command;
    command.command_id = make_command_id();
    command.frame_id = packet.frame_id;
    command.bag_id = packet.bag_id;
    command.target = target;
    command.action = action;
    command.created_at = SystemClock::now();
    return reliable_.execute(command);
}

std::vector<FrameLightAlignment> align_camera_and_plc_events(
    const CaptureGroup& group,
    const std::vector<PlcBurstEvent>& plc_events) {
    std::vector<FrameLightAlignment> alignments;
    for (const auto& image : group.images) {
        FrameLightAlignment alignment;
        alignment.capture_session_id = group.capture_session_id;
        alignment.frame_index = image.frame_index;
        alignment.expected_light_id = image.light_id;
        alignment.camera_frame_id = image.camera_frame_id;
        alignment.exposure_to_host_rx_delta_us = diff_us(image.exposure_end_hw, image.host_received_hw);

        const auto event = std::find_if(plc_events.begin(), plc_events.end(), [&](const PlcBurstEvent& item) {
            return item.frame_index == image.frame_index;
        });
        if (event != plc_events.end()) {
            alignment.light_on_before_exposure = event->light_on_hw.ns <= image.exposure_start_hw.ns;
            alignment.light_off_after_exposure = image.exposure_end_hw.ns <= event->light_off_hw.ns;
            alignment.light_to_exposure_delta_us = diff_us(event->light_on_hw, image.exposure_start_hw);
            alignment.trigger_to_exposure_jitter_us = diff_us(event->camera_trigger_hw, image.exposure_start_hw);
            alignment.within_jitter_tolerance = std::llabs(alignment.trigger_to_exposure_jitter_us) <= group.burst_plan.jitter_tolerance_us;
        } else {
            alignment.light_on_before_exposure = false;
            alignment.light_off_after_exposure = false;
            alignment.within_jitter_tolerance = false;
            alignment.light_to_exposure_delta_us = 0;
        }
        alignments.push_back(alignment);
    }
    return alignments;
}

}  // namespace waterbag
