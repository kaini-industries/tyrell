#include "status_model.h"

#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>

namespace {

void require(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}

struct Fixture {
    std::filesystem::path root = std::filesystem::temp_directory_path() /
        ("tyrell-status-test-" + std::to_string(
            std::chrono::steady_clock::now().time_since_epoch().count()));

    Fixture() { std::filesystem::create_directories(root); }
    ~Fixture() {
        std::error_code error;
        std::filesystem::remove_all(root, error);
    }

    tyrell::StatusPaths paths() const { return {root / "proc", root / "sys"}; }

    void put(const std::string& relative, const std::string& value) const {
        const auto path = root / relative;
        std::filesystem::create_directories(path.parent_path());
        std::ofstream output(path);
        output << value;
        require(static_cast<bool>(output), "Could not write fixture " + relative);
    }
};

void test_complete_snapshot() {
    Fixture fixture;
    fixture.put("proc/uptime", "90061.50 180123.00\n");
    fixture.put("proc/meminfo", "MemTotal: 360448 kB\nMemAvailable: 131072 kB\n");
    fixture.put("sys/class/net/loop0/type", "772\n");
    fixture.put("sys/class/net/loop0/operstate", "unknown\n");
    fixture.put("sys/class/net/enp9/type", "1\n");
    fixture.put("sys/class/net/enp9/operstate", "down\n");
    fixture.put("sys/class/net/enp9/carrier", "0\n");
    fixture.put("sys/class/net/wlp5/type", "1\n");
    fixture.put("sys/class/net/wlp5/operstate", "up\n");
    fixture.put("sys/class/net/wlp5/carrier", "1\n");
    std::filesystem::create_directories(fixture.root / "sys/class/net/wlp5/wireless");
    fixture.put("sys/class/power_supply/gauge/type", "Battery\n");
    fixture.put("sys/class/power_supply/gauge/capacity", "73\n");
    fixture.put("sys/class/power_supply/gauge/status", "Charging\n");
    fixture.put("sys/class/power_supply/ac/type", "Mains\n");

    const auto snapshot = tyrell::collect_status(fixture.paths());
    require(snapshot.uptime_seconds == 90061.5, "Uptime not collected from fixture");
    require(snapshot.memory_total_kib == 360448, "Total memory wrong");
    require(snapshot.memory_available_kib == 131072, "Available memory wrong");
    require(snapshot.network.size() == 2, "Loopback must be excluded by type");
    require(snapshot.network[1].wireless, "Wireless interface not identified");
    require(snapshot.network[0].carrier == false, "False carrier was lost");
    require(snapshot.batteries.size() == 1, "Mains supply must not be a battery");
    require(snapshot.batteries[0].capacity_percent == 73, "Battery percentage wrong");
    require(snapshot.warnings.empty(), "Valid fixture has warnings");
    require(tyrell::format_uptime(snapshot) == "1d 1h 1m", "Uptime formatting wrong");
    require(tyrell::format_memory(snapshot) == "224 / 352 MiB", "Memory formatting wrong");
    require(tyrell::format_network(snapshot) == "wlp5 up", "Active interface wrong");
    require(tyrell::format_battery(snapshot) == "73% charging", "Battery formatting wrong");

    const auto json = tyrell::status_to_json(snapshot, "0.1.0", "fixture");
    require(json.find("\"uptime_seconds\":90061.5") != std::string::npos, "JSON uptime wrong");
    require(json.find("\"carrier\":false") != std::string::npos, "JSON carrier false lost");
    require(json.find("\"capacity_percent\":73") != std::string::npos, "JSON capacity wrong");
}

void test_missing_sources() {
    Fixture fixture;
    const auto snapshot = tyrell::collect_status(fixture.paths());
    require(!snapshot.uptime_seconds && !snapshot.memory_total_kib, "Missing values must be unknown");
    require(snapshot.network.empty() && snapshot.batteries.empty(), "Missing hardware invented");
    require(snapshot.warnings.size() == 2, "Missing procfs must be reported");
    require(tyrell::format_uptime(snapshot) == "Unavailable", "Missing uptime display wrong");
    require(tyrell::format_memory(snapshot) == "Unavailable", "Missing memory display wrong");
    require(tyrell::format_network(snapshot) == "Unavailable", "Missing network display wrong");
    require(tyrell::format_battery(snapshot) == "Unavailable", "Missing battery display wrong");
    const auto json = tyrell::status_to_json(snapshot, "v\"\\\n", "\t");
    require(json.find("\"uptime_seconds\":null") != std::string::npos, "Missing uptime JSON not null");
    require(json.find("v\\\"\\\\\\n") != std::string::npos, "JSON string not escaped");
    require(json.find('\n') == std::string::npos, "Raw newline in JSON");
}

void test_invalid_values() {
    Fixture fixture;
    fixture.put("proc/uptime", "-1 9\n");
    fixture.put("proc/meminfo", "MemTotal: 1024 kB\nMemAvailable: 2048 kB\n");
    fixture.put("sys/class/net/unusual/operstate", "garbage\n");
    fixture.put("sys/class/net/unusual/carrier", "8\n");
    fixture.put("sys/class/power_supply/bat/type", "Battery\n");
    fixture.put("sys/class/power_supply/bat/capacity", "101\n");
    auto snapshot = tyrell::collect_status(fixture.paths());
    require(!snapshot.uptime_seconds, "Negative uptime accepted");
    require(snapshot.memory_total_kib == 1024 && !snapshot.memory_available_kib,
            "Impossible memory counters accepted");
    require(snapshot.network[0].state == "unknown" && !snapshot.network[0].carrier,
            "Invalid interface state accepted");
    require(!snapshot.batteries[0].capacity_percent && snapshot.batteries[0].status == "Unknown",
            "Invalid/missing battery fields must remain unknown");
    require(tyrell::format_network(snapshot) == "Offline", "Unknown link must not imply connected");

    fixture.put("proc/uptime", "1e300 0\n");
    fixture.put("proc/meminfo", "MemTotal: 18446744073709551616 kB\nMemAvailable: nope kB\n");
    fixture.put("sys/class/power_supply/bat/capacity", "-5\n");
    snapshot = tyrell::collect_status(fixture.paths());
    require(!snapshot.uptime_seconds, "Unrepresentable uptime accepted");
    require(!snapshot.memory_total_kib && !snapshot.memory_available_kib,
            "Overflow/malformed memory accepted");
    require(!snapshot.batteries[0].capacity_percent, "Negative capacity accepted");

    fixture.put("proc/uptime", "nan 0\n");
    require(!tyrell::collect_status(fixture.paths()).uptime_seconds, "NaN uptime accepted");
    fixture.put("proc/uptime", "1oops 0\n");
    require(!tyrell::collect_status(fixture.paths()).uptime_seconds, "Partial uptime number accepted");
    snapshot.uptime_seconds = std::numeric_limits<double>::infinity();
    require(tyrell::format_uptime(snapshot) == "Unavailable", "Infinite uptime formatting unsafe");
    require(tyrell::status_to_json(snapshot, "v", "r").find("\"uptime_seconds\":null") !=
            std::string::npos, "Nonfinite number emitted into JSON");
}

void test_zero_and_multiple_links() {
    Fixture fixture;
    fixture.put("proc/uptime", "0.00 0.00\n");
    fixture.put("proc/meminfo", "MemTotal: 1024 kB\nMemAvailable: 0 kB\n");
    fixture.put("sys/class/net/a/operstate", "up\n");
    fixture.put("sys/class/net/a/carrier", "0\n");
    fixture.put("sys/class/net/b/operstate", "unknown\n");
    fixture.put("sys/class/net/b/carrier", "1\n");
    fixture.put("sys/class/net/c/operstate", "up\n");
    fixture.put("sys/class/power_supply/bat/type", "Battery\n");
    fixture.put("sys/class/power_supply/bat/status", "Discharging\n");
    fixture.put("sys/class/power_supply/bat/capacity", "0\n");
    const auto snapshot = tyrell::collect_status(fixture.paths());
    require(snapshot.uptime_seconds == 0, "Zero uptime must be valid");
    require(snapshot.memory_available_kib == 0, "Zero available memory must be valid");
    require(snapshot.batteries[0].capacity_percent == 0, "Empty battery must be valid");
    require(tyrell::format_uptime(snapshot) == "0h 0m", "Zero uptime formatting wrong");
    require(tyrell::format_memory(snapshot) == "1 / 1 MiB", "Zero available memory formatting wrong");
    require(tyrell::format_network(snapshot) == "b up +1", "Carrier/conflicting link state mishandled");
    require(tyrell::format_battery(snapshot) == "0% battery", "Zero battery formatting wrong");
}

}  // namespace

int main() {
    try {
        test_complete_snapshot();
        test_missing_sources();
        test_invalid_values();
        test_zero_and_multiple_links();
        std::cout << "status_model: 4 fixture scenarios passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "status_model: " << error.what() << '\n';
        return 1;
    }
}
