#pragma once
#include <array>
#include <cstdint>

namespace tanks {
constexpr int kMaxPlayers = 4;
constexpr int kMaxShells = 48;
constexpr float kArenaWidth = 32.0f;
constexpr float kArenaDepth = 24.0f;
constexpr float kFixedStep = 1.0f / 60.0f;
struct Vec2 { float x = 0, z = 0; };
struct Input { float throttle = 0, turn = 0, turret = 0; bool fire = false; };
struct Tank {
    bool active = false;
    Vec2 position{};
    float yaw = 0, turret_yaw = 0;
    int health = 100, kills = 0;
    float cooldown = 0, respawn = 0;
};
struct Shell {
    bool active = false;
    Vec2 position{}, velocity{};
    int owner = -1;
    float lifetime = 0;
};
struct Obstacle { Vec2 position; float width, depth, height; };
struct World {
    std::array<Tank, kMaxPlayers> tanks{};
    std::array<Shell, kMaxShells> shells{};
    std::uint32_t tick = 0;
};
const std::array<Obstacle, 8>& obstacles();
void reset(World& world);
void spawn(World& world, int player);
void remove(World& world, int player);
void step(World& world, const std::array<Input, kMaxPlayers>& inputs, float dt);
bool valid(const World& world);
float wrap_angle(float angle);
} // namespace tanks
