#pragma once

#include <chrono>
#include <cstdint>

// Move-choice variety only: search/book state already requires a single search
// thread. Avoid std::random_device initialization on a cancelable worker path.
// This is not a cryptographic source; Chess960 and pairing randomness are not
// changed by this adaptation.
namespace ChessSearchRandom {
inline std::uint32_t next() {
    static std::uint64_t state =
        (static_cast<std::uint64_t>(std::chrono::steady_clock::now().time_since_epoch().count()) ^
         UINT64_C(0x9e3779b97f4a7c15)) | 1;
    state ^= state >> 12;
    state ^= state << 25;
    state ^= state >> 27;
    return static_cast<std::uint32_t>((state * UINT64_C(0x2545f4914f6cdd1d)) >> 32);
}
} // namespace ChessSearchRandom
