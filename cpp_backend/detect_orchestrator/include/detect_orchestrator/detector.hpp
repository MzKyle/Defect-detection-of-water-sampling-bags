#pragma once

#include <memory>

#include "detect_orchestrator/config.hpp"

namespace waterbag {

class IDetector {
public:
    virtual ~IDetector() = default;
    virtual std::string backend_name() const = 0;
    virtual PerceptionResult detect(const FramePacket& packet, DetectionStage stage) = 0;
};

class MockDetector final : public IDetector {
public:
    explicit MockDetector(std::string backend = "mock");

    std::string backend_name() const override;
    PerceptionResult detect(const FramePacket& packet, DetectionStage stage) override;

private:
    std::string backend_;
};

std::shared_ptr<IDetector> make_detector(const ModelConfig& config, std::string mock_backend = "mock");

}  // namespace waterbag
