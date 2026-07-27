#include <cassert>
#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <memory>
#include <mutex>
#include <netinet/in.h>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>
#include <string>

#include "detect_orchestrator/bag_runtime.hpp"
#include "detect_orchestrator/config.hpp"
#include "detect_orchestrator/pipeline.hpp"
#include "detect_orchestrator/storage.hpp"
#include "mock_camera_driver/mock_burst_capture.hpp"

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

void push_u16(std::vector<std::uint8_t>& bytes, std::uint16_t value) {
    bytes.push_back(static_cast<std::uint8_t>((value >> 8) & 0xFF));
    bytes.push_back(static_cast<std::uint8_t>(value & 0xFF));
}

std::uint16_t read_u16(const std::vector<std::uint8_t>& bytes, std::size_t offset) {
    return static_cast<std::uint16_t>((static_cast<std::uint16_t>(bytes[offset]) << 8) | bytes[offset + 1]);
}

bool recv_exact(int fd, std::vector<std::uint8_t>& bytes, std::size_t size) {
    bytes.assign(size, 0);
    std::size_t received = 0;
    while (received < size) {
        const auto ret = recv(fd, bytes.data() + received, size - received, 0);
        if (ret <= 0) {
            return false;
        }
        received += static_cast<std::size_t>(ret);
    }
    return true;
}

class FakeModbusTcpServer {
public:
    FakeModbusTcpServer() {
        discrete_inputs_.resize(128);
        input_registers_.resize(128);
        holding_registers_.resize(128);
        coils_.resize(128);

        server_fd_ = socket(AF_INET, SOCK_STREAM, 0);
        assert(server_fd_ >= 0);
        int enabled = 1;
        setsockopt(server_fd_, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));

        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        address.sin_port = htons(0);
        assert(bind(server_fd_, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
        assert(listen(server_fd_, 16) == 0);

        socklen_t length = sizeof(address);
        assert(getsockname(server_fd_, reinterpret_cast<sockaddr*>(&address), &length) == 0);
        port_ = ntohs(address.sin_port);
        running_ = true;
        thread_ = std::thread(&FakeModbusTcpServer::accept_loop, this);
    }

    ~FakeModbusTcpServer() {
        stop();
    }

    int port() const {
        return port_;
    }

    void set_presence(bool present) {
        std::lock_guard<std::mutex> lock(mutex_);
        discrete_inputs_[0] = present;
    }

    void set_message_id(std::uint16_t value) {
        std::lock_guard<std::mutex> lock(mutex_);
        input_registers_[0] = value;
    }

    void set_bag_id(std::uint32_t value) {
        std::lock_guard<std::mutex> lock(mutex_);
        input_registers_[1] = static_cast<std::uint16_t>((value >> 16) & 0xFFFFU);
        input_registers_[2] = static_cast<std::uint16_t>(value & 0xFFFFU);
    }

    bool coil(std::size_t address) const {
        std::lock_guard<std::mutex> lock(mutex_);
        return address < coils_.size() && coils_[address];
    }

    std::uint16_t holding_register(std::size_t address) const {
        std::lock_guard<std::mutex> lock(mutex_);
        return address < holding_registers_.size() ? holding_registers_[address] : 0;
    }

private:
    void stop() {
        bool expected = true;
        if (!running_.compare_exchange_strong(expected, false)) {
            return;
        }
        shutdown(server_fd_, SHUT_RDWR);
        close(server_fd_);
        if (thread_.joinable()) {
            thread_.join();
        }
    }

    void accept_loop() {
        while (running_) {
            const int client = accept(server_fd_, nullptr, nullptr);
            if (client < 0) {
                if (running_) {
                    continue;
                }
                break;
            }
            handle_client(client);
            close(client);
        }
    }

    void handle_client(int client) {
        std::vector<std::uint8_t> header;
        if (!recv_exact(client, header, 7)) {
            return;
        }
        const auto length = read_u16(header, 4);
        if (length < 2) {
            return;
        }
        std::vector<std::uint8_t> pdu;
        if (!recv_exact(client, pdu, static_cast<std::size_t>(length - 1))) {
            return;
        }
        if (pdu.empty()) {
            return;
        }

        const auto function = pdu[0];
        std::vector<std::uint8_t> response_pdu;
        try {
            if (function == 0x02) {
                response_pdu = read_bits(pdu);
            } else if (function == 0x04) {
                response_pdu = read_input_registers(pdu);
            } else if (function == 0x05) {
                response_pdu = write_single_coil(pdu);
            } else if (function == 0x06) {
                response_pdu = write_single_register(pdu);
            } else {
                response_pdu = {static_cast<std::uint8_t>(function | 0x80U), 0x01};
            }
        } catch (...) {
            response_pdu = {static_cast<std::uint8_t>(function | 0x80U), 0x04};
        }

        std::vector<std::uint8_t> response;
        response.push_back(header[0]);
        response.push_back(header[1]);
        response.push_back(0);
        response.push_back(0);
        push_u16(response, static_cast<std::uint16_t>(response_pdu.size() + 1));
        response.push_back(header[6]);
        response.insert(response.end(), response_pdu.begin(), response_pdu.end());
        send(client, response.data(), response.size(), 0);
    }

    std::vector<std::uint8_t> read_bits(const std::vector<std::uint8_t>& pdu) {
        const auto address = read_u16(pdu, 1);
        const auto count = read_u16(pdu, 3);
        std::vector<std::uint8_t> response{0x02, static_cast<std::uint8_t>((count + 7) / 8), 0};
        std::lock_guard<std::mutex> lock(mutex_);
        for (std::uint16_t i = 0; i < count; ++i) {
            if (address + i < discrete_inputs_.size() && discrete_inputs_[address + i]) {
                response[2 + i / 8] |= static_cast<std::uint8_t>(1U << (i % 8));
            }
        }
        return response;
    }

    std::vector<std::uint8_t> read_input_registers(const std::vector<std::uint8_t>& pdu) {
        const auto address = read_u16(pdu, 1);
        const auto count = read_u16(pdu, 3);
        std::vector<std::uint8_t> response{0x04, static_cast<std::uint8_t>(count * 2)};
        std::lock_guard<std::mutex> lock(mutex_);
        for (std::uint16_t i = 0; i < count; ++i) {
            const auto value = address + i < input_registers_.size() ? input_registers_[address + i] : 0;
            push_u16(response, value);
        }
        return response;
    }

    std::vector<std::uint8_t> write_single_coil(const std::vector<std::uint8_t>& pdu) {
        const auto address = read_u16(pdu, 1);
        const bool value = read_u16(pdu, 3) == 0xFF00;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (address < coils_.size()) {
                coils_[address] = value;
            }
            if (value) {
                input_registers_[10] = 1;
            }
        }
        return pdu;
    }

