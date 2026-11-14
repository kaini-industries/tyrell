#include "network.hpp"
#include <tyrell/game/net/codec.hpp>
#include <tyrell/game/net/transport.hpp>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <string>
#include <vector>

namespace tanks {
namespace net=tyrell::game::net;
namespace {
constexpr std::uint32_t kMagic = 0x54594c54; // TYLT
constexpr std::uint16_t kVersion = 1;
constexpr auto kControlChannel=net::kControlChannel, kStateChannel=net::kSnapshotChannel, kInputChannel=net::kInputChannel;
constexpr std::size_t kHeaderSize = 8;
constexpr std::size_t kSnapshotSize = kHeaderSize + 4 + kMaxPlayers * 31 + kMaxShells * 22;
static_assert(kSnapshotSize <= 1200, "Keep snapshots within one conservative UDP datagram");
constexpr double kSnapshotInterval = 1.0 / 20.0;
constexpr double kInputInterval = 1.0 / 30.0;
constexpr double kInputTimeout = 0.25;
constexpr double kHandshakeTimeout = 5.0;
constexpr double kSnapshotTimeout = 3.0;
enum class Message : std::uint8_t { Hello = 1, Welcome = 2, Input = 3, Snapshot = 4 };

struct Writer : net::ByteWriter {
    explicit Writer(Message message) : ByteWriter(1200) {
        u32(kMagic); u16(kVersion); u8(static_cast<std::uint8_t>(message)); u8(0);
    }
};
struct Reader : net::ByteReader {
    explicit Reader(const std::vector<std::uint8_t>& payload) : ByteReader(payload) {}
    bool header(Message& message) {
        if (size() < kHeaderSize || u32() != kMagic || u16() != kVersion) return false;
        message = static_cast<Message>(u8());
        return u8() == 0 && good();
    }
};

bool bounded(float value, float low, float high) { return net::finite_between(value, low, high); }
float control(float value) { return std::isfinite(value) ? std::clamp(value, -1.0f, 1.0f) : 0.0f; }
Input sanitized(Input input) {
    input.throttle = control(input.throttle); input.turn = control(input.turn); input.turret = control(input.turret);
    return input;
}
void encode_control(Writer& writer, float value) {
    const int quantized = static_cast<int>(std::lround(control(value) * 32767.0f));
    writer.u16(static_cast<std::uint16_t>(quantized < 0 ? quantized + 65536 : quantized));
}
bool decode_control(Reader& reader, float& output) {
    const auto raw = reader.u16();
    if (raw == 32768) return false;
    const int signed_value = raw > 32767 ? int(raw) - 65536 : int(raw);
    output = signed_value / 32767.0f;
    return reader.good();
}
bool newer(std::uint32_t value, std::uint32_t previous) {
    return net::sequence_newer(value, previous);
}

Writer snapshot(const World& world) {
    Writer writer(Message::Snapshot); writer.u32(world.tick);
    for (const auto& tank : world.tanks) {
        writer.u8(tank.active ? 1 : 0);
        writer.f32(tank.position.x); writer.f32(tank.position.z);
        writer.f32(tank.yaw); writer.f32(tank.turret_yaw);
        writer.u16(static_cast<std::uint16_t>(std::clamp(tank.health, 0, 100)));
        writer.u32(static_cast<std::uint32_t>(std::clamp(tank.kills, 0, 1000000000)));
        writer.f32(tank.cooldown); writer.f32(tank.respawn);
    }
    for (const auto& shell : world.shells) {
        writer.u8(shell.active ? 1 : 0);
        writer.f32(shell.position.x); writer.f32(shell.position.z);
        writer.f32(shell.velocity.x); writer.f32(shell.velocity.z);
        writer.u8(shell.owner < 0 ? 255 : static_cast<std::uint8_t>(shell.owner));
        writer.f32(shell.lifetime);
    }
    return writer;
}

bool decode_snapshot(Reader& reader, World& world) {
    if (reader.size() != kSnapshotSize) return false;
    World candidate; candidate.tick = reader.u32();
    for (auto& tank : candidate.tanks) {
        const auto active = reader.u8(); if (active > 1) return false;
        tank.active = active != 0;
        tank.position = {reader.f32(), reader.f32()};
        tank.yaw = reader.f32(); tank.turret_yaw = reader.f32();
        tank.health = reader.u16();
        const auto kills = reader.u32(); if (kills > 1000000000) return false;
        tank.kills = static_cast<int>(kills);
        tank.cooldown = reader.f32(); tank.respawn = reader.f32();
        if (!bounded(tank.position.x, -1024, 1024) || !bounded(tank.position.z, -1024, 1024) ||
            !bounded(tank.yaw, -100, 100) || !bounded(tank.turret_yaw, -100, 100) ||
            tank.health > 100 || !bounded(tank.cooldown, 0, 60) || !bounded(tank.respawn, 0, 60)) return false;
    }
    for (auto& shell : candidate.shells) {
        const auto active = reader.u8(); if (active > 1) return false;
        shell.active = active != 0;
        shell.position = {reader.f32(), reader.f32()};
        shell.velocity = {reader.f32(), reader.f32()};
        const auto owner = reader.u8(); shell.owner = owner == 255 ? -1 : owner;
        shell.lifetime = reader.f32();
        if (!bounded(shell.position.x, -1024, 1024) || !bounded(shell.position.z, -1024, 1024) ||
            !bounded(shell.velocity.x, -1024, 1024) || !bounded(shell.velocity.z, -1024, 1024) ||
            (shell.owner != -1 && (shell.owner < 0 || shell.owner >= kMaxPlayers)) ||
            (shell.active && shell.owner < 0) || !bounded(shell.lifetime, 0, 60)) return false;
    }
    if (!reader.finished() || !valid(candidate)) return false;
    world = candidate; return true;
}
float angle_between(float first, float second, float alpha) {
    return wrap_angle(first + wrap_angle(second - first) * alpha);
}
} // namespace

struct NetworkGame::Impl {
    enum class Mode { Offline, Host, Client } mode = Mode::Offline;
    net::Transport transport;
    net::PeerId server = net::kInvalidPeer;
    std::array<net::PeerId, kMaxPlayers> peers{};
    std::array<bool, kMaxPlayers> ready{}, has_sequence{};
    std::array<std::uint32_t, kMaxPlayers> input_sequences{};
    std::array<double, kMaxPlayers> last_input{}, joined_at{};
    std::array<Input, kMaxPlayers> inputs{};
    World current{}, previous_snapshot{}, latest_snapshot{};
    bool has_snapshot = false;
    int local = 0;
    std::uint32_t next_input_sequence = 0;
    double clock = 0, accumulator = 0, snapshot_accumulator = 0, input_accumulator = 0, snapshot_age = 0;
    double last_snapshot_at = 0;
    std::uint16_t port = 0;
    std::string message = "Offline";

