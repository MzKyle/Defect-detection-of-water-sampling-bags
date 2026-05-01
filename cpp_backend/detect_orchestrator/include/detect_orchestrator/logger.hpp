#pragma once

#include <filesystem>
#include <fstream>
#include <mutex>
#include <string>

namespace waterbag {

enum class LogLevel {
    Debug = 0,
    Info = 1,
    Warn = 2,
    Error = 3
};

struct LoggerConfig {
    LogLevel level = LogLevel::Info;
    bool console = true;
    std::filesystem::path file_path;
};

class Logger {
public:
    static Logger& instance();

    void configure(const LoggerConfig& config);
    void debug(const std::string& message);
    void info(const std::string& message);
    void warn(const std::string& message);
    void error(const std::string& message);

private:
    Logger() = default;

    void write(LogLevel level, const std::string& message);
    bool should_write(LogLevel level) const;

    mutable std::mutex mutex_;
    LoggerConfig config_;
    std::ofstream file_;
};

LogLevel parse_log_level(const std::string& value);
std::string to_string(LogLevel level);

}  // namespace waterbag
