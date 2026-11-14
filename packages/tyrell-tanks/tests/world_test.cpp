#include "world.hpp"

#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>

namespace {
constexpr float kPi = 3.14159265358979323846f;
void require(bool condition, const char* message)
{
    if (!condition) {
        std::cerr << "FAIL: " << message << '\n';
        std::exit(1);
    }
}
void advance(tanks::World& world, const std::array<tanks::Input, tanks::kMaxPlayers>& inputs, int ticks)
{
    for (int tick = 0; tick < ticks; ++tick) {
        tanks::step(world, inputs, tanks::kFixedStep);
        require(tanks::valid(world), "simulation must remain finite and valid");
    }
}
void set_pose(tanks::Tank& tank, float x, float z, float yaw)
{
    tank.position = {x, z};
    tank.yaw = tank.turret_yaw = yaw;
}
void test_join_and_input_guards()
{
    tanks::World world;
    tanks::reset(world);
    for (const auto& tank : world.tanks) require(!tank.active, "reset must not join players");
    tanks::spawn(world, -1);
    tanks::spawn(world, tanks::kMaxPlayers);
    require(tanks::valid(world), "invalid join indices must be harmless");
    for (int i = 0; i < tanks::kMaxPlayers; ++i) tanks::spawn(world, i);
    for (const auto& tank : world.tanks) require(tank.active, "spawn must join requested player");
    std::array<tanks::Input, tanks::kMaxPlayers> inputs{};
    inputs[0] = {std::numeric_limits<float>::infinity(),
                 std::numeric_limits<float>::quiet_NaN(), 0, false};
    const auto original = world.tanks[0].position;
    advance(world, inputs, 10);
    require(world.tanks[0].position.x == original.x && world.tanks[0].position.z == original.z,
            "nonfinite controls must become neutral");
    const auto tick = world.tick;
    tanks::step(world, inputs, std::numeric_limits<float>::quiet_NaN());
    tanks::step(world, inputs, -1.0f);
    require(world.tick == tick, "invalid elapsed time must not advance simulation");
    inputs[0] = {0, 1, 0, false};
    const float turret = world.tanks[0].turret_yaw;
    advance(world, inputs, 30);
    require(world.tanks[0].yaw != turret && world.tanks[0].turret_yaw == turret,
            "turret aim must remain independent of hull steering");
    tanks::remove(world, 0);
    require(!world.tanks[0].active, "remove must leave the player slot inactive");
    require(tanks::wrap_angle(std::numeric_limits<float>::infinity()) == 0,
            "angle normalization must reject nonfinite values");
}
void test_collisions()
{
    tanks::World world;
    tanks::spawn(world, 0);
    const auto obstacle = tanks::obstacles()[0];
    const float left = obstacle.position.x-obstacle.width*0.5f;
    set_pose(world.tanks[0], left-1.02f, obstacle.position.z, kPi*0.5f);
    std::array<tanks::Input, tanks::kMaxPlayers> inputs{};
    inputs[0].throttle = 1;
    advance(world, inputs, 120);
    require(world.tanks[0].position.x <= left-0.99f, "one-unit tank circle must stop at blocker surface");

    tanks::reset(world);
    tanks::spawn(world, 0);
    tanks::spawn(world, 1);
    set_pose(world.tanks[0], -2, 0, kPi*0.5f);
    set_pose(world.tanks[1], 0, 0, 0);
    advance(world, inputs, 120);
    require(world.tanks[0].position.x <= -1.99f, "two one-unit tank bodies must stay two units apart");

    tanks::reset(world);
    tanks::spawn(world, 0);
    set_pose(world.tanks[0], 14, 0, 0);
    advance(world, inputs, 400);
    require(world.tanks[0].position.z <= tanks::kArenaDepth*0.5f-0.99f,
            "tank must stay inside arena walls");
}
void test_swept_shell_and_cover()
{
    tanks::World world;
    tanks::spawn(world, 0);
    tanks::spawn(world, 1);
    set_pose(world.tanks[0], -5, 0, 0);
    set_pose(world.tanks[1], 0, 0, 0);
    // This grazing segment enters and leaves the 1.1-unit combined tank/shell
    // radius, while both segment endpoints are outside the target circle.
    world.shells[0] = {true, {-0.8f, 1.0f}, {18, 0}, 0, 1};
    std::array<tanks::Input, tanks::kMaxPlayers> inputs{};
    tanks::step(world, inputs, 0.1f);
    require(world.tanks[1].health == 75, "swept shell must hit even when endpoint passes target");
    require(!world.shells[0].active, "impact must consume shell");

    const auto blocker = tanks::obstacles()[0];
    const float left = blocker.position.x-blocker.width*0.5f;
    set_pose(world.tanks[1], blocker.position.x+blocker.width*0.5f+1.2f,
             blocker.position.z, 0);
    world.shells[0] = {true, {left-0.2f, blocker.position.z}, {18, 0}, 0, 1};
    tanks::step(world, inputs, 0.1f);
    require(!world.shells[0].active && world.tanks[1].health == 75,
            "blockers must consume shells before targets behind them");
    require(tanks::valid(world), "impact processing must leave valid state");
}
void test_combat_and_respawn()
{
    tanks::World world;
    tanks::spawn(world, 0);
    tanks::spawn(world, 1);
    set_pose(world.tanks[0], -3, 0, kPi*0.5f);
    set_pose(world.tanks[1], 3, 0, 0);
    world.tanks[1].kills = 3;
    std::array<tanks::Input, tanks::kMaxPlayers> inputs{};
    inputs[0].fire = true;
    advance(world, inputs, 30);
    require(world.tanks[1].health == 75, "one hit must do 25 damage and cooldown must prevent rapid fire");
    int elapsed = 30;
    while (world.tanks[1].health > 0 && elapsed < 200) {
        advance(world, inputs, 1);
        ++elapsed;
    }
    require(world.tanks[1].health == 0 && world.tanks[1].respawn > 2.9f,
            "four hits must kill and start a three-second respawn");
    require(world.tanks[0].kills == 1 && world.tanks[1].active,
            "kill must award score while dead joined slot stays active");
    inputs[0].fire = false;
    advance(world, inputs, 170);
    require(world.tanks[1].health == 0, "dead tank must not respawn before three seconds");
    advance(world, inputs, 11);
    require(world.tanks[1].health == 100 && world.tanks[1].respawn == 0,
            "tank must respawn with full health after three seconds");
    require(world.tanks[0].kills == 1 && world.tanks[1].kills == 3 &&
            !world.tanks[2].active && !world.tanks[3].active,
            "respawn must retain score and never activate absent players");
    tanks::remove(world, 0);
    for (const auto& shell : world.shells) require(!shell.active || shell.owner != 0,
                                                 "departing player shells must be removed");
}
void test_deterministic_steps_and_bad_state()
{
    tanks::World first, second;
    tanks::spawn(first, 0);
    tanks::spawn(second, 0);
    std::array<tanks::Input, tanks::kMaxPlayers> inputs{};
    inputs[0] = {0.7f, 0.3f, -0.6f, true};
    advance(first, inputs, 600);
    advance(second, inputs, 600);
    require(first.tick == second.tick && first.tanks[0].position.x == second.tanks[0].position.x &&
            first.tanks[0].position.z == second.tanks[0].position.z &&
            first.tanks[0].yaw == second.tanks[0].yaw,
            "same fixed-step inputs must produce the same simulation");
    first.tanks[0].position.x = std::numeric_limits<float>::quiet_NaN();
    require(!tanks::valid(first), "validation must reject nonfinite state");
    tanks::step(first, inputs, tanks::kFixedStep);
    require(tanks::valid(first) && !first.tanks[0].active,
            "invalid authoritative state must recover to a safe empty world");
}
} // namespace

int main()
{
    test_join_and_input_guards();
    test_collisions();
    test_swept_shell_and_cover();
    test_combat_and_respawn();
    test_deterministic_steps_and_bad_state();
    std::cout << "World tests passed\n";
}
