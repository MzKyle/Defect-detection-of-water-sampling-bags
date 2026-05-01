#include "detect_orchestrator/config.hpp"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace waterbag {
namespace {

std::string trim(std::string value) {
    auto not_space = [](unsigned char ch) { return !std::isspace(ch); };
    value.erase(value.begin(), std::find_if(value.begin(), value.end(), not_space));
    value.erase(std::find_if(value.rbegin(), value.rend(), not_space).base(), value.end());
    return value;
}

std::string lower_copy(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

std::vector<int> parse_int_list(const std::string& value, std::vector<int> fallback) {
    if (value.empty()) {
        return fallback;
    }
    std::vector<int> result;
    std::stringstream stream(value);
    std::string item;
    while (std::getline(stream, item, ',')) {
        item = trim(item);
        if (!item.empty()) {
            result.push_back(std::stoi(item));
        }
    }
    return result.empty() ? fallback : result;
}

ModelConfig load_model_config(const IniConfig& ini, const std::string& section, ModelConfig fallback) {
    fallback.backend = lower_copy(ini.get(section, "backend", fallback.backend));
    fallback.model_path = ini.get(section, "model_path", fallback.model_path.string());
    fallback.imgsz = ini.get_int(section, "imgsz", fallback.imgsz);
    fallback.nms_iou_threshold = ini.get_double(section, "nms_iou_threshold", fallback.nms_iou_threshold);
    fallback.cuda_device_id = ini.get_int(section, "cuda_device_id", fallback.cuda_device_id);
    fallback.use_cuda = ini.get_bool(section, "use_cuda", fallback.use_cuda);
    fallback.max_detections = ini.get_size(section, "max_detections", fallback.max_detections);
    return fallback;
}

}  // namespace

IniConfig IniConfig::load(const std::filesystem::path& path) {
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("failed to open config: " + path.string());
    }

    IniConfig config;
    std::string section;
    std::string line;
    int line_number = 0;
    while (std::getline(input, line)) {
        ++line_number;
        line = trim(line);
        if (line.empty() || line[0] == '#' || line[0] == ';') {
            continue;
        }
        if (line.front() == '[' && line.back() == ']') {
            section = trim(line.substr(1, line.size() - 2));
            continue;
        }

        const auto pos = line.find('=');
        if (pos == std::string::npos || section.empty()) {
            throw std::runtime_error("invalid config line " + std::to_string(line_number) + ": " + line);
        }
        const auto key = trim(line.substr(0, pos));
        const auto value = trim(line.substr(pos + 1));
        config.values_[section][key] = value;
    }
    return config;
}

bool IniConfig::has_section(const std::string& section) const {
    return values_.find(section) != values_.end();
}

std::string IniConfig::get(const std::string& section, const std::string& key, const std::string& fallback) const {
    const auto section_it = values_.find(section);
    if (section_it == values_.end()) {
        return fallback;
    }
    const auto key_it = section_it->second.find(key);
    return key_it == section_it->second.end() ? fallback : key_it->second;
}

int IniConfig::get_int(const std::string& section, const std::string& key, int fallback) const {
    const auto value = get(section, key);
    return value.empty() ? fallback : std::stoi(value);
}

std::size_t IniConfig::get_size(const std::string& section, const std::string& key, std::size_t fallback) const {
    const auto value = get(section, key);
    return value.empty() ? fallback : static_cast<std::size_t>(std::stoull(value));
}

bool IniConfig::get_bool(const std::string& section, const std::string& key, bool fallback) const {
    const auto value = lower_copy(get(section, key));
    if (value.empty()) {
        return fallback;
    }
    return value == "1" || value == "true" || value == "yes" || value == "on";
}

Milliseconds IniConfig::get_ms(const std::string& section, const std::string& key, Milliseconds fallback) const {
    const auto value = get(section, key);
    return value.empty() ? fallback : Milliseconds{std::stoi(value)};
}

double IniConfig::get_double(const std::string& section, const std::string& key, double fallback) const {
    const auto value = get(section, key);
    return value.empty() ? fallback : std::stod(value);
}

std::vector<std::string> IniConfig::sections_with_prefix(const std::string& prefix) const {
    std::vector<std::string> sections;
    for (const auto& [section, entries] : values_) {
        (void)entries;
        if (section.rfind(prefix, 0) == 0) {
            sections.push_back(section);
        }
    }
    return sections;
}

