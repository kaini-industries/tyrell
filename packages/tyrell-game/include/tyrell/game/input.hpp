#pragma once

#include <array>
#include <cstdint>
#include <deque>
#include <string>
#include <vector>

namespace tyrell::game {

// Device-independent evdev decoding. Event arguments are Linux numeric codes;
// queries use raylib key values. All methods are used on the runtime thread.
// A dropped kernel event stream clears controls until resync() supplies the
// current held-key snapshot; that snapshot never synthesizes press edges.
class NativeInputState {
public:
    void reset() noexcept;
    void begin_frame();
    void feed(std::uint16_t type, std::uint16_t code, std::int32_t value);
    // Resume can suppress held keys until their physical release. Ordinary
    // kernel resync preserves an existing suppression but keeps its old held
    // state semantics for keys that were not suppressed.
    void resync(const std::vector<unsigned short>& held_codes, bool caps_lock=false,
                bool suppress_held=false);
    bool sync_needed() const;
    bool key_down(int key) const;
    bool key_pressed(int key) const;
    bool key_released(int key) const;
    // Rising edges in evdev order, including repeated taps between frames.
    // Does not include Linux autorepeat or synthesize edges during resync.
    int next_key();
    int next_char();
private:
    std::array<bool,512> down_{}, pressed_{}, released_{}, suppressed_{};
    std::deque<int> keys_, chars_;
    bool caps_=false, syncing_=false;
    static int map_key(unsigned short code);
    int ascii(int key) const;
};

} // namespace tyrell::game
