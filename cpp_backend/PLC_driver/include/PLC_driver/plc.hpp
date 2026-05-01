#pragma once

#include <memory>
#include <unordered_map>

#include "detect_orchestrator/schemas.hpp"

namespace waterbag {

class IPlcTransport {
public:
    virtual ~IPlcTransport() = default;
    virtual ExecutionFeedback send_once(const ControlCommand& command) = 0;
};

class MockPlcTransport final : public IPlcTransport {
public:
    explicit MockPlcTransport(PlcConfig config);

    ExecutionFeedback send_once(const ControlCommand& command) override;

private:
    PlcConfig config_;
    std::unordered_map<std::string, int> attempts_by_command_;
};

class ReliablePlcController {
public:
    ReliablePlcController(PlcConfig config, std::unique_ptr<IPlcTransport> transport);

    ExecutionFeedback execute(const ControlCommand& command);

private:
    PlcConfig config_;
    std::unique_ptr<IPlcTransport> transport_;
};

}  // namespace waterbag
