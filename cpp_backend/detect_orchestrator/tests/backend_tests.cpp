#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstring>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <mutex>
#include <netinet/in.h>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>
#include <string>

#include "detect_orchestrator/bag_runtime.hpp"
#include "detect_orchestrator/config.hpp"
#include "detect_orchestrator/line_safety.hpp"
#include "detect_orchestrator/pipeline.hpp"
#include "detect_orchestrator/runtime.hpp"
#include "detect_orchestrator/storage.hpp"
#include "mock_camera_driver/mock_burst_capture.hpp"

namespace {

class TestFailure final : public std::runtime_error {
public:
    TestFailure(const char* expression, const char* file, int line)
        : std::runtime_error(
              std::string(file) + ":" + std::to_string(line) +
              ": requirement failed: " + expression) {}
};

#define REQUIRE(expression) \
    do { \
        if (!(expression)) { \
            throw TestFailure(#expression, __FILE__, __LINE__); \
        } \
    } while (false)

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

class InMemoryResultSink final : public waterbag::IResultSink {
public:
    void save(const waterbag::InspectionResult& result) override {
        std::lock_guard<std::mutex> lock(mutex_);
        results.push_back(result);
    }

    void close() override {}

    std::vector<waterbag::InspectionResult> snapshot() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return results;
    }

    mutable std::mutex mutex_;
    std::vector<waterbag::InspectionResult> results;
};

class CountingPlcController final : public waterbag::IPlcController {
public:
    std::string backend_name() const override {
        return "counting";
    }

    waterbag::PlcLaserPresence read_laser_presence(const waterbag::FramePacket& packet) override {
        waterbag::PlcLaserPresence signal;
        signal.camera_id = packet.camera_id;
        signal.station_id = "camera" + std::to_string(packet.camera_id);
        signal.message_id = "counting-" + packet.frame_id;
        signal.bag_id = packet.bag_id;
        signal.bag_present = true;
        return signal;
    }

    waterbag::PlcAck start_light_burst(const waterbag::CaptureSession&, const waterbag::BurstPlan&) override {
        return waterbag::PlcAck{true, "ok", 0.0};
    }

    std::vector<waterbag::PlcBurstEvent> read_burst_events(const std::string&) override {
        return {};
    }

    std::vector<waterbag::ExecutionFeedback> release_station_after_capture(const waterbag::CaptureSession&) override {
        return {};
    }

    waterbag::ExecutionFeedback route_to_ok_bin(const waterbag::FramePacket& packet) override {
        return feedback(packet, "route_to_ok_bin", true);
    }

    waterbag::ExecutionFeedback route_to_ng_bin(const waterbag::FramePacket& packet) override {
        return feedback(packet, "route_to_ng_bin", true);
    }

    bool send_heartbeat() override {
        ++heartbeat_count;
        return heartbeat_ok;
    }

    waterbag::ExecutionFeedback request_line_stop(const waterbag::RuntimeFault& fault) override {
        ++line_stop_count;
        waterbag::FramePacket packet;
        packet.frame_id = fault.frame_id.value_or("runtime-fault");
        packet.bag_id = fault.bag_id.value_or("");
        return feedback(packet, "request_line_stop", line_stop_ok);
    }

    static waterbag::ExecutionFeedback feedback(const waterbag::FramePacket& packet, const std::string& action, bool success) {
        waterbag::ExecutionFeedback result;
        result.command_id = waterbag::make_command_id();
        result.frame_id = packet.frame_id;
        result.target = "test_plc";
        result.action = action;
        result.success = success;
        result.attempts = 1;
        result.detail = success ? "ok" : "failed";
        return result;
    }

