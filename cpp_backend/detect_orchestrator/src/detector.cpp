#include <array>
#include "detect_orchestrator/detector.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <filesystem>
#include <limits>
#include <stdexcept>
#include <utility>

#if defined(WATERBAG_ENABLE_ONNXRUNTIME)
#include <opencv2/core.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>
#include <onnxruntime_cxx_api.h>
#endif

namespace waterbag {
namespace {

std::string lower_copy(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

bool contains_any(const std::string& text, const std::vector<std::string>& needles) {
    for (const auto& needle : needles) {
        if (text.find(needle) != std::string::npos) {
            return true;
        }
    }
    return false;
}

DetectionBox sample_box(double confidence) {
    return DetectionBox{120, 90, 220, 190, "defect", confidence};
}

#if defined(WATERBAG_ENABLE_ONNXRUNTIME)

struct LetterboxResult {
    cv::Mat image;
    double scale = 1.0;
    int pad_left = 0;
    int pad_top = 0;
};

void throw_ort_error(const OrtApi* api, OrtStatus* status, const std::string& context) {
    if (status == nullptr) {
        return;
    }
    std::string message = api->GetErrorMessage(status);
    api->ReleaseStatus(status);
    throw std::runtime_error(context + ": " + message);
}

int clamp_int(int value, int low, int high) {
    return std::max(low, std::min(value, high));
}

LetterboxResult letterbox(const cv::Mat& source, int target_size) {
    LetterboxResult result;
    if (source.empty()) {
        throw std::runtime_error("cannot letterbox an empty image");
    }

    const double scale = std::min(
        static_cast<double>(target_size) / static_cast<double>(source.cols),
        static_cast<double>(target_size) / static_cast<double>(source.rows));
    const int resized_width = std::max(1, static_cast<int>(std::round(source.cols * scale)));
    const int resized_height = std::max(1, static_cast<int>(std::round(source.rows * scale)));

    cv::Mat resized;
    cv::resize(source, resized, cv::Size(resized_width, resized_height), 0.0, 0.0, cv::INTER_LINEAR);

    const int pad_width = std::max(target_size - resized_width, 0);
    const int pad_height = std::max(target_size - resized_height, 0);
    const int pad_left = pad_width / 2;
    const int pad_right = pad_width - pad_left;
    const int pad_top = pad_height / 2;
    const int pad_bottom = pad_height - pad_top;

    cv::copyMakeBorder(
        resized,
        result.image,
        pad_top,
        pad_bottom,
        pad_left,
        pad_right,
        cv::BORDER_CONSTANT,
        cv::Scalar(114, 114, 114));

    result.scale = scale;
    result.pad_left = pad_left;
    result.pad_top = pad_top;
    return result;
}

double intersection_over_union(const DetectionBox& lhs, const DetectionBox& rhs) {
    const int left = std::max(lhs.x1, rhs.x1);
    const int top = std::max(lhs.y1, rhs.y1);
    const int right = std::min(lhs.x2, rhs.x2);
    const int bottom = std::min(lhs.y2, rhs.y2);

    if (right <= left || bottom <= top) {
        return 0.0;
    }

    const double intersection_area = static_cast<double>(right - left) * static_cast<double>(bottom - top);
    const double lhs_area = static_cast<double>(std::max(lhs.x2 - lhs.x1, 0)) * static_cast<double>(std::max(lhs.y2 - lhs.y1, 0));
    const double rhs_area = static_cast<double>(std::max(rhs.x2 - rhs.x1, 0)) * static_cast<double>(std::max(rhs.y2 - rhs.y1, 0));
    const double union_area = lhs_area + rhs_area - intersection_area;
    if (union_area <= 0.0) {
        return 0.0;
    }
    return intersection_area / union_area;
}

std::vector<DetectionBox> non_max_suppression(
    std::vector<DetectionBox> boxes,
    double iou_threshold,
    std::size_t max_detections) {
    std::sort(boxes.begin(), boxes.end(), [](const DetectionBox& lhs, const DetectionBox& rhs) {
        return lhs.confidence > rhs.confidence;
    });

    std::vector<DetectionBox> kept;
    kept.reserve(std::min(boxes.size(), max_detections == 0 ? boxes.size() : max_detections));
    for (const auto& box : boxes) {
        bool suppressed = false;
        for (const auto& kept_box : kept) {
            if (intersection_over_union(box, kept_box) > iou_threshold) {
                suppressed = true;
                break;
            }
        }
        if (!suppressed) {
            kept.push_back(box);
            if (max_detections > 0 && kept.size() >= max_detections) {
                break;
            }
        }
    }
    return kept;
}

struct OrtValueDeleter {
    const OrtApi* api = nullptr;

    void operator()(OrtValue* value) const {
        if (api != nullptr && value != nullptr) {
            api->ReleaseValue(value);
        }
    }
};

struct OrtMemoryInfoDeleter {
    const OrtApi* api = nullptr;

    void operator()(OrtMemoryInfo* value) const {
        if (api != nullptr && value != nullptr) {
            api->ReleaseMemoryInfo(value);
        }
    }
};

struct OrtTensorTypeAndShapeInfoDeleter {
    const OrtApi* api = nullptr;

    void operator()(OrtTensorTypeAndShapeInfo* value) const {
        if (api != nullptr && value != nullptr) {
            api->ReleaseTensorTypeAndShapeInfo(value);
        }
    }
};

std::vector<DetectionBox> decode_predictions(
    const float* values,
    const std::vector<int64_t>& dims,
    const LetterboxResult& letterbox_info,
    const cv::Size& original_size,
    DetectionStage stage,
    double nms_iou_threshold,
    std::size_t max_detections) {
    if (dims.size() < 2) {
        throw std::runtime_error("unexpected ONNX output rank for detector");
    }

    bool channels_first = false;
    std::size_t num_predictions = 0;
    std::size_t num_features = 0;
    if (dims.size() == 2) {
        num_predictions = static_cast<std::size_t>(dims[0]);
        num_features = static_cast<std::size_t>(dims[1]);
    } else if (dims.size() == 3) {
        channels_first = dims[1] < dims[2];
        if (channels_first) {
            num_features = static_cast<std::size_t>(dims[1]);
            num_predictions = static_cast<std::size_t>(dims[2]);
        } else {
            num_predictions = static_cast<std::size_t>(dims[1]);
            num_features = static_cast<std::size_t>(dims[2]);
        }
    } else {
        throw std::runtime_error("unsupported ONNX output rank for detector");
    }

    if (num_features < 4) {
        throw std::runtime_error("ONNX detector output must have at least 4 box features");
    }

    const std::size_t class_count = num_features - 4;
    if (class_count == 0) {
        throw std::runtime_error("ONNX detector output does not contain class scores");
    }

    constexpr float score_floor = 0.01f;
    std::vector<DetectionBox> boxes;
    boxes.reserve(num_predictions);

    const auto read_feature = [values, channels_first, num_predictions, num_features](std::size_t prediction_index, std::size_t feature_index) {
        if (channels_first) {
            return values[feature_index * num_predictions + prediction_index];
        }
        return values[prediction_index * num_features + feature_index];
    };

    for (std::size_t prediction_index = 0; prediction_index < num_predictions; ++prediction_index) {
        const float center_x = read_feature(prediction_index, 0);
        const float center_y = read_feature(prediction_index, 1);
        const float box_width = read_feature(prediction_index, 2);
        const float box_height = read_feature(prediction_index, 3);

        float best_confidence = 0.0f;
        std::size_t best_class = 0;
        for (std::size_t class_index = 0; class_index < class_count; ++class_index) {
            const float score = read_feature(prediction_index, 4 + class_index);
            if (score > best_confidence) {
                best_confidence = score;
                best_class = class_index;
            }
        }

        (void)best_class;
        if (best_confidence < score_floor) {
            continue;
        }

        const float x1_scaled = (center_x - box_width / 2.0f - static_cast<float>(letterbox_info.pad_left)) / static_cast<float>(letterbox_info.scale);
        const float y1_scaled = (center_y - box_height / 2.0f - static_cast<float>(letterbox_info.pad_top)) / static_cast<float>(letterbox_info.scale);
        const float x2_scaled = (center_x + box_width / 2.0f - static_cast<float>(letterbox_info.pad_left)) / static_cast<float>(letterbox_info.scale);
        const float y2_scaled = (center_y + box_height / 2.0f - static_cast<float>(letterbox_info.pad_top)) / static_cast<float>(letterbox_info.scale);

        const int x1 = clamp_int(static_cast<int>(std::round(x1_scaled)), 0, std::max(original_size.width - 1, 0));
        const int y1 = clamp_int(static_cast<int>(std::round(y1_scaled)), 0, std::max(original_size.height - 1, 0));
        const int x2 = clamp_int(static_cast<int>(std::round(x2_scaled)), 0, std::max(original_size.width - 1, 0));
        const int y2 = clamp_int(static_cast<int>(std::round(y2_scaled)), 0, std::max(original_size.height - 1, 0));

        if (x2 <= x1 || y2 <= y1) {
            continue;
        }

        boxes.push_back(DetectionBox{
            x1,
            y1,
            x2,
            y2,
            stage == DetectionStage::Presence ? "waterbag" : "defect",
            static_cast<double>(best_confidence)});
    }

    return non_max_suppression(std::move(boxes), nms_iou_threshold, max_detections);
}

class OnnxRuntimeDetector final : public IDetector {
public:
    explicit OnnxRuntimeDetector(ModelConfig config, std::string backend_name)
        : config_(std::move(config)), backend_name_(std::move(backend_name)), model_path_(config_.model_path.string()) {
        initialize();
    }

    ~OnnxRuntimeDetector() override {
        shutdown();
    }

    std::string backend_name() const override {
        return backend_name_;
    }

    PerceptionResult detect(const FramePacket& packet, DetectionStage stage) override {
        const auto started = Clock::now();
        const cv::Mat image = cv::imread(packet.source_path.string(), cv::IMREAD_COLOR);
        if (image.empty()) {
            throw std::runtime_error("failed to read image: " + packet.source_path.string());
        }

        const auto letterbox_info = letterbox(image, config_.imgsz);
        cv::Mat rgb_image;
        cv::cvtColor(letterbox_info.image, rgb_image, cv::COLOR_BGR2RGB);
        cv::Mat normalized_image;
        rgb_image.convertTo(normalized_image, CV_32F, 1.0 / 255.0);

        const int height = normalized_image.rows;
        const int width = normalized_image.cols;
        const std::size_t plane_size = static_cast<std::size_t>(height * width);
        std::vector<float> input_tensor_values(static_cast<std::size_t>(3) * plane_size);
        for (int row = 0; row < height; ++row) {
            const auto* pixels = normalized_image.ptr<cv::Vec3f>(row);
            for (int column = 0; column < width; ++column) {
                const std::size_t offset = static_cast<std::size_t>(row * width + column);
                input_tensor_values[offset] = pixels[column][0];
                input_tensor_values[plane_size + offset] = pixels[column][1];
                input_tensor_values[plane_size * 2 + offset] = pixels[column][2];
            }
        }

        std::array<int64_t, 4> input_shape{1, 3, height, width};
        OrtMemoryInfo* raw_memory_info = nullptr;
        throw_ort_error(api_, api_->CreateCpuMemoryInfo(OrtArenaAllocator, OrtMemTypeDefault, &raw_memory_info), "CreateCpuMemoryInfo");
        OrtMemoryInfoDeleter memory_info_deleter{api_};
        std::unique_ptr<OrtMemoryInfo, OrtMemoryInfoDeleter> memory_info(raw_memory_info, memory_info_deleter);

        OrtValue* raw_input_tensor = nullptr;
        throw_ort_error(
            api_,
            api_->CreateTensorWithDataAsOrtValue(
                memory_info.get(),
                input_tensor_values.data(),
                input_tensor_values.size() * sizeof(float),
                input_shape.data(),
                input_shape.size(),
                ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT,
                &raw_input_tensor),
            "CreateTensorWithDataAsOrtValue");
        OrtValueDeleter value_deleter{api_};
        std::unique_ptr<OrtValue, OrtValueDeleter> input_tensor(raw_input_tensor, value_deleter);

        const char* input_names[] = {input_name_.c_str()};
        const OrtValue* input_values[] = {input_tensor.get()};
        const char* output_names[] = {output_name_.c_str()};
        OrtValue* raw_output_tensor = nullptr;
        throw_ort_error(
            api_,
            api_->Run(
                session_,
                nullptr,
                input_names,
                input_values,
                1,
                output_names,
                1,
                &raw_output_tensor),
            "Run");
        std::unique_ptr<OrtValue, OrtValueDeleter> output_tensor(raw_output_tensor, value_deleter);

        OrtTensorTypeAndShapeInfo* raw_shape_info = nullptr;
        throw_ort_error(api_, api_->GetTensorTypeAndShape(output_tensor.get(), &raw_shape_info), "GetTensorTypeAndShape");
        OrtTensorTypeAndShapeInfoDeleter shape_deleter{api_};
        std::unique_ptr<OrtTensorTypeAndShapeInfo, OrtTensorTypeAndShapeInfoDeleter> shape_info(raw_shape_info, shape_deleter);

        size_t dimension_count = 0;
        throw_ort_error(api_, api_->GetDimensionsCount(shape_info.get(), &dimension_count), "GetDimensionsCount");
        std::vector<int64_t> dimensions(dimension_count);
        throw_ort_error(api_, api_->GetDimensions(shape_info.get(), dimensions.data(), dimension_count), "GetDimensions");

        ONNXTensorElementDataType element_type = ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
        throw_ort_error(api_, api_->GetTensorElementType(shape_info.get(), &element_type), "GetTensorElementType");
        if (element_type != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT) {
            throw std::runtime_error("onnxruntime detector expects float32 outputs");
        }

        void* raw_output_data = nullptr;
        throw_ort_error(api_, api_->GetTensorMutableData(output_tensor.get(), &raw_output_data), "GetTensorMutableData");
        const auto* output_data = static_cast<const float*>(raw_output_data);

        PerceptionResult result;
        result.stage_name = to_string(stage);
        result.detector_backend = backend_name_;
        result.triggered = true;
        result.boxes = decode_predictions(
            output_data,
            dimensions,
            letterbox_info,
            image.size(),
            stage,
            config_.nms_iou_threshold,
            config_.max_detections);
        result.inference_ms = elapsed_ms(started);
        return result;
    }

private:
    void initialize() {
        api_ = OrtGetApiBase()->GetApi(ORT_API_VERSION);
        if (api_ == nullptr) {
            throw std::runtime_error("failed to obtain ONNX Runtime API");
        }

        if (config_.model_path.empty()) {
            throw std::runtime_error("onnxruntime detector requires model_path");
        }
        if (config_.imgsz <= 0) {
            throw std::runtime_error("onnxruntime detector imgsz must be positive");
        }
        if (!std::filesystem::exists(config_.model_path)) {
            throw std::runtime_error("onnxruntime model not found: " + model_path_);
        }

        try {
            throw_ort_error(api_, api_->CreateEnv(ORT_LOGGING_LEVEL_WARNING, "waterbag_onnxruntime", &env_), "CreateEnv");
            throw_ort_error(api_, api_->CreateSessionOptions(&session_options_), "CreateSessionOptions");
            throw_ort_error(api_, api_->SetSessionGraphOptimizationLevel(session_options_, ORT_ENABLE_ALL), "SetSessionGraphOptimizationLevel");
            throw_ort_error(api_, api_->SetIntraOpNumThreads(session_options_, 1), "SetIntraOpNumThreads");

            if (config_.use_cuda) {
                append_cuda_provider();
            }

            throw_ort_error(api_, api_->CreateSession(env_, model_path_.c_str(), session_options_, &session_), "CreateSession");
            throw_ort_error(api_, api_->GetAllocatorWithDefaultOptions(&allocator_), "GetAllocatorWithDefaultOptions");
            load_io_names();
        } catch (...) {
            shutdown();
            throw;
        }
    }

    void append_cuda_provider() {
        OrtCUDAProviderOptionsV2* raw_cuda_options = nullptr;
        throw_ort_error(api_, api_->CreateCUDAProviderOptions(&raw_cuda_options), "CreateCUDAProviderOptions");
        try {
            const std::string device_id = std::to_string(config_.cuda_device_id);
            const char* keys[] = {"device_id", "arena_extend_strategy", "do_copy_in_default_stream", "cudnn_conv_use_max_workspace", "cudnn_conv_algo_search", "use_tf32"};
            const char* values[] = {device_id.c_str(), "kSameAsRequested", "1", "1", "DEFAULT", "1"};
            throw_ort_error(api_, api_->UpdateCUDAProviderOptions(raw_cuda_options, keys, values, 6), "UpdateCUDAProviderOptions");
            throw_ort_error(api_, api_->SessionOptionsAppendExecutionProvider_CUDA_V2(session_options_, raw_cuda_options), "SessionOptionsAppendExecutionProvider_CUDA_V2");
        } catch (...) {
            api_->ReleaseCUDAProviderOptions(raw_cuda_options);
            throw;
        }

        api_->ReleaseCUDAProviderOptions(raw_cuda_options);
    }

    void load_io_names() {
        size_t input_count = 0;
        throw_ort_error(api_, api_->SessionGetInputCount(session_, &input_count), "SessionGetInputCount");
        if (input_count == 0) {
            throw std::runtime_error("onnxruntime detector model has no inputs");
        }

        size_t output_count = 0;
        throw_ort_error(api_, api_->SessionGetOutputCount(session_, &output_count), "SessionGetOutputCount");
        if (output_count == 0) {
            throw std::runtime_error("onnxruntime detector model has no outputs");
        }

        char* raw_input_name = nullptr;
        throw_ort_error(api_, api_->SessionGetInputName(session_, 0, allocator_, &raw_input_name), "SessionGetInputName");
        input_name_ = raw_input_name ? raw_input_name : "images";
        if (raw_input_name != nullptr) {
            allocator_->Free(allocator_, raw_input_name);
        }

        char* raw_output_name = nullptr;
        throw_ort_error(api_, api_->SessionGetOutputName(session_, 0, allocator_, &raw_output_name), "SessionGetOutputName");
        output_name_ = raw_output_name ? raw_output_name : "output0";
        if (raw_output_name != nullptr) {
            allocator_->Free(allocator_, raw_output_name);
        }
    }

    void shutdown() {
        if (api_ == nullptr) {
            return;
        }
        if (session_ != nullptr) {
            api_->ReleaseSession(session_);
            session_ = nullptr;
        }
        if (session_options_ != nullptr) {
            api_->ReleaseSessionOptions(session_options_);
            session_options_ = nullptr;
        }
        if (env_ != nullptr) {
            api_->ReleaseEnv(env_);
            env_ = nullptr;
        }
    }

    ModelConfig config_;
    std::string backend_name_;
    std::string model_path_;
    const OrtApi* api_ = nullptr;
    OrtEnv* env_ = nullptr;
    OrtSessionOptions* session_options_ = nullptr;
    OrtSession* session_ = nullptr;
    OrtAllocator* allocator_ = nullptr;
    std::string input_name_ = "images";
    std::string output_name_ = "output0";
};

#endif

}  // namespace

MockDetector::MockDetector(std::string backend) : backend_(std::move(backend)) {}

std::string MockDetector::backend_name() const {
    return backend_;
}

PerceptionResult MockDetector::detect(const FramePacket& packet, DetectionStage stage) {
    const auto started = Clock::now();
    const std::string name = lower_copy(packet.source_name);

    PerceptionResult result;
    result.stage_name = to_string(stage);
    result.detector_backend = backend_;
    result.triggered = true;

    if (stage == DetectionStage::Presence && !contains_any(name, {"empty", "no_bag", "nobag", "background"})) {
        result.boxes.push_back(DetectionBox{80, 60, 560, 420, "waterbag", 0.96});
    }
    if (stage == DetectionStage::Stage1 && contains_any(name, {"defect", "dirty", "broken"})) {
        result.boxes.push_back(sample_box(0.88));
    }
    if (stage == DetectionStage::Stage2 && contains_any(name, {"micro", "pin", "small", "patch"})) {
        result.boxes.push_back(sample_box(0.74));
    }

    result.inference_ms = elapsed_ms(started);
    return result;
}

std::shared_ptr<IDetector> make_detector(const ModelConfig& config, std::string mock_backend) {
    const std::string backend = lower_copy(config.backend);
    if (backend.empty() || backend == "mock") {
        return std::make_shared<MockDetector>(std::move(mock_backend));
    }

    const bool wants_onnxruntime =
        backend.rfind("onnxruntime", 0) == 0 ||
        backend.rfind("ort", 0) == 0;
    if (wants_onnxruntime) {
#if defined(WATERBAG_ENABLE_ONNXRUNTIME)
        const bool use_cuda = config.use_cuda || backend.find("cuda") != std::string::npos || backend.find("gpu") != std::string::npos;
        ModelConfig resolved = config;
        resolved.use_cuda = use_cuda;
        const std::string resolved_backend = use_cuda ? "onnxruntime_cuda" : "onnxruntime_cpu";
        return std::make_shared<OnnxRuntimeDetector>(std::move(resolved), resolved_backend);
#else
        throw std::runtime_error("onnxruntime backend requested, but WATERBAG_ENABLE_ONNXRUNTIME is OFF");
#endif
    }

    throw std::runtime_error("unsupported detector backend: " + config.backend);
}

}  // namespace waterbag
