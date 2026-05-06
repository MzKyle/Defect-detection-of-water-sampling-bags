#pragma once

#include <map>
#include <memory>
#include <optional>

#include "detect_orchestrator/hardware_clock.hpp"
#include "detect_orchestrator/schemas.hpp"

namespace waterbag {

enum class LightId {
    L1Backlight,
    L2DarkfieldA,
    L3DarkfieldB,
    L2L3DualDarkfield,
    L4CrossPolarized,
};

std::string to_string(LightId light_id);

struct LightFramePlan {
    int frame_index = 0;
    LightId light_id = LightId::L1Backlight;
    int exposure_us = 100;
    int gain = 0;
    int light_pulse_us = 120;
    int settle_us = 500;
};

struct BurstPlan {
    std::string plan_id = "production_3_frame";
    std::vector<LightFramePlan> frames;
    int jitter_tolerance_us = 200;
};

struct CaptureSession {
    std::string capture_session_id;
    std::string bag_id;
    int station_id = 0;
    int camera_id = 0;
    std::string side_id;
    FramePacket packet;
    SystemClock::time_point created_at = SystemClock::now();
    HardwareTimestamp burst_start_hw;
    std::string hardware_clock_source = UnifiedHardwareClock::source_name();
};

struct CameraFrameEvent {
    std::string capture_session_id;
    int frame_index = 0;
    std::uint64_t camera_frame_id = 0;
    HardwareTimestamp exposure_start_hw;
    HardwareTimestamp exposure_end_hw;
    HardwareTimestamp host_received_hw;
    SystemClock::time_point exposure_start = SystemClock::now();
    SystemClock::time_point exposure_end = SystemClock::now();
    SystemClock::time_point host_received_at = SystemClock::now();
};

struct BurstImage {
    std::string capture_session_id;
    std::string bag_id;
    int station_id = 0;
    int camera_id = 0;
    std::string side_id;
    int frame_index = 0;
    LightId light_id = LightId::L1Backlight;
    std::filesystem::path image_path;
    std::uint64_t camera_frame_id = 0;
    HardwareTimestamp exposure_start_hw;
    HardwareTimestamp exposure_end_hw;
    HardwareTimestamp host_received_hw;
    SystemClock::time_point exposure_start = SystemClock::now();
    SystemClock::time_point exposure_end = SystemClock::now();
    SystemClock::time_point host_received_at = SystemClock::now();
};

struct FrameLightAlignment {
    std::string capture_session_id;
    int frame_index = 0;
    LightId expected_light_id = LightId::L1Backlight;
    std::uint64_t camera_frame_id = 0;
    bool light_on_before_exposure = true;
    bool light_off_after_exposure = true;
    bool within_jitter_tolerance = true;
    long long light_to_exposure_delta_us = 0;
    long long trigger_to_exposure_jitter_us = 0;
    long long exposure_to_host_rx_delta_us = 0;
    std::string hardware_clock_source = UnifiedHardwareClock::source_name();
};

struct CaptureGroup {
    std::string capture_session_id;
    std::string bag_id;
    int station_id = 0;
    int camera_id = 0;
    std::string side_id;
    BurstPlan burst_plan;
    std::vector<BurstImage> images;
    std::vector<FrameLightAlignment> alignments;
    bool complete = false;
    bool sync_valid = true;
    std::string sync_warning;
};

class ICameraBurstCapture {
public:
    virtual ~ICameraBurstCapture() = default;
    virtual void start() = 0;
    virtual void arm_burst(const CaptureSession& session, const BurstPlan& plan) = 0;
    virtual std::optional<CaptureGroup> poll_completed_group(const std::string& capture_session_id) = 0;
};

BurstPlan make_production_burst_plan();
CaptureSession make_capture_session(const FramePacket& packet);
std::vector<std::string> capture_group_trace(const CaptureGroup& group);
std::shared_ptr<ICameraBurstCapture> make_hikvision_mvs_burst_capture(
    CameraDriverConfig driver_config,
    RuntimeConfig runtime_config);

}  // namespace waterbag
