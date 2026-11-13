#pragma once

#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace tyrell {

// Alternate roots make offline fixtures possible without touching the device.
struct StatusPaths {
    std::filesystem::path proc_root = "/proc";
    std::filesystem::path sys_root = "/sys";
};

struct NetworkInterface {
    std::string name;
    std::string state;
    std::optional<bool> carrier;
    bool wireless = false;
};

struct BatteryStatus {
    std::string name;
    std::string status;
    std::optional<int> capacity_percent;
};

struct StatusSnapshot {
    std::optional<double> uptime_seconds;
    std::optional<std::uint64_t> memory_total_kib;
    std::optional<std::uint64_t> memory_available_kib;
    std::vector<NetworkInterface> network;
    std::vector<BatteryStatus> batteries;
    std::vector<std::string> warnings;
};

// Read only kernel-exported procfs/sysfs files. No commands, privileges or
// sockets are needed. Missing hardware and malformed values remain unknown.
StatusSnapshot collect_status(const StatusPaths& paths = {});

std::string format_uptime(const StatusSnapshot& snapshot);
std::string format_memory(const StatusSnapshot& snapshot);
std::string format_network(const StatusSnapshot& snapshot);
std::string format_battery(const StatusSnapshot& snapshot);

std::string status_to_json(const StatusSnapshot& snapshot,
                           const std::string& version,
                           const std::string& revision);

}  // namespace tyrell
