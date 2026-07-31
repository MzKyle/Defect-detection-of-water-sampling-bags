#pragma once

#include <atomic>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <thread>

#include "PLC_driver/plc_controller.hpp"
#include "detect_orchestrator/storage.hpp"

namespace waterbag {

struct LineSafetyOptions {
    Milliseconds heartbeat_interval{500};
    int heartbeat_failure_threshold = 3;
};

class LineSafetyController {
public:
    using FaultCallback = std::function<void(const RuntimeFault&)>;

    LineSafetyController(
        std::shared_ptr<IPlcController> plc,
        std::shared_ptr<IResultSink> result_sink,
        LineSafetyOptions options = {});
    ~LineSafetyController();

    LineSafetyController(const LineSafetyController&) = delete;
    LineSafetyController& operator=(const LineSafetyController&) = delete;

    void start();
    void stop();
    bool trip(RuntimeFault fault);
    RuntimeState state() const;
    bool fault_latched() const;
    std::optional<RuntimeFault> first_fault() const;
    void set_fault_callback(FaultCallback callback);

private:
    void heartbeat_loop();
    void persist_fault(RuntimeFault fault);

    std::shared_ptr<IPlcController> plc_;
    std::shared_ptr<IResultSink> result_sink_;
    LineSafetyOptions options_;
    mutable std::mutex mutex_;
    RuntimeState state_ = RuntimeState::Stopped;
    std::optional<RuntimeFault> first_fault_;
    FaultCallback callback_;
    std::atomic_bool stop_requested_{false};
    std::thread heartbeat_thread_;
};

}  // namespace waterbag
