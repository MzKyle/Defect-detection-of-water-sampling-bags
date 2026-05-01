#include "PLC_driver/plc.hpp"

#include <thread>

namespace waterbag {

MockPlcTransport::MockPlcTransport(PlcConfig config) : config_(config) {}

ExecutionFeedback MockPlcTransport::send_once(const ControlCommand& command) {
    const auto started = Clock::now();
    if (config_.mock_ack_latency.count() > 0) {
        std::this_thread::sleep_for(config_.mock_ack_latency);
    }

    const int attempt = ++attempts_by_command_[command.command_id];
    const bool success = attempt > config_.mock_fail_first_attempts;

    ExecutionFeedback feedback;
    feedback.command_id = command.command_id;
    feedback.frame_id = command.frame_id;
    feedback.target = command.target;
    feedback.action = command.action;
    feedback.success = success;
    feedback.latency_ms = elapsed_ms(started);
    feedback.attempts = 1;
    feedback.detail = success ? "mock_ack" : "mock_nack";
    return feedback;
}

ReliablePlcController::ReliablePlcController(PlcConfig config, std::unique_ptr<IPlcTransport> transport)
    : config_(config), transport_(std::move(transport)) {}

ExecutionFeedback ReliablePlcController::execute(const ControlCommand& command) {
    const auto started = Clock::now();
    std::vector<std::string> attempt_details;

    if (!config_.enabled) {
        ExecutionFeedback feedback;
        feedback.command_id = command.command_id;
        feedback.frame_id = command.frame_id;
        feedback.target = command.target;
        feedback.action = command.action;
        feedback.success = true;
        feedback.latency_ms = 0.0;
        feedback.attempts = 0;
        feedback.detail = "plc_disabled";
        return feedback;
    }

    ExecutionFeedback last_feedback;
    const int max_attempts = config_.max_retries + 1;
    for (int attempt = 1; attempt <= max_attempts; ++attempt) {
        last_feedback = transport_->send_once(command);
        last_feedback.timed_out = last_feedback.latency_ms > static_cast<double>(config_.ack_timeout.count());
        attempt_details.push_back(
            "attempt=" + std::to_string(attempt) + ":" + last_feedback.detail + (last_feedback.timed_out ? ":timeout" : ""));

        if (last_feedback.success && !last_feedback.timed_out) {
            last_feedback.latency_ms = elapsed_ms(started);
            last_feedback.attempts = attempt;
            last_feedback.ack_timeout_ms = static_cast<double>(config_.ack_timeout.count());
            last_feedback.attempt_details = attempt_details;
            return last_feedback;
        }

        if (attempt < max_attempts && config_.retry_interval.count() > 0) {
            std::this_thread::sleep_for(config_.retry_interval);
        }
    }

    last_feedback.success = false;
    last_feedback.latency_ms = elapsed_ms(started);
    last_feedback.attempts = max_attempts;
    last_feedback.ack_timeout_ms = static_cast<double>(config_.ack_timeout.count());
    last_feedback.attempt_details = attempt_details;
    if (last_feedback.detail.empty()) {
        last_feedback.detail = "plc_failed";
    }
    return last_feedback;
}

}  // namespace waterbag
