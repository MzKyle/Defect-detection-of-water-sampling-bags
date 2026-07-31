#include "detect_orchestrator/schemas.hpp"

#include <algorithm>
#include <atomic>
#include <cctype>
#include <ctime>
#include <iomanip>

namespace waterbag {
namespace {

std::atomic<std::uint64_t> g_frame_counter{0};
std::atomic<std::uint64_t> g_command_counter{0};

std::string lower_copy(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

std::string trim_suffix_after_token(std::string stem, const std::string& token) {
    const auto lowered = lower_copy(stem);
    const auto pos = lowered.find(token);
    if (pos == std::string::npos) {
        return stem;
    }
    return stem.substr(0, pos);
}

std::string make_hex_id(std::uint64_t value) {
    std::ostringstream out;
    out << std::hex << std::setw(12) << std::setfill('0') << value;
    return out.str();
}

}  // namespace

std::string infer_bag_id(const std::filesystem::path& source_path) {
    std::string stem = source_path.stem().string();
    for (const auto& token : {"_cam", "-cam", "_camera", "-camera", "_front", "-front", "_back", "-back", "_a", "-a", "_b", "-b"}) {
        const auto candidate = trim_suffix_after_token(stem, token);
        if (candidate != stem && !candidate.empty()) {
            return candidate;
        }
    }
    return stem;
}

FramePacket make_frame_packet(const CameraConfig& camera, const std::filesystem::path& source_path) {
    FramePacket packet;
    packet.frame_id = "cam" + std::to_string(camera.id) + "-" + make_hex_id(++g_frame_counter);
    packet.bag_id = infer_bag_id(source_path);
    packet.camera_id = camera.id;
    packet.camera_name = camera.name;
    packet.source_path = source_path;
    packet.source_name = source_path.filename().string();
    packet.received_at = SystemClock::now();
    packet.enqueued_at = packet.received_at;
    if (std::filesystem::exists(source_path)) {
        packet.source_mtime = std::filesystem::last_write_time(source_path);
    }
    return packet;
}

FramePacket make_synthetic_frame_packet(const CameraConfig& camera, const std::string& source_name) {
    FramePacket packet;
    packet.frame_id = "cam" + std::to_string(camera.id) + "-" + make_hex_id(++g_frame_counter);
    packet.bag_id = source_name + "-" + make_hex_id(g_frame_counter.load());
    packet.camera_id = camera.id;
    packet.camera_name = camera.name;
    packet.source_path = source_name;
    packet.source_name = source_name;
    packet.received_at = SystemClock::now();
    packet.enqueued_at = packet.received_at;
    packet.source = "synthetic";
    return packet;
}

std::string make_command_id() {
    return "cmd-" + make_hex_id(++g_command_counter);
}

std::string status_from_action(const std::string& action, bool timed_out) {
    if (timed_out) {
        return "timeout";
    }
    if (action == "fault" || action == "line_stop") {
        return "fault";
    }
    if (action == "reject") {
        return "defect";
    }
    if (action == "accept") {
        return "ok";
    }
    if (action == "no_bag") {
        return "no_bag";
    }
    if (action == "defect_queued") {
        return "captured";
    }
    if (action == "capture_invalid") {
        return "capture_invalid";
    }
    return "pending";
}

std::string to_string(RuntimeFaultCode code) {
    switch (code) {
        case RuntimeFaultCode::StationQueueSaturated:
            return "station_queue_saturated";
        case RuntimeFaultCode::DefectQueueSaturated:
            return "defect_queue_saturated";
        case RuntimeFaultCode::SortQueueSaturated:
            return "sort_queue_saturated";
        case RuntimeFaultCode::ThreadException:
            return "thread_exception";
        case RuntimeFaultCode::PlcCommunicationLost:
            return "plc_communication_lost";
        case RuntimeFaultCode::ResultStorageFailed:
            return "result_storage_failed";
        case RuntimeFaultCode::BagIdMissing:
            return "bag_id_missing";
        case RuntimeFaultCode::BagIdRegression:
            return "bag_id_regression";
        case RuntimeFaultCode::CheckpointFailed:
            return "checkpoint_failed";
        case RuntimeFaultCode::DeviceException:
            return "device_exception";
        case RuntimeFaultCode::ModelException:
            return "model_exception";
        case RuntimeFaultCode::FilesystemException:
            return "filesystem_exception";
    }
    return "unknown_runtime_fault";
}

std::string to_string(RuntimeState state) {
    switch (state) {
        case RuntimeState::Starting:
            return "starting";
        case RuntimeState::Running:
            return "running";
        case RuntimeState::FaultLatched:
            return "fault_latched";
        case RuntimeState::Stopped:
            return "stopped";
    }
    return "unknown";
}

InspectionResult make_runtime_fault_result(const RuntimeFault& fault) {
    InspectionResult result;
    result.runtime_fault = fault;
    result.frame_packet.frame_id = fault.frame_id.value_or("runtime-fault");
    result.frame_packet.bag_id = fault.bag_id.value_or("");
    result.frame_packet.camera_name = fault.source;
    result.frame_packet.source = "runtime_fault";
    result.frame_packet.received_at = fault.occurred_at;
    result.frame_packet.enqueued_at = fault.occurred_at;
    result.frame_packet.metadata["runtime_fault.code"] = to_string(fault.code);
    result.frame_packet.metadata["runtime_fault.source"] = fault.source;
    result.frame_packet.metadata["runtime_fault.detail"] = fault.detail;
    result.frame_packet.metadata["runtime_fault.line_stop_confirmed"] = fault.line_stop_confirmed ? "true" : "false";
    result.decision_result.finalized = true;
    result.decision_result.control_action = "fault";
    result.decision_result.reason = to_string(fault.code);
    result.bag_summary.bag_id = result.frame_packet.bag_id;
    result.bag_summary.finalized = true;
    result.bag_summary.aggregate_action = "fault";
    result.bag_summary.aggregate_reason = to_string(fault.code);
    result.state_trace.push_back("runtime_fault:" + to_string(fault.code) + ":" + fault.detail);
    return result;
}

std::string system_time_to_iso(SystemClock::time_point value) {
    const auto millis = std::chrono::duration_cast<std::chrono::milliseconds>(value.time_since_epoch()) % 1000;
    const std::time_t raw = SystemClock::to_time_t(value);
    std::tm tm{};
#if defined(_WIN32)
    localtime_s(&tm, &raw);
#else
    localtime_r(&raw, &tm);
#endif
    std::ostringstream out;
    out << std::put_time(&tm, "%Y-%m-%dT%H:%M:%S")
        << '.' << std::setw(3) << std::setfill('0') << millis.count();
    return out.str();
}

}  // namespace waterbag
