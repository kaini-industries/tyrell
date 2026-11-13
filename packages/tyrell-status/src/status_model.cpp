#include "status_model.h"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <limits>
#include <locale>
#include <sstream>
#include <system_error>
#include <utility>

namespace tyrell {
namespace {

constexpr std::size_t kMaxReadBytes = 65536;

std::string trim(const std::string& value) {
    const auto start = value.find_first_not_of(" \t\r\n");
    if (start == std::string::npos) return {};
    const auto end = value.find_last_not_of(" \t\r\n");
    return value.substr(start, end - start + 1);
}

std::optional<std::string> read_text(const std::filesystem::path& path) {
    std::ifstream input(path);
    if (!input) return std::nullopt;
    std::string value;
    char buffer[4096];
    while (input.read(buffer, sizeof(buffer)) || input.gcount() > 0) {
        value.append(buffer, static_cast<std::size_t>(input.gcount()));
        if (value.size() > kMaxReadBytes) return std::nullopt;
    }
    if (input.bad()) return std::nullopt;
    return trim(value);
}

std::optional<std::uint64_t> unsigned_value(const std::string& raw) {
    const auto text = trim(raw);
    if (text.empty()) return std::nullopt;
    std::uint64_t result = 0;
    const auto parsed = std::from_chars(text.data(), text.data() + text.size(), result);
    if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size()) {
        return std::nullopt;
    }
    return result;
}

std::vector<std::filesystem::path> entries(const std::filesystem::path& path) {
    std::vector<std::filesystem::path> result;
    std::error_code error;
    std::filesystem::directory_iterator iterator(path, error);
    const std::filesystem::directory_iterator end;
    while (!error && iterator != end) {
        result.push_back(iterator->path());
        iterator.increment(error);
    }
    std::sort(result.begin(), result.end());
    return result;
}

bool valid_network_state(const std::string& state) {
    return state == "unknown" || state == "notpresent" || state == "down" ||
           state == "lowerlayerdown" || state == "testing" ||
           state == "dormant" || state == "up";
}

bool is_connected(const NetworkInterface& interface) {
    if (interface.carrier && !*interface.carrier) return false;
    return interface.state == "up" ||
           (interface.state == "unknown" && interface.carrier.value_or(false));
}

std::string json_string(const std::string& value) {
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << '"';
    for (const unsigned char c : value) {
        switch (c) {
            case '"': out << "\\\""; break;
            case '\\': out << "\\\\"; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:
                if (c < 0x20) {
                    out << "\\u" << std::hex << std::setfill('0') << std::setw(4)
                        << static_cast<unsigned int>(c) << std::dec;
                } else {
                    out << static_cast<char>(c);
                }
        }
    }
    out << '"';
    return out.str();
}

template <typename T>
void json_number(std::ostream& out, const std::optional<T>& value) {
    if (value) out << *value;
    else out << "null";
}

}  // namespace

StatusSnapshot collect_status(const StatusPaths& paths) {
    StatusSnapshot snapshot;

    if (const auto raw = read_text(paths.proc_root / "uptime")) {
        std::istringstream values(*raw);
        values.imbue(std::locale::classic());
        std::string token;
        values >> token;
        std::istringstream uptime(token);
        uptime.imbue(std::locale::classic());
        double seconds = 0;
        if (uptime >> seconds && uptime.eof() && std::isfinite(seconds) && seconds >= 0 &&
            static_cast<long double>(seconds) <=
                static_cast<long double>(std::numeric_limits<std::uint64_t>::max())) {
            snapshot.uptime_seconds = seconds;
        } else {
            snapshot.warnings.push_back("Invalid uptime value");
        }
    } else {
        snapshot.warnings.push_back("Uptime unavailable");
    }

    if (const auto raw = read_text(paths.proc_root / "meminfo")) {
        std::istringstream lines(*raw);
        std::string line;
        while (std::getline(lines, line)) {
            std::istringstream fields(line);
            std::string key, amount, unit, extra;
            if (!(fields >> key >> amount >> unit) || (fields >> extra) || unit != "kB") {
                continue;
            }
            const auto number = unsigned_value(amount);
            if (key == "MemTotal:" && number && *number > 0) {
                snapshot.memory_total_kib = number;
            } else if (key == "MemAvailable:" && number) {
                snapshot.memory_available_kib = number;
            }
        }
        if (snapshot.memory_total_kib && snapshot.memory_available_kib &&
            *snapshot.memory_available_kib > *snapshot.memory_total_kib) {
            snapshot.memory_available_kib.reset();
        }
        if (!snapshot.memory_total_kib || !snapshot.memory_available_kib) {
            snapshot.warnings.push_back("Memory counters incomplete or invalid");
        }
    } else {
        snapshot.warnings.push_back("Memory counters unavailable");
    }

    for (const auto& path : entries(paths.sys_root / "class/net")) {
        const auto type = read_text(path / "type");
        // Linux ARPHRD_LOOPBACK. Interface names vary across boards and hosts.
        if (type && unsigned_value(*type) == 772) continue;
        NetworkInterface interface;
        interface.name = path.filename().string();
        const auto state = read_text(path / "operstate");
        interface.state = state && valid_network_state(*state) ? *state : "unknown";
        if (const auto carrier = read_text(path / "carrier")) {
            if (*carrier == "1") interface.carrier = true;
            else if (*carrier == "0") interface.carrier = false;
        }
        std::error_code error;
        interface.wireless = std::filesystem::is_directory(path / "wireless", error);
        snapshot.network.push_back(std::move(interface));
    }

    for (const auto& path : entries(paths.sys_root / "class/power_supply")) {
        const auto type = read_text(path / "type");
        if (!type || *type != "Battery") continue;
        BatteryStatus battery;
        battery.name = path.filename().string();
        battery.status = read_text(path / "status").value_or("Unknown");
        if (battery.status.empty()) battery.status = "Unknown";
        if (const auto capacity = read_text(path / "capacity")) {
            if (const auto number = unsigned_value(*capacity); number && *number <= 100) {
                battery.capacity_percent = static_cast<int>(*number);
            }
        }
        snapshot.batteries.push_back(std::move(battery));
    }

    return snapshot;
}