    Impl() { reset(current); spawn(current, 0); }
    ~Impl() { close(); }
    void close() {
        transport.close(); server = net::kInvalidPeer; peers.fill(net::kInvalidPeer); ready.fill(false);
        has_sequence.fill(false); inputs.fill({}); last_input.fill(0); joined_at.fill(0);
        input_sequences.fill(0); next_input_sequence = 0;
        clock = accumulator = snapshot_accumulator = input_accumulator = snapshot_age = last_snapshot_at = 0;
        has_snapshot = false; port = 0;
    }
    void send(net::PeerId peer, std::uint8_t channel, const Writer& writer, bool reliable=false) {
        if(peer && writer.good()) transport.send(peer,channel,writer.data(),reliable);
    }

    void make_offline() {
        close(); mode = Mode::Offline; local = 0; reset(current); spawn(current, 0); message = "Offline";
    }
    int slot(net::PeerId peer) const {
        for (int i = 1; i < kMaxPlayers; ++i) if (peers[i] == peer) return i;
        return -1;
    }
    void remove_peer(int player) {
        if (player <= 0 || player >= kMaxPlayers) return;
        peers[player] = net::kInvalidPeer; ready[player] = false; has_sequence[player] = false; inputs[player] = {};
        remove(current, player);
    }
    void reject(net::PeerId peer) {
        const int player = slot(peer); remove_peer(player);
        transport.disconnect(peer,1);
    }
    void receive_host(const net::Event& event) {
        Reader reader(event.payload); Message type{};
        const int player = slot(event.peer);
        if (player < 1 || !reader.header(type)) { reject(event.peer); return; }
        if (type == Message::Hello && event.channel == kControlChannel && reader.finished() && !ready[player]) {
            ready[player] = true; last_input[player] = clock; spawn(current, player);
            Writer welcome(Message::Welcome); welcome.u8(static_cast<std::uint8_t>(player));
            send(event.peer, kControlChannel, welcome, true);
            send(event.peer, kStateChannel, snapshot(current));
            return;
        }
        if (type == Message::Input && event.channel == kInputChannel && ready[player]) {
            const auto sequence = reader.u32(); Input input;
            bool okay = decode_control(reader, input.throttle);
            okay = decode_control(reader, input.turn) && okay;
            okay = decode_control(reader, input.turret) && okay;
            const auto fire = reader.u8(); input.fire = fire != 0;
            if (!okay || fire > 1 || !reader.finished()) { reject(event.peer); return; }
            if (!has_sequence[player] || newer(sequence, input_sequences[player])) {
                inputs[player] = input; last_input[player] = clock;
                has_sequence[player] = true; input_sequences[player] = sequence;
            }
            return;
        }
        reject(event.peer);
    }
    void disconnect_client(const std::string& reason) {
        if (server) transport.disconnect(server,0);
        server = net::kInvalidPeer; local = -1; message = reason;
        reset(current); has_snapshot = false;
    }
    void receive_client(const net::Event& event) {
        Reader reader(event.payload); Message type{};
        if (event.peer != server || !reader.header(type)) { disconnect_client("Invalid server packet"); return; }
        if (type == Message::Welcome && event.channel == kControlChannel) {
            const int player = reader.u8();
            if (local != -1 || player < 1 || player >= kMaxPlayers || !reader.finished()) {
                disconnect_client("Invalid server handshake"); return;
            }
            local = player;
            if (!has_snapshot) last_snapshot_at = clock;
            message = "Joined as Player " + std::to_string(player + 1); return;
        }
        if (type == Message::Snapshot && event.channel == kStateChannel) {
            World received;
            if (!decode_snapshot(reader, received)) { disconnect_client("Invalid world snapshot"); return; }
            if (!has_snapshot || newer(received.tick, latest_snapshot.tick)) {
                previous_snapshot = has_snapshot ? latest_snapshot : received;
                latest_snapshot = received; current = received; snapshot_age = 0; has_snapshot = true;
                last_snapshot_at = clock;
            }
            return;
        }
        disconnect_client("Unexpected server packet");
    }
    void events() {
        net::Event event;
        // Bound work per frame so a noisy peer cannot starve simulation/rendering.
        for (int count = 0; transport.active() && count < 128 && transport.poll(event); ++count) {
            if (event.type == net::EventType::Connected) {
                if (mode == Mode::Host) {
                    int player = 1; while (player < kMaxPlayers && peers[player]) ++player;
                    if (event.data != kVersion || player == kMaxPlayers) { transport.disconnect(event.peer,1); continue; }
                    peers[player] = event.peer; joined_at[player] = clock;
                } else if (mode == Mode::Client && event.peer == server) {
                    send(server, kControlChannel, Writer(Message::Hello), true); message = "Joining game...";
                }
            } else if (event.type == net::EventType::Received) {
                if (mode == Mode::Host) receive_host(event);
                else if (mode == Mode::Client && server) receive_client(event);
            } else if (event.type == net::EventType::Disconnected) {
                if (mode == Mode::Host) remove_peer(slot(event.peer));
                else if (event.peer == server) disconnect_client("Disconnected from host");
            }
        }
    }
    void update(double elapsed, const Input& input) {
        const double wall_dt = std::isfinite(elapsed) ? std::clamp(elapsed, 0.0, 60.0) : 0.0;
        const double dt = std::min(wall_dt, 0.25);
        // Expiry follows elapsed wall time, independently of bounded simulation
        // catch-up. Resuming a stalled frame must not revive old controls/state.
        clock += wall_dt;
        if (transport.active()) events();
        if (mode == Mode::Offline || mode == Mode::Host) {
            inputs[0] = sanitized(input);
            for (int i = 1; i < kMaxPlayers; ++i) {
                if (peers[i] && !ready[i] && clock - joined_at[i] > kHandshakeTimeout) reject(peers[i]);
                if (clock - last_input[i] >= kInputTimeout) inputs[i] = {};
            }
            accumulator += dt;
            while (accumulator + 1e-9 >= double(kFixedStep)) {
                step(current, inputs, kFixedStep); accumulator -= double(kFixedStep);
            }
            if (mode == Mode::Host) {
                snapshot_accumulator += dt;
                if (snapshot_accumulator + 1e-9 >= kSnapshotInterval) {
                    snapshot_accumulator = std::fmod(snapshot_accumulator, kSnapshotInterval);
                    const auto packet = snapshot(current);
                    for (int i = 1; i < kMaxPlayers; ++i) if (ready[i]) send(peers[i], kStateChannel, packet);
                }
            }
        } else if (server) {
            if (local < 0 && clock > kHandshakeTimeout) { disconnect_client("Connection timed out"); return; }
            if (local >= 0 && clock - last_snapshot_at >= kSnapshotTimeout) {
                disconnect_client("Host timed out (no world updates)"); return;
            }
            if (local >= 0) {
                input_accumulator += dt;
                if (input_accumulator + 1e-9 >= kInputInterval) {
                    input_accumulator = std::fmod(input_accumulator, kInputInterval);
                    const auto clean = sanitized(input); Writer packet(Message::Input);
                    packet.u32(next_input_sequence++);
                    encode_control(packet, clean.throttle); encode_control(packet, clean.turn); encode_control(packet, clean.turret);
                    packet.u8(clean.fire ? 1 : 0); send(server, kInputChannel, packet);
                }
            }
            if (has_snapshot) {
                snapshot_age += dt;
                const float alpha = static_cast<float>(std::clamp(snapshot_age / kSnapshotInterval, 0.0, 1.0));
                current = latest_snapshot;
                for (int i = 0; i < kMaxPlayers; ++i) {
                    if (i == local || !previous_snapshot.tanks[i].active || !current.tanks[i].active) continue;
                    const auto& old = previous_snapshot.tanks[i]; auto& tank = current.tanks[i];
                    tank.position.x = old.position.x + (tank.position.x - old.position.x) * alpha;
                    tank.position.z = old.position.z + (tank.position.z - old.position.z) * alpha;
                    tank.yaw = angle_between(old.yaw, tank.yaw, alpha);
                    tank.turret_yaw = angle_between(old.turret_yaw, tank.turret_yaw, alpha);
                }
            }
        }
        if (transport.active()) transport.flush();
    }
};

NetworkGame::NetworkGame() : impl_(std::make_unique<Impl>()) {}
NetworkGame::~NetworkGame() = default;
bool NetworkGame::host(std::uint16_t port) {
    impl_->make_offline();
    if (!impl_->transport.host(port)) { impl_->message=impl_->transport.error(); return false; }
    impl_->mode = Impl::Mode::Host; impl_->port = impl_->transport.port();
    impl_->message = "Hosting on UDP " + std::to_string(impl_->port); return true;
}
bool NetworkGame::join(const std::string& hostname, std::uint16_t port) {
    impl_->make_offline();
    impl_->server=impl_->transport.connect(hostname,port,kVersion);
    if (!impl_->server) { impl_->message=impl_->transport.error(); return false; }
    impl_->mode = Impl::Mode::Client; impl_->local = -1; reset(impl_->current);
    impl_->message = "Connecting to " + hostname + ":" + std::to_string(port); return true;
}

void NetworkGame::offline() { impl_->make_offline(); }
void NetworkGame::update(double elapsed, const Input& local_input) { impl_->update(elapsed, local_input); }
const World& NetworkGame::world() const { return impl_->current; }
int NetworkGame::local_player() const { return impl_->local; }
bool NetworkGame::is_host() const { return impl_->mode == Impl::Mode::Host; }
bool NetworkGame::connected() const { return is_host() || (impl_->mode == Impl::Mode::Client && impl_->server && impl_->local >= 0); }
std::string NetworkGame::status() const { return impl_->message; }
} // namespace tanks
