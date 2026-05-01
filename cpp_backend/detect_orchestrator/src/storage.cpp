#include "detect_orchestrator/storage.hpp"

#include <algorithm>
#include <iomanip>
#include <sstream>

namespace waterbag {
namespace {

std::string json_escape(const std::string& value) {
    std::ostringstream out;
    for (char ch : value) {
        switch (ch) {
            case '\\':
                out << "\\\\";
                break;
            case '"':
                out << "\\\"";
                break;
            case '\n':
                out << "\\n";
                break;
            case '\r':
                out << "\\r";
                break;
            case '\t':
                out << "\\t";
                break;
            default:
                out << ch;
        }
    }
    return out.str();
}

void write_string(std::ostringstream& out, const std::string& key, const std::string& value, bool comma = true) {
    out << "\"" << key << "\":\"" << json_escape(value) << "\"";
    if (comma) {
        out << ",";
    }
}

void write_number(std::ostringstream& out, const std::string& key, double value, bool comma = true) {
    out << "\"" << key << "\":" << std::fixed << std::setprecision(3) << value;
    if (comma) {
        out << ",";
    }
}

void write_bool(std::ostringstream& out, const std::string& key, bool value, bool comma = true) {
    out << "\"" << key << "\":" << (value ? "true" : "false");
    if (comma) {
        out << ",";
    }
}

void write_boxes(std::ostringstream& out, const std::vector<DetectionBox>& boxes) {
    out << "\"boxes\":[";
    for (std::size_t i = 0; i < boxes.size(); ++i) {
        const auto& box = boxes[i];
        if (i > 0) {
            out << ",";
        }
        out << "{";
        out << "\"x1\":" << box.x1 << ",\"y1\":" << box.y1 << ",\"x2\":" << box.x2 << ",\"y2\":" << box.y2 << ",";
        write_string(out, "label", box.label);
        write_number(out, "confidence", box.confidence, false);
        out << "}";
    }
    out << "]";
}

void write_string_array(std::ostringstream& out, const std::string& key, const std::vector<std::string>& values, bool comma = true) {
    out << "\"" << key << "\":[";
    for (std::size_t i = 0; i < values.size(); ++i) {
        if (i > 0) {
            out << ",";
        }
        out << "\"" << json_escape(values[i]) << "\"";
    }
    out << "]";
    if (comma) {
        out << ",";
    }
}

void write_commands(std::ostringstream& out, const std::vector<ControlCommand>& commands) {
    out << "\"control_commands\":[";
    for (std::size_t i = 0; i < commands.size(); ++i) {
        const auto& command = commands[i];
        if (i > 0) {
            out << ",";
        }
        out << "{";
        write_string(out, "command_id", command.command_id);
        write_string(out, "target", command.target);
        write_string(out, "action", command.action);
        write_string(out, "bag_id", command.bag_id, false);
        out << "}";
    }
    out << "]";
}

void write_feedbacks(std::ostringstream& out, const std::vector<ExecutionFeedback>& feedbacks) {
    out << "\"execution_feedbacks\":[";
    for (std::size_t i = 0; i < feedbacks.size(); ++i) {
        const auto& feedback = feedbacks[i];
        if (i > 0) {
            out << ",";
        }
        out << "{";
        write_string(out, "command_id", feedback.command_id);
        write_string(out, "target", feedback.target);
        write_string(out, "action", feedback.action);
        write_bool(out, "success", feedback.success);
        out << "\"attempts\":" << feedback.attempts << ",";
        write_number(out, "latency_ms", feedback.latency_ms);
        write_string(out, "detail", feedback.detail, false);
        out << "}";
    }
    out << "]";
}

}  // namespace

JsonlResultRepository::JsonlResultRepository(
    std::filesystem::path path,
    bool async_writes,
    std::size_t queue_capacity,
    bool drop_when_full)
    : path_(std::move(path)),
      async_writes_(async_writes),
      queue_capacity_(std::max<std::size_t>(1, queue_capacity)),
      drop_when_full_(drop_when_full) {
    if (!path_.parent_path().empty()) {
        std::filesystem::create_directories(path_.parent_path());
    }
    if (async_writes_) {
        writer_thread_ = std::thread(&JsonlResultRepository::writer_loop, this);
    }
}

JsonlResultRepository::~JsonlResultRepository() {
    close();
}

void JsonlResultRepository::save(const InspectionResult& result) {
    auto line = inspection_result_to_json(result);
    if (!async_writes_) {
        append_line(line);
        return;
    }

    std::unique_lock<std::mutex> lock(queue_mutex_);
    if (stop_requested_) {
        lock.unlock();
        append_line(line);
        return;
    }
    if (drop_when_full_) {
        if (queue_.size() >= queue_capacity_) {
            queue_.pop_front();
            ++dropped_results_;
        }
    } else {
        cv_.wait(lock, [&] {
            return stop_requested_ || queue_.size() < queue_capacity_;
        });
        if (stop_requested_) {
            return;
        }
    }
    queue_.push_back(std::move(line));
    lock.unlock();
    cv_.notify_one();
}

void JsonlResultRepository::close() {
    if (!async_writes_) {
        return;
    }
    {
        std::lock_guard<std::mutex> lock(queue_mutex_);
        stop_requested_ = true;
    }
    cv_.notify_all();
    if (writer_thread_.joinable()) {
        writer_thread_.join();
    }
}

const std::filesystem::path& JsonlResultRepository::path() const {
    return path_;
}

std::size_t JsonlResultRepository::dropped_results() const {
    return dropped_results_.load();
}

void JsonlResultRepository::append_line(const std::string& line) {
    std::lock_guard<std::mutex> lock(file_mutex_);
    std::ofstream out(path_, std::ios::app);
    out << line << '\n';
}

void JsonlResultRepository::writer_loop() {
    while (true) {
        std::string line;
        {
            std::unique_lock<std::mutex> lock(queue_mutex_);
            cv_.wait(lock, [&] {
                return stop_requested_ || !queue_.empty();
            });
            if (queue_.empty() && stop_requested_) {
                break;
            }
            line = std::move(queue_.front());
            queue_.pop_front();
        }
        cv_.notify_all();
        append_line(line);
    }
}

std::string inspection_result_to_json(const InspectionResult& result) {
    std::ostringstream out;
    const bool plc_success = std::all_of(
        result.execution_feedbacks.begin(),
        result.execution_feedbacks.end(),
        [](const auto& feedback) { return feedback.success; });

    int ack_attempts = 0;
    bool ack_retry = false;
    for (const auto& feedback : result.execution_feedbacks) {
        ack_attempts += feedback.attempts;
        ack_retry = ack_retry || feedback.attempts > 1;
    }

    out << "{";
    write_string(out, "timestamp", system_time_to_iso(result.frame_packet.received_at));
    write_string(out, "frame_id", result.frame_packet.frame_id);
    write_string(out, "bag_id", result.frame_packet.bag_id);
    out << "\"camera_id\":" << result.frame_packet.camera_id << ",";
    write_string(out, "camera_name", result.frame_packet.camera_name);
    write_string(out, "source_path", result.frame_packet.source_path.string());
    write_bool(out, "bag_present", result.presence_result.is_defect());
    write_number(out, "presence_ms", result.timing.presence_inference_ms);
    write_string(out, "status", status_from_action(result.decision_result.control_action, result.decision_result.timed_out));
    write_string(out, "action", result.decision_result.control_action);
    write_string(out, "reason", result.decision_result.reason);
    write_string(out, "stage_source", result.decision_result.stage_source);
    write_bool(out, "is_defect", result.decision_result.is_defect);
    write_bool(out, "finalized", result.decision_result.finalized);
    write_bool(out, "timed_out", result.decision_result.timed_out);
    write_bool(out, "stale_frame_ignored", result.bag_summary.stale_frame_ignored);
    write_bool(out, "plc_success", plc_success);
    out << "\"ack_attempts\":" << ack_attempts << ",";
    write_bool(out, "ack_retry", ack_retry);
    write_number(out, "latency_ms", result.timing.total_ms);
    write_number(out, "advance_control_ms", result.timing.advance_control_ms);
    write_number(out, "stage1_ms", result.timing.stage1_inference_ms);
    write_number(out, "stage2_ms", result.timing.stage2_inference_ms);
    write_number(out, "control_ms", result.timing.control_ms);
    write_boxes(out, result.decision_result.final_boxes);
    out << ",";
    write_commands(out, result.control_commands);
    out << ",";
    write_feedbacks(out, result.execution_feedbacks);
    out << ",";
    write_string_array(out, "state_trace", result.state_trace, false);
    out << "}";
    return out.str();
}

}  // namespace waterbag
