#include "detect_orchestrator/runtime.hpp"

#include <algorithm>
#include <cctype>
#include <functional>

#include "detect_orchestrator/logger.hpp"

namespace waterbag {
namespace {

std::vector<int> camera_ids_from_config(const RuntimeConfig& config) {
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

}  // namespace

RealtimeRuntime::RealtimeRuntime(RuntimeConfig config, std::shared_ptr<InspectionPipeline> pipeline)
    : config_(std::move(config)),
      pipeline_(std::move(pipeline)),
      capture_assembler_(
          camera_ids_from_config(config_),
          config_.expected_burst_images_per_camera,
          config_.bag_capture_timeout),
      sort_reorder_(config_.sort_result_timeout) {}

RealtimeRuntime::~RealtimeRuntime() {
    stop();
}

void RealtimeRuntime::start() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (running_) {
            return;
        }
        stop_requested_ = false;
        running_ = true;
    }

    for (const auto& camera : config_.cameras) {
        std::filesystem::create_directories(camera.watch_dir);
    }

    const auto worker_count = std::max<std::size_t>(1, config_.defect_worker_count);
    defect_workers_.clear();
    defect_threads_.clear();
    defect_workers_.reserve(worker_count);
    defect_threads_.reserve(worker_count);
    for (std::size_t i = 0; i < worker_count; ++i) {
        defect_workers_.push_back(std::make_unique<DefectWorkerShard>());
    }

    poll_thread_ = std::thread(&RealtimeRuntime::poll_loop, this);
    worker_thread_ = std::thread(&RealtimeRuntime::worker_loop, this);
    for (std::size_t i = 0; i < worker_count; ++i) {
        defect_threads_.emplace_back(&RealtimeRuntime::defect_worker_loop, this, i);
    }
    Logger::instance().info("defect worker pool started, count=" + std::to_string(worker_count));
}

void RealtimeRuntime::stop() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!running_) {
            return;
        }
        stop_requested_ = true;
        running_ = false;
    }
    cv_.notify_all();
    for (auto& worker : defect_workers_) {
        worker->cv.notify_all();
    }
    if (poll_thread_.joinable()) {
        poll_thread_.join();
    }
    if (worker_thread_.joinable()) {
        worker_thread_.join();
    }
    for (auto& thread : defect_threads_) {
        if (thread.joinable()) {
            thread.join();
        }
    }
    defect_threads_.clear();
    defect_workers_.clear();
}

void RealtimeRuntime::submit_path(int camera_id, const std::filesystem::path& path) {
    const auto* camera = find_camera(camera_id);
    if (camera == nullptr) {
        return;
    }

    auto packet = make_frame_packet(*camera, path);
    packet.source = "runtime";

    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (queue_.size() >= config_.queue_capacity) {
            queue_.erase(queue_.begin());
        }
        queue_.push_back(std::move(packet));
    }
    cv_.notify_one();
}

void RealtimeRuntime::submit_defect_packet(FramePacket packet) {
    const auto worker_index = defect_worker_index_for_bag(packet.bag_id);
    DefectWorkerShard* worker = nullptr;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (defect_workers_.empty()) {
            return;
        }
        worker = defect_workers_[worker_index].get();
        if (worker->queue.size() >= config_.queue_capacity) {
            worker->queue.erase(worker->queue.begin());
        }
        packet.metadata["defect_worker_index"] = std::to_string(worker_index);
        worker->queue.push_back(std::move(packet));
    }
    worker->cv.notify_one();
}

void RealtimeRuntime::add_listener(Listener listener) {
    std::lock_guard<std::mutex> lock(mutex_);
    listeners_.push_back(std::move(listener));
}

bool RealtimeRuntime::running() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return running_;
}

void RealtimeRuntime::poll_loop() {
    while (true) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (stop_requested_) {
                break;
            }
        }

        for (const auto& camera : config_.cameras) {
            if (!std::filesystem::exists(camera.watch_dir)) {
                continue;
            }
            for (const auto& entry : std::filesystem::directory_iterator(camera.watch_dir)) {
                if (!entry.is_regular_file() || !should_accept_extension(entry.path())) {
                    continue;
                }
                const auto path_key = entry.path().string();
                bool is_new = false;
                {
                    std::lock_guard<std::mutex> lock(mutex_);
                    is_new = seen_paths_.insert(path_key).second;
                }
                if (is_new) {
                    submit_path(camera.id, entry.path());
                }
            }
        }
        std::this_thread::sleep_for(config_.poll_interval);
    }
}

