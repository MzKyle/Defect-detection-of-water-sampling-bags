#include "PLC_driver/plc_controller.hpp"

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <netdb.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/select.h>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>

namespace waterbag {
namespace {

std::atomic<std::uint16_t> g_modbus_transaction_id{0};

std::uint16_t require_address(int address, const std::string& name) {
    if (address < 0 || address > 0xFFFF) {
        throw std::runtime_error("invalid Modbus address for " + name + ": " + std::to_string(address));
    }
    return static_cast<std::uint16_t>(address);
}

std::uint16_t register_value(int value) {
    return static_cast<std::uint16_t>(std::clamp(value, 0, 0xFFFF));
}

void push_u16(std::vector<std::uint8_t>& bytes, std::uint16_t value) {
    bytes.push_back(static_cast<std::uint8_t>((value >> 8) & 0xFF));
    bytes.push_back(static_cast<std::uint8_t>(value & 0xFF));
}

std::uint16_t read_u16(const std::vector<std::uint8_t>& bytes, std::size_t offset) {
    if (offset + 1 >= bytes.size()) {
        throw std::runtime_error("Modbus response is shorter than expected");
    }
    return static_cast<std::uint16_t>((static_cast<std::uint16_t>(bytes[offset]) << 8) | bytes[offset + 1]);
}

std::uint32_t numeric_id_from_text(const std::string& text) {
    std::string digits;
    for (char ch : text) {
        if (std::isdigit(static_cast<unsigned char>(ch))) {
            digits.push_back(ch);
        }
    }
    if (!digits.empty()) {
        try {
            return static_cast<std::uint32_t>(std::stoul(digits));
        } catch (...) {
            return 0;
        }
    }
    return static_cast<std::uint32_t>(std::hash<std::string>{}(text));
}

std::uint16_t command_id_value(const std::string& command_id) {
    return static_cast<std::uint16_t>(numeric_id_from_text(command_id) & 0xFFFFU);
}

std::uint32_t bag_id_value(const std::string& bag_id) {
    return numeric_id_from_text(bag_id);
}

int light_code(LightId light_id) {
    switch (light_id) {
        case LightId::L1Backlight:
            return 1;
        case LightId::L2DarkfieldA:
            return 2;
        case LightId::L3DarkfieldB:
            return 3;
        case LightId::L2L3DualDarkfield:
            return 23;
        case LightId::L4CrossPolarized:
            return 4;
    }
    return 0;
}

int action_code(const std::string& action) {
    if (action == "start_light_burst") {
        return 1;
    }
    if (action == "release_bag_after_capture") {
        return 10;
    }
    if (action == "push_bag_after_capture") {
        return 11;
    }
    if (action == "restore_after_push") {
        return 12;
    }
    if (action == "restore_blocking_position") {
        return 13;
    }
    if (action == "route_to_ok_bin") {
        return 20;
    }
    if (action == "route_to_ng_bin") {
        return 21;
    }
    return 0;
}

int coil_for_action(const ModbusTcpConfig& config, const std::string& action) {
    if (action == "start_light_burst") {
        return config.coil_start_burst;
    }
    if (action == "release_bag_after_capture") {
        return config.coil_station_release;
    }
    if (action == "push_bag_after_capture") {
        return config.coil_station_push;
    }
    if (action == "restore_after_push" || action == "restore_blocking_position") {
        return config.coil_station_restore;
    }
    if (action == "route_to_ok_bin") {
        return config.coil_sort_ok;
    }
    if (action == "route_to_ng_bin") {
        return config.coil_sort_ng;
    }
    return -1;
}

std::string socket_error(const std::string& context) {
    return context + ": " + std::strerror(errno);
}

class ModbusTcpClient {
public:
    explicit ModbusTcpClient(ModbusTcpConfig config) : config_(std::move(config)) {}

    bool read_discrete_input(int address) {
        auto response = read_bits(0x02, address, 1);
        return response.at(0);
    }

    std::vector<std::uint16_t> read_input_registers(int address, int count) {
        return read_registers(0x04, address, count);
    }

    void write_single_coil(int address, bool value) {
        std::vector<std::uint8_t> payload;
        push_u16(payload, require_address(address, "write_single_coil"));
        push_u16(payload, value ? 0xFF00 : 0x0000);
        auto response = exchange(0x05, payload);
        if (response.size() != 5) {
            throw std::runtime_error("unexpected write_single_coil response length");
        }
    }