    bool heartbeat_ok = true;
    bool line_stop_ok = true;
    std::atomic_int heartbeat_count{0};
    std::atomic_int line_stop_count{0};
};

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
        REQUIRE(server_fd_ >= 0);
        int enabled = 1;
        setsockopt(server_fd_, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));

        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        address.sin_port = htons(0);
        REQUIRE(bind(server_fd_, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
        REQUIRE(listen(server_fd_, 16) == 0);

        socklen_t length = sizeof(address);
        REQUIRE(getsockname(server_fd_, reinterpret_cast<sockaddr*>(&address), &length) == 0);
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

    void set_fault_code(std::uint16_t value) {
        std::lock_guard<std::mutex> lock(mutex_);
        input_registers_[11] = value;
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

    REQUIRE(result.decision_result.control_action == "no_bag");
    REQUIRE(result.decision_result.reason == "plc_laser_no_bag");
    REQUIRE(result.stage1_result.boxes.empty());
    REQUIRE(result.execution_feedbacks.empty());
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

    REQUIRE(result.presence_result.detector_backend == "plc_laser");
    REQUIRE(result.presence_result.is_defect());
    REQUIRE(result.decision_result.control_action == "defect_queued");
    REQUIRE(result.frame_packet.bag_id == "bag_from_plc_42");
    REQUIRE(result.frame_packet.metadata.at("presence.message_id") == "plc-msg-42");
    REQUIRE(trace_contains(result, "plc_laser_presence:bag_present"));
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

    REQUIRE(result.decision_result.control_action == "no_bag");
    REQUIRE(result.decision_result.reason == "plc_laser_presence_timeout");
    REQUIRE(result.decision_result.timed_out);
    REQUIRE(result.bag_summary.timed_out);
    REQUIRE(result.frame_packet.metadata.at("presence.timed_out") == "true");
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
    REQUIRE(r1.presence_result.is_defect());
    REQUIRE(r1.decision_result.control_action == "defect_queued");
    REQUIRE(command_attempts(r1, "push_bag_after_capture") == 2);
    REQUIRE(command_attempts(r1, "restore_after_push") == 2);
    REQUIRE(command_attempts(r1, "release_bag_after_capture") == 2);
    REQUIRE(command_attempts(r1, "restore_blocking_position") == 2);
    REQUIRE(has_command_target(r1, "camera1_upper_lever"));
    REQUIRE(has_command_target(r1, "camera1_bottom_lever"));
    const auto cam1_actions = command_actions_for_station(r1, 1);
    REQUIRE(cam1_actions.size() == 4);
    REQUIRE(cam1_actions[0] == "release_bag_after_capture");
    REQUIRE(cam1_actions[1] == "push_bag_after_capture");
    REQUIRE(cam1_actions[2] == "restore_after_push");
    REQUIRE(cam1_actions[3] == "restore_blocking_position");
    auto d1 = pipeline->process_defect_packet(p1);
    REQUIRE(d1.decision_result.control_action == "await_peer_camera");
    REQUIRE(command_attempts(d1, "push_bag_after_capture") == 0);

    auto p2 = waterbag::make_frame_packet(cam2, make_file(root / "bag_100_cam2_good.jpg"));
    auto r2 = pipeline->process_station_packet(p2);
    REQUIRE(r2.decision_result.control_action == "defect_queued");
    REQUIRE(command_attempts(r2, "push_bag_after_capture") == 2);
    REQUIRE(command_attempts(r2, "restore_after_push") == 2);
    REQUIRE(command_attempts(r2, "release_bag_after_capture") == 2);
    REQUIRE(command_attempts(r2, "restore_blocking_position") == 2);
    REQUIRE(has_command_target(r2, "camera2_upper_lever"));
    REQUIRE(has_command_target(r2, "camera2_bottom_lever"));
    const auto cam2_actions = command_actions_for_station(r2, 2);
    REQUIRE(cam2_actions.size() >= 4);
    REQUIRE(cam2_actions[0] == "release_bag_after_capture");
    REQUIRE(cam2_actions[1] == "push_bag_after_capture");
    auto d2 = pipeline->process_defect_packet(p2);
    REQUIRE(d2.decision_result.control_action == "accept");
    REQUIRE(command_attempts(d2, "route_to_ok_bin") == 0);
    auto sorted = pipeline->execute_sort_command(d2);
    REQUIRE(command_attempts(sorted, "route_to_ok_bin") == 2);
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
    REQUIRE(line.find("\"bag_present\":true") != std::string::npos);
    REQUIRE(line.find("\"presence_source\":\"plc_laser\"") != std::string::npos);
    REQUIRE(line.find("\"presence_message_valid\":true") != std::string::npos);
    REQUIRE(line.find("\"advance_control_ms\"") != std::string::npos);
    REQUIRE(line.find("\"capture_ms\"") != std::string::npos);
    REQUIRE(line.find("\"decision_ms\"") != std::string::npos);
    REQUIRE(line.find("\"correlation_ms\"") != std::string::npos);
    REQUIRE(line.find("\"bag_latency_ms\"") != std::string::npos);
    REQUIRE(line.find("\"control_commands\"") != std::string::npos);
    REQUIRE(line.find("push_bag_after_capture") != std::string::npos);
    REQUIRE(line.find("burst_alignment") != std::string::npos);
    REQUIRE(line.find("unified_hardware_clock") != std::string::npos);
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
    REQUIRE(line.find("\"bag_id\":\"bag_201\"") != std::string::npos);
    REQUIRE(repo.dropped_results() == 0);
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
    REQUIRE(group.has_value());
    const auto alignments = waterbag::align_camera_and_plc_events(*group, plc.read_burst_events(session.capture_session_id));
    REQUIRE(alignments.size() == plan.frames.size());
    for (const auto& alignment : alignments) {
        REQUIRE(alignment.light_on_before_exposure);
        REQUIRE(alignment.light_off_after_exposure);
        REQUIRE(alignment.within_jitter_tolerance);
        REQUIRE(alignment.trigger_to_exposure_jitter_us == 0);
        REQUIRE(alignment.hardware_clock_source == waterbag::UnifiedHardwareClock::source_name());
    }
}

void test_station_packet_exports_burst_images_for_defect_worker() {
    auto pipeline = make_pipeline();
    waterbag::CameraConfig camera{1, "A-camera", "camera1"};
    const auto root = std::filesystem::temp_directory_path() / "waterbag_cpp_tests";
    auto packet = waterbag::make_frame_packet(camera, make_file(root / "bag_400_cam1_good.jpg"));

    auto result = pipeline->process_station_packet(packet);

    REQUIRE(result.decision_result.control_action == "defect_queued");
    REQUIRE(result.frame_packet.metadata.at("burst.image_count") == "3");
    REQUIRE(result.frame_packet.metadata.at("burst.images.0.light_id") == "L1_BACKLIGHT");
    REQUIRE(result.frame_packet.metadata.at("burst.images.1.light_id") == "L2L3_DUAL_DARKFIELD");
    REQUIRE(result.frame_packet.metadata.at("burst.images.2.light_id") == "L4_CROSS_POLARIZED");
    REQUIRE(trace_contains(result, "burst_detection_inputs:3"));
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

    REQUIRE(trace_contains(defect1, "defect_inputs:3"));
    REQUIRE(trace_contains(defect1, "stage1_light:L1_BACKLIGHT:boxes=0"));
    REQUIRE(trace_contains(defect1, "stage2_light:L2L3_DUAL_DARKFIELD:boxes=1"));
    REQUIRE(trace_contains(defect1, "stage2_fused:boxes=3"));
    REQUIRE(defect1.stage2_result.detector_backend.find("multi_light_fusion") != std::string::npos);
    REQUIRE(defect1.stage2_result.boxes.size() == 3);
    REQUIRE(defect1.decision_result.control_action == "await_peer_camera");
    REQUIRE(defect1.decision_result.stage_source == "stage2");

    auto station2 = pipeline->process_station_packet(packet2);
    auto defect2 = pipeline->process_defect_packet(station2.frame_packet);
    REQUIRE(defect2.decision_result.control_action == "reject");
    REQUIRE(defect2.decision_result.reason == "aggregate_defect_detected");
    REQUIRE(command_attempts(defect2, "route_to_ng_bin") == 0);
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
    REQUIRE(first.empty());
    auto complete = assembler.register_station_capture(r2);
    REQUIRE(complete.size() == 2);
    REQUIRE(complete[0].camera_id == 1);
    REQUIRE(complete[1].camera_id == 2);
    REQUIRE(complete[0].metadata.at("burst.image_count") == "3");
    REQUIRE(complete[1].metadata.at("burst.image_count") == "3");
    REQUIRE(complete[0].metadata.at("burst.images.0.side") == "A");
    REQUIRE(complete[1].metadata.at("burst.images.0.side") == "B");
    REQUIRE(complete[0].metadata.at("burst.images.0.trigger_hw_ns").size() > 0);
    REQUIRE(complete[0].metadata.at("burst.images.0.encoder_position") == "500");
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
    REQUIRE(reorder.collect_ready().empty());

    auto r1 = waterbag::make_fail_safe_bag_result(p1, "synthetic_ng_1", false);
    reorder.store_result(r1);
    auto ready = reorder.collect_ready();
    REQUIRE(ready.size() == 2);
    REQUIRE(ready[0].frame_packet.bag_id == p1.bag_id);
    REQUIRE(ready[1].frame_packet.bag_id == p2.bag_id);
}

void test_line_safety_latches_fault_once_and_persists_fault_event() {
    auto plc = std::make_shared<CountingPlcController>();
    auto sink = std::make_shared<InMemoryResultSink>();
    waterbag::LineSafetyController safety(plc, sink);

    waterbag::RuntimeFault first;
    first.code = waterbag::RuntimeFaultCode::StationQueueSaturated;
    first.source = "test";
    first.detail = "first";
    first.frame_id = "frame-1";
    first.bag_id = "bag-1";

    waterbag::RuntimeFault second = first;
    second.detail = "second";

    REQUIRE(safety.trip(first));
    REQUIRE(!safety.trip(second));
    REQUIRE(safety.fault_latched());
    REQUIRE(plc->line_stop_count == 1);
    const auto results = sink->snapshot();
    REQUIRE(results.size() == 1);
    REQUIRE(results[0].runtime_fault.has_value());
    REQUIRE(results[0].runtime_fault->code == waterbag::RuntimeFaultCode::StationQueueSaturated);
    const auto json = waterbag::inspection_result_to_json(results[0]);
    REQUIRE(json.find("\"event_type\":\"runtime_fault\"") != std::string::npos);
    REQUIRE(json.find("\"status\":\"fault\"") != std::string::npos);
}

void test_line_safety_heartbeat_threshold_latches_plc_fault() {
    auto plc = std::make_shared<CountingPlcController>();
    plc->heartbeat_ok = false;
    auto sink = std::make_shared<InMemoryResultSink>();
    waterbag::LineSafetyOptions options;
    options.heartbeat_interval = waterbag::Milliseconds{5};
    options.heartbeat_failure_threshold = 3;
    waterbag::LineSafetyController safety(plc, sink, options);

    safety.start();
    const auto deadline = waterbag::Clock::now() + waterbag::Milliseconds{200};
    while (!safety.fault_latched() && waterbag::Clock::now() < deadline) {
        std::this_thread::sleep_for(waterbag::Milliseconds{5});
    }
    safety.stop();

    REQUIRE(safety.fault_latched());
    REQUIRE(plc->heartbeat_count >= 3);
    REQUIRE(plc->line_stop_count == 1);
    REQUIRE(safety.first_fault()->code == waterbag::RuntimeFaultCode::PlcCommunicationLost);
}

void test_jsonl_storage_throws_on_failed_write() {
    waterbag::JsonlResultRepository repo("/dev/full");
    waterbag::RuntimeFault fault;
    fault.code = waterbag::RuntimeFaultCode::ResultStorageFailed;
    fault.source = "test";
    fault.detail = "write failure";

    bool threw = false;
    try {
        repo.save(waterbag::make_runtime_fault_result(fault));
    } catch (const std::exception&) {
        threw = true;
    }
    REQUIRE(threw);
}

void test_runtime_station_queue_saturation_latches_fault_without_dropping_oldest() {
    auto plc = std::make_shared<CountingPlcController>();
    auto sink = std::make_shared<InMemoryResultSink>();
    auto safety = std::make_shared<waterbag::LineSafetyController>(plc, sink);
    waterbag::RuntimeConfig config;
    config.queue_capacity = 1;
    config.defect_worker_count = 1;
    config.input_mode = "watch_dir";
    config.cameras = {waterbag::CameraConfig{1, "A-camera", "camera1"}};
    auto runtime = waterbag::RealtimeRuntime(config, make_pipeline(), safety);
    const auto root = std::filesystem::temp_directory_path() / "waterbag_cpp_tests";

    runtime.submit_path(1, make_file(root / "bag_700_cam1_good.jpg"));
    runtime.submit_path(1, make_file(root / "bag_701_cam1_good.jpg"));
    runtime.submit_path(1, make_file(root / "bag_702_cam1_good.jpg"));

    REQUIRE(safety->fault_latched());
    REQUIRE(plc->line_stop_count == 1);
    const auto results = sink->snapshot();
    REQUIRE(results.size() == 1);
    REQUIRE(results[0].runtime_fault->code == waterbag::RuntimeFaultCode::StationQueueSaturated);
}

void test_runtime_listener_exception_latches_storage_fault() {
    auto plc = std::make_shared<CountingPlcController>();
    auto sink = std::make_shared<InMemoryResultSink>();
    auto safety = std::make_shared<waterbag::LineSafetyController>(plc, sink);
    waterbag::RuntimeConfig config;
    config.queue_capacity = 8;
    config.defect_worker_count = 1;
    config.input_mode = "watch_dir";
    config.poll_interval = waterbag::Milliseconds{5};
    config.file_stable_for = waterbag::Milliseconds{1};
    config.file_ready_timeout = waterbag::Milliseconds{200};
    config.cooldown = waterbag::Milliseconds{1};
    config.heartbeat_interval = waterbag::Milliseconds{20};
    config.cameras = {waterbag::CameraConfig{1, "A-camera", "camera1"}};
    waterbag::RealtimeRuntime runtime(config, make_pipeline(), safety);
    runtime.add_listener([](const waterbag::InspectionResult&) {
        throw std::runtime_error("listener disk failed");
    });

    runtime.start();
    runtime.submit_path(1, make_file(std::filesystem::temp_directory_path() / "waterbag_cpp_tests" / "bag_710_cam1_good.jpg"));
    const auto deadline = waterbag::Clock::now() + waterbag::Milliseconds{500};
    while (!safety->fault_latched() && waterbag::Clock::now() < deadline) {
        std::this_thread::sleep_for(waterbag::Milliseconds{10});
    }
    runtime.stop();

    REQUIRE(safety->fault_latched());
    REQUIRE(plc->line_stop_count == 1);
    REQUIRE(safety->first_fault()->code == waterbag::RuntimeFaultCode::ResultStorageFailed);
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
    config.presence_checkpoint_path =
        std::filesystem::temp_directory_path() /
        "waterbag_cpp_tests" /
        ("presence_checkpoint_" + std::to_string(port) + ".txt");
    std::filesystem::remove(config.presence_checkpoint_path);
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
    REQUIRE(!presence.bag_present);
    REQUIRE(presence.message_valid);
    REQUIRE(presence.message_id == "modbus-7");
    REQUIRE(presence.detail == "modbus_tcp_laser_clear");
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
    REQUIRE(presence.bag_present);
    REQUIRE(presence.message_valid);
    REQUIRE(presence.bag_id == "1234");
    const auto duplicate = plc.read_laser_presence(packet);
    REQUIRE(!duplicate.bag_present);
    REQUIRE(duplicate.detail == "modbus_tcp_duplicate_presence_ignored");

    packet.bag_id = presence.bag_id;
    auto session = waterbag::make_capture_session(packet);
    const auto plan = waterbag::make_production_burst_plan();
    const auto burst_ack = plc.start_light_burst(session, plan);
    REQUIRE(burst_ack.success);
    REQUIRE(burst_ack.detail == "modbus_tcp_ack_success");
    REQUIRE(server.holding_register(1) == 0);
    REQUIRE(server.holding_register(2) == 1234);
    REQUIRE(server.holding_register(6) == 3);
    REQUIRE(server.holding_register(20) == 1);
    REQUIRE(server.holding_register(21) == 100);
    REQUIRE(plc.read_burst_events(session.capture_session_id).size() == plan.frames.size());

    const auto station_feedbacks = plc.release_station_after_capture(session);
    REQUIRE(station_feedbacks.size() == 4);
    for (const auto& feedback : station_feedbacks) {
        REQUIRE(feedback.success);
        REQUIRE(feedback.detail == "modbus_tcp_ack_success");
    }

    const auto ok_feedback = plc.route_to_ok_bin(packet);
    REQUIRE(ok_feedback.success);
    REQUIRE(server.holding_register(5) == 20);
    const auto ng_feedback = plc.route_to_ng_bin(packet);
    REQUIRE(ng_feedback.success);
    REQUIRE(server.holding_register(5) == 21);

    REQUIRE(plc.send_heartbeat());
    waterbag::RuntimeFault fault;
    fault.code = waterbag::RuntimeFaultCode::ThreadException;
    fault.source = "test";
    fault.detail = "stop";
    fault.bag_id = packet.bag_id;
    const auto stop_feedback = plc.request_line_stop(fault);
    REQUIRE(stop_feedback.success);
    REQUIRE(server.holding_register(5) == 30);
}

void test_modbus_tcp_hardware_check() {
    FakeModbusTcpServer server;
    server.set_presence(true);
    server.set_message_id(88);

    waterbag::ModbusTcpPlcController plc(make_modbus_test_config(server.port()));
    const auto result = plc.check_hardware();
    REQUIRE(result.success);
    REQUIRE(!result.details.empty());
}

void test_modbus_tcp_hardware_check_fails_on_fault_register() {
    FakeModbusTcpServer server;
    server.set_fault_code(9);

    waterbag::ModbusTcpPlcController plc(make_modbus_test_config(server.port()));
    const auto result = plc.check_hardware();
    REQUIRE(!result.success);
}

void test_modbus_tcp_presence_checkpoint_survives_restart_and_faults_on_regression() {
    FakeModbusTcpServer server;
    server.set_presence(true);
    server.set_message_id(100);
    server.set_bag_id(5000);

    auto config = make_modbus_test_config(server.port());
    {
        waterbag::ModbusTcpPlcController plc(config);
        waterbag::CameraConfig camera{1, "A-camera", "camera1"};
        auto packet = waterbag::make_synthetic_frame_packet(camera, "plc_presence_camera1");
        const auto presence = plc.read_laser_presence(packet);
        REQUIRE(presence.bag_present);
        REQUIRE(presence.message_valid);
    }

    {
        waterbag::ModbusTcpPlcController plc(config);
        waterbag::CameraConfig camera{1, "A-camera", "camera1"};
        auto packet = waterbag::make_synthetic_frame_packet(camera, "plc_presence_camera1");
        const auto duplicate = plc.read_laser_presence(packet);
        REQUIRE(!duplicate.bag_present);
        REQUIRE(duplicate.message_valid);
        REQUIRE(duplicate.detail == "modbus_tcp_duplicate_presence_ignored");

        server.set_message_id(101);
        server.set_bag_id(5000);
        const auto regression = plc.read_laser_presence(packet);
        REQUIRE(!regression.message_valid);
        REQUIRE(regression.detail.find("bag_id_regression") != std::string::npos);
    }
}

void test_config_loads_presence_settings() {
    const auto config = waterbag::load_app_config("config/cpp_backend/demo.ini");
    REQUIRE(config.runtime.input_mode == "watch_dir");
    REQUIRE(config.runtime.camera_backend == "mock");
    REQUIRE(config.runtime.plc_backend == "mock");
    REQUIRE(config.detection.presence_enabled);
    REQUIRE(config.detection.advance_on_presence);
    REQUIRE(config.detection.advance_trigger_camera_id == 0);
    REQUIRE(config.plc.presence_message_timeout == waterbag::Milliseconds{200});
    REQUIRE(config.runtime.defect_worker_count == 4);
    REQUIRE(config.runtime.expected_burst_images_per_camera == 3);
    REQUIRE(config.runtime.bag_capture_timeout == waterbag::Milliseconds{1500});
    REQUIRE(config.runtime.sort_result_timeout == waterbag::Milliseconds{1500});
    REQUIRE(config.storage.async_result_writes);
    REQUIRE(config.storage.result_queue_capacity == 512);
    REQUIRE(!config.storage.drop_results_when_full);
    REQUIRE(config.camera_driver.backend == "mock");
    REQUIRE(config.camera_driver.default_trigger_source == "Line0");
    REQUIRE(config.camera_driver.enable_chunk_timestamp);
    REQUIRE(config.runtime.cameras.size() == 2);
}

void test_config_loads_hardware_modbus_settings() {
    const auto config = waterbag::load_app_config("config/cpp_backend/hardware_hik_mvs_modbus.ini");
    REQUIRE(config.runtime.input_mode == "plc_presence");
    REQUIRE(!config.runtime.publish_no_bag_results);
    REQUIRE(config.camera_driver.backend == "hikvision_mvs");
    REQUIRE(config.plc.backend == "modbus_tcp");
    REQUIRE(config.plc.modbus_tcp.host == "192.168.1.50");
    REQUIRE(config.plc.modbus_tcp.port == 502);
    REQUIRE(config.plc.modbus_tcp.coil_start_burst == 0);
    REQUIRE(config.plc.modbus_tcp.coil_line_stop == 21);
    REQUIRE(config.plc.modbus_tcp.holding_register_burst_frame_count == 6);
    REQUIRE(config.runtime.camera_backend == "hikvision_mvs");
    REQUIRE(config.runtime.plc_backend == "modbus_tcp");
}

void test_config_validation_reports_all_startup_errors() {
    waterbag::AppConfig config;
    config.runtime.queue_capacity = 0;
    config.runtime.defect_worker_count = 0;
    config.runtime.input_mode = "plc_presence";
    config.runtime.cameras = {
        waterbag::CameraConfig{1, "A", "a"},
        waterbag::CameraConfig{1, "B", "b"},
    };
    config.correlation.expected_camera_ids = {1, 2};
    config.detection.primary_conf_threshold = 1.5;
    config.plc.backend = "mock";
    config.plc.modbus_tcp.coil_line_stop = -1;

    const auto errors = waterbag::validate_app_config(config);
    REQUIRE(errors.size() >= 6);
    const auto joined = [&] {
        std::string value;
        for (const auto& error : errors) {
            value += error + "\n";
        }
        return value;
    }();
    REQUIRE(joined.find("queue_capacity") != std::string::npos);
    REQUIRE(joined.find("defect_worker_count") != std::string::npos);
    REQUIRE(joined.find("camera id must be unique") != std::string::npos);
    REQUIRE(joined.find("primary_conf_threshold") != std::string::npos);
    REQUIRE(joined.find("plc.backend=modbus_tcp") != std::string::npos);
    REQUIRE(joined.find("coil_line_stop") != std::string::npos);
}

}  // namespace

int main() {
    const std::vector<std::pair<std::string, void (*)()>> tests = {
        {"presence gate skips empty frame", test_presence_gate_skips_empty_frame},
        {"plc laser presence message drives gate", test_plc_laser_presence_message_drives_gate},
        {"plc laser presence timeout is reported", test_plc_laser_presence_timeout_is_reported},
        {"presence triggers lever actions before defect decision", test_presence_triggers_lever_actions_before_defect_decision},
        {"jsonl storage contains presence fields", test_jsonl_storage_contains_presence_fields},
        {"jsonl storage can write asynchronously", test_jsonl_storage_can_write_asynchronously},
        {"burst alignment uses unified hardware clock", test_burst_alignment_uses_unified_hardware_clock},
        {"station packet exports burst images for defect worker", test_station_packet_exports_burst_images_for_defect_worker},
        {"defect detection fuses multi light burst inputs", test_defect_detection_fuses_multi_light_burst_inputs},
        {"bag capture assembler waits for six images", test_bag_capture_assembler_waits_for_six_images},
        {"sort reorder buffer releases results by bag order", test_sort_reorder_buffer_releases_results_by_bag_order},
        {"line safety latches fault once and persists fault event", test_line_safety_latches_fault_once_and_persists_fault_event},
        {"line safety heartbeat threshold latches plc fault", test_line_safety_heartbeat_threshold_latches_plc_fault},
        {"jsonl storage throws on failed write", test_jsonl_storage_throws_on_failed_write},
        {"runtime station queue saturation latches fault without dropping oldest", test_runtime_station_queue_saturation_latches_fault_without_dropping_oldest},
        {"runtime listener exception latches storage fault", test_runtime_listener_exception_latches_storage_fault},
        {"modbus tcp presence false", test_modbus_tcp_presence_false},
        {"modbus tcp presence bag id and commands", test_modbus_tcp_presence_bag_id_and_commands},
        {"modbus tcp hardware check", test_modbus_tcp_hardware_check},
        {"modbus tcp hardware check fails on fault register", test_modbus_tcp_hardware_check_fails_on_fault_register},
        {"modbus tcp presence checkpoint survives restart and faults on regression", test_modbus_tcp_presence_checkpoint_survives_restart_and_faults_on_regression},
        {"config loads presence settings", test_config_loads_presence_settings},
        {"config loads hardware modbus settings", test_config_loads_hardware_modbus_settings},
        {"config validation reports all startup errors", test_config_validation_reports_all_startup_errors},
    };

    for (const auto& [name, test] : tests) {
        std::cout << "[test] " << name << "\n";
        try {
            test();
        } catch (const std::exception& error) {
            std::cerr << "[failed] " << name << ": " << error.what() << "\n";
            return 1;
        } catch (...) {
            std::cerr << "[failed] " << name << ": unknown exception\n";
            return 1;
        }
    }
    std::cout << "[tests] " << tests.size() << " passed\n";
    return 0;
}
