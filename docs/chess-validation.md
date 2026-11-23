# Tyrell Chess port validation

This records the original 0.1.0 port. See the [0.1.1 UI/input update](chess-ui-input-validation.md) for the currently installed version.

Completed November 15, 2026. Tyrell Chess 0.1.0 is installed on the network
Cardputer Zero's existing Debian ARM64 installation. It is available through
stock APPLaunch. No firmware, controller, kernel or launcher replacement was
performed.

## Exact installed artifact

- Package: `tyrell-chess 0.1.0-1`, ARM64.
- SHA-256: `d04b6b77b3553b1b9d75cca5ef522eec235ebe40cd3a3087884fde35d26874d3`.
- [Retained package and provenance](../dist/tyrell-chess/0.1.0/README.md).
- Upstream engine: `kaini-industries/cardputer-chess` commit
  `64419e29c828b77b420221472e8a314778713a07` with retained MIT attribution,
  original-file hashes and documented adaptations.

The source-input manifest matches the final source files. The isolated Debian
ARM64 builder ran all **11 CTest groups**, four packaging-tool regression tests,
exact package payload/dependency/license checks, and installation, CLI execution,
integrity and removal checks. All passed. The builder was restored to stopped.

## Automated and adversarial coverage

Twelve specialists contributed implementation and adversarial review rounds.
Concrete findings were reproduced, fixed and covered with regressions:

- Exact legal move counts: initial position depth 4 = 197,281; Kiwipete depth 3
  = 97,862. Move reversal, castling, pinned en passant, all promotions, mate,
  stalemate, repetition, material, every embedded puzzle and all 960 starts.
- Independent pinned `chess==1.11.2` oracle: **87,663** complete move-set and
  per-side mating-material comparisons, plus **13,914** SAN/resulting-FEN
  comparisons. All matched. Includes Standard games, every Chess960 start,
  stripped castling geometry and attacked castling routes. Reproducible optional
  sources are in [tests/oracle](../packages/tyrell-chess/tests/oracle/README.md).
- Two knights versus a lone king no longer causes a false automatic draw. A
  constructive legal Nf7# position verifies that mate remains possible.
  Material-aware timeout and resignation follow the possible-mate criterion
  in [FIDE articles 5.1.2, 5.2.2 and 6.9](https://handbook.fide.com/chapter/E012023).
- Ambiguous Chess960 king/castle destinations have explicit UI choices and
  unambiguous `c`-suffixed save/network tokens. A full legal position-12 game
  roundtrips through disk and real TCP while preserving king and rook placement.
- Checked save/load rejects corruption, truncation, illegal moves, inconsistent
  clocks and contradictory results transactionally. Storage-failure fixtures
  verify visible errors, retry throttling, blocked destructive navigation,
  damaged-save preservation and idempotent archive recovery across restart.
- Puzzles and archive replay preserve the active offline game. Review hides
  future moves. Black orientation/cursor restore, confirmations and PGN setup
  metadata are tested. Automation requires a nonempty explicit state directory.
- One asynchronous AI worker; stale generations cannot alter another game or
  Home. Help and game overlays do not stop the AI while its clock runs.
  Resignation uses the human's color. Completed timed games cannot restart their
  clocks on Resume or become invalid after a flag deadline passes.
- Real-loopback TCP tests cover both roles, pairing, timed/untimed games,
  checkmate, timeout, resignation, draw offers in either direction, crossed and
  expired acceptances, pending duplicate input, bad turns/sequences, disconnect,
  final messages at EOF, and a legal request crossing flag fall.
- The framework now exposes ordered key events. Two Down taps followed by Enter
  within one poll remain exactly Down, Down, Enter. Duplicate makes/autorepeat
  do not repeat confirmations; queue bounds and dropped-event resync are tested.
  Existing framework pressed/held behavior remains unchanged; all six framework
  CTests also passed.

The 11 chess groups are engine, model, transport, controller, CLI, rendering,
lifecycle, protocol adversarial, fault handling, completed-clock faults and
keyboard faults. Engine/model sanitizer checks also passed during development.

## Real Cardputer Zero observations

Native testing used the actual 320 × 170 RGB565 LCD, 640-byte stride, and the
existing TCA8418 evdev keyboard device. Inputs were synthetic EV_KEY events
through that real driver, not a display-free simulation.

| Check | Observed result |
| --- | --- |
| Stock launcher integration | Chess selected and launched through APPLaunch; actual process ancestry includes the original launcher |
| Rapid menu input | Correct Two players/Start selection after replacing per-frame booleans with ordered events |
| Board play | Native screen and saved history verified `e2e4 e7e5` |
| Alternate keys | Punctuation and WASD navigation, Space destination cycling and Enter move confirmation |
| Cancel | Selected knight move canceled with Backspace, without adding a ply |
| Display | Pixel pieces, coordinates, clocks, SAN and legal destinations fit the native panel |
| Preferences | High contrast and board flip visually verified on the real framebuffer |
| Replay/undo/resume | Review at ply 1 showed only e4; undo removed e5; relaunch resumed the saved one-ply e4 game |
| Computer mode | Final package played White's e4 while the human was Black; Black orientation, own-piece cursor and Help were visually verified |
| Wi-Fi host | Independent Python peer on the development computer paired, exchanged e4/e5, observed 3+2 clock increments, offered a draw and received the agreed result |
| Wi-Fi join | IP typed through the real ordered keyboard path; Zero paired as Black, received e4, played e5, accepted a draw and exited normally |
| Resource snapshot | Native process RSS roughly 5–5.5 MiB; this is a snapshot, not a long-run benchmark |
| Exit | Q returned to the same active APPLaunch PID 1065, original start time, NRestarts = 0 |

Detailed ordered-input/LAN tests used the preceding reviewed builds; the final
installed package additionally received live computer-mode, Help, save and exit
smoke checks. Its changes after those LAN tests are covered by the expanded
ARM64 completed-clock, archive and IP-menu regression groups. There was one
physical Zero; the other network participant was an independent software peer.

The initial device pass exposed real event reordering that headless controller
calls had not reproduced. Framebuffer evidence and saved moves verified the
ordered-event fix. A separate live pass caught opponent moves stealing the
joining player's cursor; the fix was verified with both controller and native
network play. Test-harness navigation/alias errors were corrected and successful
runs were retained separately from failed attempts.

## Preservation and limits

The final before/after comparison found exactly one added package:
`tyrell-chess`. All 12 selected protected file hashes matched, the kernel was
unchanged, and APPLaunch retained its PID/start time/restart count. Package
integrity verification and `dpkg --audit` were clean. This compares the selected
files/package inventory, not every byte of the device filesystem.

Physical keyboard switches were not pressed; the real driver path was exercised
with injected events. Cold boot, battery transitions, prolonged play and a second
physical Zero were not tested. Native promotion and every Chess960 layout were
covered by automated controller/rules tests rather than physically pressing each
combination. Network play uses trusted private-LAN TCP and has no cryptographic
peer authentication or Advance ESP-NOW interoperability. Multi-profile UI and
importing Advance NVS data are not part of this port.

Raw framebuffer captures, per-device inventory, event timings and software-peer
logs are retained locally under ignored `device-results/` and on the device's
`/var/tmp/tyrell-chess-install/`. Credentials and private network details are not
included in release artifacts. The remaining resumable computer game is the
recorded Black-player test position after White's e4.
