# Tyrell Status

Small read-only status application for the CardputerZero's 320 × 170 display.
It shows uptime, used/total memory, battery status and network link state.
It makes no network requests, invokes no shell commands, and needs no root
privileges. Missing or malformed telemetry remains unknown.

Current source is **0.1.2**. The retained 0.1.0 package below keeps its existing
provenance; it does not contain the experimental shell integration.

The verified [0.1.0 ARM64 package](../../dist/tyrell-status/0.1.0/tyrell-status_0.1.0-1_arm64.deb)
is committed in the repository, with [checksums and provenance](../../dist/README.md).
From the repository root on the device, install it with
`sudo apt install ./dist/tyrell-status/0.1.0/tyrell-status_0.1.0-1_arm64.deb`.

The normal UI uses the stock `/dev/fb_lcd` framebuffer and opens the stable
`/dev/input/cardputer-zero-internal` keyboard link read-only, with a TCA8418
device-name fallback. It does not grab the keyboard exclusively or change
boot or persistent display configuration. Existing video/input group permissions are required.
Keys: **1 / left / Z** for System, **2 / right / C** for Network, **R** to refresh,
and **ESC / Q** to quit. Network state is local link state, not proof of Internet
access. At most three interfaces fit on the network page; JSON includes all.

```sh
tyrell-status --json
tyrell-status --version
tyrell-status --headless-screenshot status.ppm --page system --frames 2
tyrell-status --headless-screenshot network.ppm --page network --frames 2
```

`--json`, `--version`, `--help` and headless previews never open framebuffer or
input devices. `--proc-root` and `--sys-root` support deterministic offline
fixtures. The headless screenshot is a real LVGL software-rendered PPM image.
An optional SDL desktop simulator is available with `TYRELL_ENABLE_SDL=ON` and
`--simulator`; SDL is disabled in the device package to avoid extra dependencies.

The native GUI uses an [event-driven idle loop](../../docs/status-idle-loop.md):
it waits for keyboard/shell/shutdown events and telemetry/LVGL deadlines rather
than waking every 10 ms. Telemetry still refreshes once per second. Hidden
shell-linked instances stop input, telemetry and rendering until explicit resume.

## Experimental Gh0st pilot

`TYRELL_ENABLE_SHELL_LINK=ON` adds the optional `Tyrell::ShellLink` library on
Linux. It is **off by default** and has no Gh0st runtime dependency. With no
valid inherited `GH0ST_SHELL_FD`, the normal M5 launch path remains available.
JSON, help/version, headless previews and SDL never adopt native shell IPC.
The supported subset and pinned upstream source are described in
[the shell-link contract](../../docs/shell-link-status.md).

For a valid native link, Status waits for protocol-v1 `hello` before touching
devices. A queued startup `pause` also defers device initialization. Missing
hello exits after a nominal 100 ms wait; this is not a hard real-time guarantee.
The shell's `LV_LINUX_FBDEV_DEVICE` and `LV_LINUX_KEYBOARD_DEVICE` select devices
only for valid linked launches, with explicit CLI paths taking priority.

Pause stops the single GUI loop's rendering and input processing, closes the
evdev client and clears key state before queuing `paused`. Resume opens a fresh
client, discards pending edges, baselines held keys and redraws the full frame.
Held keys need a release before acting; `SYN_DROPPED` similarly requires a fresh
kernel key-state snapshot. Input and link processing use finite batches so a
busy input queue does not indefinitely postpone the next lifecycle check.

Link loss exits in either foreground or hidden state, with input released and
no further drawing. Hidden redraw is deferred until explicit resume. The
pinned LVGL framebuffer destructor only unmaps/closes/frees; it does not clear
or restore framebuffer pixels. Status starts no workers or helper processes.
Linux device open/ioctl, telemetry reads and scheduling can still delay the
loop, so passing fake-shell checks does not establish Gh0st's approximately
100 ms pause deadline on hardware. The shell may stop the app on timeout.

Build the pilot with `-DTYRELL_ENABLE_SHELL_LINK=ON` added to the CMake command
below. Tests cover the real GUI loop with a memory-backed LVGL display and a
scripted fake shell, plus key-state and lifecycle contracts. This integration
remains **experimental** until separate M5 and pinned Gh0st physical acceptance;
the file-image and package checks do not establish seat or display ownership.

Build with CMake, C/C++ compilers and an explicitly supplied, verified LVGL
source directory. CMake performs no downloads:

```sh
cmake -S packages/tyrell-status -B build/status -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DLVGL_SOURCE_DIR=/path/to/verified/lvgl \
  -DTYRELL_BUILD_REVISION=your-source-revision
cmake --build build/status --parallel 4
ctest --test-dir build/status --output-on-failure
cpack --config build/status/CPackConfig.cmake -G DEB -B artifacts
```

Linux packaging requires native Debian ARM64. CPack discovers runtime library
dependencies with `dpkg-shlibdeps`. The package installs only its binary,
launcher/desktop entries, unique icon and documentation. It contains no
maintainer scripts, services, autostart entries, kernel modules, boot assets or
configuration replacements. Building it does not install anything on the
CardputerZero; physical device testing belongs on a development microSD card.
