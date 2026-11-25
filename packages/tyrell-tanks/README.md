# Sandline / Tyrell Tanks

A small native C++17 / raylib / ENet tank arena prototype for desktop development
of a CardputerZero game. All geometry and colors are generated in code. The
desktop game renders to 320 × 170 and scales with nearest-neighbor sampling.
It consumes the shared [Tyrell Game Framework](../tyrell-game/README.md) for
runtime, rendering helpers, input, and network transport; its simulation and
packet schema remain game-specific.

Current source is 0.1.1. Its optional native offline shell policy defaults off;
the new package has not received physical M5/Gh0st acceptance. Earlier device
records apply only to their exact tested bytes.

## Build on macOS or Linux

From the repository root, with CMake 3.25+, a C/C++ toolchain, Python 3.12+, and
curl installed:

```sh
python3 scripts/tanks deps
python3 scripts/tanks build
python3 scripts/tanks test
python3 scripts/tanks run
```

Dependencies are downloaded only by `deps`. Their archives and extracted source
files are checked against `locks/game-build-inputs.json` before compiling.
Downloads, build outputs, and screenshots remain in ignored local directories.
The Apple desktop build uses bundled GLFW/Cocoa/OpenGL. A Linux desktop build
also needs GLFW's development dependencies (for Debian: `libx11-dev`,
`libxrandr-dev`, `libxinerama-dev`, `libxcursor-dev`, `libxi-dev`,
`libgl1-mesa-dev`, `libwayland-dev`, `libxkbcommon-dev`, and `wayland-protocols`).
The Linux benchmark-only build does not need a desktop or X11.

## Play over a LAN

Choose **Host LAN match** on one computer. On the other computer choose **Join
LAN match**, enter the host's LAN address, and press Enter. Both computers need
the same build and network access to UDP port 28770. Each participant controls
one tank. Four player slots include the host; scores reset for a new session.

Equivalent commands, in separate terminals or computers:

```sh
python3 scripts/tanks run --host
python3 scripts/tanks run --join 127.0.0.1
```

Replace `127.0.0.1` with the host's LAN address on another computer. `--port PORT`
changes the UDP port on both sides. The game does not configure firewalls,
discover hosts, authenticate players, or provide internet matchmaking.

| Action | Keys |
| --- | --- |
| Drive forward / reverse | W / S |
| Turn hull left / right | A / D |
| Turn turret left / right | Q / E |
| Fire | Space |
| Scoreboard | Hold Tab |
| Return to menu / quit menu | Escape |
| Navigate menu | W/S or arrows, then Enter; 1/2/3 select a row |

Four hits destroy a tank; it respawns after three seconds. Shells stop on cover
and arena walls. The driving range is a solo control practice mode without AI.
Turret rotation is independent of hull rotation.

## Architecture and current limits

`world.cpp` is a graphics-free simulation shared by offline play and the host.
The host runs it at 60 Hz and publishes snapshots at 20 Hz. Clients send controls
and receive validated, bounded binary packets; they cannot set position, damage,
or score. Handshakes and movement use independent ENet channels. Inputs expire
when a client stops sending; clients leave a frozen match after three seconds
without a newer snapshot. Remote tanks interpolate between snapshots.

Local client movement currently follows authoritative snapshots. Client-side
prediction/reconciliation, audio, AI, round limits, and map selection are future
work. Desktop exit and session switching are implemented. The native-LCD build
uses raylib software rendering, the Linux keyboard, and
30 Hz pacing with one framebuffer commit per finished frame. It is available as
an ARM64 APPLaunch package named **Sandline Tanks**.

`--server --seconds 60` runs without a window for testing. This prototype reserves
the host's tank/slot, so it accepts three remote clients. `--autoplay`, `--frames`,
`--seconds`, `--demo`, and `--screenshot FILE.png` support bounded visual and
two-process smoke checks. `--help` lists the complete CLI.

## Native CardputerZero package

On Debian ARM64, after fetching the pinned dependencies:

