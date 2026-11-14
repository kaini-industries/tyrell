#include <tyrell/game/fixed_step.hpp>
#include <tyrell/game/runtime.hpp>
#include <tyrell/game/render.hpp>
#include <tyrell/game/net/transport.hpp>
int main() {
    tyrell::game::FixedStepClock clock;
    tyrell::game::net::Transport network;
    return clock.advance(0)==0 && !network.active()?0:1;
}
