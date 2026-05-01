#include "detect_orchestrator/hardware_clock.hpp"

namespace waterbag {

HardwareTimestamp UnifiedHardwareClock::now() {
    return HardwareTimestamp{now_ns()};
}

long long UnifiedHardwareClock::now_ns() {
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    return std::chrono::duration_cast<std::chrono::nanoseconds>(now).count();
}

std::string UnifiedHardwareClock::source_name() {
    return "unified_hardware_clock_mock";
}

}  // namespace waterbag