```sh
cmake -S packages/tyrell-tanks -B build/tanks-native \
  -DCMAKE_BUILD_TYPE=Release -DTANKS_BUILD_DESKTOP=OFF \
  -DTANKS_BUILD_NATIVE=ON -DTANKS_BUILD_BENCHMARK=OFF \
  -DTYRELL_ENABLE_SHELL_LINK=OFF \
  -DRAYLIB_SOURCE_DIR="$PWD/build/game-deps/raylib-6.2" \
  -DENET_SOURCE_DIR="$PWD/build/game-deps/enet-1.3.18"
cmake --build build/tanks-native --parallel 4
ctest --test-dir build/tanks-native --output-on-failure
(cd build/tanks-native && cpack -G DEB)
```

This builds `tyrell-tanks_0.1.1-1_arm64.deb` with shell support disabled and the
existing **Sandline Tanks** APPLaunch entry. Physical acceptance of those new
bytes remains pending. The native controls keep the existing mapping:
W/S navigates the menu without modifier keys, and Enter selects. Escape returns
to the game menu, then exits to APPLaunch. `--headless` renders without opening device nodes for automated
tests; `--framebuffer` and `--input` override automatic native device discovery.

## Experimental offline shell pilot

The [offline lifecycle guide](../../docs/sandline-offline-lifecycle.md) describes
the policy, tests and qualification boundary. The pilot supports Driving Range,
the offline menu and demo. A linked launch refuses `--host`, `--join` and
`--server` before networking or native device acquisition, and LAN menu choices
are unavailable. Unlinked LAN remains unchanged.

On Debian ARM64, use the explicit helper flag and its separate build directory:

```sh
python3 scripts/game deps --project tanks --backend native
python3 scripts/game build --project tanks --backend native --shell-link
python3 scripts/game test --project tanks --backend native --shell-link
```

The pilot uses `build/tanks-native-shell`; normal native builds use
`build/tanks-native`. Direct CMake builds may add
`-DTYRELL_ENABLE_SHELL_LINK=ON` to the native flags above with a separate output
directory. The helper accepts `--shell-link` only for native Sandline. Desktop
and headless entry points do not adopt shell endpoints.

Pause releases input/render ownership while keeping practice state in RAM.
Resume clears stale input, suppresses held keys and drops only the first practice
simulation interval. Shared Runtime elapsed time and unlinked network expiry stay truthful.
Link loss or failed resume exits without restoring saved pixels. Explicit CLI
device paths, including `auto`, override linked shell device environment paths.
Linked screenshots are disabled; reports require a permitted foreground exit.

Six headless and twelve native controlled lifecycle cases passed on Linux.
These checks do not establish a physical pause deadline or device compatibility;
physical testing remains pending. No Store approval or default Zero inclusion
is added by this pilot.

## CardputerZero benchmark

The independent software and GLES2 executables render the same 854-triangle
scene. They report rendering, readback, RGB565 conversion, framebuffer copy,
fsync, and frame pacing separately. See the
[benchmark guide](../../docs/tanks-display-benchmark.md) for native ARM64 builds,
device runs, restoration behavior, and measurement limitations.

This is the Linux CM0 CardputerZero, not the ESP32 Cardputer/ADV. Neither Arduino
nor ESP-NOW is used. No kernel, firmware, launcher, or boot configuration change
is required.

## Validation

CTest exercises movement/collision, swept-shell hits, cooldowns, scoring and
respawn, real UDP replication with three clients, reconnects, stale controls,
abrupt server loss, malformed packets, and the benchmark's rendered image/report.
The network test binds loopback UDP sockets and needs a runtime that permits it.
Graphical screenshots are separate smoke checks, since CTest need not own a
desktop display. Recorded validation and hardware measurements are in
[`docs/tanks-validation.md`](../../docs/tanks-validation.md).
Native installation and real-device checks are recorded in
[installation validation](../../docs/tanks-install-validation.md).

Original code uses the repository MIT license. See [THIRD_PARTY.md](THIRD_PARTY.md).