    void write_single_register(int address, std::uint16_t value) {
        std::vector<std::uint8_t> payload;
        push_u16(payload, require_address(address, "write_single_register"));
        push_u16(payload, value);
        auto response = exchange(0x06, payload);
        if (response.size() != 5) {
            throw std::runtime_error("unexpected write_single_register response length");
        }
    }

private:
    std::vector<bool> read_bits(std::uint8_t function, int address, int count) {
        if (count <= 0 || count > 2000) {
            throw std::runtime_error("invalid Modbus bit count: " + std::to_string(count));
        }
        std::vector<std::uint8_t> payload;
        push_u16(payload, require_address(address, "read_bits"));
        push_u16(payload, static_cast<std::uint16_t>(count));
        auto response = exchange(function, payload);
        if (response.size() < 2) {
            throw std::runtime_error("short Modbus bit response");
        }
        const int byte_count = response[1];
        if (static_cast<std::size_t>(byte_count + 2) > response.size()) {
            throw std::runtime_error("invalid Modbus bit response byte count");
        }
        std::vector<bool> bits;
        bits.reserve(static_cast<std::size_t>(count));
        for (int i = 0; i < count; ++i) {
            const int byte_index = 2 + i / 8;
            const int bit_index = i % 8;
            bits.push_back((response[byte_index] & (1U << bit_index)) != 0);
        }
        return bits;
    }

    std::vector<std::uint16_t> read_registers(std::uint8_t function, int address, int count) {
        if (count <= 0 || count > 125) {
            throw std::runtime_error("invalid Modbus register count: " + std::to_string(count));
        }
        std::vector<std::uint8_t> payload;
        push_u16(payload, require_address(address, "read_registers"));
        push_u16(payload, static_cast<std::uint16_t>(count));
        auto response = exchange(function, payload);
        if (response.size() < 2) {
            throw std::runtime_error("short Modbus register response");
        }
        const int byte_count = response[1];
        if (byte_count != count * 2 || static_cast<std::size_t>(byte_count + 2) > response.size()) {
            throw std::runtime_error("invalid Modbus register response byte count");
        }
        std::vector<std::uint16_t> values;
        values.reserve(static_cast<std::size_t>(count));
        for (int i = 0; i < count; ++i) {
            values.push_back(read_u16(response, static_cast<std::size_t>(2 + i * 2)));
        }
        return values;
    }

    std::vector<std::uint8_t> exchange(std::uint8_t function, const std::vector<std::uint8_t>& payload) {
        const int fd = connect_socket();
        try {
            const auto transaction_id = ++g_modbus_transaction_id;
            std::vector<std::uint8_t> request;
            push_u16(request, transaction_id);
            push_u16(request, 0);
            push_u16(request, static_cast<std::uint16_t>(payload.size() + 2));
            request.push_back(static_cast<std::uint8_t>(config_.unit_id));
            request.push_back(function);
            request.insert(request.end(), payload.begin(), payload.end());
            send_all(fd, request);

            const auto header = recv_exact(fd, 7);
            if (read_u16(header, 0) != transaction_id) {
                throw std::runtime_error("Modbus transaction id mismatch");
            }
            if (read_u16(header, 2) != 0) {
                throw std::runtime_error("Modbus protocol id mismatch");
            }
            const auto length = read_u16(header, 4);
            if (length < 2) {
                throw std::runtime_error("invalid Modbus response length");
            }
            const auto unit_id = header[6];
            if (unit_id != static_cast<std::uint8_t>(config_.unit_id)) {
                throw std::runtime_error("Modbus unit id mismatch");
            }
            auto pdu = recv_exact(fd, static_cast<std::size_t>(length - 1));
            if (pdu.empty()) {
                throw std::runtime_error("empty Modbus PDU");
            }
            if (pdu[0] == static_cast<std::uint8_t>(function | 0x80U)) {
                const int exception_code = pdu.size() > 1 ? pdu[1] : 0;
                throw std::runtime_error("Modbus exception code=" + std::to_string(exception_code));
            }
            if (pdu[0] != function) {
                throw std::runtime_error("Modbus function mismatch");
            }
            ::close(fd);
            return pdu;
        } catch (...) {
            ::close(fd);
            throw;
        }
    }

