# Optional shell link and Status pilot

L1 adds a separate C++17 `Tyrell::ShellLink` transport and an experimental
Status 0.1.1 pilot. Normal builds keep `TYRELL_ENABLE_SHELL_LINK=OFF`. Video,
Chess, games, APPLaunch, the approved Store catalog and retained image payloads
do not gain shell integration from this change. New source/package bytes need
their own device acceptance.

## Reviewed protocol

The supported source is Gh0st
[`a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5`](https://github.com/therezor/CardPuterZeroOS/tree/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5).
This is a source interoperability target, not a claim about the v0.2.0 release
image. The transport and app behavior are defined by
[`gh0st-core/link.rs`](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/crates/gh0st-core/src/link.rs),
[`gh0st-app/run.rs`](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/crates/gh0st-app/src/run.rs),
[`gh0st-core/child.rs`](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/crates/gh0st-core/src/child.rs)
and [`gh0st-shell/run.rs`](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/crates/gh0st-shell/src/run.rs).

| Packet | Direction | Supported meaning |
| --- | --- | --- |
| `hello\t1` | Both | Protocol version handshake |
| `pause` | Shell to app | Relinquish display and input before acknowledgment |
| `paused` | App to shell | Ownership has been released |
| `resume` | Shell to app | Reacquire input and redraw the whole frame |
| `redraw` | Shell to app | Invalidate the full frame when foreground |
| `awake\t0` / `awake\t1` | App to shell | Idle-dark inhibition only |

Packets are UTF-8, tab-separated, at most 512 bytes, one packet per Unix
`SOCK_SEQPACKET` message on Linux. Tyrell rejects malformed UTF-8, truncated or
oversized packets and invalid field counts instead of accepting a partial
command. Unknown verbs do not grant ownership. No theme, content rectangle,
shell-owned bar, pause refusal or deadline extension is implemented.

`GH0ST_SHELL_FD` transfers exclusive ownership of an inherited descriptor to
the app before workers start. The transport validates decimal text, an open
descriptor above stderr, a connected Unix sequenced-packet socket and peer UID
before adoption. Validation failure leaves the descriptor and its flags alone.
The environment is consumed; an adopted descriptor is close-on-exec. Calls use
nonblocking I/O without changing the socket's shared file status flags.
The app must not offer an FD already owned by another component: kernel socket
checks cannot prove exclusive ownership within the process.

The dependency-free public target has a disabled implementation on non-Linux
platforms. Linking command metadata does not require linking the shell target.
Output buffering and receive work are bounded; acknowledgments that encounter
backpressure are retried while hidden. Apps retain lifecycle policy and must
explicitly acknowledge a completed pause.

## Status ownership policy

The shell-enabled native GUI handshakes before opening display or input and
processes pending lifecycle messages before device access. A queued startup
pause therefore cannot unblank the framebuffer. Ordinary absent or invalid
link descriptors preserve M5 operation. JSON, help, version, headless previews
and the SDL simulator remain usable independently of device IPC.

Pause closes the keyboard, clears input state and prevents input reads,
telemetry refresh and LVGL rendering before sending `paused`. Repeated events
are idempotent. Resume opens the keyboard again, suppresses keys already held,
discards stale events and invalidates the complete frame. The input adapter
also handles `SYN_DROPPED` without turning held keys into fresh actions.
Linked launches honor Gh0st's framebuffer/keyboard environment paths unless
the corresponding CLI path was supplied explicitly.

Loss of an adopted link, including while foreground, ends the GUI without
another render. Hidden EOF never becomes a foreground grant. Status has no
saved-frame restoration; the pinned LVGL fbdev destructor only unmaps and
closes the display. Cleanup must not call its rendering handlers after loss.
There are no Status worker processes or background-resume promises.

The reviewed shell waits about 100 ms for `paused`, then may send `SIGSTOP`
regardless of acknowledgment. A late acknowledgment does not guarantee a later
resume. [A1's Status idle loop](status-idle-loop.md) replaces the native 10 ms
polling loop introduced before L1. Bounded packet/input batches do not establish a hard deadline:
device operations, synchronous telemetry reads and OS scheduling can stall.
Fake-shell checks establish ordering, not a hardware latency or shutdown bound.

## Build and evidence

Use the normal documented build for the default M5 package. For the experimental
pilot, add `-DTYRELL_ENABLE_SHELL_LINK=ON` to Status's CMake configuration and run
CTest. Configuration performs no dependency downloads; LVGL still comes from
the existing verified lock. No Gh0st runtime library or service is required.

The Status CI suite builds and packages with shell support disabled, then builds
the enabled pilot separately. Required tests cover descriptor validation,
packet boundaries, malformed traffic, backpressure, descriptor inheritance,
input resynchronization, repeated lifecycle events, startup pause and link loss.
The GUI test uses the production loop with a real LVGL memory display and fake
device hooks, checking reads/writes and acknowledgment order without device
nodes. Existing model, CLI and headless-render checks run in both configurations.

Physical M5 launch/exit/input regression and pause/resume against the pinned
Gh0st build remain pending. Until those pass on exact new binaries, integration
is **experimental**, and the compatibility ledger retains untested runtime
records. Historical Status 0.1.0 packages and Zero image locks remain unchanged.
