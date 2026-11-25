# Tyrell Game Framework

Reusable C++17 building blocks for small 3D multiplayer games in Tyrell. It
extracts the working desktop and CardputerZero paths from Sandline into public
headers under `include/tyrell/game/`. Sandline remains a consumer; **Beacon
Arena** is a second consumer with its own simulation and network schema.

| Target | Responsibility |
| --- | --- |
| `Tyrell::GameCore` | Bounded fixed-step simulation clock; no graphics or sockets |
| `Tyrell::GameRender` | Flat-lit 3D boxes, scoped transforms/cameras, logical-viewport projection |
| `Tyrell::GameRuntime` | Window/LCD ownership, input, frame lifecycle, pacing, screenshots |
| `Tyrell::GameNetwork` | Nonblocking ENet transport, peer IDs/events, bounded scalar codecs |

The framework has no tank, weapon, health, map, player-slot, or snapshot schema.
Games own those policies. This is an initial framework API; it is not a scene
editor, physics engine, matchmaking service, or complete replication engine.

## Build and run

From the Tyrell repository root:

```sh
python3 scripts/game deps
python3 scripts/game build --backend desktop
python3 scripts/game test --backend desktop
python3 scripts/game run --backend desktop
```

`deps` downloads the versions/checksums in `locks/game-build-inputs.json` into
ignored `build/game-deps/`. The `scripts/game` build workflow verifies both
archives and extracted files. Direct CMake callers supply their own verified
source trees. CMake itself never downloads dependencies.

The sample controls are **WASD** to move a beacon, **Space** to pulse, and
**Escape** to exit. Its default UDP port is **28771**, separate from Sandline.
Start a host and client in different terminals or computers:

```sh
python3 scripts/game run --backend desktop -- --host
python3 scripts/game run --backend desktop -- --join 127.0.0.1
```

Replace `127.0.0.1` with the host's reachable LAN address for two computers. A
bounded host that opens no graphics context is also available:

```sh
python3 scripts/game run --backend desktop -- --server --seconds 60 --report /tmp/beacons-host.json
```

The sample supports `--frames`, `--seconds`, `--autoplay`, `--screenshot`, and
`--report` for repeatable checks. `--headless` selects memory rendering in a
memory-renderer build; it cannot switch a desktop build's raylib backend.

Version 0.1.2 adds an optional native offline shell policy. Build and test it on
Linux with `python3 scripts/game build --backend native --shell-link` and
`python3 scripts/game test --backend native --shell-link`, after fetching native
dependencies. The helper uses `build/game-native-shell`, separate from the
default `build/game-native`; direct CMake builds use
`-DTYRELL_ENABLE_SHELL_LINK=ON`. The option defaults off and only integrates the
Beacon runtime example, leaving core/network-only consumers independent.

Linked host, join and server launches are rejected before networking or device
access. Offline state survives pause/resume in RAM; hidden iterations perform
no input, update or rendering. Only the first resumed simulation interval is
discarded; runtime and ordinary LAN elapsed time remain truthful. `--framebuffer`
and `--input` override the linked shell's device-path environment, including an
explicit `auto`. Linked screenshots are disabled and hidden/lost exits suppress
reports. See [policy and controlled-check boundaries](../../docs/beacon-offline-lifecycle.md).
This adds no package, launcher entry or physical qualification.

## Backends and smaller builds

The build selects one raylib backend. `Backend::Default` uses that selection:

| Build value | Use |
| --- | --- |
| `DESKTOP` | GLFW/OpenGL window; logical viewport scaled into a resizable window |
| `NATIVE` | CardputerZero Linux LCD and evdev keyboard; software rendering |
| `HEADLESS` | Software rendering to memory; no display/input devices |

`NATIVE` uses the existing 320 × 170 RGB565 panel with one framebuffer commit per
completed frame and 30 Hz default pacing. The display path and keyboard may be
overridden in `RuntimeOptions`; native geometry is checked rather than assumed.
`Backend::Headless` also works in a native software-renderer build for tests.
Desktop builds reject native/headless requests instead of silently using a
different renderer. Only one runtime/context may be active in a process.
The software backend supports the primitive rendering used by these examples;
desktop GLSL shaders are not portable to it.

The network and core modules do not depend on raylib or open device nodes.
For a socket-only server build, use `--backend network`; for timing-only code,
use `--backend core`. Native builds require Linux and the appropriate device
permissions; the headless backend works on macOS and Linux.

## Foreground lifecycle hooks

Version 0.1.1 adds main-thread ownership hooks without a shell dependency:

| API | Contract |
| --- | --- |
| `suspend()` | Cancel an open frame without presentation, release/reset native input, suppress saved-screen restoration while hidden |
| `resume(&error)` | Reopen input, discard queued edges, suppress currently held keys until release, then request a full redraw; failure retains suspended ownership |
| `abandon_foreground()` | Terminal ownership loss: close native descriptors without replaying the saved prelaunch frame; future resume fails |
| `request_redraw()` / `redraw_requested()` | Request a complete app-owned frame; the request clears after a successful `end_frame()` |

Construct with `RuntimeOptions::restore_framebuffer_on_close=false` when another
launcher owns screen restoration. This permission is fixed before native device
acquisition, covers failed construction, and stays disabled after resume. Normal
M5 callers retain the default foreground restoration behavior. Hidden or
abandoned cleanup never restores the saved frame, regardless of that default.

Suspension does not reset or clamp `time()` or `elapsed()`. The first poll after
a hidden gap reports the real elapsed interval; only pacing debt is discarded.
Games decide how to avoid simulation catch-up while retaining real elapsed time
for clocks and network expiry. Suspended input queries return no actions, and
drawing is rejected until an explicit successful resume.

