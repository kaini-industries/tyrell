# Game framework adversarial review

Reviewed on November 14, 2026 with three fresh agents covering runtime/rendering,
networking, and build integration. The primary agent reviewed the consumer game
loop, CLI behavior, fixes, and combined validation. This review examined the
source, exercised failure cases, and added regressions for confirmed defects.

## Findings and fixes

| Priority | Confirmed failure | Fix and regression evidence |
| --- | --- | --- |
| P2 | Finite camera axes could underflow normalization or overflow view translation. Positive denormal fields of view produced infinite projection scales in both projection modes. | Validate the actual view basis and float projection arithmetic, viewport aspect, and clipping planes before modifying graphics state. Tests reject tiny axes/FOVs, overflowing translations, invalid clip planes, and singular extreme-aspect projections while preserving the previous matrix. |
| P2 | Excessive transform nesting exhausted raylib's fixed matrix stack; unmatched pops then corrupted outer state. Beginning a camera inside a transform also replaced matrices belonging to that transform. | Bound framework transforms to 30 levels, reserve stack capacity for the camera/drawing helper, and require cameras outside transforms. Tests verify rejection and restoration of the outer transform. Raw rlgl stack operations remain the caller's responsibility. |
| P2 | Main-keyboard and keypad digits/Enter shared held-key slots. Releasing either could clear the other key. | Preserve distinct raylib keypad identities while retaining printable characters. The simultaneous-key regression failed before the fix. |
| P2 | Sandline advanced connection/handshake/input ages with simulation-clamped time. A long frame could leave a stale world playable. Its main loop also discarded elapsed time before calling the adapter. | Pass full runtime elapsed time and separate wall-time expiry from bounded simulation catch-up. Raw-server tests reproduce stale-state retention and held-input expiry across long frames. The wire schema is unchanged. |
| P2 | The pinned-source verifier accepted extra extracted files, allowing an injected header to shadow a system include without changing archive-member hashes. | Require an exact extracted tree and reject replaced source directories. Python regressions cover added headers, symlinked directories, and changed source contents. |
| P2 | Benchmark-only Sandline configurations unnecessarily enabled GLFW and the desktop runtime, breaking Linux builders without desktop dependencies. | Enable runtime/render targets only when a game backend is selected. The benchmark-only configuration now builds and runs without desktop targets. |
| P2 | Explicit `TYRELL_GAME_BUILD_TESTS=ON` with `BUILD_TESTING=OFF` built test binaries but registered zero CTests. | Explicitly enable testing when framework tests are requested. A configure/build/CTest regression now finds and runs the core test. |
| P2 | Sandline accepted `--server --report FILE` and exited successfully without writing the report. | Write the server's final simulation report. A real two-process CLI test verifies the report, a joined peer, and stale-client cleanup after pausing both processes. |
| P3 | Editing the shared license-notice collector did not trigger native package reconfiguration. | Add the shared collector to CMake's configuration dependencies. Native package notice/payload validation passes. |

No confirmed finding remains open. The transport/codec and Beacon protocol
review found no additional confirmed ownership, queue-bound, authority, or
sequence-handling defect.

## Final validation

| Configuration | Result |
| --- | --- |
| macOS framework DESKTOP | Build; 4/4 CTests |
| macOS framework HEADLESS | Build; 6/6 CTests |
| macOS framework HEADLESS, ASan + UBSan including dependencies | Build; 6/6 CTests |
| macOS Sandline DESKTOP | Build; 8/8 CTests |
| macOS Sandline HEADLESS | Build; 12/12 CTests |
| Debian ARM64 framework NATIVE | Fresh build; 6/6 CTests |
| Debian ARM64 Sandline NATIVE | Fresh build; 10/10 CTests |
| Native ARM64 Debian package | CPack; exact payload, ELF, dependencies, launcher/icon, and upstream license checks |
| Sandline network suite, ASan + UBSan | Passed, including the new long-frame regressions |
| Benchmark-only configuration | Build; 6/6 CTests |
| Explicit tests with `BUILD_TESTING=OFF` | Build; 1/1 CTest |
| Python game-tool regressions | 8/8 tests |

Actual macOS desktop runs of Beacon Arena and Sandline exported logical
320 × 170 images, which were visually inspected. Local screenshots are under
ignored `artifacts/framework-review/`. CTest logs and the ARM64 source snapshot
remain in ignored build/artifact directories. The temporary ARM64 builder was
stopped after validation.

This review did not access or update the physical CardputerZero. Native tests
rendered to memory inside the ARM64 builder; physical keyboard, panel, thermal,
and battery acceptance remain separate hardware work. The framework's
[documented networking and rendering limits](../packages/tyrell-game/README.md)
still apply.
