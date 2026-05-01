#pragma once

#include <optional>
#include <unordered_map>

#include "detect_orchestrator/schemas.hpp"

namespace waterbag {

struct BagObservationContext {
    FramePacket packet;
    DecisionResult local_decision;
    PerceptionResult stage1_result;
    PerceptionResult stage2_result;
    Clock::time_point updated_at = Clock::now();
};

struct TimedOutBagContext {
    FramePacket packet;
    PerceptionResult stage1_result;
    PerceptionResult stage2_result;
    BagSummary summary;
};

class BagCorrelator {
public:
    explicit BagCorrelator(CorrelationConfig config);

    BagSummary update(
        const FramePacket& packet,
        const DecisionResult& local_decision,
        const PerceptionResult& stage1_result,
        const PerceptionResult& stage2_result);

    std::vector<TimedOutBagContext> collect_timeouts();

private:
    struct BagSession {
        std::string bag_id;
        std::unordered_map<int, BagObservationContext> observations;
        bool command_issued = false;
        bool timed_out = false;
        std::string final_action;
        std::string final_reason;
        Clock::time_point last_updated = Clock::now();
        Clock::time_point finalized_at = Clock::time_point{};
    };

    BagSummary build_summary(const BagSession& session, bool stale_frame_ignored, bool new_command_required) const;
    void finalize_if_ready(BagSession& session);
    void prune_finalized();
    bool is_stale(const FramePacket& incoming, const BagObservationContext& existing) const;
    std::vector<int> missing_camera_ids(const BagSession& session) const;
    const BagObservationContext* latest_observation(const BagSession& session) const;

    CorrelationConfig config_;
    std::unordered_map<std::string, BagSession> sessions_;
};

}  // namespace waterbag
