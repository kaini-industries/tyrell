#pragma once

// Test-only native syscall fixture. Link using tyrell_game_attach_native_test_io.
// The implementation includes Linux headers; this API does not leak KEY macros
// into a controller TU that uses raylib's different key-code values.
#include "tyrell/game/runtime.hpp"
#include <cstdint>
#include <functional>
#include <vector>

namespace tyrell::game::test {
struct NativeIoStats {
    unsigned commits=0,reads=0,key_opens=0,key_closes=0,fb_closes=0,unmaps=0,open_keyboards=0;
};
void reset_native_fixture();
RuntimeOptions native_fixture_options();
void write_foreground_pixels(std::uint16_t color);
bool foreground_pixels_equal(std::uint16_t color);
NativeIoStats native_io_stats();
// Key/sync arguments use Linux numeric evdev codes, not raylib key values.
void queue_native_key(unsigned short code,int value);
void queue_native_sync(unsigned short code);
void set_native_held(const std::vector<unsigned short>& held);
void queue_native_resume_key(unsigned short code,int value);
// Optional nonzero color simulates a new owner drawing during failed setup.
void fail_native_keyboard_open(bool fail,std::uint16_t overwrite_color=0);
void fail_native_keyboard_resync(bool fail);
void fail_native_commit(bool fail);
// Raise once on the GUI thread during its next synthetic keyboard read.
void signal_on_native_read(int signal);
// Run once on the GUI thread during the next synthetic keyboard read. This
// lets integrated tests deliver shell traffic while Runtime::poll is active.
void on_next_native_read(std::function<void()> callback);
// Observe the next synthetic keyboard close during ownership release.
void on_next_native_keyboard_close(std::function<void()> callback);
} // namespace tyrell::game::test
