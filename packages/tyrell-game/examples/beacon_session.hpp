#pragma once

#include <tyrell/game/fixed_step.hpp>
#include <tyrell/game/net/transport.hpp>
#include <array>
#include <cstdint>
#include <string>

// A deliberately separate game model/protocol. Nothing in the framework knows
// about these actors, controls, bounds, or message schema.
namespace beacon {
constexpr int kPlayers = 4;
struct Input { float x=0, z=0; bool pulse=false; };
struct Actor { bool active=false; float x=0, z=0, pulse=0; };
struct State { std::uint32_t tick=0; std::array<Actor,kPlayers> actors{}; };

class Session {
public:
    Session();
    bool host(std::uint16_t port);
    bool join(const std::string& hostname,std::uint16_t port);
    void offline();
    void update(double elapsed,Input input);
    const State& state() const { return state_; }
    int local_player() const { return local_; }
    bool connected() const;
    bool is_host() const;
    std::uint16_t port() const { return transport_.port(); }
    const std::string& status() const { return status_; }
private:
    enum class Mode { Offline,Host,Client } mode_=Mode::Offline;
    tyrell::game::net::Transport transport_;
    tyrell::game::FixedStepClock simulation_;
    State state_{};
    std::array<tyrell::game::net::PeerId,kPlayers> peers_{};
    std::array<bool,kPlayers> ready_{},has_input_{};
    std::array<Input,kPlayers> inputs_{};
    std::array<std::uint32_t,kPlayers> sequences_{};
    std::array<double,kPlayers> joined_{},last_input_{};
    tyrell::game::net::PeerId server_=0;
    int local_=0;
    std::uint32_t next_input_=0;
    double clock_=0,last_state_=0,snapshot_clock_=0,input_clock_=0;
    bool received_state_=false;
    std::string status_="Offline";
    void clear();
    void disconnect(const std::string& reason);
    void receive_host(const tyrell::game::net::Event& event);
    void receive_client(const tyrell::game::net::Event& event);
    void publish();
};
} // namespace beacon
