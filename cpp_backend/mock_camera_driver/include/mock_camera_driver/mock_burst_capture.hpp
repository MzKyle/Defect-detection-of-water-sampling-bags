#pragma once

#include <map>

#include "camera_driver/burst_capture.hpp"

namespace waterbag {

class MockCameraBurstCapture final : public ICameraBurstCapture {
public:
    void start() override;
    void arm_burst(const CaptureSession& session, const BurstPlan& plan) override;
    std::optional<CaptureGroup> poll_completed_group(const std::string& capture_session_id) override;

private:
    std::map<std::string, CaptureGroup> completed_groups_;
    std::uint64_t next_frame_id_ = 1;
};

}  // namespace waterbag
