# Chess LAN lifecycle loop integration

A3-LAN-2, November 25, 2026. Chess 0.1.6 connects the
[terminal controller](chess-lan-terminal.md) to the actual application loop and
optional native ownership hooks. It supplies a controlled software integration
path, while public linked Host/Join admission remains closed and shell support
defaults off. No CLI or environment option enables linked LAN. Physical testing
remains pending.

## Admission and ownership

A controlled C++ caller explicitly arms an App controller before opening its
TCP transport, then runs the same `runSession` and native hooks used by the app.
The public entry point retains both startup gates, linked Host/Join rejection,
device-free startup pause handling and disabled framebuffer restoration before
linked Runtime construction. Ordinary unlinked M5 play retains its existing
path. The opt-in fixture is not permission to advertise a supported LAN shell
session.

On an observed pause, the adapter first latches the interruption reason and
monotonic timestamp and seals application/transport admission. It then suspends
Runtime input and rendering, settles the authoritative state at that latched
boundary, and queues `paused`. Transport cleanup closes local descriptors and
discards buffers separately. It does not flush application messages, await a
peer, join AI work or write files. Acknowledgment describes local foreground
release, without proving remote delivery or an agreed result.

Lifecycle drains follow native input and network polls and precede further
packet, command and render admission. A full bounded receive batch forces more
draining before progress; a pause behind unknown packets must not wait behind a
frame or input action. The controller's existing uint64 service-gap and joiner
authority guards still run before stale buffered traffic can refresh state.

Output backpressure leaves a pause acknowledgment pending without retaining
foreground input/render ownership. Resume cancels an obsolete unsent
acknowledgment. A pause followed immediately by resume still ends the old LAN
instance; it cannot reopen its transport or continue the match. Resume reopens
and baselines native input only for the read-only ended view. Failed reopening
exits while remaining closed.

Link loss, linked termination and exception cleanup seal the controller and
abandon foreground ownership. Hidden exits never acquire a display or restore
saved pixels. Runtime releases devices before App cleanup waits for AI work.
No path here guarantees a hard 100 ms handoff, kernel-call or process-exit
deadline.

## Ended session and fresh controllers

The ended view preserves role-specific authority: a host settles real elapsed
clock time and due flag fall; a joiner retains the last validated host position,
clock and result plus unresolved request evidence. Interruption is not a
resignation, forfeit, draw or victory. The current protocol still cannot prove
wire age or that a peer remained scheduled.

Foreground exit remains available from the ended view. No Home reload, save,
export, automatic reconnect or offline continuation is performed. A separate
explicit C++ fresh-session request needs foreground state and completed old
transport cleanup, and receives a new identity. Resuming or redrawing never
clears the old seal. The existing offline save and retained qualified packages
keep their original bytes and evidence.

## Software validation and remaining gates

The Linux shell-enabled `chess-lan-shell-lifecycle` acceptance test uses the real
App, `runSession`, TCP peers, sequenced-packet shell sockets, Runtime and native
adapter. Its framebuffer and keyboard syscalls use a test-only fixture. The
fixture opens no physical device nodes and runs no actual Gh0st shell.
Default-off headless/native and desktop checks remain separate regressions.

The [validation record](../research/chess-lan-loop-validation.json) records the
actual local software checks. Clean hosted CI binds its own exact source and
retained package separately. Neither evidence source grants release, Store,
image inclusion or device approval. Physical M5 launch/input/exit, pinned-Gh0st
lifecycle and exact-artifact LAN acceptance remain pending.

The next source increment is **A3-LAN-3: Sandline terminal transport/world policy
and loop**, with its own service-gap policy and ENet acceptance cases. Chess
enablement remains part of A3-LAN-5 after exact-artifact qualification.