    std::vector<std::uint8_t> write_single_register(const std::vector<std::uint8_t>& pdu) {
        const auto address = read_u16(pdu, 1);
        const auto value = read_u16(pdu, 3);
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (address < holding_registers_.size()) {
                holding_registers_[address] = value;
            }
        }
        return pdu;
    }

    int server_fd_ = -1;
    int port_ = 0;
    std::atomic_bool running_{false};
    std::thread thread_;
    mutable std::mutex mutex_;
    std::vector<bool> discrete_inputs_;
    std::vector<std::uint16_t> input_registers_;
    std::vector<std::uint16_t> holding_registers_;
    std::vector<bool> coils_;
};

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
    assert(result.decision_result.reason == "plc_laser_no_bag");
    assert(result.stage1_result.boxes.empty());
    assert(result.execution_feedbacks.empty());
}

void test_plc_laser_presence_message_drives_gate() {
    auto pipeline = make_pipeline();
    waterbag::CameraConfig camera{1, "A-camera", "camera1"};
    const auto path = make_file(std::filesystem::temp_directory_path() / "waterbag_cpp_tests" / "empty_name_but_plc_present.jpg");

    auto packet = waterbag::make_frame_packet(camera, path);
    packet.metadata["plc.laser_present"] = "true";
    packet.metadata["plc.presence_message_id"] = "plc-msg-42";
    packet.metadata["plc.bag_id"] = "bag_from_plc_42";
    auto result = pipeline->process_station_packet(packet);

    assert(result.presence_result.detector_backend == "plc_laser");
    assert(result.presence_result.is_defect());
    assert(result.decision_result.control_action == "defect_queued");
    assert(result.frame_packet.bag_id == "bag_from_plc_42");
    assert(result.frame_packet.metadata.at("presence.message_id") == "plc-msg-42");
    assert(trace_contains(result, "plc_laser_presence:bag_present"));
}

