# Chess 0.1.1 UI and keyboard validation

Installed and verified on the network Cardputer Zero on November 15, 2026.
This is a follow-up to the [0.1.0 port validation](chess-validation.md).
Four additional adversarial reviewers examined keyboard semantics, rendering,
menu/state transitions and completion overlays. Their reproduced findings were
fixed and retained as regression tests.

## Installed artifact

- `tyrell-chess 0.1.1-1`, ARM64.
- SHA-256: `c14cb5fee72c2d2123d7f78ececfa539dd3b8ce67cc9a8e8f60c8541d0616ca4`.
- [Exact package, source manifest and logs](../dist/tyrell-chess/0.1.1/README.md).
- All captured source hashes match this build. The 0.1.0 package is retained
  unchanged in its original version folder.

## Changes verified

| Problem reproduced | Result in 0.1.1 |
| --- | --- |
| Right on a selected knight could choose a target to the left | Imported upstream spatial navigation in displayed coordinates; works in either board orientation. Space separately cycles unique destinations. |
| Holding directions did nothing | App-controlled repeat begins after 350 ms, then runs at most once per frame with 100 ms spacing; no accumulated burst after a stall. Release and context changes stop it. |
| Confirmations could be confused with movement shortcuts | D always remains movement. Y opens an explicit draw confirmation whose default keeps playing. Stale offers cannot change a completed result. |
| Live LAN menu said Save despite disconnecting | Live matches show Leave match. Q and Leave both require confirmation; Escape or Stay restores the prior screen, menu row and selection. |
| Setup WASD/keypad behavior differed from arrow/Enter controls | A/D changes setup/preferences, W/S navigates, and keypad Enter shares the Enter action. |
| Puzzles inherited an empty/enemy cursor or old animation | New puzzles focus a movable own piece and clear transient animation. Wrong puzzle moves return focus to the source, ready to retry. |
| Completed/paused clocks looked active | Explicit clock/game state drives the highlight. Completed boards show the result and reason with menu/replay hints. |
| Promotion or confirmation stayed open after timeout | Completion dismisses obsolete action/Help screens, clears selection/candidates/repeat and shows the authoritative finished board. |
| Generic Help and narrow feedback obscured controls/errors | Help is contextual. Selection, completion and dialogs have specific hints. Feedback uses a readable full-width strip below the pieces. |
| Puzzle progress/settings writes silently failed | Checked atomic writes preserve the old file, show errors, retain pending changes and block ordinary exit/navigation until retry succeeds. |

## Automated verification

All **17 ARM64 CTest groups passed**, plus four packaging-tool tests. The exact
package passed payload, dependency, architecture and license checks, and was
installed, executed, integrity-checked and removed in the isolated ARM64 VM.
The builder returned to its previous stopped state.

The added UI tests verify spatial/flipped targets, promotion deduplication,
WASD/keypad parity, safe draw/leave actions, progress/settings failure recovery,
ordered event bursts, held repeat, release/context resets and no Enter repeat.
Pixel tests verify running, inactive, paused, replay and completed clock states,
and full-width feedback without covering board pieces or clocks. Overlay tests
use legal promotion/Chess960 sequences and cover timeout/remote completion and
cancel-origin restoration. Existing rules, AI, clock, save and real-loopback
network tests continue to pass.

## Real device verification

Tests ran through stock APPLaunch on the actual 320 × 170 RGB565 display and
TCA8418 evdev device. Key events were injected through that driver; physical
switches were not pressed.

- Held Right moved across the board to the expected file on the flipped Black
  view. A selected b8 knight then moved its cursor to a6 on displayed Right.
- Held Enter selected the knight without repeated actions. Backspace canceled
  selection and contextual Help displayed correctly.
- An independent computer on Wi-Fi paired with the Zero and exchanged e4/e5.
  With an incoming draw offer, D moved the cursor to f4 without accepting it.
  Y showed the confirmation; its default kept playing. Q showed Leave; Escape
  kept the connection alive. Explicitly confirming the draw produced the same
  result on the peer, inactive clocks and a clear completed-game display.
- The puzzle menu loaded the first puzzle with focus on its movable rook.
  An incorrect legal rook move left the board unchanged and returned focus to
  the rook. Retrying selection, the hint and puzzle-specific Help all worked.
- Q returned to the same APPLaunch PID 1065, original start time and zero
  restarts throughout the successful runs.

An initial live harness attempt revealed that the internal keyboard does not
advertise keypad Enter: Linux silently ignored that unsupported injection. The
helper now queries EVIOCGBIT and rejects unsupported keys before injection;
regressions also verify that a partially failed chord releases its supported
keys. The live checks were rerun with ordinary Enter and **visually inspected
cursor positions**. Keypad semantics are covered by controller tests, not by
claims of physical-keypad testing on this handheld.

## Preservation and limits

The device comparison found only the `tyrell-chess` package version changed.
All 12 selected protected-file hashes, the kernel and the launcher identity
remained unchanged. Package integrity and `dpkg --audit` were clean. All original
Chess state files were byte-for-byte unchanged after testing; no new state
files remained. Raw captures and test logs are local under ignored
`device-results/chess-011/` and `/var/tmp/tyrell-chess-ui-011/` on the device.

These are finite automated and live-driver checks. They do not establish that
every physical switch/FN combination, external keyboard, cold boot, battery
state or prolonged game has been exercised. Network play remains trusted-LAN
TCP, as described in the [app guide](../packages/tyrell-chess/README.md).