These hooks contain no match, clock, save or network policy. Apps must establish
foreground ownership before resuming and finish a complete redraw themselves.
Filesystem/device open, ioctl and framebuffer commit calls can block; tests with
fake native syscalls establish ordering and cleanup, not a physical handoff
deadline. Desktop lifecycle smoke tests run on an isolated display, while native
fixtures exercise production Runtime/native code without opening device nodes.

Direct CMake configuration is useful for embedding or a Linux builder:

```sh
cmake -S packages/tyrell-game -B build/game-manual \
  -DCMAKE_BUILD_TYPE=Release -DTYRELL_GAME_BACKEND=HEADLESS \
  -DRAYLIB_SOURCE_DIR="$PWD/build/game-deps/raylib-6.2" \
  -DENET_SOURCE_DIR="$PWD/build/game-deps/enet-1.3.18"
cmake --build build/game-manual --parallel 4
ctest --test-dir build/game-manual --output-on-failure
```

`TYRELL_GAME_BUILD_RUNTIME=OFF` leaves core/network only.
`TYRELL_GAME_BUILD_NETWORK=OFF` omits ENet.
`TYRELL_GAME_BUILD_TESTS` and `TYRELL_GAME_BUILD_EXAMPLES` control tests and the
sample. The graphical sample requires both runtime and network; its independent
session/tests can also build with networking alone. Source directories must
point to the verified pinned dependency versions.

## Use from a game

Add the framework as a subdirectory after setting its backend and dependency
source paths, then link only the modules used by your executable:

```cmake
add_subdirectory(../tyrell-game tyrell-game)
add_executable(my-game main.cpp)
target_link_libraries(my-game PRIVATE
    Tyrell::GameRuntime Tyrell::GameRender Tyrell::GameNetwork)
```

These are source/build-tree targets. An installed SDK or CMake package export is
not provided yet.

Runtime and drawing remain ordinary C++ and raylib:

```cpp
#include <tyrell/game/runtime.hpp>
#include <tyrell/game/render.hpp>
#include <tyrell/game/fixed_step.hpp>

int main() {
    using namespace tyrell::game;
    Runtime app;
    FixedStepClock simulation;
    Vector3 position{0, 0.5f, 0};
    Camera3D camera{{8, 10, 12}, {0, 0, 0}, {0, 1, 0}, 16, CAMERA_ORTHOGRAPHIC};
    while (!app.closed()) {
        app.poll();
        if (app.pressed(KEY_ESCAPE)) break;
        const int steps = simulation.advance(app.elapsed());
        for (int i = 0; i < steps; ++i)
            position.x += app.down(KEY_D) * float(simulation.step_seconds()) * 2;
        app.begin_frame();
        ClearBackground({25, 34, 43, 255});
        {
            CameraPass view(camera);
            draw_box(position, {1, 1, 1}, {80, 180, 130, 255});
        }
        app.end_frame();
    }
}
```

`poll()` updates input edges and elapsed time. Drain `next_key()` for menu
actions whose order matters: it preserves separate rapid taps received between
display frames. Native key events exclude autorepeat and are frame-scoped,
bounded to 128 edges; held/pressed/released queries retain their existing
gameplay semantics. Call `begin_frame()` and
`end_frame()` once per rendered frame; the latter presents and applies pacing.
The runtime owns initialization and cleanup, so consumers do not call
`InitWindow`, `CloseWindow`, native framebuffer APIs, or their own frame swaps.
Raylib drawing calls are supported inside the frame. Scoped cameras/transforms
restore rendering state when their C++ scopes end. Supply logical width/height
to `world_to_screen`, not the scaled desktop window dimensions.
Begin a camera pass outside all transform scopes. Camera passes cannot nest;
transform scopes can nest up to 30 levels. Both scopes must end before ending
the frame. Raw rlgl matrix-stack operations remain the caller's responsibility.
Invalid or singular camera projections raise an exception.

See [the framework guide](../../docs/game-framework.md) for protocol ownership,
failure behavior, and the extraction boundaries. Beacon Arena's
`examples/beacon_session.cpp` shows a complete independent protocol adapter.

## Validation and limits

Tests cover the timing clock, renderer/input/runtime behavior, binary codecs,
real UDP transport, and Beacon Arena's own authority/validation rules. The
sample's network test binds loopback UDP sockets. Sandline retains its
game-specific regression suite, including malformed packets and reconnects.
Headless rendering checks do not establish physical LCD performance. The
original device benchmark and installed-game results remain historical evidence
for those exact earlier binaries, not a claim about a newly built framework.
See the [framework validation record](../../docs/game-framework-validation.md)
for the desktop, memory-rendering, ARM64, and consumer checks.
The [adversarial review](../../docs/game-framework-adversarial-review.md) records
the confirmed failures, fixes, and regression evidence.

The networking module provides reliable/unreliable delivery and peer events.
Polling and sending are nonblocking; resolving a hostname while connecting is
synchronous. Use numeric LAN addresses when that startup delay matters.
It does not provide encryption, authentication, NAT traversal, discovery,
matchmaking, or automatic state replication. Snapshot validation, game-version
handshakes, authority, interpolation, and stale-state policy belong to each
game. Beacon Arena implements a small authoritative example without prediction
or interpolation; Sandline retains its richer game adapter.

Tyrell's original framework code follows the repository MIT license. Raylib,
its bundled components, and ENet retain their upstream notices and licenses.
See [THIRD_PARTY.md](THIRD_PARTY.md).
