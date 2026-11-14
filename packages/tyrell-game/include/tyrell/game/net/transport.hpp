#pragma once
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace tyrell::game::net {
using PeerId=std::uint64_t;
constexpr PeerId kInvalidPeer=0;
constexpr std::uint8_t kControlChannel=0,kSnapshotChannel=1,kInputChannel=2;
enum class EventType { Connected,Received,Disconnected };
struct Event {
    EventType type=EventType::Disconnected;
    PeerId peer=kInvalidPeer;
    std::uint8_t channel=0;
    std::uint32_t data=0;
    std::vector<std::uint8_t> payload;
    bool reliable=false;
};
struct Config {
    std::size_t max_peers=3;
    std::size_t channels=3;
    std::size_t max_packet_bytes=1200;
    std::size_t max_waiting_bytes=64*1024;
    std::size_t max_queued_bytes=64*1024;
    std::size_t max_queued_packets=512;
    std::uint32_t timeout_limit=10,timeout_min_ms=3000,timeout_max_ms=6000;
};

// Main-thread, nonblocking ENet transport. It owns sockets/packets and copies
// received bytes into Event; public interfaces never borrow ENet objects.
// Application handshakes, slots, schemas and simulation remain consumer code.
// Peer IDs remain unique across close/reopen, so stale IDs cannot address a
// newly connected peer. A transport instance must not be used concurrently.
class Transport {
public:
    explicit Transport(Config config={});
    ~Transport();
    Transport(const Transport&)=delete;
    Transport& operator=(const Transport&)=delete;
    bool host(std::uint16_t port);
    // host/connect replace the previous endpoint. connect opens one outgoing
    // server connection; a listening host accepts up to Config::max_peers.
    PeerId connect(const std::string& hostname,std::uint16_t port,std::uint32_t connect_data=0);
    bool poll(Event& event);
    bool send(PeerId peer,std::uint8_t channel,const std::vector<std::uint8_t>& payload,bool reliable=false);
    void disconnect(PeerId peer,std::uint32_t reason=0);
    void flush();
    void close();
    bool active() const;
    std::uint16_t port() const;
    std::string error() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace tyrell::game::net
