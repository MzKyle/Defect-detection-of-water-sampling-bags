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
