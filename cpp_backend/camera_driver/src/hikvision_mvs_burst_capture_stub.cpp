#include "camera_driver/burst_capture.hpp"

#include <stdexcept>

namespace waterbag {

std::shared_ptr<ICameraBurstCapture> make_hikvision_mvs_burst_capture(
    CameraDriverConfig,
    RuntimeConfig) {
    throw std::runtime_error(
        "Hikvision MVS SDK is not available in this build. Reconfigure with the SDK installed under /opt/MVS or keep camera_driver.backend=mock.");
}

}  // namespace waterbag