    int connect_socket() const {
        addrinfo hints{};
        hints.ai_family = AF_UNSPEC;
        hints.ai_socktype = SOCK_STREAM;

        addrinfo* raw_results = nullptr;
        const auto port = std::to_string(config_.port);
        const int gai = getaddrinfo(config_.host.c_str(), port.c_str(), &hints, &raw_results);
        if (gai != 0) {
            throw std::runtime_error("getaddrinfo failed for " + config_.host + ":" + port + ": " + gai_strerror(gai));
        }

        std::unique_ptr<addrinfo, decltype(&freeaddrinfo)> results(raw_results, freeaddrinfo);
        std::string last_error = "no address candidates";
        for (auto* item = results.get(); item != nullptr; item = item->ai_next) {
            const int fd = socket(item->ai_family, item->ai_socktype, item->ai_protocol);
            if (fd < 0) {
                last_error = socket_error("socket");
                continue;
            }
            try {
                connect_with_timeout(fd, item->ai_addr, item->ai_addrlen);
                set_timeouts(fd);
                return fd;
            } catch (const std::exception& error) {
                last_error = error.what();
                ::close(fd);
            }
        }
        throw std::runtime_error("Modbus TCP connect failed: " + last_error);
    }

    void connect_with_timeout(int fd, const sockaddr* address, socklen_t length) const {
        const int original_flags = fcntl(fd, F_GETFL, 0);
        if (original_flags < 0) {
            throw std::runtime_error(socket_error("fcntl F_GETFL"));
        }
        if (fcntl(fd, F_SETFL, original_flags | O_NONBLOCK) < 0) {
            throw std::runtime_error(socket_error("fcntl F_SETFL"));
        }

        const int ret = connect(fd, address, length);
        if (ret == 0) {
            fcntl(fd, F_SETFL, original_flags);
            return;
        }
        if (errno != EINPROGRESS) {
            throw std::runtime_error(socket_error("connect"));
        }

        fd_set write_set;
        FD_ZERO(&write_set);
        FD_SET(fd, &write_set);
        timeval timeout = to_timeval(config_.connect_timeout);
        const int selected = select(fd + 1, nullptr, &write_set, nullptr, &timeout);
        if (selected <= 0) {
            throw std::runtime_error(selected == 0 ? "connect timeout" : socket_error("select"));
        }

        int socket_status = 0;
        socklen_t status_length = sizeof(socket_status);
        if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &socket_status, &status_length) < 0) {
            throw std::runtime_error(socket_error("getsockopt SO_ERROR"));
        }
        if (socket_status != 0) {
            throw std::runtime_error("connect failed: " + std::string(std::strerror(socket_status)));
        }
        if (fcntl(fd, F_SETFL, original_flags) < 0) {
            throw std::runtime_error(socket_error("fcntl restore"));
        }
    }

    static timeval to_timeval(Milliseconds timeout) {
        timeval value{};
        value.tv_sec = static_cast<long>(timeout.count() / 1000);
        value.tv_usec = static_cast<long>((timeout.count() % 1000) * 1000);
        return value;
    }

    void set_timeouts(int fd) const {
        timeval read_timeout = to_timeval(config_.read_timeout);
        timeval write_timeout = to_timeval(config_.write_timeout);
        if (setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &read_timeout, sizeof(read_timeout)) < 0) {
            throw std::runtime_error(socket_error("setsockopt SO_RCVTIMEO"));
        }
        if (setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &write_timeout, sizeof(write_timeout)) < 0) {
            throw std::runtime_error(socket_error("setsockopt SO_SNDTIMEO"));
        }
    }

    static void send_all(int fd, const std::vector<std::uint8_t>& bytes) {
        std::size_t sent = 0;
        while (sent < bytes.size()) {
            const auto ret = send(fd, bytes.data() + sent, bytes.size() - sent, 0);
            if (ret <= 0) {
                throw std::runtime_error(socket_error("send"));
            }
            sent += static_cast<std::size_t>(ret);
        }
    }

    static std::vector<std::uint8_t> recv_exact(int fd, std::size_t size) {
        std::vector<std::uint8_t> bytes(size);
        std::size_t received = 0;
        while (received < size) {
            const auto ret = recv(fd, bytes.data() + received, size - received, 0);
            if (ret <= 0) {
                throw std::runtime_error(socket_error("recv"));
            }
            received += static_cast<std::size_t>(ret);
        }
        return bytes;
    }

    ModbusTcpConfig config_;
};

class ModbusTcpPlcTransport final : public IPlcTransport {
public:
    explicit ModbusTcpPlcTransport(PlcConfig config) : config_(std::move(config)) {}

