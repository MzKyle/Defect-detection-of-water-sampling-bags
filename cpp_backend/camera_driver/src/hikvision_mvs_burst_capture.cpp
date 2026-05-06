#include "camera_driver/burst_capture.hpp"

#include <MvCameraControl.h>
#include <MvErrorDefine.h>

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cmath>
#include <condition_variable>
#include <cstring>
#include <filesystem>
#include <iomanip>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <unordered_map>
#include <utility>

#include "detect_orchestrator/logger.hpp"

namespace waterbag {
namespace {

constexpr unsigned int kSupportedTransportLayers =
    MV_GIGE_DEVICE |
    MV_USB_DEVICE |
    MV_GENTL_GIGE_DEVICE |
    MV_GENTL_CAMERALINK_DEVICE |
    MV_GENTL_CXP_DEVICE |
    MV_GENTL_XOF_DEVICE;

std::string lower_copy(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

std::string ret_code(int ret) {
    std::ostringstream out;
    out << "0x" << std::hex << std::uppercase << ret;
    return out.str();
}

void throw_on_mvs_error(const std::string& context, int ret) {
    if (ret != MV_OK) {
        throw std::runtime_error(context + " failed, nRet=" + ret_code(ret));
    }
}

bool is_gige_device(unsigned int transport_type) {
    return transport_type == MV_GIGE_DEVICE || transport_type == MV_GENTL_GIGE_DEVICE;
}

std::string bytes_to_string(const unsigned char* data, std::size_t capacity) {
    const auto* chars = reinterpret_cast<const char*>(data);
    return std::string(chars, strnlen(chars, capacity));
}

std::string device_serial(const MV_CC_DEVICE_INFO& info) {
    switch (info.nTLayerType) {
        case MV_GIGE_DEVICE:
        case MV_GENTL_GIGE_DEVICE:
            return bytes_to_string(info.SpecialInfo.stGigEInfo.chSerialNumber, sizeof(info.SpecialInfo.stGigEInfo.chSerialNumber));
        case MV_USB_DEVICE:
            return bytes_to_string(info.SpecialInfo.stUsb3VInfo.chSerialNumber, sizeof(info.SpecialInfo.stUsb3VInfo.chSerialNumber));
        case MV_GENTL_CAMERALINK_DEVICE:
            return bytes_to_string(info.SpecialInfo.stCMLInfo.chSerialNumber, sizeof(info.SpecialInfo.stCMLInfo.chSerialNumber));
        case MV_GENTL_CXP_DEVICE:
            return bytes_to_string(info.SpecialInfo.stCXPInfo.chSerialNumber, sizeof(info.SpecialInfo.stCXPInfo.chSerialNumber));
        case MV_GENTL_XOF_DEVICE:
            return bytes_to_string(info.SpecialInfo.stXoFInfo.chSerialNumber, sizeof(info.SpecialInfo.stXoFInfo.chSerialNumber));
        default:
            return {};
    }
}

std::string device_user_id(const MV_CC_DEVICE_INFO& info) {
    switch (info.nTLayerType) {
        case MV_GIGE_DEVICE:
        case MV_GENTL_GIGE_DEVICE:
            return bytes_to_string(info.SpecialInfo.stGigEInfo.chUserDefinedName, sizeof(info.SpecialInfo.stGigEInfo.chUserDefinedName));
        case MV_USB_DEVICE:
            return bytes_to_string(info.SpecialInfo.stUsb3VInfo.chUserDefinedName, sizeof(info.SpecialInfo.stUsb3VInfo.chUserDefinedName));
        case MV_GENTL_CAMERALINK_DEVICE:
            return bytes_to_string(info.SpecialInfo.stCMLInfo.chUserDefinedName, sizeof(info.SpecialInfo.stCMLInfo.chUserDefinedName));
        case MV_GENTL_CXP_DEVICE:
            return bytes_to_string(info.SpecialInfo.stCXPInfo.chUserDefinedName, sizeof(info.SpecialInfo.stCXPInfo.chUserDefinedName));
        case MV_GENTL_XOF_DEVICE:
            return bytes_to_string(info.SpecialInfo.stXoFInfo.chUserDefinedName, sizeof(info.SpecialInfo.stXoFInfo.chUserDefinedName));
        default:
            return {};
    }
}

std::string device_model(const MV_CC_DEVICE_INFO& info) {
    switch (info.nTLayerType) {
        case MV_GIGE_DEVICE:
        case MV_GENTL_GIGE_DEVICE:
            return bytes_to_string(info.SpecialInfo.stGigEInfo.chModelName, sizeof(info.SpecialInfo.stGigEInfo.chModelName));
        case MV_USB_DEVICE:
            return bytes_to_string(info.SpecialInfo.stUsb3VInfo.chModelName, sizeof(info.SpecialInfo.stUsb3VInfo.chModelName));
        case MV_GENTL_CAMERALINK_DEVICE:
            return bytes_to_string(info.SpecialInfo.stCMLInfo.chModelName, sizeof(info.SpecialInfo.stCMLInfo.chModelName));
        case MV_GENTL_CXP_DEVICE:
            return bytes_to_string(info.SpecialInfo.stCXPInfo.chModelName, sizeof(info.SpecialInfo.stCXPInfo.chModelName));
        case MV_GENTL_XOF_DEVICE:
            return bytes_to_string(info.SpecialInfo.stXoFInfo.chModelName, sizeof(info.SpecialInfo.stXoFInfo.chModelName));
        default:
            return {};
    }
}

std::string transport_name(unsigned int transport_type) {
    switch (transport_type) {
        case MV_GIGE_DEVICE:
            return "GigE";
        case MV_USB_DEVICE:
            return "USB3";
        case MV_GENTL_GIGE_DEVICE:
            return "GenTL-GigE";
        case MV_GENTL_CAMERALINK_DEVICE:
            return "GenTL-CameraLink";
        case MV_GENTL_CXP_DEVICE:
            return "GenTL-CXP";
        case MV_GENTL_XOF_DEVICE:
            return "GenTL-XoF";
        default:
            return "Unknown";
    }
}

std::string sanitize_filename(std::string value) {
    if (value.empty()) {
        return "unknown";
    }
    for (auto& ch : value) {
        const bool ok = std::isalnum(static_cast<unsigned char>(ch)) || ch == '-' || ch == '_' || ch == '.';
        if (!ok) {
            ch = '_';
        }
    }
    return value;
}

unsigned int frame_width(const MV_FRAME_OUT_INFO_EX& info) {
    return info.nExtendWidth > 0 ? info.nExtendWidth : info.nWidth;
}

unsigned int frame_height(const MV_FRAME_OUT_INFO_EX& info) {
    return info.nExtendHeight > 0 ? info.nExtendHeight : info.nHeight;
}

std::uint64_t frame_device_ticks(const MV_FRAME_OUT_INFO_EX& info) {
    return (static_cast<std::uint64_t>(info.nDevTimeStampHigh) << 32U) |
        static_cast<std::uint64_t>(info.nDevTimeStampLow);
}

long long ticks_to_ns(std::uint64_t ticks, std::uint64_t frequency_hz) {
    if (frequency_hz == 0) {
        return 0;
    }
    const long double ns = (static_cast<long double>(ticks) * 1000000000.0L) /
        static_cast<long double>(frequency_hz);
    return static_cast<long long>(ns);
}

MV_SAVE_IAMGE_TYPE image_type_from_format(const std::string& format) {
    const auto lowered = lower_copy(format);
    if (lowered == "bmp") {
        return MV_Image_Bmp;
    }
    if (lowered == "png") {
        return MV_Image_Png;
    }
    if (lowered == "tif" || lowered == "tiff") {
        return MV_Image_Tif;
    }
    return MV_Image_Jpeg;
}

std::string extension_from_format(const std::string& format) {
    const auto lowered = lower_copy(format);
    if (lowered == "bmp" || lowered == "png" || lowered == "tif") {
        return "." + lowered;
    }
    if (lowered == "tiff") {
        return ".tif";
    }
    return ".jpg";
}

struct EnumeratedDevice {
    unsigned int index = 0;
    MV_CC_DEVICE_INFO* info = nullptr;
    std::string serial;
    std::string user_id;
    std::string model;
    bool used = false;
};

struct ActiveBurst {
    CaptureSession session;
    BurstPlan plan;
    CaptureGroup group;
    std::size_t next_frame = 0;
    Clock::time_point armed_at = Clock::now();
};

struct DeviceState {
    CameraConfig camera;
    void* handle = nullptr;
    unsigned int transport_type = 0;
    std::string serial;
    std::string user_id;
    std::string model;
    std::uint64_t timestamp_frequency_hz = 1000000000ULL;
    long long camera_to_unified_offset_ns = 0;
    bool time_offset_ready = false;
    std::atomic_bool running{false};
    std::thread worker;
    std::mutex sdk_mutex;
    std::mutex time_mutex;
};

class HikvisionMvsBurstCapture final : public ICameraBurstCapture {
public:
    HikvisionMvsBurstCapture(CameraDriverConfig driver_config, RuntimeConfig runtime_config)
        : driver_config_(std::move(driver_config)), runtime_config_(std::move(runtime_config)) {}

    ~HikvisionMvsBurstCapture() override {
        shutdown();
    }

    void start() override {
        std::lock_guard<std::mutex> lock(lifecycle_mutex_);
        if (started_) {
            return;
        }

        try {
            throw_on_mvs_error("MV_CC_Initialize", MV_CC_Initialize());
            sdk_initialized_ = true;
            open_devices();
            for (auto& device : devices_) {
                {
                    std::lock_guard<std::mutex> sdk_lock(device->sdk_mutex);
                    throw_on_mvs_error("MV_CC_StartGrabbing camera " + std::to_string(device->camera.id), MV_CC_StartGrabbing(device->handle));
                }
                device->running = true;
                device->worker = std::thread(&HikvisionMvsBurstCapture::worker_loop, this, device.get());
            }
            started_ = true;
        } catch (...) {
            shutdown_unlocked();
            throw;
        }
    }

    void arm_burst(const CaptureSession& session, const BurstPlan& plan) override {
        auto* device = device_for_camera(session.camera_id);
        if (device == nullptr) {
            throw std::runtime_error("hikvision_mvs: no opened camera for camera_id=" + std::to_string(session.camera_id));
        }
        if (plan.frames.empty()) {
            throw std::runtime_error("hikvision_mvs: burst plan has no frames");
        }

        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (active_by_camera_.find(session.camera_id) != active_by_camera_.end()) {
                throw std::runtime_error("hikvision_mvs: camera already has an active burst, camera_id=" + std::to_string(session.camera_id));
            }
        }

        if (driver_config_.apply_frame_settings) {
            apply_frame_settings(*device, plan.frames.front(), true);
        }

        ActiveBurst active;
        active.session = session;
        active.plan = plan;
        active.group.capture_session_id = session.capture_session_id;
        active.group.bag_id = session.bag_id;
        active.group.station_id = session.station_id;
        active.group.camera_id = session.camera_id;
        active.group.side_id = session.side_id;
        active.group.burst_plan = plan;
        active.armed_at = Clock::now();

        {
            std::lock_guard<std::mutex> lock(mutex_);
            session_camera_[session.capture_session_id] = session.camera_id;
            active_by_camera_[session.camera_id] = std::move(active);
        }
        cv_.notify_all();
    }

    std::optional<CaptureGroup> poll_completed_group(const std::string& capture_session_id) override {
        std::unique_lock<std::mutex> lock(mutex_);
        const auto deadline = Clock::now() + driver_config_.frame_timeout;
        cv_.wait_until(lock, deadline, [&] {
            return completed_groups_.find(capture_session_id) != completed_groups_.end();
        });

        const auto completed = completed_groups_.find(capture_session_id);
        if (completed != completed_groups_.end()) {
            auto group = completed->second;
            completed_groups_.erase(completed);
            session_camera_.erase(capture_session_id);
            return group;
        }

        const auto session_it = session_camera_.find(capture_session_id);
        if (session_it == session_camera_.end()) {
            return std::nullopt;
        }

        const auto active_it = active_by_camera_.find(session_it->second);
        if (active_it == active_by_camera_.end()) {
            return std::nullopt;
        }

        auto group = active_it->second.group;
        const auto expected = active_it->second.plan.frames.size();
        group.complete = false;
        group.sync_valid = false;
        group.sync_warning = "hikvision_mvs_burst_timeout:expected=" +
            std::to_string(expected) + ":received=" + std::to_string(group.images.size());
        active_by_camera_.erase(active_it);
        session_camera_.erase(session_it);
        return group;
    }

private:
    void shutdown() {
        std::lock_guard<std::mutex> lock(lifecycle_mutex_);
        shutdown_unlocked();
    }

    void shutdown_unlocked() {
        for (auto& device : devices_) {
            device->running = false;
        }
        for (auto& device : devices_) {
            if (device->worker.joinable()) {
                device->worker.join();
            }
        }
        for (auto& device : devices_) {
            if (device->handle != nullptr) {
                MV_CC_StopGrabbing(device->handle);
                MV_CC_CloseDevice(device->handle);
                MV_CC_DestroyHandle(device->handle);
                device->handle = nullptr;
            }
        }
        devices_by_camera_.clear();
        devices_.clear();
        {
            std::lock_guard<std::mutex> lock(mutex_);
            active_by_camera_.clear();
            completed_groups_.clear();
            session_camera_.clear();
        }
        if (sdk_initialized_) {
            MV_CC_Finalize();
            sdk_initialized_ = false;
        }
        started_ = false;
    }

    void open_devices() {
        MV_CC_DEVICE_INFO_LIST device_list{};
        throw_on_mvs_error("MV_CC_EnumDevices", MV_CC_EnumDevices(kSupportedTransportLayers, &device_list));
        if (device_list.nDeviceNum == 0) {
            throw std::runtime_error("hikvision_mvs: no Hikvision MVS cameras found");
        }

        std::vector<EnumeratedDevice> enumerated;
        enumerated.reserve(device_list.nDeviceNum);
        for (unsigned int i = 0; i < device_list.nDeviceNum; ++i) {
            if (device_list.pDeviceInfo[i] == nullptr) {
                continue;
            }
            EnumeratedDevice item;
            item.index = i;
            item.info = device_list.pDeviceInfo[i];
            item.serial = device_serial(*item.info);
            item.user_id = device_user_id(*item.info);
            item.model = device_model(*item.info);
            enumerated.push_back(item);
            Logger::instance().info(
                "hikvision_mvs device[" + std::to_string(i) + "] transport=" + transport_name(item.info->nTLayerType) +
                " model=" + item.model +
                " serial=" + item.serial +
                " user_id=" + item.user_id);
        }

        if (runtime_config_.cameras.empty()) {
            throw std::runtime_error("hikvision_mvs: runtime camera list is empty");
        }

        for (const auto& camera : runtime_config_.cameras) {
            auto& selected = select_device_for_camera(camera, enumerated);
            selected.used = true;

            auto device = std::make_unique<DeviceState>();
            device->camera = camera;
            device->transport_type = selected.info->nTLayerType;
            device->serial = selected.serial;
            device->user_id = selected.user_id;
            device->model = selected.model;

            throw_on_mvs_error("MV_CC_CreateHandle camera " + std::to_string(camera.id), MV_CC_CreateHandle(&device->handle, selected.info));
            throw_on_mvs_error(
                "MV_CC_OpenDevice camera " + std::to_string(camera.id),
                MV_CC_OpenDevice(device->handle, MV_ACCESS_Exclusive, 0));
            configure_device(*device);

            devices_by_camera_[camera.id] = device.get();
            devices_.push_back(std::move(device));
        }
    }

    EnumeratedDevice& select_device_for_camera(const CameraConfig& camera, std::vector<EnumeratedDevice>& devices) {
        if (!camera.serial_number.empty()) {
            auto found = std::find_if(devices.begin(), devices.end(), [&](const auto& item) {
                return !item.used && item.serial == camera.serial_number;
            });
            if (found != devices.end()) {
                return *found;
            }
            throw std::runtime_error("hikvision_mvs: camera " + std::to_string(camera.id) +
                " serial_number not found: " + camera.serial_number);
        }

        if (!camera.device_user_id.empty()) {
            auto found = std::find_if(devices.begin(), devices.end(), [&](const auto& item) {
                return !item.used && item.user_id == camera.device_user_id;
            });
            if (found != devices.end()) {
                return *found;
            }
            throw std::runtime_error("hikvision_mvs: camera " + std::to_string(camera.id) +
                " device_user_id not found: " + camera.device_user_id);
        }

        if (camera.device_index >= 0) {
            auto found = std::find_if(devices.begin(), devices.end(), [&](const auto& item) {
                return !item.used && item.index == static_cast<unsigned int>(camera.device_index);
            });
            if (found != devices.end()) {
                return *found;
            }
            throw std::runtime_error("hikvision_mvs: camera " + std::to_string(camera.id) +
                " device_index not available: " + std::to_string(camera.device_index));
        }

        auto found = std::find_if(devices.begin(), devices.end(), [](const auto& item) {
            return !item.used;
        });
        if (found == devices.end()) {
            throw std::runtime_error("hikvision_mvs: not enough physical cameras for configured camera list");
        }
        return *found;
    }

    void configure_device(DeviceState& device) {
        if (is_gige_device(device.transport_type)) {
            const int packet_size = MV_CC_GetOptimalPacketSize(device.handle);
            if (packet_size > 0) {
                warn_if_failed(
                    device,
                    "GevSCPSPacketSize",
                    MV_CC_SetIntValueEx(device.handle, "GevSCPSPacketSize", packet_size));
            }
        }

        warn_if_failed(device, "AcquisitionFrameRateEnable", MV_CC_SetBoolValue(device.handle, "AcquisitionFrameRateEnable", false));
        warn_if_failed(device, "ExposureAuto", MV_CC_SetEnumValueByString(device.handle, "ExposureAuto", "Off"));
        warn_if_failed(device, "GainAuto", MV_CC_SetEnumValueByString(device.handle, "GainAuto", "Off"));
        warn_if_failed(device, "TriggerSelector", MV_CC_SetEnumValueByString(device.handle, "TriggerSelector", "FrameStart"));
        throw_on_mvs_error("TriggerMode camera " + std::to_string(device.camera.id), MV_CC_SetEnumValue(device.handle, "TriggerMode", MV_TRIGGER_MODE_ON));

        const auto trigger_source = device.camera.trigger_source.empty()
            ? driver_config_.default_trigger_source
            : device.camera.trigger_source;
        throw_on_mvs_error(
            "TriggerSource " + trigger_source + " camera " + std::to_string(device.camera.id),
            MV_CC_SetEnumValueByString(device.handle, "TriggerSource", trigger_source.c_str()));

        const auto trigger_activation = device.camera.trigger_activation.empty()
            ? driver_config_.default_trigger_activation
            : device.camera.trigger_activation;
        if (!trigger_activation.empty()) {
            warn_if_failed(
                device,
                "TriggerActivation",
                MV_CC_SetEnumValueByString(device.handle, "TriggerActivation", trigger_activation.c_str()));
        }

        if (driver_config_.enable_chunk_timestamp) {
            configure_chunk_timestamp(device);
        }
        if (driver_config_.enable_ptp && is_gige_device(device.transport_type)) {
            configure_ptp(device);
        }
        device.timestamp_frequency_hz = read_timestamp_frequency(device);

        Logger::instance().info(
            "hikvision_mvs camera_id=" + std::to_string(device.camera.id) +
            " opened serial=" + device.serial +
            " user_id=" + device.user_id +
            " trigger_source=" + trigger_source +
            " timestamp_frequency_hz=" + std::to_string(device.timestamp_frequency_hz));
    }

    void configure_chunk_timestamp(DeviceState& device) {
        const int chunk_mode = MV_CC_SetBoolValue(device.handle, "ChunkModeActive", true);
        warn_if_failed(device, "ChunkModeActive", chunk_mode);
        if (chunk_mode != MV_OK) {
            return;
        }

        warn_if_failed(device, "ChunkSelector=Exposure", MV_CC_SetEnumValueByString(device.handle, "ChunkSelector", "Exposure"));
        warn_if_failed(device, "ChunkEnable Exposure", MV_CC_SetBoolValue(device.handle, "ChunkEnable", true));
        warn_if_failed(device, "ChunkSelector=Timestamp", MV_CC_SetEnumValueByString(device.handle, "ChunkSelector", "Timestamp"));
        warn_if_failed(device, "ChunkEnable Timestamp", MV_CC_SetBoolValue(device.handle, "ChunkEnable", true));
    }

    void configure_ptp(DeviceState& device) {
        const int gev_ret = MV_CC_SetBoolValue(device.handle, "GevIEEE1588", true);
        const int ptp_ret = MV_CC_SetBoolValue(device.handle, "PtpEnable", true);
        if (gev_ret != MV_OK && ptp_ret != MV_OK) {
            Logger::instance().warn(
                "hikvision_mvs camera_id=" + std::to_string(device.camera.id) +
                " could not enable PTP/IEEE1588, GevIEEE1588=" + ret_code(gev_ret) +
                " PtpEnable=" + ret_code(ptp_ret));
        }
    }

    std::uint64_t read_timestamp_frequency(DeviceState& device) {
        MVCC_INTVALUE_EX value{};
        if (MV_CC_GetIntValueEx(device.handle, "GevTimestampTickFrequency", &value) == MV_OK && value.nCurValue > 0) {
            return static_cast<std::uint64_t>(value.nCurValue);
        }
        return 1000000000ULL;
    }

    void warn_if_failed(DeviceState& device, const std::string& node, int ret) {
        if (ret != MV_OK) {
            Logger::instance().warn(
                "hikvision_mvs camera_id=" + std::to_string(device.camera.id) +
                " optional node " + node + " failed, nRet=" + ret_code(ret));
        }
    }

    DeviceState* device_for_camera(int camera_id) const {
        const auto found = devices_by_camera_.find(camera_id);
        return found == devices_by_camera_.end() ? nullptr : found->second;
    }

    void worker_loop(DeviceState* device) {
        while (device->running) {
            MV_FRAME_OUT frame{};
            int ret = MV_E_NODATA;
            {
                std::lock_guard<std::mutex> sdk_lock(device->sdk_mutex);
                ret = MV_CC_GetImageBuffer(device->handle, &frame, 100);
            }

            if (ret == MV_OK) {
                handle_frame(*device, frame);
                std::lock_guard<std::mutex> sdk_lock(device->sdk_mutex);
                MV_CC_FreeImageBuffer(device->handle, &frame);
            } else if (static_cast<unsigned int>(ret) != MV_E_NODATA &&
                       static_cast<unsigned int>(ret) != MV_E_GC_TIMEOUT) {
                Logger::instance().warn(
                    "hikvision_mvs camera_id=" + std::to_string(device->camera.id) +
                    " GetImageBuffer failed, nRet=" + ret_code(ret));
            }
        }
    }

    void handle_frame(DeviceState& device, const MV_FRAME_OUT& frame) {
        CaptureSession session;
        LightFramePlan frame_plan;
        std::optional<LightFramePlan> next_plan;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            const auto active_it = active_by_camera_.find(device.camera.id);
            if (active_it == active_by_camera_.end()) {
                return;
            }
            auto& active = active_it->second;
            if (active.next_frame >= active.plan.frames.size()) {
                return;
            }
            session = active.session;
            frame_plan = active.plan.frames[active.next_frame];
            ++active.next_frame;
            if (active.next_frame < active.plan.frames.size()) {
                next_plan = active.plan.frames[active.next_frame];
            }
        }

        if (driver_config_.apply_frame_settings && next_plan) {
            apply_frame_settings(device, *next_plan, false);
        }

        std::string warning;
        auto image = build_burst_image(device, session, frame_plan, frame);
        try {
            save_frame(device, frame, image.image_path);
        } catch (const std::exception& error) {
            warning = error.what();
            Logger::instance().warn("hikvision_mvs save frame failed: " + warning);
        }

        {
            std::lock_guard<std::mutex> lock(mutex_);
            const auto active_it = active_by_camera_.find(device.camera.id);
            if (active_it == active_by_camera_.end() || active_it->second.session.capture_session_id != session.capture_session_id) {
                return;
            }

            auto& active = active_it->second;
            active.group.images.push_back(std::move(image));
            if (!warning.empty()) {
                active.group.sync_warning = warning;
                active.group.sync_valid = false;
            }

            if (active.group.images.size() >= active.plan.frames.size()) {
                active.group.complete = active.group.images.size() == active.plan.frames.size();
                active.group.sync_valid = active.group.complete && active.group.sync_warning.empty();
                completed_groups_[session.capture_session_id] = active.group;
                active_by_camera_.erase(active_it);
                cv_.notify_all();
            }
        }
    }

