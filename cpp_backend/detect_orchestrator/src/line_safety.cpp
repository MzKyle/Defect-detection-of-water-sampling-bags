#include "detect_orchestrator/line_safety.hpp"

#include <iostream>

namespace waterbag {

LineSafetyController::LineSafetyController(
    std::shared_ptr<IPlcController> plc,
    std::shared_ptr<IResultSink> result_sink,
    LineSafetyOptions options)
    : plc_(std::move(plc)),
      result_sink_(std::move(result_sink)),
      options_(options) {
    if (options_.heartbeat_failure_threshold <= 0) {
        options_.heartbeat_failure_threshold = 3;
    }
    if (options_.heartbeat_interval.count() <= 0) {
        options_.heartbeat_interval = Milliseconds{500};
    }
}

LineSafetyController::~LineSafetyController() {
    stop();
}

void LineSafetyController::start() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (state_ == RuntimeState::Running) {
            return;
        }
        if (state_ == RuntimeState::FaultLatched) {
            return;
        }
        state_ = RuntimeState::Running;
        stop_requested_ = false;
    }
    heartbeat_thread_ = std::thread(&LineSafetyController::heartbeat_loop, this);
}

void LineSafetyController::stop() {
    stop_requested_ = true;
    if (heartbeat_thread_.joinable()) {
        heartbeat_thread_.join();
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (state_ != RuntimeState::FaultLatched) {
        state_ = RuntimeState::Stopped;
    }
}

bool LineSafetyController::trip(RuntimeFault fault) {
    FaultCallback callback;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (state_ == RuntimeState::FaultLatched) {
            return false;
        }
        fault.occurred_at = SystemClock::now();
        state_ = RuntimeState::FaultLatched;
        first_fault_ = fault;
        callback = callback_;
    }
    stop_requested_ = true;

    if (plc_) {
        try {
            auto feedback = plc_->request_line_stop(fault);
            fault.line_stop_confirmed = feedback.success;
            if (!feedback.success) {
                fault.detail += ";line_stop_unconfirmed:" + feedback.detail;
            }
        } catch (const std::exception& error) {
            fault.line_stop_confirmed = false;
            fault.detail += ";line_stop_unconfirmed:" + std::string(error.what());
        } catch (...) {
            fault.line_stop_confirmed = false;
            fault.detail += ";line_stop_unconfirmed:unknown";
        }
    }

    {
        std::lock_guard<std::mutex> lock(mutex_);
        first_fault_ = fault;
    }
    persist_fault(fault);
    if (callback) {
        callback(fault);
    }
    return true;
}

RuntimeState LineSafetyController::state() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return state_;
}

bool LineSafetyController::fault_latched() const {
    return state() == RuntimeState::FaultLatched;
}

std::optional<RuntimeFault> LineSafetyController::first_fault() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return first_fault_;
}

void LineSafetyController::set_fault_callback(FaultCallback callback) {
    std::lock_guard<std::mutex> lock(mutex_);
    callback_ = std::move(callback);
}

void LineSafetyController::heartbeat_loop() {
    int failures = 0;
    while (!stop_requested_) {
        std::this_thread::sleep_for(options_.heartbeat_interval);
        if (stop_requested_) {
            break;
        }
        bool ok = false;
        try {
            ok = plc_ ? plc_->send_heartbeat() : true;
        } catch (...) {
            ok = false;
        }
        if (ok) {
            failures = 0;
            continue;
        }
        ++failures;
        if (failures >= options_.heartbeat_failure_threshold) {
            RuntimeFault fault;
            fault.code = RuntimeFaultCode::PlcCommunicationLost;
            fault.source = "plc_heartbeat";
            fault.detail = "heartbeat failed " + std::to_string(failures) + " consecutive times";
            trip(std::move(fault));
            break;
        }
    }
}

void LineSafetyController::persist_fault(RuntimeFault fault) {
    if (!result_sink_) {
        std::cerr << "runtime_fault " << to_string(fault.code) << " " << fault.detail << "\n";
        return;
    }
    try {
        result_sink_->save(make_runtime_fault_result(fault));
    } catch (const std::exception& error) {
        std::cerr << "failed to persist runtime fault: " << error.what()
                  << "; original_fault=" << to_string(fault.code)
                  << "; detail=" << fault.detail << "\n";
    } catch (...) {
        std::cerr << "failed to persist runtime fault: unknown"
                  << "; original_fault=" << to_string(fault.code)
                  << "; detail=" << fault.detail << "\n";
    }
}

}  // namespace waterbag