    ExecutionFeedback send_once(const ControlCommand& command) override {
        const auto started = Clock::now();
        ExecutionFeedback feedback;
        feedback.command_id = command.command_id;
        feedback.frame_id = command.frame_id;
        feedback.target = command.target;
        feedback.action = command.action;
        feedback.attempts = 1;

        try {
            ModbusTcpClient client(config_.modbus_tcp);
            write_command_context(client, command);
            const int coil = coil_for_action(config_.modbus_tcp, command.action);
            if (coil < 0) {
                throw std::runtime_error("no Modbus coil mapped for action=" + command.action);
            }
            client.write_single_coil(coil, true);

            std::string ack_detail;
            feedback.success = wait_for_ack(client, ack_detail);
            feedback.detail = ack_detail;
            if (config_.modbus_tcp.clear_command_coil_after_ack) {
                client.write_single_coil(coil, false);
            }
        } catch (const std::exception& error) {
            feedback.success = false;
            feedback.detail = "modbus_tcp_error:" + std::string(error.what());
        }

        feedback.latency_ms = elapsed_ms(started);
        return feedback;
    }

private:
    void write_command_context(ModbusTcpClient& client, const ControlCommand& command) const {
        const auto& config = config_.modbus_tcp;
        client.write_single_register(config.holding_register_command_id, command_id_value(command.command_id));
        client.write_single_register(config.holding_register_action_code, register_value(action_code(command.action)));
        if (config.write_bag_id_registers) {
            const auto bag = bag_id_value(command.bag_id);
            client.write_single_register(config.holding_register_bag_id_high, static_cast<std::uint16_t>((bag >> 16) & 0xFFFFU));
            client.write_single_register(config.holding_register_bag_id_low, static_cast<std::uint16_t>(bag & 0xFFFFU));
        }
    }

    bool wait_for_ack(ModbusTcpClient& client, std::string& detail) const {
        const auto ack_register = config_.modbus_tcp.input_register_ack_status;
        if (ack_register < 0) {
            detail = "modbus_tcp_write_no_ack";
            return true;
        }

        const auto deadline = Clock::now() + config_.modbus_tcp.ack_timeout;
        while (Clock::now() <= deadline) {
            const auto values = client.read_input_registers(ack_register, 1);
            const int ack = values.empty() ? config_.modbus_tcp.ack_idle_value : static_cast<int>(values[0]);
            if (ack == config_.modbus_tcp.ack_success_value) {
                detail = "modbus_tcp_ack_success";
                return true;
            }
            if (ack == config_.modbus_tcp.ack_failure_value) {
                detail = "modbus_tcp_ack_failure";
                if (config_.modbus_tcp.input_register_fault_code >= 0) {
                    const auto faults = client.read_input_registers(config_.modbus_tcp.input_register_fault_code, 1);
                    if (!faults.empty()) {
                        detail += ":fault_code=" + std::to_string(faults[0]);
                    }
                }
                return false;
            }
            if (config_.modbus_tcp.ack_poll_interval.count() > 0) {
                std::this_thread::sleep_for(config_.modbus_tcp.ack_poll_interval);
            }
        }
        detail = "modbus_tcp_ack_timeout";
        return false;
    }

    PlcConfig config_;
};

}  // namespace

ModbusTcpPlcController::ModbusTcpPlcController(PlcConfig config)
    : config_(std::move(config)),
      reliable_(config_, std::make_unique<ModbusTcpPlcTransport>(config_)) {}

std::string ModbusTcpPlcController::backend_name() const {
    return "modbus_tcp";
}

HardwareCheckResult ModbusTcpPlcController::check_hardware() {
    HardwareCheckResult result;
    if (!config_.enabled) {
        result.details.push_back("plc_disabled");
        return result;
    }

    try {
        ModbusTcpClient client(config_.modbus_tcp);
        const bool present = client.read_discrete_input(config_.modbus_tcp.discrete_input_bag_present);
        result.details.push_back(std::string("presence_discrete_input=") + (present ? "true" : "false"));

        if (config_.modbus_tcp.input_register_message_id >= 0) {
            const auto values = client.read_input_registers(config_.modbus_tcp.input_register_message_id, 1);
            result.details.push_back("message_id_register=" + std::to_string(values.empty() ? 0 : values[0]));
        }
        if (config_.modbus_tcp.input_register_fault_code >= 0) {
            const auto values = client.read_input_registers(config_.modbus_tcp.input_register_fault_code, 1);
            result.details.push_back("fault_code_register=" + std::to_string(values.empty() ? 0 : values[0]));
        }
        if (config_.modbus_tcp.coil_heartbeat >= 0) {
            client.write_single_coil(config_.modbus_tcp.coil_heartbeat, true);
            client.write_single_coil(config_.modbus_tcp.coil_heartbeat, false);
            result.details.push_back("heartbeat_coil_write=ok");
        }
    } catch (const std::exception& error) {
        result.success = false;
        result.details.push_back("modbus_tcp_check_failed:" + std::string(error.what()));
    }
    return result;
}

