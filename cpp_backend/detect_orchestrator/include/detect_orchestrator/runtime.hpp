#pragma once

#include <condition_variable>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "detect_orchestrator/bag_runtime.hpp"
#include "detect_orchestrator/pipeline.hpp"

namespace waterbag {

class RealtimeRuntime {
public:
    using Listener = std::function<void(const InspectionResult&)>;

    RealtimeRuntime(RuntimeConfig config, std::shared_ptr<InspectionPipeline> pipeline);
    ~RealtimeRuntime();

    RealtimeRuntime(const RealtimeRuntime&) = delete;
    RealtimeRuntime& operator=(const RealtimeRuntime&) = delete;

    void start();
    void stop();
    void submit_path(int camera_id, const std::filesystem::path& path);
    void submit_defect_packet(FramePacket packet);
    void add_listener(Listener listener);
    bool running() const;

private:
    struct DefectWorkerShard {
        std::condition_variable cv;
        std::deque<FramePacket> queue;
    };

    void poll_loop();
    void worker_loop();
    void defect_worker_loop(std::size_t worker_index);
    void sorter_loop();
    std::vector<FramePacket> handle_station_capture(const InspectionResult& station_result);
    void handle_defect_result(InspectionResult result);
    void collect_and_publish_pending_results();
    void publish_sorted_results(std::vector<InspectionResult> results);
    void enqueue_sort_result(InspectionResult result);
    bool wait_until_ready(const std::filesystem::path& path) const;
    const CameraConfig* find_camera(int camera_id) const;
    bool should_accept_extension(const std::filesystem::path& path) const;
    std::size_t defect_worker_index_for_bag(const std::string& bag_id) const;
    void publish(const InspectionResult& result);

    RuntimeConfig config_;
    std::shared_ptr<InspectionPipeline> pipeline_;

    mutable std::mutex mutex_;
    std::mutex flow_mutex_;
    std::mutex sort_mutex_;
    std::condition_variable cv_;
    std::condition_variable sort_cv_;
    std::deque<FramePacket> queue_;
    std::deque<InspectionResult> sort_queue_;
    std::vector<std::unique_ptr<DefectWorkerShard>> defect_workers_;
    std::vector<Listener> listeners_;
    std::unordered_set<std::string> seen_paths_;
    std::unordered_map<int, Clock::time_point> last_processed_;
    bool stop_requested_ = false;
    bool sort_stop_requested_ = false;
    bool running_ = false;
    std::thread poll_thread_;
    std::thread worker_thread_;
    std::thread sorter_thread_;
    std::vector<std::thread> defect_threads_;
    BagCaptureAssembler capture_assembler_;
    SortReorderBuffer sort_reorder_;
};

}  // namespace waterbag
