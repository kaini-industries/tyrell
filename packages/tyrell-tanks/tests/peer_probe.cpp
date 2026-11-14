// Headless, real-network acceptance peer. No graphics or device access.
#include "network.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <csignal>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>

namespace {
volatile std::sig_atomic_t stopped = 0;
void stop(int) { stopped = 1; }
struct Options {
    bool host = false, autoplay = false, require_proof = false, stop_on_proof = false;
    std::string address, output = "-";
    int port = 28770;
    double seconds = 60;
};
double number(const std::string& text, double low, double high) {
    std::size_t end = 0; const auto value = std::stod(text, &end);
    if (end != text.size() || !std::isfinite(value) || value < low || value > high)
        throw std::runtime_error("Numeric argument out of range: " + text);
    return value;
}
Options parse(int argc, char** argv) {
    Options result;
    for (int i = 1; i < argc; ++i) {
        const std::string key = argv[i];
        auto value = [&] { if (++i >= argc) throw std::runtime_error("Missing value for " + key); return std::string(argv[i]); };
        if (key == "--host") result.host = true;
        else if (key == "--join") result.address = value();
        else if (key == "--port") {
            const double parsed = number(value(), 1, 65535);
            if (std::floor(parsed) != parsed) throw std::runtime_error("Port must be an integer");
            result.port = static_cast<int>(parsed);
        } else if (key == "--seconds") result.seconds = number(value(), 1, 120);
        else if (key == "--report") result.output = value();
        else if (key == "--autoplay") result.autoplay = true;
        else if (key == "--require-proof") result.require_proof = true;
        else if (key == "--stop-on-proof") result.stop_on_proof = true;
        else if (key == "--help") {
            std::cout << "Usage: tanks-peer-probe (--host|--join HOST) [--port 28770] [--seconds 60]\n"
                "  [--report report.json|-] [--autoplay] [--require-proof] [--stop-on-proof]\n"
                "Host proof requires a remote player joining, moving >=0.25 units, and firing.\n"
                "Client proof requires handshake plus authoritative local movement and firing.\n";
            std::exit(0);
        } else throw std::runtime_error("Unknown option: " + key);
    }
    if (result.host == !result.address.empty()) throw std::runtime_error("Choose exactly one of --host or --join HOST");
    return result;
}
std::string quoted(const std::string& value) {
    std::ostringstream out; out << '"';
    for (const unsigned char character : value) {
        if (character == '"' || character == '\\') out << '\\' << character;
        else if (character < 32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(character) << std::dec;
        else out << character;
    }
    out << '"'; return out.str();
}
int run(const Options& options) {
    using Clock = std::chrono::steady_clock;
    tanks::NetworkGame game;
    const bool started_session = options.host ? game.host(static_cast<std::uint16_t>(options.port))
                                             : game.join(options.address, static_cast<std::uint16_t>(options.port));
    if (!started_session) throw std::runtime_error(game.status());
    std::signal(SIGINT, stop); std::signal(SIGTERM, stop);
    std::array<bool, tanks::kMaxPlayers> observed{}, fired{};
    std::array<tanks::Vec2, tanks::kMaxPlayers> initial{};
    std::array<float, tanks::kMaxPlayers> movement{};
    int peak_players = 0, remote_slots = 0, peak_remote_shells = 0;
    bool ever_connected = false, proof = false, world_valid = true;
    double first_join = -1, first_motion = -1, first_fire = -1;
    const auto started = Clock::now(); auto previous = started;
    double elapsed = 0;
    std::cerr << (options.host ? "Hosting" : "Joining") << " UDP " << options.port << "; waiting for live multiplayer evidence\n";
    while (!stopped) {
        const auto now = Clock::now(); elapsed = std::chrono::duration<double>(now - started).count();
        if (elapsed >= options.seconds) break;
        tanks::Input input;
        if (options.autoplay) input = {0.7f, 0.3f, 0.5f, true};
        game.update(std::chrono::duration<double>(now - previous).count(), input); previous = now;
        ever_connected = ever_connected || game.connected();
        const auto& world = game.world(); world_valid = world_valid && tanks::valid(world);
        int players = 0, shells = 0;
        bool moved_remote = false, fired_remote = false;
        for (int player = 0; player < tanks::kMaxPlayers; ++player) {
            const auto& tank = world.tanks[player];
            if (!tank.active) continue;
            ++players;
            const bool participant = options.host ? player != 0 : player == game.local_player();
            if (!participant) continue;
            if (!observed[player]) {
                observed[player] = true; initial[player] = tank.position; remote_slots |= 1 << player;
                if (first_join < 0) first_join = elapsed;
            }
            movement[player] = std::max(movement[player], std::hypot(tank.position.x - initial[player].x, tank.position.z - initial[player].z));
            fired[player] = fired[player] || tank.cooldown > 0.1f;
        }
        for (int player = 0; player < tanks::kMaxPlayers; ++player) {
            moved_remote = moved_remote || movement[player] >= 0.25f;
            fired_remote = fired_remote || fired[player];
        }
        for (const auto& shell : world.shells) {
            if (shell.active && shell.owner >= 0 && shell.owner < tanks::kMaxPlayers &&
                (options.host ? shell.owner != 0 : shell.owner == game.local_player())) ++shells;
        }
        peak_players = std::max(peak_players, players); peak_remote_shells = std::max(peak_remote_shells, shells);
        if (moved_remote && first_motion < 0) first_motion = elapsed;
        if (fired_remote && first_fire < 0) first_fire = elapsed;
        proof = first_join >= 0 && moved_remote && fired_remote && ever_connected && world_valid;
        if (proof && options.stop_on_proof) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    std::ostringstream report; report << std::fixed << std::setprecision(6);
    report << "{\n  \"mode\":" << quoted(options.host ? "host" : "client")
           << ",\n  \"port\":" << options.port << ",\n  \"elapsed_seconds\":" << elapsed
           << ",\n  \"peak_players\":" << peak_players << ",\n  \"participant_slot_mask\":" << remote_slots
           << ",\n  \"peak_participant_shells\":" << peak_remote_shells
           << ",\n  \"ever_connected\":" << (ever_connected ? "true" : "false")
           << ",\n  \"world_valid\":" << (world_valid ? "true" : "false")
           << ",\n  \"first_join_seconds\":" << first_join << ",\n  \"first_movement_seconds\":" << first_motion
           << ",\n  \"first_fire_seconds\":" << first_fire << ",\n  \"final_tick\":" << game.world().tick
           << ",\n  \"final_status\":" << quoted(game.status())
           << ",\n  \"proof_passed\":" << (proof ? "true" : "false") << ",\n  \"max_movement_by_slot\":[";
    for (int player = 0; player < tanks::kMaxPlayers; ++player) report << (player ? "," : "") << movement[player];
    report << "],\n  \"fired_by_slot\":[";
    for (int player = 0; player < tanks::kMaxPlayers; ++player) report << (player ? "," : "") << (fired[player] ? "true" : "false");
    report << "]\n}\n";
    if (options.output == "-") std::cout << report.str();
    else {
        std::ofstream output(options.output); if (!output) throw std::runtime_error("Cannot open report output");
        output << report.str(); if (!output) throw std::runtime_error("Cannot write report output");
    }
    if (options.require_proof && !proof) { std::cerr << "Multiplayer proof was incomplete\n"; return 2; }
    return world_valid ? 0 : 3;
}
} // namespace
int main(int argc, char** argv) {
    try { return run(parse(argc, argv)); }
    catch (const std::exception& error) { std::cerr << "tanks-peer-probe: " << error.what() << '\n'; return 1; }
}
