#pragma once

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <map>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

namespace waterbag {

using Clock = std::chrono::steady_clock;
using SystemClock = std::chrono::system_clock;
using Milliseconds = std::chrono::milliseconds;

struct CameraConfig {
    int id = 0;
    std::string name;
    std::filesystem::path watch_dir;
    std::string serial_number;
    std::string device_user_id;
    int device_index = -1;
    std::string trigger_source;
    std::string trigger_activation;
};

struct CameraDriverConfig {
    std::string backend = "mock";
    std::filesystem::path output_dir = "artifacts/cpp_backend/captures";
    Milliseconds frame_timeout{1500};
    bool enable_ptp = true;
    bool enable_chunk_timestamp = true;
    bool host_correlate_camera_time = true;
    bool apply_frame_settings = true;
    std::string default_trigger_source = "Line0";
    std::string default_trigger_activation = "RisingEdge";
    std::string save_format = "jpg";
    int jpeg_quality = 90;
};

struct RuntimeConfig {
    std::vector<CameraConfig> cameras;
    Milliseconds poll_interval{100};
    Milliseconds file_stable_for{300};
    Milliseconds file_ready_timeout{5000};
    Milliseconds cooldown{300};
    std::size_t queue_capacity = 256;
    std::size_t defect_worker_count = 4;
    std::size_t expected_burst_images_per_camera = 3;
    Milliseconds bag_capture_timeout{1500};
    Milliseconds sort_result_timeout{1500};
};

struct DetectionConfig {
    bool presence_enabled = true;
    bool advance_on_presence = true;
    int advance_trigger_camera_id = 0;
    bool patch_enabled = true;
    int patch_horizontal = 4;
    int patch_vertical = 5;
    double primary_conf_threshold = 0.30;
    double patch_conf_threshold = 0.20;
};

struct CorrelationConfig {
    bool enabled = true;
    std::vector<int> expected_camera_ids{1, 2};
    bool hold_non_defect_until_complete = true;
    Milliseconds pending_timeout{1500};
    Milliseconds finalized_retention{5000};
    std::string timeout_action = "reject";
};

struct PlcConfig {
    bool enabled = true;
    Milliseconds ack_timeout{200};
    Milliseconds presence_message_timeout{200};
    int max_retries = 1;
    Milliseconds retry_interval{50};
    int mock_fail_first_attempts = 0;
    Milliseconds mock_ack_latency{0};
    Milliseconds mock_presence_latency{0};
};

struct DetectionBox {
    int x1 = 0;
    int y1 = 0;
    int x2 = 0;
    int y2 = 0;
    std::string label = "defect";
    double confidence = 0.0;
};

enum class DetectionStage {
    Presence,
    Stage1,
    Stage2
};

inline std::string to_string(DetectionStage stage) {
    if (stage == DetectionStage::Presence) {
        return "presence";
    }
    return stage == DetectionStage::Stage1 ? "stage1" : "stage2";
}

struct FramePacket {
    std::string frame_id;
    std::string bag_id;
    int camera_id = 0;
    std::string camera_name;
    std::filesystem::path source_path;
    std::string source_name;
    SystemClock::time_point received_at = SystemClock::now();
    SystemClock::time_point enqueued_at = SystemClock::now();
    std::optional<std::filesystem::file_time_type> source_mtime;
    bool replayed = false;
    std::string source = "runtime";
    std::map<std::string, std::string> metadata;
};

struct PerceptionResult {
    std::string stage_name;
    std::string detector_backend;
    std::vector<DetectionBox> boxes;
    double inference_ms = 0.0;
    bool triggered = false;

    bool is_defect() const {
        return !boxes.empty();
    }
};

struct DecisionResult {
    bool is_defect = false;
    bool repeated = false;
    bool should_run_stage2 = false;
    bool finalized = false;
    bool timed_out = false;
    std::string stage_source = "none";
    std::string control_action = "await_peer_camera";
    std::string reason = "await_peer_camera";
    std::vector<DetectionBox> final_boxes;
};

struct ControlCommand {
    std::string command_id;
    std::string frame_id;
    std::string bag_id;
    std::string target = "bag_controller";
    std::string action;
    SystemClock::time_point created_at = SystemClock::now();
};

struct ExecutionFeedback {
    std::string command_id;
    std::string frame_id;
    std::string target;
    std::string action;
    bool success = false;
    double latency_ms = 0.0;
    int attempts = 0;
    bool timed_out = false;
    double ack_timeout_ms = 0.0;
    std::string detail;
    std::vector<std::string> attempt_details;
};

struct CameraObservation {
    int camera_id = 0;
    std::string camera_name;
    std::string frame_id;
    bool is_defect = false;
    bool repeated = false;
    std::string stage_source;
    int final_box_count = 0;
    SystemClock::time_point received_at = SystemClock::now();
    std::optional<std::filesystem::file_time_type> source_mtime;
};

struct BagSummary {
    std::string bag_id;
    bool finalized = false;
    bool timed_out = false;
    bool stale_frame_ignored = false;
    bool new_command_required = false;
    bool aggregate_defect = false;
    bool aggregate_repeated = false;
    std::string aggregate_action = "await_peer_camera";
    std::string aggregate_reason = "await_peer_camera";
    std::vector<int> missing_camera_ids;
    std::vector<CameraObservation> observations;
};

struct TimingBreakdown {
    double queue_delay_ms = 0.0;
    double presence_inference_ms = 0.0;
    double advance_control_ms = 0.0;
    double stage1_inference_ms = 0.0;
    double stage2_inference_ms = 0.0;
    double decision_ms = 0.0;
    double correlation_ms = 0.0;
    double control_ms = 0.0;
    double total_ms = 0.0;
};

struct InspectionResult {
    FramePacket frame_packet;
    PerceptionResult presence_result;
    PerceptionResult stage1_result;
    PerceptionResult stage2_result;
    DecisionResult decision_result;
    BagSummary bag_summary;
    std::vector<ControlCommand> control_commands;
    std::vector<ExecutionFeedback> execution_feedbacks;
    TimingBreakdown timing;
    std::vector<std::string> state_trace;
};

inline double elapsed_ms(Clock::time_point started) {
    return std::chrono::duration<double, std::milli>(Clock::now() - started).count();
}

inline std::string join_ints(const std::vector<int>& values, const std::string& sep = ",") {
    std::ostringstream out;
    for (std::size_t i = 0; i < values.size(); ++i) {
        if (i > 0) {
            out << sep;
        }
        out << values[i];
    }
    return out.str();
}

std::string infer_bag_id(const std::filesystem::path& source_path);
FramePacket make_frame_packet(const CameraConfig& camera, const std::filesystem::path& source_path);
std::string make_command_id();
std::string status_from_action(const std::string& action, bool timed_out);
std::string system_time_to_iso(SystemClock::time_point value);

}  // namespace waterbag
