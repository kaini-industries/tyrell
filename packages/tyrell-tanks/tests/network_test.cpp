#include "network.hpp"
#include <enet/enet.h>

#include <array>
#include <chrono>
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {
using tanks::Input;
using tanks::NetworkGame;
constexpr double kDt = 1.0 / 120.0;
void check(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}
float distance(tanks::Vec2 a, tanks::Vec2 b) { return std::hypot(a.x - b.x, a.z - b.z); }
void pause() { std::this_thread::sleep_for(std::chrono::milliseconds(1)); }
void advance(NetworkGame& host, const std::vector<NetworkGame*>& clients, int frames,
             const std::vector<Input>& inputs = {}) {
    for (int frame = 0; frame < frames; ++frame) {
        for (std::size_t i = 0; i < clients.size(); ++i) clients[i]->update(kDt, i < inputs.size() ? inputs[i] : Input{});
        host.update(kDt, {});
        for (auto* client : clients) check(tanks::valid(client->world()), "client world remains valid");
        check(tanks::valid(host.world()), "host world remains valid");
        pause(); // ENet uses actual sockets and its own real-time retransmission clock.
    }
}
std::uint16_t start_host(NetworkGame& host, int base) {
    for (int offset = 0; offset < 100; ++offset) {
        const auto port = static_cast<std::uint16_t>(base + offset);
        if (host.host(port)) return port;
    }
    throw std::runtime_error("No loopback test UDP port available");
}
void wait_join(NetworkGame& host, const std::vector<NetworkGame*>& clients) {
    for (int frame = 0; frame < 400; ++frame) {
        advance(host, clients, 1);
        bool joined = true;
        for (const auto* client : clients) joined = joined && client->connected();
        if (joined) { advance(host, clients, 12); return; }
    }
    std::string states;
    for (const auto* client : clients) states += " " + client->status();
    throw std::runtime_error("Loopback clients did not finish handshake:" + states);
}

struct RawEndpoint {
    ENetHost* host = nullptr;
    ENetPeer* peer = nullptr;
    bool disconnected = false;
    ~RawEndpoint() {
        if (peer) enet_peer_disconnect_now(peer, 0);
        if (host) { enet_host_flush(host); enet_host_destroy(host); }
    }
    void client(std::uint16_t port, std::uint32_t version = 1) {
        host = enet_host_create(nullptr, 1, 3, 0, 0); check(host != nullptr, "raw client socket");
        ENetAddress address{}; address.port = port;
        check(enet_address_set_host_ip(&address, "127.0.0.1") == 0, "loopback address");
        peer = enet_host_connect(host, &address, 3, version); check(peer != nullptr, "raw connect");
    }
    std::uint16_t server(int base) {
        ENetAddress address{}; address.host = ENET_HOST_ANY;
        for (int offset = 0; offset < 100; ++offset) {
            address.port = static_cast<std::uint16_t>(base + offset);
            host = enet_host_create(&address, 1, 3, 0, 0);
            if (host) return address.port;
        }
        throw std::runtime_error("raw server bind");
    }
    void send(std::uint8_t channel, const std::vector<std::uint8_t>& data, bool reliable = true) {
        auto* packet = enet_packet_create(data.data(), data.size(), reliable ? ENET_PACKET_FLAG_RELIABLE : 0);
        check(packet != nullptr, "test packet allocation");
        if (enet_peer_send(peer, channel, packet) != 0) { enet_packet_destroy(packet); throw std::runtime_error("raw send failed"); }
        enet_host_flush(host);
    }
    bool poll() {
        bool received = false;
        ENetEvent event;
        while (enet_host_service(host, &event, 0) > 0) {
            if (event.type == ENET_EVENT_TYPE_CONNECT) peer = event.peer;
            if (event.type == ENET_EVENT_TYPE_DISCONNECT) { disconnected = true; peer = nullptr; }
            if (event.type == ENET_EVENT_TYPE_RECEIVE) { received = true; enet_packet_destroy(event.packet); }
        }
        enet_host_flush(host); return received;
    }
};
std::vector<std::uint8_t> header(std::uint8_t type) { return {0x54, 0x59, 0x4c, 0x54, 0, 1, type, 0}; }
void u16(std::vector<std::uint8_t>& data, std::uint16_t value) { data.push_back(value >> 8); data.push_back(value & 255); }
void u32(std::vector<std::uint8_t>& data, std::uint32_t value) { u16(data, value >> 16); u16(data, value & 65535); }
void pump_raw(NetworkGame& game, RawEndpoint& raw, int frames) {
    for (int i = 0; i < frames; ++i) { game.update(kDt, {}); raw.poll(); pause(); }
}
void raw_connect(NetworkGame& game, RawEndpoint& raw) {
    for (int i = 0; i < 300; ++i) {
        pump_raw(game, raw, 1);
        if (raw.peer && raw.peer->state == ENET_PEER_STATE_CONNECTED) return;
    }
    throw std::runtime_error("raw ENet handshake failed");
}

void test_replication_and_slots() {
    NetworkGame host, first, second, third;
    check(!host.connected() && host.local_player() == 0, "default offline session");
    const auto port = start_host(host, 29470);
    check(first.join("127.0.0.1", port) && first.local_player() == -1, "joining client is unassigned");
    wait_join(host, {&first});
    check(first.local_player() == 1, "first client assigned slot 1");
    check(second.join("127.0.0.1", port), "second client starts"); wait_join(host, {&first, &second});
    check(second.local_player() == 2, "second client assigned slot 2");
    check(third.join("127.0.0.1", port), "third client starts"); wait_join(host, {&first, &second, &third});
    check(third.local_player() == 3, "third client assigned slot 3");
    const auto start = host.world().tanks[1].position;
    const auto idle = host.world().tanks[2].position;
    advance(host, {&first, &second, &third}, 100, {Input{1, 0, 0, false}});
    check(distance(start, host.world().tanks[1].position) > 2, "client input moves authoritative tank");
    check(distance(idle, host.world().tanks[2].position) < 0.001f, "idle peer is not driven by another client's input");
    check(distance(first.world().tanks[1].position, host.world().tanks[1].position) < 0.5f, "local authoritative state replicates");
    check(distance(second.world().tanks[1].position, host.world().tanks[1].position) < 0.7f, "remote state interpolates");
    first.offline(); advance(host, {&second, &third}, 40);
    check(!host.world().tanks[1].active, "disconnect removes tank and frees slot");
    check(second.local_player() == 2 && third.local_player() == 3 && host.world().tanks[2].active,
          "remaining player slots remain stable");
    check(first.join("127.0.0.1", port), "client reconnect starts"); wait_join(host, {&first, &second, &third});
    check(first.local_player() == 1, "reconnect uses vacant slot");
    check(distance(host.world().tanks[2].position, idle) < 0.001f, "reconnect does not respawn another player");
    host.offline();
    for (int i = 0; i < 80; ++i) { first.update(kDt, {}); second.update(kDt, {}); third.update(kDt, {}); pause(); }
    check(!first.connected() && first.local_player() == -1, "host shutdown disconnects client");
    first.offline(); check(first.local_player() == 0 && first.world().tanks[0].active, "offline transition restores local tank");
    check(!first.join("", port) && first.local_player() == 0, "failed resolution leaves usable offline game");
}

void test_validation_and_stale_input() {
    NetworkGame game; const auto port = start_host(game, 29580);
    {
        RawEndpoint raw; raw.client(port); raw_connect(game, raw);
        raw.send(0, {1, 2, 3}); pump_raw(game, raw, 30);
        check(raw.disconnected && !game.world().tanks[1].active, "truncated handshake rejected");
    }
    {
        RawEndpoint raw; raw.client(port, 99); pump_raw(game, raw, 100);
        check(raw.disconnected && !game.world().tanks[1].active, "incompatible transport version rejected");
    }
    {
        RawEndpoint raw; raw.client(port); raw_connect(game, raw);
        raw.send(0, header(1)); pump_raw(game, raw, 20);
        check(game.world().tanks[1].active, "valid handshake spawns raw test client");
        auto input = header(3); u32(input, 1); u16(input, 32767); u16(input, 0); u16(input, 0); input.push_back(0);
        const auto initial = game.world().tanks[1].position;
        raw.send(2, input, false); pump_raw(game, raw, 50);
        const auto stopped = game.world().tanks[1].position;
        check(distance(initial, stopped) > 0.5f, "raw valid input accepted");
        pump_raw(game, raw, 50);
        check(distance(stopped, game.world().tanks[1].position) < 0.001f, "lost input stops within stale-input deadline");
        // -32768 is outside the explicitly defined signed control range.
        input = header(3); u32(input, 2); u16(input, 32768); u16(input, 0); u16(input, 0); input.push_back(0);
        raw.send(2, input, false); pump_raw(game, raw, 30);
        check(raw.disconnected && !game.world().tanks[1].active, "out-of-range control rejected");
    }
    {
        RawEndpoint raw; raw.client(port); raw_connect(game, raw);
        raw.send(0, header(1)); pump_raw(game, raw, 10);
        auto input = header(3); u32(input, 1); u16(input, 0); u16(input, 0); u16(input, 0); input.push_back(0); input.push_back(0);
        raw.send(2, input, false); pump_raw(game, raw, 30);
        check(raw.disconnected, "trailing bytes rejected instead of silently accepted");
    }
}

void raw_server_handshake(NetworkGame& client, RawEndpoint& server, int base) {
    const auto port = server.server(base);
    check(client.join("127.0.0.1", port), "client joins test server");
    bool hello = false;
    for (int i = 0; i < 300 && !hello; ++i) { client.update(kDt, {}); hello = server.poll(); pause(); }
    check(hello && server.peer, "server receives game handshake");
    auto welcome = header(2); welcome.push_back(1); server.send(0, welcome);
    for (int i = 0; i < 100 && !client.connected(); ++i) { client.update(kDt, {}); server.poll(); pause(); }
    check(client.connected(), "valid server welcome accepted");
}
std::vector<std::uint8_t> fixture_snapshot(bool nonfinite) {
    auto packet = header(4); u32(packet, 10);
    for (int i = 0; i < tanks::kMaxPlayers; ++i) {
        packet.push_back(0); u32(packet, 0); u32(packet, 0); u32(packet, nonfinite && i == 0 ? 0x7fc00000 : 0); u32(packet, 0);
        u16(packet, 100); u32(packet, 0); u32(packet, 0); u32(packet, 0);
    }
    for (int i = 0; i < tanks::kMaxShells; ++i) {
        packet.push_back(0); u32(packet, 0); u32(packet, 0); u32(packet, 0); u32(packet, 0); packet.push_back(255); u32(packet, 0);
    }
    check(packet.size() == 1192, "snapshot fixture has correct packet size");
    return packet;
}
void test_malformed_server_snapshot() {
    NetworkGame client; RawEndpoint server; raw_server_handshake(client, server, 29690);
    // A correctly sized snapshot with a quiet NaN in the first tank's yaw.
    server.send(1, fixture_snapshot(true), false);
    for (int i = 0; i < 100 && client.connected(); ++i) { client.update(kDt, {}); server.poll(); pause(); }
    check(!client.connected() && client.local_player() == -1, "non-finite snapshot rejected");
    check(client.status() == "Invalid world snapshot" && tanks::valid(client.world()), "malformed world never committed");
}
void test_silent_host_timeout() {
    NetworkGame client; RawEndpoint server; raw_server_handshake(client, server, 29800);
    server.send(1, fixture_snapshot(false), false);
    for (int i = 0; i < 100 && client.world().tick != 10; ++i) { client.update(kDt, {}); server.poll(); pause(); }
    check(client.connected() && client.world().tick == 10, "valid world received before outage");
    // Destroy only the server socket: no ENet disconnect notification reaches the
    // client. Advance the same elapsed-time clock the application's update uses.
    enet_host_destroy(server.host); server.host = nullptr; server.peer = nullptr;
    for (int i = 0; i < 10; ++i) { client.update(0.25, {}); pause(); }
    check(client.connected(), "short network gap tolerates temporary packet loss");
    for (int i = 0; i < 3; ++i) { client.update(0.25, {}); pause(); }
    check(!client.connected() && client.local_player() == -1, "silent host outage has bounded application timeout");
    check(client.status() == "Host timed out (no world updates)" && client.world().tick == 0,
          "timeout clears stale world and exposes actionable status");
}
void test_frame_gap_liveness() {
    NetworkGame client; RawEndpoint server; raw_server_handshake(client, server, 29910);
    server.send(1, fixture_snapshot(false), false);
    for (int i = 0; i < 100 && client.world().tick != 10; ++i) { client.update(kDt, {}); server.poll(); pause(); }
    check(client.connected() && client.world().tick == 10, "frame-gap client starts with a valid world");
    enet_host_destroy(server.host); server.host = nullptr; server.peer = nullptr;
    client.update(3.1, {});
    check(!client.connected() && client.world().tick == 0,
          "one long frame must expire stale world state instead of using simulation-clamped time");

    NetworkGame host; const auto port = start_host(host, 30020);
    RawEndpoint raw; raw.client(port); raw_connect(host, raw);
    raw.send(0, header(1)); pump_raw(host, raw, 10);
    auto input = header(3); u32(input, 1); u16(input, 32767); u16(input, 0); u16(input, 0); input.push_back(0);
    raw.send(2, input, false); pump_raw(host, raw, 2);
    const auto before = host.world().tanks[1].position;
    host.update(.3, {});
    check(distance(before, host.world().tanks[1].position) < .001f,
          "remote controls must expire during a long frame before bounded simulation catch-up");
}
} // namespace

int main() {
    try {
        test_replication_and_slots();
        test_validation_and_stale_input();
        test_malformed_server_snapshot();
        test_silent_host_timeout();
        test_frame_gap_liveness();
        std::cout << "network: real loopback replication, stable slots, transitions, stale inputs, host outage, and malformed packets passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "network test failed: " << error.what() << '\n'; return 1;
    }
}
