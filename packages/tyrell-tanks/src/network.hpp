#pragma once

#include "world.hpp"
#include <cstdint>
#include <memory>
#include <string>

namespace tanks {

// All methods run on the game's main thread. The same simulation is used by
// offline play, a listen server, and the headless server executable.
class NetworkGame {
public:
    NetworkGame();
    ~NetworkGame();
    NetworkGame(const NetworkGame&) = delete;
    NetworkGame& operator=(const NetworkGame&) = delete;

    bool host(std::uint16_t port);
    bool join(const std::string& hostname, std::uint16_t port);
    void offline();
    // Supply elapsed wall time; simulation catch-up is bounded internally while
    // handshake/input/snapshot expiry must still observe a long frame gap.
    void update(double elapsed, const Input& local_input);

    const World& world() const;
    int local_player() const;
    bool is_host() const;
    // True for a running host, or a client whose handshake has completed.
    bool connected() const;
    std::string status() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace tanks
