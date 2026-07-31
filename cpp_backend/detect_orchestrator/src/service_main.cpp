#include <atomic>
#include <algorithm>
#include <cctype>
#include <csignal>
#include <filesystem>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "detect_orchestrator/bag_runtime.hpp"
#include "detect_orchestrator/config.hpp"
#include "detect_orchestrator/detector.hpp"
#include "detect_orchestrator/logger.hpp"
#include "detect_orchestrator/pipeline.hpp"
#include "detect_orchestrator/runtime.hpp"
#include "detect_orchestrator/storage.hpp"
#include "mock_camera_driver/mock_burst_capture.hpp"

namespace {

std::atomic_bool g_stop_requested{false};

void handle_signal(int) {
    g_stop_requested = true;
}

bool has_image_extension(const std::filesystem::path& path) {
    auto ext = path.extension().string();
    for (auto& ch : ext) {
        ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    }
    return ext == ".jpg" || ext == ".jpeg" || ext == ".png" || ext == ".bmp";
}

std::vector<std::filesystem::path> list_images(const std::filesystem::path& dir) {
    std::vector<std::filesystem::path> images;
    if (!std::filesystem::exists(dir)) {
        return images;
    }
    for (const auto& entry : std::filesystem::directory_iterator(dir)) {
        if (entry.is_regular_file() && has_image_extension(entry.path())) {
            images.push_back(entry.path());
        }
    }
    std::sort(images.begin(), images.end());
    return images;
}

std::vector<int> camera_ids_from_config(const waterbag::RuntimeConfig& config) {
    std::vector<int> ids;
    ids.reserve(config.cameras.size());
    for (const auto& camera : config.cameras) {
        ids.push_back(camera.id);
    }
    if (ids.empty()) {
        ids = {1, 2};
    }
    std::sort(ids.begin(), ids.end());
    return ids;
}

std::shared_ptr<waterbag::ICameraBurstCapture> make_burst_capture(const waterbag::AppConfig& config) {
    const auto& backend = config.camera_driver.backend;
    if (backend == "mock") {
        return std::make_shared<waterbag::MockCameraBurstCapture>();
    }
    if (backend == "hikvision_mvs" || backend == "hik" || backend == "mvs") {
        return waterbag::make_hikvision_mvs_burst_capture(config.camera_driver, config.runtime);
    }
    throw std::runtime_error("unsupported camera_driver.backend: " + backend);
}

std::shared_ptr<waterbag::IPlcController> make_plc_controller(const waterbag::AppConfig& config) {
    const auto& backend = config.plc.backend;
    if (backend.empty() || backend == "mock") {
        return std::make_shared<waterbag::MockSemanticPlcController>(config.plc);
    }
    if (backend == "modbus_tcp" || backend == "modbus") {
        return std::make_shared<waterbag::ModbusTcpPlcController>(config.plc);
    }
    throw std::runtime_error("unsupported plc.backend: " + backend);
}

void save_and_log(waterbag::JsonlResultRepository& repository, const waterbag::InspectionResult& result) {
    repository.save(result);
    waterbag::Logger::instance().info(
        "result frame=" + result.frame_packet.frame_id +
        " bag=" + result.frame_packet.bag_id +
        " camera=" + std::to_string(result.frame_packet.camera_id) +
        " action=" + result.decision_result.control_action +
        " reason=" + result.decision_result.reason +
        " latency_ms=" + std::to_string(result.timing.total_ms));
}

std::shared_ptr<waterbag::InspectionPipeline> build_pipeline(const waterbag::AppConfig& config) {
    auto primary_detector = waterbag::make_detector(config.detectors.primary, "mock-primary");
    auto patch_detector = waterbag::make_detector(config.detectors.patch, "mock-patch");
    auto burst_capture = make_burst_capture(config);
    auto plc = make_plc_controller(config);
    burst_capture->start();

    return std::make_shared<waterbag::InspectionPipeline>(
        config.detection,
        config.correlation,
        burst_capture,
        plc,
        primary_detector,
        patch_detector);
}

int run_once(const waterbag::AppConfig& config, waterbag::InspectionPipeline& pipeline, waterbag::JsonlResultRepository& repository) {
    int processed = 0;
    std::vector<waterbag::FramePacket> defect_packets;
    waterbag::BagCaptureAssembler capture_assembler(
        camera_ids_from_config(config.runtime),
        config.runtime.expected_burst_images_per_camera,
        config.runtime.bag_capture_timeout);
    waterbag::SortReorderBuffer sort_reorder(config.runtime.sort_result_timeout);

    auto drain_sorter = [&] {
        for (auto& ready_result : sort_reorder.collect_ready()) {
            save_and_log(repository, pipeline.execute_sort_command(std::move(ready_result)));
            ++processed;
        }
    };

    for (const auto& camera : config.runtime.cameras) {
        for (const auto& path : list_images(camera.watch_dir)) {
            auto packet = waterbag::make_frame_packet(camera, path);
            packet.source = "cpp_once";
            packet.metadata["camera.backend"] = config.camera_driver.backend;
            packet.metadata["plc.backend"] = config.plc.backend;
            const auto station_result = pipeline.process_station_packet(packet);
            save_and_log(repository, station_result);
            if (station_result.decision_result.control_action == "defect_queued") {
                sort_reorder.register_bag(station_result.frame_packet);
                for (auto& ready_packet : capture_assembler.register_station_capture(station_result)) {
                    defect_packets.push_back(std::move(ready_packet));
                }
                for (auto& timeout_result : capture_assembler.collect_timeouts()) {
                    sort_reorder.store_result(std::move(timeout_result));
                }
                drain_sorter();
            }
            ++processed;
        }
    }

    for (const auto& packet : defect_packets) {
        auto result = pipeline.process_defect_packet(packet);
        if (result.decision_result.finalized) {
            sort_reorder.store_result(std::move(result));
            drain_sorter();
        } else {
            save_and_log(repository, result);
            ++processed;
        }
    }

    std::this_thread::sleep_for(config.correlation.pending_timeout + waterbag::Milliseconds{10});
    for (auto& timeout_result : capture_assembler.collect_timeouts()) {
        sort_reorder.store_result(std::move(timeout_result));
    }
    for (const auto& timeout_result : pipeline.flush_timeouts()) {
        sort_reorder.store_result(timeout_result);
    }
    drain_sorter();
    return processed;
}

std::string json_escape(const std::string& value) {
    std::ostringstream out;
    for (char ch : value) {
        switch (ch) {
            case '\\':
                out << "\\\\";
                break;
            case '"':
                out << "\\\"";
                break;
            case '\n':
                out << "\\n";
                break;
            case '\r':
                out << "\\r";
                break;
            case '\t':
                out << "\\t";
                break;
            default:
                out << ch;
        }
    }
    return out.str();
}

void print_check_result(const std::string& name, const waterbag::HardwareCheckResult& result) {
    std::cout << "\"" << name << "\":{";
    std::cout << "\"success\":" << (result.success ? "true" : "false") << ",";
    std::cout << "\"details\":[";
    for (std::size_t i = 0; i < result.details.size(); ++i) {
        if (i > 0) {
            std::cout << ",";
        }
        std::cout << "\"" << json_escape(result.details[i]) << "\"";
    }
    std::cout << "]}";
}

int run_hardware_check(const waterbag::AppConfig& config) {
    waterbag::HardwareCheckResult camera_result;
    waterbag::HardwareCheckResult plc_result;

    try {
        auto burst_capture = make_burst_capture(config);
        burst_capture->start();
        camera_result.details.push_back("camera_backend=" + config.camera_driver.backend);
        camera_result.details.push_back("camera_count=" + std::to_string(config.runtime.cameras.size()));
    } catch (const std::exception& error) {
        camera_result.success = false;
        camera_result.details.push_back("camera_check_failed:" + std::string(error.what()));
    }

    try {
        auto plc = make_plc_controller(config);
        plc_result = plc->check_hardware();
        plc_result.details.insert(plc_result.details.begin(), "plc_backend=" + plc->backend_name());
    } catch (const std::exception& error) {
        plc_result.success = false;
        plc_result.details.push_back("plc_check_failed:" + std::string(error.what()));
    }

    const bool success = camera_result.success && plc_result.success;
    std::cout << "{";
    std::cout << "\"hardware_check_status\":\"" << (success ? "ok" : "failed") << "\",";
    print_check_result("camera", camera_result);
    std::cout << ",";
    print_check_result("plc", plc_result);
    std::cout << "}\n";
    return success ? 0 : 1;
}

void print_usage(const char* program) {
    std::cout
        << "Usage: " << program << " [--config config/cpp_backend/demo.ini] [--once|--watch|--check-hardware]\n"
        << "\n"
        << "  --once   Process images already present in camera watch dirs, then exit.\n"
        << "  --watch  Run realtime directory polling service until Ctrl+C.\n"
        << "  --check-hardware  Open the configured camera backend and run PLC/IO preflight checks, then exit.\n"
        << "  --defect-workers N  Override runtime.defect_worker_count from config.\n";
}

}  // namespace

