#pragma once

#include <chrono>
#include <cstdint>
#include <random>

// Host replacements for the upstream engine's only hardware dependencies.
// The uint32_t steady clock intentionally preserves its rollover arithmetic.
namespace ChessPlatform {
inline uint32_t millis() {
    using namespace std::chrono;
    return static_cast<uint32_t>(duration_cast<milliseconds>(steady_clock::now().time_since_epoch()).count());
}
inline uint32_t random() {
    thread_local std::mt19937 generator(std::random_device{}());
    return generator();
}
} // namespace ChessPlatform