void test_plc_laser_presence_timeout_is_reported() {
    waterbag::PlcConfig plc_config;
    plc_config.presence_message_timeout = waterbag::Milliseconds{1};
    plc_config.mock_presence_latency = waterbag::Milliseconds{2};
    auto pipeline = make_pipeline(plc_config);
    waterbag::CameraConfig camera{1, "A-camera", "camera1"};
    const auto path = make_file(std::filesystem::temp_directory_path() / "waterbag_cpp_tests" / "bag_099_cam1_good.jpg");

    auto packet = waterbag::make_frame_packet(camera, path);
    auto result = pipeline->process_station_packet(packet);

    assert(result.decision_result.control_action == "no_bag");
    assert(result.decision_result.reason == "plc_laser_presence_timeout");
    assert(result.decision_result.timed_out);
    assert(result.bag_summary.timed_out);
    assert(result.frame_packet.metadata.at("presence.timed_out") == "true");
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
    assert(line.find("\"presence_source\":\"plc_laser\"") != std::string::npos);
    assert(line.find("\"presence_message_valid\":true") != std::string::npos);
    assert(line.find("\"advance_control_ms\"") != std::string::npos);
    assert(line.find("\"control_commands\"") != std::string::npos);
    assert(line.find("push_bag_after_capture") != std::string::npos);
    assert(line.find("burst_alignment") != std::string::npos);
    assert(line.find("unified_hardware_clock") != std::string::npos);
}

