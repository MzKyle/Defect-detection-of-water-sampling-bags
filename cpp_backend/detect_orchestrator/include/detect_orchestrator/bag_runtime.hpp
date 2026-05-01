#pragma once

#include <deque>
#include <unordered_map>
#include <unordered_set>

#include "detect_orchestrator/schemas.hpp"

namespace waterbag {

InspectionResult make_fail_safe_bag_result(
    const FramePacket& packet,
    const std::string& reason,
    bool timed_out);

class BagCaptureAssembler {
public:
    BagCaptureAssembler(
        std::vector<int> expected_camera_ids,
        std::size_t expected_images_per_camera,
        Milliseconds capture_timeout);

    std::vector<FramePacket> register_station_capture(const InspectionResult& station_result);
    std::vector<InspectionResult> collect_timeouts();

private:
    struct CaptureContext {
        FramePacket representative;
        std::unordered_map<int, FramePacket> sides;
        Clock::time_point first_seen = Clock::now();
    };

    bool side_has_complete_burst(const FramePacket& packet) const;
    bool context_complete(const CaptureContext& context) const;
    std::vector<FramePacket> ordered_packets(const CaptureContext& context) const;
    std::string missing_camera_summary(const CaptureContext& context) const;

    std::vector<int> expected_camera_ids_;
    std::size_t expected_images_per_camera_ = 3;
    Milliseconds capture_timeout_{1500};
    std::unordered_map<std::string, CaptureContext> contexts_;
    std::unordered_set<std::string> closed_bag_ids_;
};

class SortReorderBuffer {
public:
    explicit SortReorderBuffer(Milliseconds result_timeout);

    void register_bag(const FramePacket& packet);
    void store_result(InspectionResult result);
    std::vector<InspectionResult> collect_ready();

private:
    struct SortSlot {
        FramePacket representative;
        Clock::time_point registered_at = Clock::now();
        bool result_ready = false;
        InspectionResult result;
    };

    Milliseconds result_timeout_{1500};
    std::deque<std::string> order_;
    std::unordered_set<std::string> registered_;
    std::unordered_map<std::string, SortSlot> slots_;
};

}  // namespace waterbag