PlcLaserPresence ModbusTcpPlcController::read_laser_presence(const FramePacket& packet) {
    const auto started = Clock::now();
    PlcLaserPresence signal;
    signal.camera_id = packet.camera_id;
    signal.station_id = "camera" + std::to_string(packet.camera_id) + "_laser";
    signal.received_at = SystemClock::now();

    if (!config_.enabled) {
        signal.bag_present = true;
        signal.detail = "plc_disabled_presence_assumed";
        signal.message_id = "plc-disabled-" + packet.frame_id;
        signal.message_valid = true;
        signal.timed_out = false;
        signal.latency_ms = elapsed_ms(started);
        return signal;
    }

    try {
        ModbusTcpClient client(config_.modbus_tcp);
        signal.bag_present = client.read_discrete_input(config_.modbus_tcp.discrete_input_bag_present);

        if (config_.modbus_tcp.input_register_message_id >= 0) {
            const auto values = client.read_input_registers(config_.modbus_tcp.input_register_message_id, 1);
            signal.message_id = "modbus-" + std::to_string(values.empty() ? 0 : values[0]);
        } else {
            signal.message_id = "modbus-" + packet.frame_id;
        }

        if (config_.modbus_tcp.input_register_bag_id_high >= 0 && config_.modbus_tcp.input_register_bag_id_low >= 0) {
            const auto high = client.read_input_registers(config_.modbus_tcp.input_register_bag_id_high, 1);
            const auto low = client.read_input_registers(config_.modbus_tcp.input_register_bag_id_low, 1);
            if (!high.empty() && !low.empty()) {
                const auto value = (static_cast<std::uint32_t>(high[0]) << 16) | static_cast<std::uint32_t>(low[0]);
                if (value > 0) {
                    signal.bag_id = std::to_string(value);
                }
            }
        }

        signal.latency_ms = elapsed_ms(started);
        signal.timed_out = signal.latency_ms > static_cast<double>(config_.presence_message_timeout.count());
        signal.message_valid = !signal.timed_out;
        signal.detail = signal.timed_out
            ? "modbus_tcp_presence_timeout"
            : (signal.bag_present ? "modbus_tcp_laser_bag_present" : "modbus_tcp_laser_clear");
        if (signal.bag_present && signal.message_valid) {
            const auto key = signal.message_id + ":" + signal.bag_id;
            std::lock_guard<std::mutex> lock(presence_mutex_);
            const auto found = last_presence_key_by_camera_.find(packet.camera_id);
            if (found != last_presence_key_by_camera_.end() && found->second == key) {
                signal.bag_present = false;
                signal.detail = "modbus_tcp_duplicate_presence_ignored";
            } else {
                last_presence_key_by_camera_[packet.camera_id] = key;
            }
        }
    } catch (const std::exception& error) {
        signal.latency_ms = elapsed_ms(started);
        signal.timed_out = signal.latency_ms > static_cast<double>(config_.presence_message_timeout.count());
        signal.message_valid = false;
        signal.bag_present = false;
        signal.message_id = "modbus-error-" + packet.frame_id;
        signal.detail = "modbus_tcp_presence_error:" + std::string(error.what());
    }
    return signal;
}

PlcAck ModbusTcpPlcController::start_light_burst(const CaptureSession& session, const BurstPlan& plan) {
    try {
        write_burst_plan(session, plan);
        const auto feedback = execute_semantic_command(session.packet, "light_burst_controller", "start_light_burst");
        if (feedback.success) {
            store_planned_burst_events(session, plan);
        }
        return PlcAck{feedback.success, feedback.detail, feedback.latency_ms};
    } catch (const std::exception& error) {
        return PlcAck{false, "modbus_tcp_start_burst_error:" + std::string(error.what()), 0.0};
    }
}

std::vector<PlcBurstEvent> ModbusTcpPlcController::read_burst_events(const std::string& capture_session_id) {
    const auto found = burst_events_.find(capture_session_id);
    if (found == burst_events_.end()) {
        return {};
    }
    return found->second;
}