void test_jsonl_storage_can_write_asynchronously() {
    auto pipeline = make_pipeline();
    waterbag::CameraConfig camera{1, "A-camera", "camera1"};
    const auto root = std::filesystem::temp_directory_path() / "waterbag_cpp_tests";
    const auto result_path = root / "async_results.jsonl";
    std::filesystem::remove(result_path);

    auto packet = waterbag::make_frame_packet(camera, make_file(root / "bag_201_cam1_defect.jpg"));
    auto result = pipeline->process_packet(packet);
    waterbag::JsonlResultRepository repo(result_path, true, 8, true);
    repo.save(result);
    repo.close();

    std::ifstream input(result_path);
    std::string line;
    std::getline(input, line);
    assert(line.find("\"bag_id\":\"bag_201\"") != std::string::npos);
    assert(repo.dropped_results() == 0);
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

waterbag::PlcConfig make_modbus_test_config(int port) {
    waterbag::PlcConfig config;
    config.backend = "modbus_tcp";
    config.ack_timeout = waterbag::Milliseconds{200};
    config.presence_message_timeout = waterbag::Milliseconds{200};
    config.max_retries = 0;
    config.modbus_tcp.host = "127.0.0.1";
    config.modbus_tcp.port = port;
    config.modbus_tcp.connect_timeout = waterbag::Milliseconds{100};
    config.modbus_tcp.read_timeout = waterbag::Milliseconds{100};
    config.modbus_tcp.write_timeout = waterbag::Milliseconds{100};
    config.modbus_tcp.ack_timeout = waterbag::Milliseconds{200};
    config.modbus_tcp.ack_poll_interval = waterbag::Milliseconds{1};
    return config;
}

void test_modbus_tcp_presence_false() {
    FakeModbusTcpServer server;
    server.set_presence(false);
    server.set_message_id(7);

    waterbag::ModbusTcpPlcController plc(make_modbus_test_config(server.port()));
    waterbag::CameraConfig camera{1, "A-camera", "camera1"};
    auto packet = waterbag::make_synthetic_frame_packet(camera, "plc_presence_camera1");

    const auto presence = plc.read_laser_presence(packet);
    assert(!presence.bag_present);
    assert(presence.message_valid);
    assert(presence.message_id == "modbus-7");
    assert(presence.detail == "modbus_tcp_laser_clear");
}

void test_modbus_tcp_presence_bag_id_and_commands() {
    FakeModbusTcpServer server;
    server.set_presence(true);
    server.set_message_id(42);
    server.set_bag_id(1234);

    waterbag::ModbusTcpPlcController plc(make_modbus_test_config(server.port()));
    waterbag::CameraConfig camera{1, "A-camera", "camera1"};
    auto packet = waterbag::make_synthetic_frame_packet(camera, "plc_presence_camera1");

    const auto presence = plc.read_laser_presence(packet);
    assert(presence.bag_present);
    assert(presence.message_valid);
    assert(presence.bag_id == "1234");
    const auto duplicate = plc.read_laser_presence(packet);
    assert(!duplicate.bag_present);
    assert(duplicate.detail == "modbus_tcp_duplicate_presence_ignored");

    packet.bag_id = presence.bag_id;
    auto session = waterbag::make_capture_session(packet);
    const auto plan = waterbag::make_production_burst_plan();
    const auto burst_ack = plc.start_light_burst(session, plan);
    assert(burst_ack.success);
    assert(burst_ack.detail == "modbus_tcp_ack_success");
    assert(server.holding_register(1) == 0);
    assert(server.holding_register(2) == 1234);
    assert(server.holding_register(6) == 3);
    assert(server.holding_register(20) == 1);
    assert(server.holding_register(21) == 100);
    assert(plc.read_burst_events(session.capture_session_id).size() == plan.frames.size());

    const auto station_feedbacks = plc.release_station_after_capture(session);
    assert(station_feedbacks.size() == 4);
    for (const auto& feedback : station_feedbacks) {
        assert(feedback.success);
        assert(feedback.detail == "modbus_tcp_ack_success");
    }

    const auto ok_feedback = plc.route_to_ok_bin(packet);
    assert(ok_feedback.success);
    assert(server.holding_register(5) == 20);
    const auto ng_feedback = plc.route_to_ng_bin(packet);
    assert(ng_feedback.success);
    assert(server.holding_register(5) == 21);
}

void test_modbus_tcp_hardware_check() {
    FakeModbusTcpServer server;
    server.set_presence(true);
    server.set_message_id(88);

    waterbag::ModbusTcpPlcController plc(make_modbus_test_config(server.port()));
    const auto result = plc.check_hardware();
    assert(result.success);
    assert(!result.details.empty());
}

void test_config_loads_presence_settings() {
    const auto config = waterbag::load_app_config("config/cpp_backend/demo.ini");
    assert(config.runtime.input_mode == "watch_dir");
    assert(config.runtime.camera_backend == "mock");
    assert(config.runtime.plc_backend == "mock");
    assert(config.detection.presence_enabled);
    assert(config.detection.advance_on_presence);
    assert(config.detection.advance_trigger_camera_id == 0);
    assert(config.plc.presence_message_timeout == waterbag::Milliseconds{200});
    assert(config.runtime.defect_worker_count == 4);
    assert(config.runtime.expected_burst_images_per_camera == 3);
    assert(config.runtime.bag_capture_timeout == waterbag::Milliseconds{1500});
    assert(config.runtime.sort_result_timeout == waterbag::Milliseconds{1500});
    assert(config.storage.async_result_writes);
    assert(config.storage.result_queue_capacity == 512);
    assert(config.storage.drop_results_when_full);
    assert(config.camera_driver.backend == "mock");
    assert(config.camera_driver.default_trigger_source == "Line0");
    assert(config.camera_driver.enable_chunk_timestamp);
    assert(config.runtime.cameras.size() == 2);
}

void test_config_loads_hardware_modbus_settings() {
    const auto config = waterbag::load_app_config("config/cpp_backend/hardware_hik_mvs_modbus.ini");
    assert(config.runtime.input_mode == "plc_presence");
    assert(!config.runtime.publish_no_bag_results);
    assert(config.camera_driver.backend == "hikvision_mvs");
    assert(config.plc.backend == "modbus_tcp");
    assert(config.plc.modbus_tcp.host == "192.168.1.50");
    assert(config.plc.modbus_tcp.port == 502);
    assert(config.plc.modbus_tcp.coil_start_burst == 0);
    assert(config.plc.modbus_tcp.holding_register_burst_frame_count == 6);
    assert(config.runtime.camera_backend == "hikvision_mvs");
    assert(config.runtime.plc_backend == "modbus_tcp");
}

}  // namespace

int main() {
    test_presence_gate_skips_empty_frame();
    test_plc_laser_presence_message_drives_gate();
    test_plc_laser_presence_timeout_is_reported();
    test_presence_triggers_lever_actions_before_defect_decision();
    test_jsonl_storage_contains_presence_fields();
    test_jsonl_storage_can_write_asynchronously();
    test_burst_alignment_uses_unified_hardware_clock();
    test_station_packet_exports_burst_images_for_defect_worker();
    test_defect_detection_fuses_multi_light_burst_inputs();
    test_bag_capture_assembler_waits_for_six_images();
    test_sort_reorder_buffer_releases_results_by_bag_order();
    test_modbus_tcp_presence_false();
    test_modbus_tcp_presence_bag_id_and_commands();
    test_modbus_tcp_hardware_check();
    test_config_loads_presence_settings();
    test_config_loads_hardware_modbus_settings();
    return 0;
}