std::string format_uptime(const StatusSnapshot& snapshot) {
    if (!snapshot.uptime_seconds || !std::isfinite(*snapshot.uptime_seconds) ||
        *snapshot.uptime_seconds < 0 ||
        static_cast<long double>(*snapshot.uptime_seconds) >
            static_cast<long double>(std::numeric_limits<std::uint64_t>::max())) {
        return "Unavailable";
    }
    const auto seconds = static_cast<std::uint64_t>(*snapshot.uptime_seconds);
    const auto days = seconds / 86400;
    const auto hours = seconds / 3600 % 24;
    const auto minutes = seconds / 60 % 60;
    std::ostringstream out;
    if (days) out << days << "d ";
    out << hours << "h " << minutes << "m";
    return out.str();
}

std::string format_memory(const StatusSnapshot& snapshot) {
    if (!snapshot.memory_total_kib || !snapshot.memory_available_kib ||
        *snapshot.memory_total_kib == 0 ||
        *snapshot.memory_available_kib > *snapshot.memory_total_kib) return "Unavailable";
    const auto used = (*snapshot.memory_total_kib - *snapshot.memory_available_kib) / 1024;
    return std::to_string(used) + " / " +
           std::to_string(*snapshot.memory_total_kib / 1024) + " MiB";
}

std::string format_network(const StatusSnapshot& snapshot) {
    std::string first;
    std::size_t count = 0;
    for (const auto& interface : snapshot.network) {
        if (!is_connected(interface)) continue;
        if (first.empty()) first = interface.name;
        ++count;
    }
    if (!count) return snapshot.network.empty() ? "Unavailable" : "Offline";
    return first + " up" + (count > 1 ? " +" + std::to_string(count - 1) : "");
}

std::string format_battery(const StatusSnapshot& snapshot) {
    if (snapshot.batteries.empty()) return "Unavailable";
    const auto& battery = snapshot.batteries.front();
    std::string value = battery.capacity_percent
        ? std::to_string(*battery.capacity_percent) + "%" : "?%";
    if (battery.status == "Charging") value += " charging";
    else if (battery.status == "Discharging") value += " battery";
    else if (battery.status == "Full") value += " full";
    else if (battery.status == "Not charging") value += " plugged in";
    return value;
}

std::string status_to_json(const StatusSnapshot& snapshot,
                           const std::string& version,
                           const std::string& revision) {
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << std::setprecision(17);
    out << "{\"version\":" << json_string(version)
        << ",\"revision\":" << json_string(revision) << ",\"uptime_seconds\":";
    if (snapshot.uptime_seconds && std::isfinite(*snapshot.uptime_seconds)) {
        out << *snapshot.uptime_seconds;
    } else out << "null";
    out << ",\"memory_total_kib\":";
    json_number(out, snapshot.memory_total_kib);
    out << ",\"memory_available_kib\":";
    json_number(out, snapshot.memory_available_kib);
    out << ",\"network\":[";
    for (std::size_t i = 0; i < snapshot.network.size(); ++i) {
        if (i) out << ',';
        const auto& interface = snapshot.network[i];
        out << "{\"name\":" << json_string(interface.name)
            << ",\"state\":" << json_string(interface.state) << ",\"carrier\":";
        if (interface.carrier) out << (*interface.carrier ? "true" : "false");
        else out << "null";
        out << ",\"wireless\":" << (interface.wireless ? "true" : "false") << '}';
    }
    out << "],\"batteries\":[";
    for (std::size_t i = 0; i < snapshot.batteries.size(); ++i) {
        if (i) out << ',';
        const auto& battery = snapshot.batteries[i];
        out << "{\"name\":" << json_string(battery.name)
            << ",\"status\":" << json_string(battery.status) << ",\"capacity_percent\":";
        json_number(out, battery.capacity_percent);
        out << '}';
    }
    out << "],\"warnings\":[";
    for (std::size_t i = 0; i < snapshot.warnings.size(); ++i) {
        if (i) out << ',';
        out << json_string(snapshot.warnings[i]);
    }
    out << "]}";
    return out.str();
}

}  // namespace tyrell