std::vector<ExecutionFeedback> ModbusTcpPlcController::release_station_after_capture(const CaptureSession& session) {
    const auto& packet = session.packet;
    const auto station = "camera" + std::to_string(session.camera_id);
    std::vector<ExecutionFeedback> feedbacks;
    feedbacks.push_back(execute_semantic_command(packet, station + "_bottom_lever", "release_bag_after_capture"));
    feedbacks.push_back(execute_semantic_command(packet, station + "_upper_lever", "push_bag_after_capture"));
    feedbacks.push_back(execute_semantic_command(packet, station + "_upper_lever", "restore_after_push"));
    feedbacks.push_back(execute_semantic_command(packet, station + "_bottom_lever", "restore_blocking_position"));
    return feedbacks;
}

ExecutionFeedback ModbusTcpPlcController::route_to_ok_bin(const FramePacket& packet) {
    return execute_semantic_command(packet, "end_sorter", "route_to_ok_bin");
}

ExecutionFeedback ModbusTcpPlcController::route_to_ng_bin(const FramePacket& packet) {
    return execute_semantic_command(packet, "end_sorter", "route_to_ng_bin");
}

ExecutionFeedback ModbusTcpPlcController::execute_semantic_command(
    const FramePacket& packet,
    const std::string& target,
    const std::string& action) {
    ControlCommand command;
    command.command_id = make_command_id();
    command.frame_id = packet.frame_id;
    command.bag_id = packet.bag_id;
    command.target = target;
    command.action = action;
    command.created_at = SystemClock::now();
    return reliable_.execute(command);
}

void ModbusTcpPlcController::write_burst_plan(const CaptureSession& session, const BurstPlan& plan) {
    if (!config_.enabled) {
        return;
    }

    ModbusTcpClient client(config_.modbus_tcp);
    const auto& modbus = config_.modbus_tcp;
    if (modbus.write_bag_id_registers) {
        const auto bag = bag_id_value(session.bag_id);
        client.write_single_register(modbus.holding_register_bag_id_high, static_cast<std::uint16_t>((bag >> 16) & 0xFFFFU));
        client.write_single_register(modbus.holding_register_bag_id_low, static_cast<std::uint16_t>(bag & 0xFFFFU));
    }
    if (modbus.holding_register_burst_frame_count >= 0) {
        client.write_single_register(modbus.holding_register_burst_frame_count, register_value(static_cast<int>(plan.frames.size())));
    }
    if (modbus.holding_register_burst_frame_base >= 0 && modbus.burst_frame_register_stride > 0) {
        for (const auto& frame : plan.frames) {
            const int base = modbus.holding_register_burst_frame_base + frame.frame_index * modbus.burst_frame_register_stride;
            client.write_single_register(base, register_value(light_code(frame.light_id)));
            client.write_single_register(base + 1, register_value(frame.exposure_us));
            client.write_single_register(base + 2, register_value(frame.settle_us));
            client.write_single_register(base + 3, register_value(frame.light_pulse_us));
        }
    }
}

void ModbusTcpPlcController::store_planned_burst_events(const CaptureSession& session, const BurstPlan& plan) {
    const auto base_time = SystemClock::now();
    const auto base_hw_ns = UnifiedHardwareClock::now_ns();
    std::vector<PlcBurstEvent> events;
    for (const auto& frame_plan : plan.frames) {
        const auto light_on = base_time + std::chrono::microseconds(frame_plan.frame_index * 1000);
        const auto camera_trigger = light_on + std::chrono::microseconds(frame_plan.settle_us);
        const auto light_off = camera_trigger + std::chrono::microseconds(frame_plan.light_pulse_us);
        const auto light_on_hw = HardwareTimestamp{base_hw_ns + frame_plan.frame_index * 1000LL * 1000LL};
        const auto camera_trigger_hw = HardwareTimestamp{light_on_hw.ns + frame_plan.settle_us * 1000LL};
        const auto light_off_hw = HardwareTimestamp{camera_trigger_hw.ns + frame_plan.light_pulse_us * 1000LL};

        PlcBurstEvent event;
        event.capture_session_id = session.capture_session_id;
        event.plan_id = plan.plan_id;
        event.frame_index = frame_plan.frame_index;
        event.light_id = frame_plan.light_id;
        event.light_on_hw = light_on_hw;
        event.camera_trigger_hw = camera_trigger_hw;
        event.light_off_hw = light_off_hw;
        event.light_on = light_on;
        event.camera_trigger = camera_trigger;
        event.light_off = light_off;
        events.push_back(event);
    }
    burst_events_[session.capture_session_id] = std::move(events);
}

}  // namespace waterbag