    void apply_frame_settings(DeviceState& device, const LightFramePlan& frame_plan, bool required) {
        std::lock_guard<std::mutex> sdk_lock(device.sdk_mutex);
        const int exposure_ret = MV_CC_SetFloatValue(device.handle, "ExposureTime", static_cast<float>(frame_plan.exposure_us));
        if (required) {
            throw_on_mvs_error(
                "ExposureTime camera " + std::to_string(device.camera.id) +
                " frame " + std::to_string(frame_plan.frame_index),
                exposure_ret);
        } else {
            warn_if_failed(device, "ExposureTime", exposure_ret);
        }

        const int gain_ret = MV_CC_SetFloatValue(device.handle, "Gain", static_cast<float>(frame_plan.gain));
        if (gain_ret != MV_OK) {
            warn_if_failed(device, "Gain", gain_ret);
        }
    }

    BurstImage build_burst_image(
        DeviceState& device,
        const CaptureSession& session,
        const LightFramePlan& frame_plan,
        const MV_FRAME_OUT& frame) {
        const auto& info = frame.stFrameInfo;
        const auto host_received_hw = UnifiedHardwareClock::now();
        const auto host_received_at = SystemClock::now();
        const int exposure_us = info.fExposureTime > 0.0F
            ? static_cast<int>(std::llround(info.fExposureTime))
            : frame_plan.exposure_us;

        HardwareTimestamp exposure_start_hw{host_received_hw.ns - static_cast<long long>(exposure_us) * 1000LL};
        const auto ticks = frame_device_ticks(info);
        if (ticks > 0) {
            const auto camera_ns = ticks_to_ns(ticks, device.timestamp_frequency_hz);
            if (driver_config_.host_correlate_camera_time) {
                std::lock_guard<std::mutex> lock(device.time_mutex);
                if (!device.time_offset_ready) {
                    device.camera_to_unified_offset_ns = host_received_hw.ns - camera_ns;
                    device.time_offset_ready = true;
                }
                exposure_start_hw.ns = camera_ns + device.camera_to_unified_offset_ns;
            } else {
                exposure_start_hw.ns = camera_ns;
            }
        }

        const auto exposure_end_hw = HardwareTimestamp{exposure_start_hw.ns + static_cast<long long>(exposure_us) * 1000LL};
        const auto exposure_to_host_us = driver_config_.host_correlate_camera_time
            ? std::max<long long>(0, diff_us(exposure_start_hw, host_received_hw))
            : static_cast<long long>(exposure_us);

        BurstImage image;
        image.capture_session_id = session.capture_session_id;
        image.bag_id = session.bag_id;
        image.station_id = session.station_id;
        image.camera_id = session.camera_id;
        image.side_id = session.side_id;
        image.frame_index = frame_plan.frame_index;
        image.light_id = frame_plan.light_id;
        image.image_path = make_image_path(session, frame_plan);
        image.camera_frame_id = info.nFrameNum > 0 ? info.nFrameNum : info.nFrameCounter;
        image.exposure_start_hw = exposure_start_hw;
        image.exposure_end_hw = exposure_end_hw;
        image.host_received_hw = host_received_hw;
        image.host_received_at = host_received_at;
        image.exposure_start = host_received_at - std::chrono::microseconds(exposure_to_host_us);
        image.exposure_end = image.exposure_start + std::chrono::microseconds(exposure_us);
        return image;
    }