int main(int argc, char** argv) {
    std::filesystem::path config_path = "config/cpp_backend/demo.ini";
    bool once = false;
    bool watch = false;
    bool check_hardware = false;
    std::size_t defect_worker_override = 0;

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--config" && i + 1 < argc) {
            config_path = argv[++i];
        } else if (arg == "--once") {
            once = true;
        } else if (arg == "--watch") {
            watch = true;
        } else if (arg == "--check-hardware") {
            check_hardware = true;
        } else if (arg == "--defect-workers" && i + 1 < argc) {
            defect_worker_override = static_cast<std::size_t>(std::stoull(argv[++i]));
        } else if (arg == "--help" || arg == "-h") {
            print_usage(argv[0]);
            return 0;
        } else {
            std::cerr << "Unknown argument: " << arg << "\n";
            print_usage(argv[0]);
            return 2;
        }
    }

    try {
        auto config = waterbag::load_app_config(config_path);
        if (defect_worker_override > 0) {
            config.runtime.defect_worker_count = defect_worker_override;
        }
        waterbag::Logger::instance().configure(config.logger);
        waterbag::Logger::instance().info("loaded config: " + config_path.string());
        waterbag::Logger::instance().info("defect_worker_count=" + std::to_string(config.runtime.defect_worker_count));
        waterbag::Logger::instance().info("camera_driver.backend=" + config.camera_driver.backend);
        waterbag::Logger::instance().info("plc.backend=" + config.plc.backend);
        waterbag::Logger::instance().info("runtime.input_mode=" + config.runtime.input_mode);

        if (check_hardware) {
            return run_hardware_check(config);
        }

        waterbag::JsonlResultRepository repository(
            config.storage.result_jsonl,
            config.storage.async_result_writes,
            config.storage.result_queue_capacity,
            config.storage.drop_results_when_full);
        auto pipeline = build_pipeline(config);

        if (once || !watch) {
            const int processed = run_once(config, *pipeline, repository);
            waterbag::Logger::instance().info("once mode completed, events=" + std::to_string(processed));
            return 0;
        }

        std::signal(SIGINT, handle_signal);
        std::signal(SIGTERM, handle_signal);

        waterbag::RealtimeRuntime runtime(config.runtime, pipeline);
        runtime.add_listener([&repository](const waterbag::InspectionResult& result) {
            save_and_log(repository, result);
        });

        runtime.start();
        waterbag::Logger::instance().info("watch mode started");
        const auto started = waterbag::Clock::now();
        while (!g_stop_requested) {
            if (config.service.run_for.count() > 0 && waterbag::Clock::now() - started >= config.service.run_for) {
                break;
            }
            std::this_thread::sleep_for(waterbag::Milliseconds{100});
        }
        runtime.stop();
        waterbag::Logger::instance().info("watch mode stopped");
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "waterbag_cpp_service failed: " << error.what() << "\n";
        return 1;
    }
}
