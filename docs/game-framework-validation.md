# Game framework extraction validation

Validated on November 14, 2026. Three fresh agents worked on the runtime,
transport, and independent consumer, with cross-review of their interfaces.
The reusable code is in `packages/tyrell-game/`; Sandline and Beacon Arena both
consume its public targets. Public headers and implementation have no tank
simulation or protocol includes.

## Build and test results

All entries below passed using the pinned raylib 6.2 and ENet 1.3.18 inputs in
`locks/game-build-inputs.json`.

| Environment / configuration | Result |
| --- | --- |
| Apple Silicon macOS, framework DESKTOP | Build and 4 CTests |
| Apple Silicon macOS, framework HEADLESS | Build and 6 CTests |
| Apple Silicon macOS, network-only | Build and 2 CTests, without raylib |
| Apple Silicon macOS, core-only | Build and 1 CTest, without dependencies |
| Apple Silicon macOS, Sandline DESKTOP | Build and 8 CTests |
| Apple Silicon macOS, Sandline HEADLESS | Build and 12 CTests |
| Debian ARM64 builder, framework NATIVE | Build and 6 CTests |
| Debian ARM64 builder, Sandline NATIVE | Build and 10 CTests |
| Debian ARM64 native Sandline package | CPack and package payload/license validation |
| External CMake consumer, HEADLESS | Linked all public modules and ran successfully; parent `BUILD_SHARED_LIBS=ON` preserved |
| Python build-tool regression suite | 8 tests |

The subsequent [fresh adversarial review](game-framework-adversarial-review.md)
records the defects fixed after extraction and the final sanitizer/build matrix.

The timing tests cover bounded catch-up, fractional remainder, invalid inputs,
and extreme arithmetic. Memory-renderer checks cover per-face lighting, depth
occlusion, logical projection, invalid cameras/geometry, scope nesting, and
runtime frame/context ownership. Input checks cover modifiers, repeat events,
key edges, held state, and dropped-event resynchronization.

Real UDP tests cover reliable control messages, sequenced snapshots, multiple
peers, reconnects, packet bounds, disconnects, and codec failure behavior.
Sandline's existing version-1 `TYLT` wire format and 1,192-byte snapshots were
preserved. Its protocol suite still covers malformed packets, stale updates,
authoritative simulation, and peer-to-player assignment. Both framework and
Sandline network suites also passed AddressSanitizer/UndefinedBehaviorSanitizer.

## Consumer and visual checks

Beacon Arena has an independent simulation and `TYBC` packet schema. Its tests
cover host authority, bad versions, trailing bytes, nonfinite values, incomplete
handshakes, stale state, and disconnects. Separate host/client processes reached
two participants and replicated 2.30209 units of authoritative client movement.

Actual macOS desktop runs rendered Beacon Arena and Sandline successfully.
Their 320 × 170 logical screenshots were inspected after extraction:

- `artifacts/framework-beacon-desktop.png`
- `artifacts/sandline-framework-desktop.png`

Additional ignored local evidence includes Beacon Arena host/client JSON
reports, memory-rendered screenshots, and CTest logs in `build/game-*` and
`build/tanks-*`.

## Hardware boundary

The ARM64 native checks ran in the Debian builder, using memory rendering for
automated tests. The temporary validation package was not installed or
published. No physical CardputerZero files, running application, firmware,
launcher, services, or boot configuration were changed during this extraction.

The [earlier device installation](tanks-install-validation.md) and
[display benchmark](tanks-display-benchmark.md) apply to their recorded binaries.
They do not qualify newly built framework binaries for physical input, LCD
transfer performance, panel tearing, thermals, or battery life. A future device
deployment needs its own hardware acceptance record.
