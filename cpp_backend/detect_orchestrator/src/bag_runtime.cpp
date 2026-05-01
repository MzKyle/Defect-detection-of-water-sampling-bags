#include "detect_orchestrator/bag_runtime.hpp"

#include <algorithm>
#include <sstream>

namespace waterbag {
namespace {

std::size_t burst_image_count(const FramePacket& packet) {
    const auto found = packet.metadata.find("burst.image_count");
    if (found == packet.metadata.end()) {
        return 0;
    }
    try {
        return static_cast<std::size_t>(std::stoull(found->second));
    } catch (...) {
        return 0;
    }
}

}  // namespace

InspectionResult make_fail_safe_bag_result(
    const FramePacket& packet,
    const std::string& reason,
    bool timed_out) {
    InspectionResult result;
    result.frame_packet = packet;
    result.decision_result.finalized = true;
    result.decision_result.timed_out = timed_out;
    result.decision_result.is_defect = true;
    result.decision_result.control_action = "reject";
    result.decision_result.reason = reason;
    result.bag_summary.bag_id = packet.bag_id;
    result.bag_summary.finalized = true;
    result.bag_summary.timed_out = timed_out;
    result.bag_summary.aggregate_defect = true;
    result.bag_summary.aggregate_action = "reject";
    result.bag_summary.aggregate_reason = reason;
    result.state_trace.push_back("fail_safe_ng:" + reason);
    return result;
}

BagCaptureAssembler::BagCaptureAssembler(
    std::vector<int> expected_camera_ids,
    std::size_t expected_images_per_camera,
    Milliseconds capture_timeout)
    : expected_camera_ids_(std::move(expected_camera_ids)),
      expected_images_per_camera_(expected_images_per_camera),
      capture_timeout_(capture_timeout) {}

std::vector<FramePacket> BagCaptureAssembler::register_station_capture(const InspectionResult& station_result) {
    const auto& packet = station_result.frame_packet;
    if (packet.bag_id.empty() || station_result.decision_result.control_action != "defect_queued") {
        return {};
    }
    if (closed_bag_ids_.find(packet.bag_id) != closed_bag_ids_.end()) {
        return {};
    }

    auto& context = contexts_[packet.bag_id];
    if (context.representative.bag_id.empty()) {
        context.representative = packet;
        context.first_seen = Clock::now();
    }
    context.sides[packet.camera_id] = packet;

    if (!context_complete(context)) {
        return {};
    }

    auto packets = ordered_packets(context);
    contexts_.erase(packet.bag_id);
    closed_bag_ids_.insert(packet.bag_id);
    return packets;
}

std::vector<InspectionResult> BagCaptureAssembler::collect_timeouts() {
    std::vector<InspectionResult> results;
    const auto now = Clock::now();

    for (auto it = contexts_.begin(); it != contexts_.end();) {
        auto& context = it->second;
        if (now - context.first_seen < capture_timeout_) {
            ++it;
            continue;
        }

        auto result = make_fail_safe_bag_result(
            context.representative,
            "image_lost_capture_timeout:missing_cameras=" + missing_camera_summary(context),
            true);
        result.state_trace.push_back("capture_reorder_timeout:" + it->first);
        results.push_back(std::move(result));
        closed_bag_ids_.insert(it->first);
        it = contexts_.erase(it);
    }
    return results;
}

bool BagCaptureAssembler::side_has_complete_burst(const FramePacket& packet) const {
    const auto sync = packet.metadata.find("burst.sync_valid");
    return burst_image_count(packet) >= expected_images_per_camera_ &&
        (sync == packet.metadata.end() || sync->second == "true");
}

bool BagCaptureAssembler::context_complete(const CaptureContext& context) const {
    for (int camera_id : expected_camera_ids_) {
        const auto found = context.sides.find(camera_id);
        if (found == context.sides.end() || !side_has_complete_burst(found->second)) {
            return false;
        }
    }
    return true;
}

std::vector<FramePacket> BagCaptureAssembler::ordered_packets(const CaptureContext& context) const {
    std::vector<FramePacket> packets;
    packets.reserve(expected_camera_ids_.size());
    for (int camera_id : expected_camera_ids_) {
        const auto found = context.sides.find(camera_id);
        if (found != context.sides.end()) {
            packets.push_back(found->second);
        }
    }
    return packets;
}

std::string BagCaptureAssembler::missing_camera_summary(const CaptureContext& context) const {
    std::vector<int> missing;
    for (int camera_id : expected_camera_ids_) {
        const auto found = context.sides.find(camera_id);
        if (found == context.sides.end() || !side_has_complete_burst(found->second)) {
            missing.push_back(camera_id);
        }
    }
    return join_ints(missing);
}

SortReorderBuffer::SortReorderBuffer(Milliseconds result_timeout)
    : result_timeout_(result_timeout) {}

void SortReorderBuffer::register_bag(const FramePacket& packet) {
    if (packet.bag_id.empty() || registered_.find(packet.bag_id) != registered_.end()) {
        return;
    }

    registered_.insert(packet.bag_id);
    order_.push_back(packet.bag_id);
    SortSlot slot;
    slot.representative = packet;
    slot.registered_at = Clock::now();
    slots_[packet.bag_id] = std::move(slot);
}

void SortReorderBuffer::store_result(InspectionResult result) {
    if (result.frame_packet.bag_id.empty()) {
        return;
    }
    register_bag(result.frame_packet);
    auto& slot = slots_[result.frame_packet.bag_id];
    result.state_trace.push_back("sort_result_ready:" + result.frame_packet.bag_id);
    slot.result = std::move(result);
    slot.result_ready = true;
}

std::vector<InspectionResult> SortReorderBuffer::collect_ready() {
    std::vector<InspectionResult> ready;
    const auto now = Clock::now();

    while (!order_.empty()) {
        const auto bag_id = order_.front();
        auto found = slots_.find(bag_id);
        if (found == slots_.end()) {
            order_.pop_front();
            registered_.erase(bag_id);
            continue;
        }

        auto& slot = found->second;
        if (slot.result_ready) {
            auto result = slot.result;
            result.state_trace.push_back("sort_reorder_release:" + bag_id);
            ready.push_back(std::move(result));
            slots_.erase(found);
            registered_.erase(bag_id);
            order_.pop_front();
            continue;
        }

        if (now - slot.registered_at >= result_timeout_) {
            auto result = make_fail_safe_bag_result(
                slot.representative,
                "sort_result_timeout_fail_safe_ng",
                true);
            result.state_trace.push_back("sort_reorder_timeout:" + bag_id);
            ready.push_back(std::move(result));
            slots_.erase(found);
            registered_.erase(bag_id);
            order_.pop_front();
            continue;
        }

        break;
    }
    return ready;
}

}  // namespace waterbag
