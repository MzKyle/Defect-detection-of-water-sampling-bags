#pragma once

#include <memory>
#include <mutex>

#include "detect_orchestrator/correlator.hpp"
#include "detect_orchestrator/detector.hpp"
#include "camera_driver/burst_capture.hpp"
#include "PLC_driver/plc_controller.hpp"

namespace waterbag {

class InspectionPipeline {
public:
    InspectionPipeline(
        DetectionConfig detection_config,
        CorrelationConfig correlation_config,
        std::shared_ptr<ICameraBurstCapture> burst_capture,
        std::shared_ptr<IPlcController> plc_controller,
        std::shared_ptr<IDetector> primary_detector,
        std::shared_ptr<IDetector> patch_detector);

    InspectionResult process_packet(FramePacket packet);
    InspectionResult process_station_packet(FramePacket packet);
    InspectionResult process_defect_packet(FramePacket packet);
    InspectionResult execute_sort_command(InspectionResult result);
    std::vector<InspectionResult> flush_timeouts();

private:
    DecisionResult build_local_decision(
        const FramePacket& packet,
        const PerceptionResult& stage1_result,
        const PerceptionResult& stage2_result,
        bool should_run_stage2) const;

    DecisionResult build_final_decision(
        const DecisionResult& local_decision,
        const BagSummary& summary) const;

    std::vector<ControlCommand> build_commands(const FramePacket& packet, const DecisionResult& decision) const;
    std::vector<ControlCommand> build_advance_commands(const FramePacket& packet, const PerceptionResult& presence_result) const;
    std::vector<ControlCommand> build_commands_from_feedbacks(const std::vector<ExecutionFeedback>& feedbacks, const FramePacket& packet) const;
    InspectionResult build_timeout_result(const TimedOutBagContext& context);

    DetectionConfig detection_config_;
    BagCorrelator correlator_;
    BurstPlan burst_plan_;
    std::shared_ptr<ICameraBurstCapture> burst_capture_;
    std::shared_ptr<IPlcController> plc_controller_;
    std::shared_ptr<IDetector> primary_detector_;
    std::shared_ptr<IDetector> patch_detector_;
    std::mutex correlation_mutex_;
};

}  // namespace waterbag
