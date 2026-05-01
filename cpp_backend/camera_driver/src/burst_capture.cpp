#include "camera_driver/burst_capture.hpp"

#include <atomic>

namespace waterbag {
namespace {

std::atomic<std::uint64_t> g_capture_session_counter{0};

}  // namespace

std::string to_string(LightId light_id) {
    switch (light_id) {
        case LightId::L1Backlight:
            return "L1_BACKLIGHT";
        case LightId::L2DarkfieldA:
            return "L2_DARKFIELD_A";
        case LightId::L3DarkfieldB:
            return "L3_DARKFIELD_B";
        case LightId::L2L3DualDarkfield:
            return "L2L3_DUAL_DARKFIELD";
        case LightId::L4CrossPolarized:
            return "L4_CROSS_POLARIZED";
    }
    return "UNKNOWN_LIGHT";
}

void MockCameraBurstCapture::start() {}

void MockCameraBurstCapture::arm_burst(const CaptureSession& session, const BurstPlan& plan) {
    CaptureGroup group;
    group.capture_session_id = session.capture_session_id;
    group.bag_id = session.bag_id;
    group.station_id = session.station_id;
    group.camera_id = session.camera_id;
    group.side_id = session.side_id;
    group.burst_plan = plan;

    const auto base_time = SystemClock::now();
    const auto base_hw_ns = session.burst_start_hw.ns > 0 ? session.burst_start_hw.ns : UnifiedHardwareClock::now_ns();
    for (const auto& frame_plan : plan.frames) {
        const auto exposure_start = base_time + std::chrono::microseconds(frame_plan.frame_index * 1000 + frame_plan.settle_us);
        const auto exposure_end = exposure_start + std::chrono::microseconds(frame_plan.exposure_us);
        const auto host_received = exposure_end + std::chrono::microseconds(300);
        const auto exposure_start_hw = HardwareTimestamp{base_hw_ns + (frame_plan.frame_index * 1000LL + frame_plan.settle_us) * 1000LL};
        const auto exposure_end_hw = HardwareTimestamp{exposure_start_hw.ns + frame_plan.exposure_us * 1000LL};
        const auto host_received_hw = HardwareTimestamp{exposure_end_hw.ns + 300000LL};

        BurstImage image;
        image.capture_session_id = session.capture_session_id;
        image.bag_id = session.bag_id;
        image.station_id = session.station_id;
        image.camera_id = session.camera_id;
        image.side_id = session.side_id;
        image.frame_index = frame_plan.frame_index;
        image.light_id = frame_plan.light_id;
        image.image_path = session.packet.source_path.string() + "." + to_string(frame_plan.light_id) + ".mock";
        image.camera_frame_id = next_frame_id_++;
        image.exposure_start_hw = exposure_start_hw;
        image.exposure_end_hw = exposure_end_hw;
        image.host_received_hw = host_received_hw;
        image.exposure_start = exposure_start;
        image.exposure_end = exposure_end;
        image.host_received_at = host_received;
        group.images.push_back(image);

        FrameLightAlignment alignment;
        alignment.capture_session_id = session.capture_session_id;
        alignment.frame_index = frame_plan.frame_index;
        alignment.expected_light_id = frame_plan.light_id;
        alignment.camera_frame_id = image.camera_frame_id;
        alignment.light_on_before_exposure = true;
        alignment.light_off_after_exposure = true;
        alignment.light_to_exposure_delta_us = 0;
        alignment.exposure_to_host_rx_delta_us = diff_us(exposure_end_hw, host_received_hw);
        group.alignments.push_back(alignment);
    }

    group.complete = group.images.size() == plan.frames.size();
    group.sync_valid = group.complete;
    completed_groups_[session.capture_session_id] = group;
}

std::optional<CaptureGroup> MockCameraBurstCapture::poll_completed_group(const std::string& capture_session_id) {
    const auto found = completed_groups_.find(capture_session_id);
    if (found == completed_groups_.end()) {
        return std::nullopt;
    }
    auto group = found->second;
    completed_groups_.erase(found);
    return group;
}

BurstPlan make_production_burst_plan() {
    BurstPlan plan;
    plan.plan_id = "production_3_frame";
    plan.frames = {
        LightFramePlan{0, LightId::L1Backlight, 100, 0, 120, 500},
        LightFramePlan{1, LightId::L2L3DualDarkfield, 200, 0, 240, 500},
        LightFramePlan{2, LightId::L4CrossPolarized, 600, 0, 700, 800},
    };
    plan.jitter_tolerance_us = 200;
    return plan;
}

CaptureSession make_capture_session(const FramePacket& packet) {
    const auto id = ++g_capture_session_counter;
    CaptureSession session;
    session.capture_session_id = packet.bag_id + "_cam" + std::to_string(packet.camera_id) + "_burst_" + std::to_string(id);
    session.bag_id = packet.bag_id;
    session.station_id = packet.camera_id;
    session.camera_id = packet.camera_id;
    session.side_id = packet.camera_id == 1 ? "upper_side" : "lower_side";
    session.packet = packet;
    session.created_at = SystemClock::now();
    session.burst_start_hw = UnifiedHardwareClock::now();
    session.hardware_clock_source = UnifiedHardwareClock::source_name();
    return session;
}

std::vector<std::string> capture_group_trace(const CaptureGroup& group) {
    std::vector<std::string> trace;
    trace.push_back("burst_group:" + group.capture_session_id + ":frames=" + std::to_string(group.images.size()));
    for (const auto& image : group.images) {
        trace.push_back(
            "burst_frame:" + std::to_string(image.frame_index) +
            ":" + to_string(image.light_id) +
            ":camera_frame=" + std::to_string(image.camera_frame_id));
    }
    for (const auto& alignment : group.alignments) {
        trace.push_back(
            "burst_alignment:" + std::to_string(alignment.frame_index) +
            ":jitter_us=" + std::to_string(alignment.trigger_to_exposure_jitter_us) +
            ":window=" + (alignment.light_on_before_exposure && alignment.light_off_after_exposure ? "ok" : "bad") +
            ":jitter=" + (alignment.within_jitter_tolerance ? "ok" : "bad") +
            ":clock=" + alignment.hardware_clock_source);
    }
    if (!group.sync_valid) {
        trace.push_back("burst_sync_warning:" + group.sync_warning);
    }
    return trace;
}

}  // namespace waterbag
