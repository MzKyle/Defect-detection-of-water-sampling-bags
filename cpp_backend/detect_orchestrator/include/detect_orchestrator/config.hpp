#pragma once

#include <cstddef>
#include <filesystem>
#include <map>
#include <string>

#include "detect_orchestrator/logger.hpp"
#include "detect_orchestrator/schemas.hpp"

namespace waterbag {

struct ModelConfig {
    std::string backend = "mock";
    std::filesystem::path model_path;
    int imgsz = 640;
    double nms_iou_threshold = 0.45;
    int cuda_device_id = 0;
    bool use_cuda = false;
    std::size_t max_detections = 100;
};

struct DetectorConfig {
    ModelConfig primary;
    ModelConfig patch;
};

struct StorageConfig {
    std::filesystem::path result_jsonl = "artifacts/cpp_backend/results.jsonl";
    bool async_result_writes = false;
    std::size_t result_queue_capacity = 256;
    bool drop_results_when_full = true;
};

struct ServiceConfig {
    bool auto_start = true;
    Milliseconds run_for{0};
};

struct AppConfig {
    RuntimeConfig runtime;
    CameraDriverConfig camera_driver;
    DetectionConfig detection;
    DetectorConfig detectors;
    CorrelationConfig correlation;
    PlcConfig plc;
    StorageConfig storage;
    LoggerConfig logger;
    ServiceConfig service;
};

class IniConfig {
public:
    static IniConfig load(const std::filesystem::path& path);

    bool has_section(const std::string& section) const;
    std::string get(const std::string& section, const std::string& key, const std::string& fallback = "") const;
    int get_int(const std::string& section, const std::string& key, int fallback) const;
    std::size_t get_size(const std::string& section, const std::string& key, std::size_t fallback) const;
    bool get_bool(const std::string& section, const std::string& key, bool fallback) const;
    Milliseconds get_ms(const std::string& section, const std::string& key, Milliseconds fallback) const;
    double get_double(const std::string& section, const std::string& key, double fallback) const;
    std::vector<std::string> sections_with_prefix(const std::string& prefix) const;

private:
    std::map<std::string, std::map<std::string, std::string>> values_;
};

AppConfig load_app_config(const std::filesystem::path& path);

}  // namespace waterbag
