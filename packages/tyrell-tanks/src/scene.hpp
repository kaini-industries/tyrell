#pragma once
#include "world.hpp"
#include "raylib.h"
namespace tanks {
void draw_scene(const World& world, int local_player, double time, bool overview = false);
Color player_color(int index);
}