void RealtimeRuntime::worker_loop() {
    while (true) {
        FramePacket packet;
        bool has_packet = false;
        {
            std::unique_lock<std::mutex> lock(mutex_);
            cv_.wait_for(lock, config_.poll_interval, [&] {
                return stop_requested_ || !queue_.empty();
            });
            if (stop_requested_ && queue_.empty()) {
                break;
            }
            if (!queue_.empty()) {
                packet = std::move(queue_.front());
                queue_.erase(queue_.begin());
                has_packet = true;
            }
        }

        if (!has_packet) {
            continue;
        }

        if (!wait_until_ready(packet.source_path)) {
            Logger::instance().warn("skipped unstable image: " + packet.source_path.string());
            continue;
        }
        packet.source_mtime = std::filesystem::last_write_time(packet.source_path);

        const auto now = Clock::now();
        {
            std::lock_guard<std::mutex> lock(mutex_);
            const auto found = last_processed_.find(packet.camera_id);
            if (found != last_processed_.end() && now - found->second < config_.cooldown) {
                continue;
            }
            last_processed_[packet.camera_id] = now;
        }

        const auto station_result = pipeline_->process_station_packet(packet);
        publish(station_result);
        if (station_result.decision_result.control_action == "defect_queued") {
            for (auto& ready_packet : handle_station_capture(station_result)) {
                submit_defect_packet(std::move(ready_packet));
            }
        }
        collect_and_publish_pending_results();
    }
}

void RealtimeRuntime::defect_worker_loop(std::size_t worker_index) {
    while (true) {
        FramePacket packet;
        bool has_packet = false;
        {
            std::unique_lock<std::mutex> lock(mutex_);
            auto& shard = *defect_workers_[worker_index];
            shard.cv.wait_for(lock, config_.poll_interval, [&] {
                return stop_requested_ || !shard.queue.empty();
            });
            if (stop_requested_ && shard.queue.empty()) {
                break;
            }
            if (!shard.queue.empty()) {
                packet = std::move(shard.queue.front());
                shard.queue.erase(shard.queue.begin());
                has_packet = true;
            }
        }

        if (has_packet) {
            auto result = pipeline_->process_defect_packet(packet);
            result.state_trace.push_back("defect_worker_index:" + std::to_string(worker_index));
            if (result.decision_result.finalized) {
                handle_defect_result(std::move(result));
            } else {
                publish(result);
            }
        }
        for (const auto& timeout_result : pipeline_->flush_timeouts()) {
            handle_defect_result(timeout_result);
        }
        collect_and_publish_pending_results();
    }
}

std::vector<FramePacket> RealtimeRuntime::handle_station_capture(const InspectionResult& station_result) {
    std::lock_guard<std::mutex> lock(flow_mutex_);
    sort_reorder_.register_bag(station_result.frame_packet);
    return capture_assembler_.register_station_capture(station_result);
}

void RealtimeRuntime::handle_defect_result(InspectionResult result) {
    std::vector<InspectionResult> ready;
    {
        std::lock_guard<std::mutex> lock(flow_mutex_);
        sort_reorder_.store_result(std::move(result));
        ready = sort_reorder_.collect_ready();
    }
    publish_sorted_results(std::move(ready));
}

void RealtimeRuntime::collect_and_publish_pending_results() {
    std::vector<InspectionResult> ready;
    {
        std::lock_guard<std::mutex> lock(flow_mutex_);
        for (auto& timeout_result : capture_assembler_.collect_timeouts()) {
            sort_reorder_.store_result(std::move(timeout_result));
        }
        ready = sort_reorder_.collect_ready();
    }
    publish_sorted_results(std::move(ready));
}

void RealtimeRuntime::publish_sorted_results(std::vector<InspectionResult> results) {
    for (auto& result : results) {
        publish(pipeline_->execute_sort_command(std::move(result)));
    }
}

std::size_t RealtimeRuntime::defect_worker_index_for_bag(const std::string& bag_id) const {
    const auto worker_count = std::max<std::size_t>(1, defect_workers_.size());
    return std::hash<std::string>{}(bag_id) % worker_count;
}

bool RealtimeRuntime::wait_until_ready(const std::filesystem::path& path) const {
    const auto deadline = Clock::now() + config_.file_ready_timeout;
    std::optional<std::uintmax_t> last_size;
    std::optional<std::filesystem::file_time_type> last_mtime;
    std::optional<Clock::time_point> stable_since;

    while (Clock::now() < deadline) {
        if (!std::filesystem::exists(path)) {
            std::this_thread::sleep_for(Milliseconds{50});
            continue;
        }

        const auto size = std::filesystem::file_size(path);
        const auto mtime = std::filesystem::last_write_time(path);
        if (last_size && last_mtime && *last_size == size && *last_mtime == mtime) {
            if (!stable_since) {
                stable_since = Clock::now();
            }
            if (Clock::now() - *stable_since >= config_.file_stable_for) {
                return true;
            }
        } else {
            stable_since.reset();
            last_size = size;
            last_mtime = mtime;
        }
        std::this_thread::sleep_for(Milliseconds{50});
    }
    return false;
}

const CameraConfig* RealtimeRuntime::find_camera(int camera_id) const {
    for (const auto& camera : config_.cameras) {
        if (camera.id == camera_id) {
            return &camera;
        }
    }
    return nullptr;
}

bool RealtimeRuntime::should_accept_extension(const std::filesystem::path& path) const {
    auto ext = path.extension().string();
    std::transform(ext.begin(), ext.end(), ext.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return ext == ".jpg" || ext == ".jpeg" || ext == ".png" || ext == ".bmp";
}

void RealtimeRuntime::publish(const InspectionResult& result) {
    std::vector<Listener> listeners;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        listeners = listeners_;
    }
    for (const auto& listener : listeners) {
        listener(result);
    }
}

}  // namespace waterbag