    std::filesystem::path make_image_path(const CaptureSession& session, const LightFramePlan& frame_plan) const {
        const auto camera_dir = "camera" + std::to_string(session.camera_id);
        const auto filename =
            sanitize_filename(session.capture_session_id) +
            "_f" + std::to_string(frame_plan.frame_index) +
            "_" + sanitize_filename(to_string(frame_plan.light_id)) +
            extension_from_format(driver_config_.save_format);
        return driver_config_.output_dir / camera_dir / sanitize_filename(session.bag_id) / filename;
    }

    void save_frame(DeviceState& device, const MV_FRAME_OUT& frame, const std::filesystem::path& path) {
        std::filesystem::create_directories(path.parent_path());
        auto path_string = path.string();

        MV_SAVE_IMAGE_TO_FILE_PARAM_EX save{};
        save.nWidth = frame_width(frame.stFrameInfo);
        save.nHeight = frame_height(frame.stFrameInfo);
        save.enPixelType = frame.stFrameInfo.enPixelType;
        save.pData = frame.pBufAddr;
        save.nDataLen = frame.stFrameInfo.nFrameLen;
        save.enImageType = image_type_from_format(driver_config_.save_format);
        save.pcImagePath = const_cast<char*>(path_string.c_str());
        save.nQuality = static_cast<unsigned int>(std::clamp(driver_config_.jpeg_quality, 50, 99));
        save.iMethodValue = 1;

        std::lock_guard<std::mutex> sdk_lock(device.sdk_mutex);
        throw_on_mvs_error("MV_CC_SaveImageToFileEx " + path.string(), MV_CC_SaveImageToFileEx(device.handle, &save));
    }

    CameraDriverConfig driver_config_;
    RuntimeConfig runtime_config_;
    bool sdk_initialized_ = false;
    bool started_ = false;
    mutable std::mutex lifecycle_mutex_;
    std::vector<std::unique_ptr<DeviceState>> devices_;
    std::unordered_map<int, DeviceState*> devices_by_camera_;

    std::mutex mutex_;
    std::condition_variable cv_;
    std::unordered_map<int, ActiveBurst> active_by_camera_;
    std::unordered_map<std::string, int> session_camera_;
    std::map<std::string, CaptureGroup> completed_groups_;
};

}  // namespace

std::shared_ptr<ICameraBurstCapture> make_hikvision_mvs_burst_capture(
    CameraDriverConfig driver_config,
    RuntimeConfig runtime_config) {
    return std::make_shared<HikvisionMvsBurstCapture>(std::move(driver_config), std::move(runtime_config));
}

}  // namespace waterbag
