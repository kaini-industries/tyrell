#include "input_edges.h"
#include <cstdlib>
#include <iostream>

void check(bool value) { if(!value) { std::cerr << "input edge contract failed\n"; std::exit(1); } }
int main() {
    tyrell::status::InputEdges input;
    check(input.press(16, 1));
    check(!input.press(16, 1));
    check(!input.press(16, 2));
    check(!input.press(16, 0));
    check(input.press(16, 1));
    tyrell::status::InputEdges::State held{};
    held[16] = true;
    input.reset(held);
    check(!input.press(16, 1));
    check(!input.press(16, 2));
    check(!input.press(16, 0));
    check(input.press(16, 1));
    input.dropped();
    check(input.needs_sync());
    check(!input.press(17, 1));
    check(!input.press(16, 0));
    input.reset(held);
    check(!input.needs_sync());
    check(!input.press(16, 1));
    check(input.press(17, 1));
    check(!input.press(9999, 1));
    input.reset();
    check(input.press(16, 1));
}
