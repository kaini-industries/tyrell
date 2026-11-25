# Offline Sandline shell lifecycle

A3 adds an optional native offline policy to Sandline 0.1.1. It uses the existing
[GameRuntime ownership hooks](game-chess-lifecycle.md) and
[reviewed shell-link contract](shell-link-status.md). Normal M5 builds keep
`TYRELL_ENABLE_SHELL_LINK=OFF`; their LAN and framebuffer-restoration policies
remain unchanged. New 0.1.1 binaries need their own physical M5/Gh0st acceptance.
Earlier [device results](tanks-install-validation.md) remain historical evidence
for the exact tested package.

This increment supports the menu, Driving Range and offline demo in an explicitly
linked native session. Linked `--host`, `--join` and `--server` launches are
refused before a UDP host/connect operation or Runtime acquisition. LAN menu
rows identify their unavailable state; numeric shortcuts, address entry and
activation cannot enter a network match. Unlinked LAN continues to use its
existing simulation, transport and timeout paths. No network continuity across
a shell pause is promised. Beacon's independent lifecycle policy remains pending.

## Startup and ownership

Only an enabled, native Linux, non-headless entry adopts `GH0ST_SHELL_FD`.
Desktop builds, headless runs and CLI help do not adopt the caller's endpoint.
The existing transport validates a connected sequenced-packet socket, consumes
the environment only after adoption and makes the owned descriptor close-on-exec.
An absent or invalid descriptor preserves normal M5 operation and leaves an
unrelated descriptor unchanged.

Startup waits for the peer's hello and drains lifecycle packets before a game
host/connect call or native device acquisition. A queued pause can remain hidden
without opening the LCD or keyboard. The loop drains again after CPU-side game
initialization, before constructing Runtime. Silent negotiation has the existing
100 ms software budget; it does not grant ownership on timeout.

For a linked launch, `LV_LINUX_FBDEV_DEVICE` and `LV_LINUX_KEYBOARD_DEVICE` supply
paths only when the corresponding `--framebuffer` or `--input` option was absent.
Explicit paths and an explicit `auto` take precedence. These environment paths
are not used to alter an ordinary unlinked M5 launch.

[`OfflineLifecycle`](../packages/tyrell-tanks/src/offline_lifecycle.h) supplies
app policy. Pause suspends Runtime input/rendering before queuing `paused`.
It does not reset the driving-range world or call `NetworkGame::offline()`.
Repeated pause is idempotent and may repeat the acknowledgement. Redraw requests
only invalidate the next foreground frame. Resume reopens and baselines input,
discards stale edges, suppresses held keys until release and requests a full
redraw. A failed reopen or resynchronization keeps ownership closed and exits.

Receive batches are bounded to 32 packets. A full batch is drained again before
waiting or making progress through input/update/render. The actual loop checks
lifecycle traffic before input polling, after that potentially blocking poll,
and before beginning a frame. Acknowledgements remain pending under socket
backpressure; resume cancels an obsolete unsent pause acknowledgement.

## World state, elapsed time and exit

The offline world, projectiles, cooldowns, respawn state and input counters stay
in RAM while hidden. Hidden iterations do not poll input, update the world or
render. After successful resume, the first linked offline simulation interval
is discarded, preventing a burst of accumulated physics work. Subsequent
foreground updates use normal elapsed time.

`Runtime::time()` and `Runtime::elapsed()` remain truthful across suspension.
No shared clock or network timeout is reset or clamped. The first-interval
discard belongs to the Sandline practice policy; the shared Runtime, network
protocol and unlinked LAN liveness remain unchanged. This preserves practice
state in RAM, not a new persistent-save guarantee.

A linked Runtime disables saved-frame restoration before construction, including
partial device-acquisition failure. Linked loop exit abandons foreground
ownership before Runtime cleanup or later report I/O. Hidden EOF, malformed/lost
transport, failed resume, handled termination and exceptions cannot replay a
prelaunch framebuffer over another foreground owner. EOF never becomes a resume
grant. The normal unlinked M5 path retains its saved-frame restoration.

Linked screenshots are disabled. A requested JSON report is written only when
the linked loop exits in a permitted foreground state; hidden/lost/failed-resume
and interrupted exits suppress it. This is a final state report, not a save or
hardware-acceptance receipt.

## Build and controlled checks

Build the normal native package with `TYRELL_ENABLE_SHELL_LINK=OFF`. On Debian
ARM64, the [game helper](../scripts/game) builds the experimental native pilot
into `build/tanks-native-shell`, separate from `build/tanks-native`:

```sh
python3 scripts/game deps --project tanks --backend native
python3 scripts/game build --project tanks --backend native --shell-link
python3 scripts/game test --project tanks --backend native --shell-link
```

The equivalent CMake configuration adds `-DTYRELL_ENABLE_SHELL_LINK=ON` to the
normal native flags and uses a separate build directory. The feature defaults
off; no launcher, helper service, permissions, Store policy or image payload is
changed by enabling a test build.

`tanks-shell-lifecycle` exercises the actual production session loop through
real socketpairs. Six headless and twelve native syscall-fixture cases passed
on Linux. The native fixture wraps the production device path without opening
physical nodes. Tests preserve a moving/firing world through a held hidden
interval while observing truthful elapsed time, check held keys and failed
reacquisition, queue pause during native input polling, and inject termination
during that poll. They also cover startup pause, packet batches, backpressure,
CLI/menu LAN refusal, path precedence, ordinary M5 restoration and linked
construction failure without restoration. CI retains `a3-sandline-lifecycle.json`
with the controlled scenario count and explicit pending physical status.

The pinned Gh0st protocol 1 may stop a process after roughly 100 ms even without
an acknowledgement. Bounded packet batches do not establish a device release,
resume or exit deadline: syscalls and OS scheduling may block. Physical M5
launch/input/exit, pinned-Gh0st pause/resume and responsiveness remain pending.
This software increment grants no hardware compatibility or release approval.
Video's [D5 live/device gates](video-qualified-integration.md) and idle-optimization
gate remain unchanged.
