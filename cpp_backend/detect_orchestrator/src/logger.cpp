#include "detect_orchestrator/logger.hpp"

#include <chrono>
#include <iostream>

#include "detect_orchestrator/schemas.hpp"

namespace waterbag {

Logger& Logger::instance() {
    static Logger logger;
    return logger;
}

void Logger::configure(const LoggerConfig& config) {
    std::lock_guard<std::mutex> lock(mutex_);
    config_ = config;
    if (!config_.file_path.empty()) {
        if (!config_.file_path.parent_path().empty()) {
            std::filesystem::create_directories(config_.file_path.parent_path());
        }
        file_.open(config_.file_path, std::ios::app);
    }
}

void Logger::debug(const std::string& message) {
    write(LogLevel::Debug, message);
}

void Logger::info(const std::string& message) {
    write(LogLevel::Info, message);
}

void Logger::warn(const std::string& message) {
    write(LogLevel::Warn, message);
}

void Logger::error(const std::string& message) {
    write(LogLevel::Error, message);
}

void Logger::write(LogLevel level, const std::string& message) {
    if (!should_write(level)) {
        return;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    const auto line = system_time_to_iso(SystemClock::now()) + " [" + to_string(level) + "] " + message;
    if (config_.console) {
        std::ostream& out = level == LogLevel::Error ? std::cerr : std::cout;
        out << line << '\n';
    }
    if (file_.is_open()) {
        file_ << line << '\n';
        file_.flush();
    }
}

bool Logger::should_write(LogLevel level) const {
    return static_cast<int>(level) >= static_cast<int>(config_.level);
}

LogLevel parse_log_level(const std::string& value) {
    if (value == "debug" || value == "DEBUG") {
        return LogLevel::Debug;
    }
    if (value == "warn" || value == "warning" || value == "WARN" || value == "WARNING") {
        return LogLevel::Warn;
    }
    if (value == "error" || value == "ERROR") {
        return LogLevel::Error;
    }
    return LogLevel::Info;
}

std::string to_string(LogLevel level) {
    switch (level) {
        case LogLevel::Debug:
            return "DEBUG";
        case LogLevel::Info:
            return "INFO";
        case LogLevel::Warn:
            return "WARN";
        case LogLevel::Error:
            return "ERROR";
    }
    return "INFO";
}

}  // namespace waterbag