AppConfig load_app_config(const std::filesystem::path& path) {
    const auto ini = IniConfig::load(path);
    AppConfig config;

    config.runtime.poll_interval = ini.get_ms("runtime", "poll_interval_ms", config.runtime.poll_interval);
    config.runtime.file_stable_for = ini.get_ms("runtime", "file_stable_ms", config.runtime.file_stable_for);
    config.runtime.file_ready_timeout = ini.get_ms("runtime", "file_ready_timeout_ms", config.runtime.file_ready_timeout);
    config.runtime.cooldown = ini.get_ms("runtime", "cooldown_ms", config.runtime.cooldown);
    config.runtime.queue_capacity = ini.get_size("runtime", "queue_capacity", config.runtime.queue_capacity);
    config.runtime.defect_worker_count = ini.get_size("runtime", "defect_worker_count", config.runtime.defect_worker_count);
    config.runtime.expected_burst_images_per_camera = ini.get_size(
        "runtime",
        "expected_burst_images_per_camera",
        config.runtime.expected_burst_images_per_camera);
    config.runtime.bag_capture_timeout = ini.get_ms("runtime", "bag_capture_timeout_ms", config.runtime.bag_capture_timeout);
    config.runtime.sort_result_timeout = ini.get_ms("runtime", "sort_result_timeout_ms", config.runtime.sort_result_timeout);

    const auto camera_sections = ini.sections_with_prefix("camera.");
    for (const auto& section : camera_sections) {
        CameraConfig camera;
        camera.id = ini.get_int(section, "id", static_cast<int>(config.runtime.cameras.size() + 1));
        camera.name = ini.get(section, "name", "camera-" + std::to_string(camera.id));
        camera.watch_dir = ini.get(section, "watch_dir", "demo_data/camera" + std::to_string(camera.id));
        config.runtime.cameras.push_back(camera);
    }
    if (config.runtime.cameras.empty()) {
        config.runtime.cameras = {
            CameraConfig{1, "A-camera", "demo_data/camera1"},
            CameraConfig{2, "B-camera", "demo_data/camera2"},
        };
    }

    config.detection.presence_enabled = ini.get_bool("detection", "presence_enabled", config.detection.presence_enabled);
    config.detection.advance_on_presence = ini.get_bool("detection", "advance_on_presence", config.detection.advance_on_presence);
    config.detection.advance_trigger_camera_id = ini.get_int("detection", "advance_trigger_camera_id", config.detection.advance_trigger_camera_id);
    config.detection.presence_conf_threshold = ini.get_double("detection", "presence_conf_threshold", config.detection.presence_conf_threshold);
    config.detection.patch_enabled = ini.get_bool("detection", "patch_enabled", config.detection.patch_enabled);
    config.detection.patch_horizontal = ini.get_int("detection", "patch_horizontal", config.detection.patch_horizontal);
    config.detection.patch_vertical = ini.get_int("detection", "patch_vertical", config.detection.patch_vertical);
    config.detection.primary_conf_threshold = ini.get_double("detection", "primary_conf_threshold", config.detection.primary_conf_threshold);
    config.detection.patch_conf_threshold = ini.get_double("detection", "patch_conf_threshold", config.detection.patch_conf_threshold);

    config.detectors.presence = load_model_config(ini, "detector.presence", config.detectors.presence);
    config.detectors.primary = load_model_config(ini, "detector.primary", config.detectors.primary);
    config.detectors.patch = load_model_config(ini, "detector.patch", config.detectors.patch);

    config.correlation.enabled = ini.get_bool("correlation", "enabled", config.correlation.enabled);
    config.correlation.expected_camera_ids = parse_int_list(
        ini.get("correlation", "expected_camera_ids", "1,2"),
        config.correlation.expected_camera_ids);
    config.correlation.hold_non_defect_until_complete = ini.get_bool(
        "correlation",
        "hold_non_defect_until_complete",
        config.correlation.hold_non_defect_until_complete);
    config.correlation.pending_timeout = ini.get_ms("correlation", "pending_timeout_ms", config.correlation.pending_timeout);
    config.correlation.finalized_retention = ini.get_ms("correlation", "finalized_retention_ms", config.correlation.finalized_retention);
    config.correlation.timeout_action = ini.get("correlation", "timeout_action", config.correlation.timeout_action);

    config.plc.enabled = ini.get_bool("plc", "enabled", config.plc.enabled);
    config.plc.ack_timeout = ini.get_ms("plc", "ack_timeout_ms", config.plc.ack_timeout);
    config.plc.max_retries = ini.get_int("plc", "max_retries", config.plc.max_retries);
    config.plc.retry_interval = ini.get_ms("plc", "retry_interval_ms", config.plc.retry_interval);
    config.plc.mock_fail_first_attempts = ini.get_int("plc", "mock_fail_first_attempts", config.plc.mock_fail_first_attempts);
    config.plc.mock_ack_latency = ini.get_ms("plc", "mock_ack_latency_ms", config.plc.mock_ack_latency);

    config.storage.result_jsonl = ini.get("storage", "result_jsonl", config.storage.result_jsonl.string());

    config.logger.level = parse_log_level(ini.get("logging", "level", "info"));
    config.logger.console = ini.get_bool("logging", "console", config.logger.console);
    config.logger.file_path = ini.get("logging", "file", "");

    config.service.auto_start = ini.get_bool("service", "auto_start", config.service.auto_start);
    config.service.run_for = ini.get_ms("service", "run_for_ms", config.service.run_for);

    return config;
}

}  // namespace waterbag
