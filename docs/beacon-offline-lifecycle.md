# Offline Beacon Arena shell lifecycle

A3 adds an optional native offline policy to Beacon Arena in framework/example
0.1.2. It uses the existing [GameRuntime hooks](game-chess-lifecycle.md) and
[shell-link contract](shell-link-status.md); shared Runtime, simulation, Session,
network packet schema and transport implementations are unchanged. Normal M5
builds keep `TYRELL_ENABLE_SHELL_LINK=OFF`. No Beacon package, launcher entry or
default image inclusion is introduced. Physical testing remains pending.

## Admission and ownership

Only an enabled, native Linux, non-headless entry adopts `GH0ST_SHELL_FD`.
Desktop, headless and CLI-help paths leave the caller's endpoint alone. Invalid
or absent endpoints preserve ordinary unlinked operation without modifying an
unrelated descriptor. The shared transport validates and adopts a connected
sequenced-packet socket, consumes its environment entry and sets close-on-exec.

A linked `--host`, `--join` or `--server` launch is rejected before UDP creation
or native device acquisition. Startup requires hello and drains queued lifecycle
events before constructing Runtime. A startup pause waits without opening the
LCD or keyboard. Silent negotiation has a 100 ms software budget; timeout grants
no foreground ownership. The loop drains again after CPU-side initialization.

For linked native launches, `LV_LINUX_FBDEV_DEVICE` and
`LV_LINUX_KEYBOARD_DEVICE` provide paths only when `--framebuffer` or `--input`
was omitted. Explicit paths and an explicit `auto` take precedence. Ordinary
unlinked launches keep their existing device discovery.

[`OfflineLifecycle`](../packages/tyrell-game/examples/beacon_lifecycle.hpp) owns
Beacon's policy. Pause suspends input/rendering before queuing `paused`; repeated
pause is idempotent. Resume reopens and baselines input, clears stale edges,
suppresses held keys until release and requests a full redraw. Failed reopening
or input resynchronization keeps ownership closed and exits. Redraw invalidates
the next foreground frame. EOF or malformed/lost transport ends ownership;
it never grants resume.

Receive batches are limited to 32 packets and a full batch is drained again
before any input, update or rendering progress. The production loop checks
traffic before polling input, after that potentially blocking poll and before
starting a frame. Backpressure retains acknowledgements; resume cancels an
obsolete unsent pause acknowledgement.

## Session state and exit

The existing offline Session, actor positions, pulse state, tick and
fixed-step remainder remain in RAM. Pause/resume never calls `Session::offline()`
or resets the world. Hidden iterations perform no input polling, updates or
rendering. Only the first resumed offline simulation interval is discarded;
later foreground updates advance normally. This is in-memory preservation,
without a persistent-save guarantee.

`Runtime::time()` and `Runtime::elapsed()` remain truthful across suspension.
The first-interval discard is local to the linked offline app. Ordinary unlinked
offline, host, join and server paths retain their monotonic wall-clock elapsed
time and existing peer/input/snapshot expiry. Linked LAN remains unavailable;
this implementation makes no claim of networking continuity while hidden.

Linked construction disables saved-frame restoration before acquiring devices,
including partial construction failure. A loop exit guard abandons ownership
before Runtime cleanup or report I/O. Hidden/lost exits and exceptions cannot
replay a prelaunch framebuffer over another foreground owner. Normal unlinked
M5 exit retains its saved-frame restoration behavior.

Linked screenshots are disabled. Reports require a permitted foreground exit;
hidden/lost/failed-resume, interrupted and bounded linked exits suppress them.
Once Runtime exists, `--seconds` also bounds hidden waiting without requiring a
resume or reopening devices. Reports remain diagnostic state, without device
acceptance authority.

## Builds and evidence

On Linux with the native build prerequisites:

```sh
python3 scripts/game deps --backend native
python3 scripts/game build --backend native --shell-link
python3 scripts/game test --backend native --shell-link
```

The helper isolates `build/game-native-shell` from the default
`build/game-native`. Direct CMake callers use `-DTYRELL_ENABLE_SHELL_LINK=ON`.
The option defaults off and belongs to the Beacon runtime executable, leaving
core-only, network-only and examples-disabled consumers free of shell targets.

`tyrell-beacon-shell-lifecycle` exercises the actual Session loop with socket
peers and controlled input/time. The native fixture wraps production device
calls without opening physical nodes. It covers moving/pulsing state across
pause/resume, truthful elapsed gaps and single-interval discard, held input,
queued lifecycle events during polling, startup admission, batching,
backpressure, failed acquisition and ordinary M5 restoration. CI retains
`a3-beacon-lifecycle.json` from both headless and native enabled builds, with
seven and fourteen controlled case groups respectively.

Default builds check CLI help, headless rendering and bounded real UDP host/join
sessions. A separate disabled-entry test verifies endpoint isolation. The
desktop app renders two Xvfb frames. The `game-native` evidence also retains the
exact default 0.1.2 ARM64 executable, with SHA-256, source revision and explicit
pending physical status. It is an executable artifact, without a Debian package
or an installation claim. See [scoped CI](continuous-integration.md).

The pinned Gh0st protocol 1 may stop a process after roughly 100 ms without an
acknowledgement. Software packet bounds do not establish a physical release,
resume or exit deadline; syscalls and scheduling can block. Physical M5 launch,
input/exit and pinned-Gh0st pause/resume remain pending. This increment grants
no hardware compatibility, power improvement or release promotion. Video's
[D5 live/device gates](video-qualified-integration.md) and idle gate stay open.
