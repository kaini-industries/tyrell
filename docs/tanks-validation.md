# Sandline prototype validation

November 14, 2026. This records the desktop game and independent CardputerZero
display benchmark milestone. The subsequent native LCD game and APPLaunch
package are covered in [installation validation](tanks-install-validation.md).

## Implemented

- C++17 simulation with four player slots, eight cover blocks, arena/tank
  collision, independent turrets, swept-shell collisions, damage, scoring,
  firing cooldown and respawn.
- raylib 6.2 desktop renderer, generated low-poly models, 320 × 170 render
  texture, nearest-neighbor scaling, menu, LAN host/join, armor/reload HUD,
  scoreboard, and exit/session transitions.
- ENet 1.3.18 authoritative 60 Hz host, 20 Hz snapshots, bounded explicit binary
  serialization, version handshake, stable slots, stale input handling,
  remote interpolation and three-second client snapshot timeout.
- Separate software/GLES2 benchmark executables with stage timings, native
  framebuffer output/commit/restoration, renderer identification, and bounded
  device-run orchestration.

## Host and ARM64 checks

The desktop build succeeded on macOS ARM64 with Apple Clang 21. All **five
desktop CTests** passed, covering simulation, real UDP networking, CLI help,
benchmark image/report validation, and invalid benchmark options. The
simulation also passed standalone optimized Release and ASan/UBSan checks.

The Linux build succeeded in the existing isolated Debian ARM64 Lima builder
with GCC 14.2. Both software and EGL/GLES2 targets linked. All **four ARM64
CTests** passed; the desktop-only help test is excluded from this build.

Real UDP tests exercised a host and three clients, authoritative movement,
remote interpolation, disconnect/reconnect and slot reuse, malformed packets,
nonfinite/out-of-range data, protocol mismatch, stale controls, and abrupt
server loss. A separate desktop process test joined the headless host over
loopback, rendered 180 frames with scripted driving/aiming/firing, and exited
successfully as Player 2. Both processes returned zero. This establishes
same-host networking; play between two physical computers is still pending.

Menu, four-tank overview, and joined gameplay screenshots were captured from
the running raylib application and visually inspected. A review caught and
fixed render-texture health-bar projection and aligned tank model dimensions
with collision clearance. Physical keyboard play and subjective latency are
still untested. Local client prediction, audio, AI, discovery, and internet
services are outside this first prototype.

Dependency archives and extracted source files are verified against the lock
before builds. A separate integrity check confirmed a valid extracted source
passes and a modified cached file fails. CMake never downloads dependencies.
Python tools compile, and Git whitespace checks pass.

## Physical Zero display results

Both paced rendering paths delivered approximately 30 SPI full-frame
equivalents/s for the representative scene. The unpaced transfer ceiling was
approximately 38.4–38.5 equivalents/s. Ordinary deferred mapped writes delivered
approximately 15 equivalents/s despite the app producing 30 frames/s.

The GPU identified itself as VC4 V3D 2.1. Software had the lower total frame
work time in this small scene; GLES2's faster drawing was outweighed by pixel
readback. Use the software path as the baseline for the next native game port.
See the [full measurements and limits](tanks-display-benchmark.md) and
[curated JSON](tanks-benchmark-results.json).

The runs used temporary executables under `/tmp`. Before testing, the original
launcher was the only observed framebuffer owner. After each series its PID,
active state, and restart count were unchanged; it was resumed, and the original
visible framebuffer was restored byte-for-byte. No package installation,
firmware, kernel, driver, SPI rate, boot, or launcher configuration change was
performed. These short tests do not establish complete-game performance,
physical panel vsync, tearing behavior, sustained thermals, or battery life.

Local screenshots and raw evidence remain ignored under `artifacts/` and
`device-results/tanks/`. The Linux builder was used only for native compilation
and tests; credentials were not included in its source bundle.
