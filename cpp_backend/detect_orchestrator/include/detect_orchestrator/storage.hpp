#pragma once

#include <filesystem>
#include <fstream>
#include <mutex>
#include <string>

#include "detect_orchestrator/schemas.hpp"

namespace waterbag {

class JsonlResultRepository {
public:
    explicit JsonlResultRepository(std::filesystem::path path);

    void save(const InspectionResult& result);
    const std::filesystem::path& path() const;

private:
    std::filesystem::path path_;
    std::mutex mutex_;
};

std::string inspection_result_to_json(const InspectionResult& result);

}  // namespace waterbag
