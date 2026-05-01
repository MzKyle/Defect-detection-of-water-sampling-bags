#pragma once

#include <chrono>
#include <cstdint>
#include <string>

namespace waterbag {

struct HardwareTimestamp {
    long long ns = 0;
};

class UnifiedHardwareClock {
public:
    static HardwareTimestamp now();
    static long long now_ns();
    static std::string source_name();
};

inline long long diff_us(HardwareTimestamp start, HardwareTimestamp end) {
    return (end.ns - start.ns) / 1000;
}

}  // namespace waterbag
