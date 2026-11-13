# Tyrell Status

Small read-only status application for the CardputerZero's 320 × 170 display.
It shows uptime, used/total memory, battery status and network link state.
It makes no network requests, invokes no shell commands, and needs no root
privileges. Missing or malformed telemetry remains unknown.

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
