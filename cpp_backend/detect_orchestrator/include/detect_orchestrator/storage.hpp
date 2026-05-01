#pragma once

#include <atomic>
#include <condition_variable>
#include <cstddef>
#include <deque>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <string>
#include <thread>

#include "detect_orchestrator/schemas.hpp"

namespace waterbag {

class JsonlResultRepository {
public:
    explicit JsonlResultRepository(
        std::filesystem::path path,
        bool async_writes = false,
        std::size_t queue_capacity = 256,
        bool drop_when_full = true);
    ~JsonlResultRepository();

    void save(const InspectionResult& result);
    void close();
    const std::filesystem::path& path() const;
    std::size_t dropped_results() const;

private:
    void append_line(const std::string& line);
    void writer_loop();

    std::filesystem::path path_;
    bool async_writes_ = false;
    std::size_t queue_capacity_ = 256;
    bool drop_when_full_ = true;
    bool stop_requested_ = false;
    std::atomic_size_t dropped_results_{0};
    std::deque<std::string> queue_;
    std::mutex queue_mutex_;
    std::mutex file_mutex_;
    std::condition_variable cv_;
    std::thread writer_thread_;
};

std::string inspection_result_to_json(const InspectionResult& result);

}  // namespace waterbag
