#pragma once

#include "camera_driver/burst_capture.hpp"
#include "PLC_driver/plc.hpp"

namespace waterbag {

struct PlcBurstEvent {
    std::string capture_session_id;
    std::string plan_id;
    int frame_index = 0;
    LightId light_id = LightId::L1Backlight;
    HardwareTimestamp light_on_hw;
    HardwareTimestamp camera_trigger_hw;
    HardwareTimestamp light_off_hw;
    SystemClock::time_point light_on = SystemClock::now();
    SystemClock::time_point camera_trigger = SystemClock::now();
    SystemClock::time_point light_off = SystemClock::now();
};

struct PlcAck {
    bool success = true;
    std::string detail = "ok";
    double latency_ms = 0.0;
};

struct PlcLaserPresence {
    bool message_valid = true;
    bool bag_present = false;
    bool timed_out = false;
    int camera_id = 0;
    std::string station_id;
    std::string message_id;
    std::string bag_id;
    std::string detail = "ok";
    double latency_ms = 0.0;
    SystemClock::time_point received_at = SystemClock::now();
};

class IPlcController {
public:
    virtual ~IPlcController() = default;
    virtual PlcLaserPresence read_laser_presence(const FramePacket& packet) = 0;
    virtual PlcAck start_light_burst(const CaptureSession& session, const BurstPlan& plan) = 0;
    virtual std::vector<PlcBurstEvent> read_burst_events(const std::string& capture_session_id) = 0;
    virtual std::vector<ExecutionFeedback> release_station_after_capture(const CaptureSession& session) = 0;
    virtual ExecutionFeedback route_to_ok_bin(const FramePacket& packet) = 0;
    virtual ExecutionFeedback route_to_ng_bin(const FramePacket& packet) = 0;
};

class MockSemanticPlcController final : public IPlcController {
public:
    explicit MockSemanticPlcController(PlcConfig config);

    PlcLaserPresence read_laser_presence(const FramePacket& packet) override;
    PlcAck start_light_burst(const CaptureSession& session, const BurstPlan& plan) override;
    std::vector<PlcBurstEvent> read_burst_events(const std::string& capture_session_id) override;
    std::vector<ExecutionFeedback> release_station_after_capture(const CaptureSession& session) override;
    ExecutionFeedback route_to_ok_bin(const FramePacket& packet) override;
    ExecutionFeedback route_to_ng_bin(const FramePacket& packet) override;

private:
    ExecutionFeedback execute_semantic_command(const FramePacket& packet, const std::string& target, const std::string& action);

    PlcConfig config_;
    ReliablePlcController reliable_;
    std::map<std::string, std::vector<PlcBurstEvent>> burst_events_;
};

std::vector<FrameLightAlignment> align_camera_and_plc_events(
    const CaptureGroup& group,
    const std::vector<PlcBurstEvent>& plc_events);

}  // namespace waterbag
