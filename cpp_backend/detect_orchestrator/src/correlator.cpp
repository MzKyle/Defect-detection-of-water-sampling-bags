#include "detect_orchestrator/correlator.hpp"

#include <algorithm>

namespace waterbag {

BagCorrelator::BagCorrelator(CorrelationConfig config) : config_(std::move(config)) {}

BagSummary BagCorrelator::update(
    const FramePacket& packet,
    const DecisionResult& local_decision,
    const PerceptionResult& stage1_result,
    const PerceptionResult& stage2_result) {
    if (!config_.enabled) {
        BagSession single;
        single.bag_id = packet.bag_id;
        single.observations.emplace(packet.camera_id, BagObservationContext{packet, local_decision, stage1_result, stage2_result, Clock::now()});
        single.final_action = local_decision.is_defect ? "reject" : "accept";
        single.final_reason = local_decision.reason;
        return build_summary(single, false, true);
    }

    prune_finalized();
    auto& session = sessions_[packet.bag_id];
    if (session.bag_id.empty()) {
        session.bag_id = packet.bag_id;
    }

    const auto existing = session.observations.find(packet.camera_id);
    if (existing != session.observations.end() && is_stale(packet, existing->second)) {
        return build_summary(session, true, false);
    }

    const bool had_command = session.command_issued;
    session.observations[packet.camera_id] = BagObservationContext{
        packet,
        local_decision,
        stage1_result,
        stage2_result,
        Clock::now(),
    };
    session.last_updated = Clock::now();

    if (!session.command_issued) {
        finalize_if_ready(session);
    }

    const bool new_command = !had_command && !session.final_action.empty();
    if (new_command) {
        session.command_issued = true;
        session.finalized_at = Clock::now();
    }
    return build_summary(session, false, new_command);
}

std::vector<TimedOutBagContext> BagCorrelator::collect_timeouts() {
    std::vector<TimedOutBagContext> timed_out;
    const auto now = Clock::now();

    for (auto& [bag_id, session] : sessions_) {
        (void)bag_id;
        if (session.command_issued || session.timed_out || session.observations.empty()) {
            continue;
        }
        if (now - session.last_updated < config_.pending_timeout) {
            continue;
        }

        const auto* latest = latest_observation(session);
        if (latest == nullptr) {
            continue;
        }

        session.timed_out = true;
        session.final_action = config_.timeout_action;
        session.final_reason = "peer_camera_timeout:" + join_ints(missing_camera_ids(session));
        session.command_issued = true;
        session.finalized_at = now;

        timed_out.push_back(TimedOutBagContext{
            latest->packet,
            latest->stage1_result,
            latest->stage2_result,
            build_summary(session, false, true),
        });
    }

    prune_finalized();
    return timed_out;
}

BagSummary BagCorrelator::build_summary(const BagSession& session, bool stale_frame_ignored, bool new_command_required) const {
    BagSummary summary;
    summary.bag_id = session.bag_id;
    summary.finalized = !session.final_action.empty();
    summary.timed_out = session.timed_out;
    summary.stale_frame_ignored = stale_frame_ignored;
    summary.new_command_required = new_command_required;
    summary.aggregate_action = summary.finalized ? session.final_action : "await_peer_camera";
    summary.aggregate_reason = summary.finalized ? session.final_reason : "await_peer_camera";
    summary.missing_camera_ids = missing_camera_ids(session);

    for (const auto& [camera_id, context] : session.observations) {
        (void)camera_id;
        summary.aggregate_defect = summary.aggregate_defect || context.local_decision.is_defect;
        summary.aggregate_repeated = summary.aggregate_repeated || context.local_decision.repeated;
        summary.observations.push_back(CameraObservation{
            context.packet.camera_id,
            context.packet.camera_name,
            context.packet.frame_id,
            context.local_decision.is_defect,
            context.local_decision.repeated,
            context.local_decision.stage_source,
            static_cast<int>(context.local_decision.final_boxes.size()),
            context.packet.received_at,
            context.packet.source_mtime,
        });
    }

    std::sort(summary.observations.begin(), summary.observations.end(), [](const auto& lhs, const auto& rhs) {
        return lhs.camera_id < rhs.camera_id;
    });
    return summary;
}

void BagCorrelator::finalize_if_ready(BagSession& session) {
    const bool any_defect = std::any_of(session.observations.begin(), session.observations.end(), [](const auto& item) {
        return item.second.local_decision.is_defect;
    });
    const auto missing = missing_camera_ids(session);
    if (!missing.empty()) {
        if (!config_.hold_non_defect_until_complete && !any_defect) {
            session.final_action = "accept";
            session.final_reason = "single_camera_passed";
        }
        return;
    }

    if (any_defect) {
        session.final_action = "reject";
        session.final_reason = "aggregate_defect_detected";
    } else {
        session.final_action = "accept";
        session.final_reason = "all_cameras_passed";
    }
}

void BagCorrelator::prune_finalized() {
    const auto now = Clock::now();
    for (auto it = sessions_.begin(); it != sessions_.end();) {
        const auto& session = it->second;
        if (!session.final_action.empty() && session.finalized_at != Clock::time_point{} && now - session.finalized_at > config_.finalized_retention) {
            it = sessions_.erase(it);
        } else {
            ++it;
        }
    }
}

bool BagCorrelator::is_stale(const FramePacket& incoming, const BagObservationContext& existing) const {
    if (incoming.source_mtime && existing.packet.source_mtime) {
        return *incoming.source_mtime < *existing.packet.source_mtime;
    }
    return incoming.received_at < existing.packet.received_at;
}

std::vector<int> BagCorrelator::missing_camera_ids(const BagSession& session) const {
    std::vector<int> missing;
    for (int camera_id : config_.expected_camera_ids) {
        if (session.observations.find(camera_id) == session.observations.end()) {
            missing.push_back(camera_id);
        }
    }
    return missing;
}

const BagObservationContext* BagCorrelator::latest_observation(const BagSession& session) const {
    const BagObservationContext* latest = nullptr;
    for (const auto& [camera_id, context] : session.observations) {
        (void)camera_id;
        if (latest == nullptr || context.updated_at > latest->updated_at) {
            latest = &context;
        }
    }
    return latest;
}

}  // namespace waterbag
