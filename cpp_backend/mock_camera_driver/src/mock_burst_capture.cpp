#include "mock_camera_driver/mock_burst_capture.hpp"

namespace waterbag {

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

}  // namespace waterbag
