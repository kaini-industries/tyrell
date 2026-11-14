#include "world.hpp"

#include <algorithm>
#include <cmath>
#include <limits>

namespace tanks {
namespace {
constexpr float kPi = 3.14159265358979323846f;
constexpr float kTankRadius = 1.0f;
constexpr float kShellRadius = 0.10f;
constexpr float kTankSpeed = 4.0f;
constexpr float kHullTurnSpeed = 1.8f;
constexpr float kTurretTurnSpeed = 2.2f;
constexpr float kShellSpeed = 18.0f;
constexpr float kShotCooldown = 0.6f;
constexpr float kRespawnTime = 3.0f;
constexpr float kShellLifetime = 2.5f;
constexpr float kEpsilon = 0.000001f;
constexpr std::array<Obstacle, 8> kObstacles{{
    {{-6.0f, -4.0f}, 3.0f, 2.5f, 1.8f},
    {{ 6.0f, -4.0f}, 3.0f, 2.5f, 1.8f},
    {{-6.0f,  4.0f}, 3.0f, 2.5f, 1.8f},
    {{ 6.0f,  4.0f}, 3.0f, 2.5f, 1.8f},
    {{ 0.0f, -7.0f}, 3.0f, 2.0f, 1.2f},
    {{ 0.0f,  7.0f}, 3.0f, 2.0f, 1.2f},
    {{-11.0f, 0.0f}, 2.0f, 3.0f, 1.5f},
    {{ 11.0f, 0.0f}, 2.0f, 3.0f, 1.5f},
}};
constexpr std::array<Vec2, kMaxPlayers> kSpawns{{
    {-12.0f, -8.0f}, {12.0f, 8.0f}, {12.0f, -8.0f}, {-12.0f, 8.0f}
}};

bool finite(Vec2 p) { return std::isfinite(p.x) && std::isfinite(p.z); }
float length_squared(Vec2 p) { return p.x*p.x + p.z*p.z; }
float axis_input(float value)
{
    return std::isfinite(value) ? std::clamp(value, -1.0f, 1.0f) : 0.0f;
}
bool hits_obstacle(Vec2 p, const Obstacle& obstacle, float radius)
{
    const float x = std::clamp(p.x, obstacle.position.x - obstacle.width*0.5f,
                              obstacle.position.x + obstacle.width*0.5f);
    const float z = std::clamp(p.z, obstacle.position.z - obstacle.depth*0.5f,
                              obstacle.position.z + obstacle.depth*0.5f);
    return length_squared({p.x-x, p.z-z}) < radius*radius - kEpsilon;
}
bool clear_position(const World& world, Vec2 position, int player)
{
    for (const auto& obstacle : kObstacles) {
        if (hits_obstacle(position, obstacle, kTankRadius)) return false;
    }
    for (int other = 0; other < kMaxPlayers; ++other) {
        const auto& tank = world.tanks[other];
        if (other == player || !tank.active || tank.health <= 0) continue;
        if (length_squared({position.x-tank.position.x, position.z-tank.position.z}) <
            4.0f*kTankRadius*kTankRadius - kEpsilon) return false;
    }
    return true;
}
void place_tank(World& world, int player)
{
    auto& tank = world.tanks[player];
    // A dead tank is non-solid. With four corner locations and at most three
    // other players, a respawn always has an unoccupied location.
    Vec2 position = kSpawns[player];
    for (int offset = 0; offset < kMaxPlayers; ++offset) {
        const Vec2 candidate = kSpawns[(player+offset)%kMaxPlayers];
        if (clear_position(world, candidate, player)) {
            position = candidate;
            break;
        }
    }
    const int score = tank.kills;
    tank = Tank{};
    tank.active = true;
    tank.position = position;
    tank.yaw = std::atan2(-position.x, -position.z);
    tank.turret_yaw = tank.yaw;
    tank.kills = score;
}

// Segment versus an AABB expanded by the shell radius. An initial overlap
// returns zero, so firing into an adjacent blocker cannot bypass its surface.
bool segment_box(Vec2 start, Vec2 delta, const Obstacle& obstacle, float& time)
{
    float first = 0.0f, last = 1.0f;
    const float starts[] = {start.x, start.z};
    const float deltas[] = {delta.x, delta.z};
    const float centers[] = {obstacle.position.x, obstacle.position.z};
    const float half[] = {obstacle.width*0.5f + kShellRadius,
                          obstacle.depth*0.5f + kShellRadius};
    for (int axis = 0; axis < 2; ++axis) {
        if (std::abs(deltas[axis]) < kEpsilon) {
            if (starts[axis] < centers[axis]-half[axis] ||
                starts[axis] > centers[axis]+half[axis]) return false;
            continue;
        }
        float near = (centers[axis]-half[axis]-starts[axis])/deltas[axis];
        float far = (centers[axis]+half[axis]-starts[axis])/deltas[axis];
        if (near > far) std::swap(near, far);
        first = std::max(first, near);
        last = std::min(last, far);
        if (first > last) return false;
    }
    time = first;
    return true;
}
bool segment_tank(Vec2 start, Vec2 delta, Vec2 target, float& time)
{
    const Vec2 relative{start.x-target.x, start.z-target.z};
    const float radius = kTankRadius+kShellRadius;
    const float c = length_squared(relative)-radius*radius;
    if (c <= 0.0f) { time = 0.0f; return true; }
    const float a = length_squared(delta);
    if (a < kEpsilon) return false;
    const float b = relative.x*delta.x + relative.z*delta.z;
    const float discriminant = b*b-a*c;
    if (discriminant < 0.0f) return false;
    const float hit = (-b-std::sqrt(discriminant))/a;
    if (hit < 0.0f || hit > 1.0f) return false;
    time = hit;
    return true;
}
void fire_shell(World& world, int player)
{
    auto& tank = world.tanks[player];
    for (auto& shell : world.shells) {
        if (shell.active) continue;
        const Vec2 forward{std::sin(tank.turret_yaw), std::cos(tank.turret_yaw)};
        shell = Shell{};
        shell.active = true;
        shell.owner = player;
        shell.position = {tank.position.x + forward.x,
                          tank.position.z + forward.z};
        shell.velocity = {forward.x*kShellSpeed, forward.z*kShellSpeed};
        shell.lifetime = kShellLifetime;
        tank.cooldown = kShotCooldown;
        return;
    }
}
} // namespace

const std::array<Obstacle, 8>& obstacles() { return kObstacles; }

float wrap_angle(float angle)
{
    if (!std::isfinite(angle)) return 0.0f;
    angle = std::fmod(angle, 2.0f*kPi);
    if (angle > kPi) angle -= 2.0f*kPi;
    if (angle < -kPi) angle += 2.0f*kPi;
    return angle;
}
void reset(World& world) { world = World{}; }
void spawn(World& world, int player)
{
    if (player < 0 || player >= kMaxPlayers) return;
    // An explicit join starts a fresh score; automatic respawn retains it.
    world.tanks[player] = Tank{};
    place_tank(world, player);
}
void remove(World& world, int player)
{
    if (player < 0 || player >= kMaxPlayers) return;
    world.tanks[player] = Tank{};
    for (auto& shell : world.shells) {
        if (shell.owner == player) shell = Shell{};
    }
}
void step(World& world, const std::array<Input, kMaxPlayers>& inputs, float dt)
{
    if (!std::isfinite(dt) || dt <= 0.0f) return;
    if (!valid(world)) { reset(world); return; }
    dt = std::min(dt, 0.1f);
    ++world.tick;
    for (int player = 0; player < kMaxPlayers; ++player) {
        auto& tank = world.tanks[player];
        if (!tank.active) continue;
        if (tank.health <= 0) {
            tank.respawn = std::max(0.0f, tank.respawn-dt);
            if (tank.respawn <= kEpsilon) place_tank(world, player);
            continue;
        }
        tank.cooldown = std::max(0.0f, tank.cooldown-dt);
        tank.yaw = wrap_angle(tank.yaw + axis_input(inputs[player].turn)*kHullTurnSpeed*dt);
        tank.turret_yaw = wrap_angle(tank.turret_yaw +
                                    axis_input(inputs[player].turret)*kTurretTurnSpeed*dt);
        const float distance = axis_input(inputs[player].throttle)*kTankSpeed*dt;
        Vec2 candidate = tank.position;
        candidate.x = std::clamp(candidate.x + std::sin(tank.yaw)*distance,
                                 -kArenaWidth*0.5f+kTankRadius,
                                  kArenaWidth*0.5f-kTankRadius);
        if (clear_position(world, candidate, player)) tank.position.x = candidate.x;
        candidate = tank.position;
        candidate.z = std::clamp(candidate.z + std::cos(tank.yaw)*distance,
                                 -kArenaDepth*0.5f+kTankRadius,
                                  kArenaDepth*0.5f-kTankRadius);
        if (clear_position(world, candidate, player)) tank.position.z = candidate.z;
        if (inputs[player].fire && tank.cooldown <= kEpsilon) fire_shell(world, player);
    }
    for (auto& shell : world.shells) {
        if (!shell.active) continue;
        const float travel_time = std::min(dt, shell.lifetime);
        const Vec2 delta{shell.velocity.x*travel_time, shell.velocity.z*travel_time};
        float first_hit = 1.0f;
        int target = -1;
        bool collided = false;
        const float x_limit = kArenaWidth*0.5f-kShellRadius;
        const float z_limit = kArenaDepth*0.5f-kShellRadius;
        const Vec2 end{shell.position.x+delta.x, shell.position.z+delta.z};
        if (std::abs(shell.position.x) >= x_limit || std::abs(shell.position.z) >= z_limit) {
            first_hit = 0.0f;
            collided = true;
        } else {
            if (std::abs(end.x) > x_limit && std::abs(delta.x) > kEpsilon) {
                first_hit = std::min(first_hit, ((delta.x > 0 ? x_limit : -x_limit)-shell.position.x)/delta.x);
                collided = true;
            }
            if (std::abs(end.z) > z_limit && std::abs(delta.z) > kEpsilon) {
                first_hit = std::min(first_hit, ((delta.z > 0 ? z_limit : -z_limit)-shell.position.z)/delta.z);
                collided = true;
            }
        }
        for (const auto& obstacle : kObstacles) {
            float time = 0.0f;
            if (segment_box(shell.position, delta, obstacle, time) && time <= first_hit) {
                first_hit = time;
                collided = true;
            }
        }
        for (int player = 0; player < kMaxPlayers; ++player) {
            const auto& tank = world.tanks[player];
            if (player == shell.owner || !tank.active || tank.health <= 0) continue;
            float time = 0.0f;
            // Static geometry wins exact ties so targets behind cover stay safe.
            if (segment_tank(shell.position, delta, tank.position, time) &&
                (time < first_hit || (!collided && time <= first_hit))) {
                first_hit = time;
                target = player;
                collided = true;
            }
        }
        shell.position.x += delta.x*first_hit;
        shell.position.z += delta.z*first_hit;
        shell.lifetime = std::max(0.0f, shell.lifetime-travel_time);
        if (target >= 0) {
            auto& tank = world.tanks[target];
            tank.health = std::max(0, tank.health-25);
            if (tank.health == 0) {
                tank.respawn = kRespawnTime;
                if (shell.owner >= 0 && shell.owner < kMaxPlayers &&
                    world.tanks[shell.owner].active &&
                    world.tanks[shell.owner].kills < 1000000000) {
                    ++world.tanks[shell.owner].kills;
                }
            }
        }
        if (collided || shell.lifetime <= kEpsilon) shell = Shell{};
    }
}
bool valid(const World& world)
{
    for (const auto& tank : world.tanks) {
        if (!finite(tank.position) || !std::isfinite(tank.yaw) ||
            !std::isfinite(tank.turret_yaw) || !std::isfinite(tank.cooldown) ||
            !std::isfinite(tank.respawn) || tank.health < 0 || tank.health > 100 ||
            tank.kills < 0 || tank.kills > 1000000000 ||
            tank.cooldown < 0 || tank.cooldown > kShotCooldown+kEpsilon ||
            tank.respawn < 0 || tank.respawn > kRespawnTime+kEpsilon ||
            std::abs(tank.position.x) > kArenaWidth*0.5f ||
            std::abs(tank.position.z) > kArenaDepth*0.5f ||
            std::abs(tank.yaw) > kPi+kEpsilon || std::abs(tank.turret_yaw) > kPi+kEpsilon) return false;
        if (tank.active && ((tank.health == 0 && tank.respawn <= 0) ||
                            (tank.health > 0 && tank.respawn > 0))) return false;
    }
    for (const auto& shell : world.shells) {
        if (!finite(shell.position) || !finite(shell.velocity) ||
            !std::isfinite(shell.lifetime) || shell.lifetime < 0 ||
            shell.lifetime > kShellLifetime+kEpsilon) return false;
        if (shell.active && (shell.owner < 0 || shell.owner >= kMaxPlayers ||
            !world.tanks[shell.owner].active || shell.lifetime <= 0 ||
            std::abs(shell.position.x) > kArenaWidth*0.5f ||
            std::abs(shell.position.z) > kArenaDepth*0.5f ||
            length_squared(shell.velocity) > kShellSpeed*kShellSpeed+0.01f)) return false;
    }
    return true;
}
} // namespace tanks
