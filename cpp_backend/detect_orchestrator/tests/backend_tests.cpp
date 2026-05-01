#include <cassert>
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <memory>
#include <string>

#include "detect_orchestrator/bag_runtime.hpp"
#include "detect_orchestrator/config.hpp"
#include "detect_orchestrator/pipeline.hpp"
#include "detect_orchestrator/storage.hpp"

namespace {

std::filesystem::path make_file(const std::filesystem::path& path) {
    std::filesystem::create_directories(path.parent_path());
    std::ofstream out(path);
    out << "test";
    return path;
}

std::shared_ptr<waterbag::InspectionPipeline> make_pipeline(waterbag::PlcConfig plc_config = {}) {
    waterbag::DetectionConfig detection;
    detection.presence_enabled = true;
    detection.advance_on_presence = true;
    detection.advance_trigger_camera_id = 0;

    waterbag::CorrelationConfig correlation;
    correlation.pending_timeout = waterbag::Milliseconds{10};

    auto presence = std::make_shared<waterbag::MockDetector>("mock-presence");
    auto primary = std::make_shared<waterbag::MockDetector>("mock-primary");
    auto patch = std::make_shared<waterbag::MockDetector>("mock-patch");
    auto burst_capture = std::make_shared<waterbag::MockCameraBurstCapture>();
    auto plc = std::make_shared<waterbag::MockSemanticPlcController>(plc_config);
    burst_capture->start();

    return std::make_shared<waterbag::InspectionPipeline>(
        detection,
        correlation,
        burst_capture,
        plc,
        presence,
        primary,
        patch);
}

int command_attempts(const waterbag::InspectionResult& result, const std::string& action) {
    int attempts = 0;
    for (const auto& feedback : result.execution_feedbacks) {
        if (feedback.action == action) {
            attempts += feedback.attempts;
        }
    }
    return attempts;
}

bool has_command_target(const waterbag::InspectionResult& result, const std::string& target) {
    for (const auto& command : result.control_commands) {
        if (command.target == target) {
            return true;
        }
    }
    return false;
}

bool trace_contains(const waterbag::InspectionResult& result, const std::string& needle) {
    return std::any_of(result.state_trace.begin(), result.state_trace.end(), [&](const auto& item) {
        return item.find(needle) != std::string::npos;
    });
}

std::vector<std::string> command_actions_for_station(const waterbag::InspectionResult& result, int camera_id) {
    std::vector<std::string> actions;
    const std::string upper = "camera" + std::to_string(camera_id) + "_upper_lever";
    const std::string bottom = "camera" + std::to_string(camera_id) + "_bottom_lever";
    for (const auto& command : result.control_commands) {
        if (command.target == upper || command.target == bottom) {
            actions.push_back(command.action);
        }
    }
    return actions;
}

void test_presence_gate_skips_empty_frame() {
    auto pipeline = make_pipeline();
    waterbag::CameraConfig camera{1, "A-camera", "camera1"};
    const auto path = make_file(std::filesystem::temp_directory_path() / "waterbag_cpp_tests" / "empty_cam1_background.jpg");

    auto packet = waterbag::make_frame_packet(camera, path);
    auto result = pipeline->process_station_packet(packet);

    assert(result.decision_result.control_action == "no_bag");
    assert(result.stage1_result.boxes.empty());
    assert(result.execution_feedbacks.empty());
}

void test_presence_triggers_lever_actions_before_defect_decision() {
    waterbag::PlcConfig plc_config;
    plc_config.mock_fail_first_attempts = 1;
    plc_config.max_retries = 1;
    auto pipeline = make_pipeline(plc_config);

    waterbag::CameraConfig cam1{1, "A-camera", "camera1"};
    waterbag::CameraConfig cam2{2, "B-camera", "camera2"};
    const auto root = std::filesystem::temp_directory_path() / "waterbag_cpp_tests";

    auto p1 = waterbag::make_frame_packet(cam1, make_file(root / "bag_100_cam1_good.jpg"));
    auto r1 = pipeline->process_station_packet(p1);
    assert(r1.presence_result.is_defect());
    assert(r1.decision_result.control_action == "defect_queued");
    assert(command_attempts(r1, "push_bag_after_capture") == 2);
    assert(command_attempts(r1, "restore_after_push") == 2);
    assert(command_attempts(r1, "release_bag_after_capture") == 2);
    assert(command_attempts(r1, "restore_blocking_position") == 2);
    assert(has_command_target(r1, "camera1_upper_lever"));
    assert(has_command_target(r1, "camera1_bottom_lever"));
    const auto cam1_actions = command_actions_for_station(r1, 1);
    assert(cam1_actions.size() == 4);
    assert(cam1_actions[0] == "release_bag_after_capture");
    assert(cam1_actions[1] == "push_bag_after_capture");
    assert(cam1_actions[2] == "restore_after_push");
    assert(cam1_actions[3] == "restore_blocking_position");
    auto d1 = pipeline->process_defect_packet(p1);
    assert(d1.decision_result.control_action == "await_peer_camera");
    assert(command_attempts(d1, "push_bag_after_capture") == 0);

    auto p2 = waterbag::make_frame_packet(cam2, make_file(root / "bag_100_cam2_good.jpg"));
    auto r2 = pipeline->process_station_packet(p2);
    assert(r2.decision_result.control_action == "defect_queued");
    assert(command_attempts(r2, "push_bag_after_capture") == 2);
    assert(command_attempts(r2, "restore_after_push") == 2);
    assert(command_attempts(r2, "release_bag_after_capture") == 2);
    assert(command_attempts(r2, "restore_blocking_position") == 2);
    assert(has_command_target(r2, "camera2_upper_lever"));
    assert(has_command_target(r2, "camera2_bottom_lever"));
    const auto cam2_actions = command_actions_for_station(r2, 2);
    assert(cam2_actions.size() >= 4);
    assert(cam2_actions[0] == "release_bag_after_capture");
    assert(cam2_actions[1] == "push_bag_after_capture");
    auto d2 = pipeline->process_defect_packet(p2);
    assert(d2.decision_result.control_action == "accept");
    assert(command_attempts(d2, "route_to_ok_bin") == 0);
    auto sorted = pipeline->execute_sort_command(d2);
    assert(command_attempts(sorted, "route_to_ok_bin") == 2);
}

void test_jsonl_storage_contains_presence_fields() {
    auto pipeline = make_pipeline();
    waterbag::CameraConfig camera{1, "A-camera", "camera1"};
    const auto root = std::filesystem::temp_directory_path() / "waterbag_cpp_tests";
    const auto result_path = root / "results.jsonl";
    std::filesystem::remove(result_path);

    auto packet = waterbag::make_frame_packet(camera, make_file(root / "bag_200_cam1_defect.jpg"));
    auto result = pipeline->process_packet(packet);
    waterbag::JsonlResultRepository repo(result_path);
    repo.save(result);

    std::ifstream input(result_path);
    std::string line;
    std::getline(input, line);
    assert(line.find("\"bag_present\":true") != std::string::npos);
    assert(line.find("\"advance_control_ms\"") != std::string::npos);
    assert(line.find("\"control_commands\"") != std::string::npos);
    assert(line.find("push_bag_after_capture") != std::string::npos);
    assert(line.find("burst_alignment") != std::string::npos);
    assert(line.find("unified_hardware_clock") != std::string::npos);
}

void test_burst_alignment_uses_unified_hardware_clock() {
    waterbag::CameraConfig camera{1, "A-camera", "camera1"};
    const auto root = std::filesystem::temp_directory_path() / "waterbag_cpp_tests";
    auto packet = waterbag::make_frame_packet(camera, make_file(root / "bag_300_cam1_good.jpg"));
    auto session = waterbag::make_capture_session(packet);
    const auto plan = waterbag::make_production_burst_plan();

    waterbag::MockCameraBurstCapture camera_burst;
    waterbag::MockSemanticPlcController plc({});
    camera_burst.start();
    camera_burst.arm_burst(session, plan);
    plc.start_light_burst(session, plan);

    auto group = camera_burst.poll_completed_group(session.capture_session_id);
    assert(group.has_value());
    const auto alignments = waterbag::align_camera_and_plc_events(*group, plc.read_burst_events(session.capture_session_id));
    assert(alignments.size() == plan.frames.size());
    for (const auto& alignment : alignments) {
        assert(alignment.light_on_before_exposure);
        assert(alignment.light_off_after_exposure);
        assert(alignment.within_jitter_tolerance);
        assert(alignment.trigger_to_exposure_jitter_us == 0);
        assert(alignment.hardware_clock_source == waterbag::UnifiedHardwareClock::source_name());
    }
}

void test_station_packet_exports_burst_images_for_defect_worker() {
    auto pipeline = make_pipeline();
    waterbag::CameraConfig camera{1, "A-camera", "camera1"};
    const auto root = std::filesystem::temp_directory_path() / "waterbag_cpp_tests";
    auto packet = waterbag::make_frame_packet(camera, make_file(root / "bag_400_cam1_good.jpg"));

    auto result = pipeline->process_station_packet(packet);

    assert(result.decision_result.control_action == "defect_queued");
    assert(result.frame_packet.metadata.at("burst.image_count") == "3");
    assert(result.frame_packet.metadata.at("burst.images.0.light_id") == "L1_BACKLIGHT");
    assert(result.frame_packet.metadata.at("burst.images.1.light_id") == "L2L3_DUAL_DARKFIELD");
    assert(result.frame_packet.metadata.at("burst.images.2.light_id") == "L4_CROSS_POLARIZED");
    assert(trace_contains(result, "burst_detection_inputs:3"));
}

void test_defect_detection_fuses_multi_light_burst_inputs() {
    auto pipeline = make_pipeline();
    waterbag::CameraConfig cam1{1, "A-camera", "camera1"};
    waterbag::CameraConfig cam2{2, "B-camera", "camera2"};
    const auto root = std::filesystem::temp_directory_path() / "waterbag_cpp_tests";
    auto packet1 = waterbag::make_frame_packet(cam1, make_file(root / "bag_401_cam1_micro.jpg"));
    auto packet2 = waterbag::make_frame_packet(cam2, make_file(root / "bag_401_cam2_good.jpg"));

    auto station1 = pipeline->process_station_packet(packet1);
    auto defect1 = pipeline->process_defect_packet(station1.frame_packet);

    assert(trace_contains(defect1, "defect_inputs:3"));
    assert(trace_contains(defect1, "stage1_light:L1_BACKLIGHT:boxes=0"));
    assert(trace_contains(defect1, "stage2_light:L2L3_DUAL_DARKFIELD:boxes=1"));
    assert(trace_contains(defect1, "stage2_fused:boxes=3"));
    assert(defect1.stage2_result.detector_backend.find("multi_light_fusion") != std::string::npos);
    assert(defect1.stage2_result.boxes.size() == 3);
    assert(defect1.decision_result.control_action == "await_peer_camera");
    assert(defect1.decision_result.stage_source == "stage2");

    auto station2 = pipeline->process_station_packet(packet2);
    auto defect2 = pipeline->process_defect_packet(station2.frame_packet);
    assert(defect2.decision_result.control_action == "reject");
    assert(defect2.decision_result.reason == "aggregate_defect_detected");
    assert(command_attempts(defect2, "route_to_ng_bin") == 0);
}

void test_bag_capture_assembler_waits_for_six_images() {
    auto pipeline = make_pipeline();
    waterbag::BagCaptureAssembler assembler({1, 2}, 3, waterbag::Milliseconds{100});
    waterbag::CameraConfig cam1{1, "A-camera", "camera1"};
    waterbag::CameraConfig cam2{2, "B-camera", "camera2"};
    const auto root = std::filesystem::temp_directory_path() / "waterbag_cpp_tests";

    auto p1 = waterbag::make_frame_packet(cam1, make_file(root / "bag_500_cam1_good.jpg"));
    auto p2 = waterbag::make_frame_packet(cam2, make_file(root / "bag_500_cam2_good.jpg"));
    auto r1 = pipeline->process_station_packet(p1);
    auto r2 = pipeline->process_station_packet(p2);

    auto first = assembler.register_station_capture(r1);
    assert(first.empty());
    auto complete = assembler.register_station_capture(r2);
    assert(complete.size() == 2);
    assert(complete[0].camera_id == 1);
    assert(complete[1].camera_id == 2);
    assert(complete[0].metadata.at("burst.image_count") == "3");
    assert(complete[1].metadata.at("burst.image_count") == "3");
    assert(complete[0].metadata.at("burst.images.0.side") == "A");
    assert(complete[1].metadata.at("burst.images.0.side") == "B");
    assert(complete[0].metadata.at("burst.images.0.trigger_hw_ns").size() > 0);
    assert(complete[0].metadata.at("burst.images.0.encoder_position") == "500");
}

void test_sort_reorder_buffer_releases_results_by_bag_order() {
    waterbag::SortReorderBuffer reorder(waterbag::Milliseconds{1000});
    waterbag::CameraConfig cam1{1, "A-camera", "camera1"};
    const auto root = std::filesystem::temp_directory_path() / "waterbag_cpp_tests";

    auto p1 = waterbag::make_frame_packet(cam1, make_file(root / "bag_600_cam1_good.jpg"));
    auto p2 = waterbag::make_frame_packet(cam1, make_file(root / "bag_601_cam1_good.jpg"));
    reorder.register_bag(p1);
    reorder.register_bag(p2);

    auto r2 = waterbag::make_fail_safe_bag_result(p2, "synthetic_ng_2", false);
    reorder.store_result(r2);
    assert(reorder.collect_ready().empty());

    auto r1 = waterbag::make_fail_safe_bag_result(p1, "synthetic_ng_1", false);
    reorder.store_result(r1);
    auto ready = reorder.collect_ready();
    assert(ready.size() == 2);
    assert(ready[0].frame_packet.bag_id == p1.bag_id);
    assert(ready[1].frame_packet.bag_id == p2.bag_id);
}

void test_config_loads_presence_settings() {
    const auto config = waterbag::load_app_config("config/cpp_backend/demo.ini");
    assert(config.detection.presence_enabled);
    assert(config.detection.advance_on_presence);
    assert(config.detection.advance_trigger_camera_id == 0);
    assert(config.runtime.defect_worker_count == 4);
    assert(config.runtime.expected_burst_images_per_camera == 3);
    assert(config.runtime.bag_capture_timeout == waterbag::Milliseconds{1500});
    assert(config.runtime.sort_result_timeout == waterbag::Milliseconds{1500});
    assert(config.runtime.cameras.size() == 2);
}

}  // namespace

int main() {
    test_presence_gate_skips_empty_frame();
    test_presence_triggers_lever_actions_before_defect_decision();
    test_jsonl_storage_contains_presence_fields();
    test_burst_alignment_uses_unified_hardware_clock();
    test_station_packet_exports_burst_images_for_defect_worker();
    test_defect_detection_fuses_multi_light_burst_inputs();
    test_bag_capture_assembler_waits_for_six_images();
    test_sort_reorder_buffer_releases_results_by_bag_order();
    test_config_loads_presence_settings();
    return 0;
}
