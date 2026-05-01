#include <cctype>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

#include "detect_orchestrator/detector.hpp"
#include "detect_orchestrator/pipeline.hpp"

namespace {

std::string lower_copy(std::string value) {
    for (auto& ch : value) {
        ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    }
    return value;
}

void touch_file(const std::filesystem::path& path) {
    std::filesystem::create_directories(path.parent_path());
    std::ofstream out(path, std::ios::binary);
    out << "demo";
}

int infer_camera_id_from_name(const std::filesystem::path& path) {
    const auto name = lower_copy(path.filename().string());
    if (name.find("cam2") != std::string::npos ||
        name.find("camera2") != std::string::npos ||
        name.find("_b_") != std::string::npos ||
        name.find("-b-") != std::string::npos ||
        name.find("_b.") != std::string::npos ||
        name.find("-b.") != std::string::npos) {
        return 2;
    }
    return 1;
}

const waterbag::CameraConfig& find_camera(const std::vector<waterbag::CameraConfig>& cameras, int camera_id) {
    for (const auto& camera : cameras) {
        if (camera.id == camera_id) {
            return camera;
        }
    }
    return cameras.front();
}

void print_result(const waterbag::InspectionResult& result) {
    int attempts = 0;
    bool plc_success = true;
    for (const auto& feedback : result.execution_feedbacks) {
        attempts += feedback.attempts;
        plc_success = plc_success && feedback.success;
    }

    std::cout
        << "frame=" << result.frame_packet.frame_id
        << " bag=" << result.frame_packet.bag_id
        << " camera=" << result.frame_packet.camera_id
        << " action=" << result.decision_result.control_action
        << " reason=" << result.decision_result.reason
        << " status=" << waterbag::status_from_action(result.decision_result.control_action, result.decision_result.timed_out)
        << " boxes=" << result.decision_result.final_boxes.size()
        << " plc_success=" << (plc_success ? "true" : "false")
        << " ack_attempts=" << attempts
        << " total_ms=" << result.timing.total_ms
        << '\n';
}

}  // namespace

int main(int argc, char** argv) {
    std::vector<waterbag::CameraConfig> cameras{
        {1, "A-camera", "camera1"},
        {2, "B-camera", "camera2"},
    };

    waterbag::DetectionConfig detection_config;
    waterbag::CorrelationConfig correlation_config;
    waterbag::PlcConfig plc_config;
    plc_config.mock_fail_first_attempts = 1;
    plc_config.max_retries = 1;

    waterbag::DetectorConfig detector_config;
    auto primary_detector = waterbag::make_detector(detector_config.primary, "mock-primary");
    auto patch_detector = waterbag::make_detector(detector_config.patch, "mock-patch");
    auto presence_detector = waterbag::make_detector(detector_config.presence, "mock-presence");
    auto burst_capture = std::make_shared<waterbag::MockCameraBurstCapture>();
    auto plc = std::make_shared<waterbag::MockSemanticPlcController>(plc_config);
    burst_capture->start();

    waterbag::InspectionPipeline pipeline(
        detection_config,
        correlation_config,
        burst_capture,
        plc,
        presence_detector,
        primary_detector,
        patch_detector);

    std::vector<std::filesystem::path> inputs;
    if (argc > 1) {
        for (int i = 1; i < argc; ++i) {
            inputs.emplace_back(argv[i]);
        }
    } else {
        const auto root = std::filesystem::temp_directory_path() / "waterbag_cpp_smoke";
        inputs = {
            root / "camera1" / "bag_0001_cam1_good.jpg",
            root / "camera2" / "bag_0001_cam2_good.jpg",
            root / "camera1" / "bag_0002_cam1_defect.jpg",
            root / "camera1" / "bag_0003_cam1_micro.jpg",
            root / "camera1" / "empty_cam1_background.jpg",
        };
        for (const auto& path : inputs) {
            touch_file(path);
        }
    }

    for (const auto& path : inputs) {
        const int camera_id = infer_camera_id_from_name(path);
        auto packet = waterbag::make_frame_packet(find_camera(cameras, camera_id), path);
        packet.source = "cpp_demo";
        print_result(pipeline.process_packet(packet));
    }

    for (const auto& timeout_result : pipeline.flush_timeouts()) {
        print_result(timeout_result);
    }

    return 0;
}
