# GameRuntime ownership and offline Chess lifecycle

A3 adds GameRuntime 0.1.1 ownership hooks and an experimental offline Chess
0.1.4 adapter. The normal M5 build keeps `TYRELL_ENABLE_SHELL_LINK=OFF`.
This original increment does not enable shell integration for Sandline, Beacon
Arena, LAN Chess or Video, and does not qualify a new package for a physical device,
the approved Store catalog or a Zero image.

The later [Sandline 0.1.1 offline policy](sandline-offline-lifecycle.md) reuses
those ownership hooks in a separate default-off native pilot. Its controlled
checks and pending physical acceptance are independent of the Chess evidence
below. Beacon and linked LAN policies remain pending.

The interoperability target remains Gh0st source revision
[`a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5`](https://github.com/therezor/CardPuterZeroOS/tree/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5),
using the [reviewed L1 packet and descriptor contract](shell-link-status.md).
It is not a claim about the v0.2.0 release image or later upstream revisions.

## Framework ownership and application policy

[`Runtime`](../packages/tyrell-game/include/tyrell/game/runtime.hpp) owns
graphics, input and presentation. Its main-thread hooks have no dependency on
`Tyrell::ShellLink`, Chess state or network policy:

| API | Contract |
| --- | --- |
| `suspend()` | Cancel an open frame without presenting, close/reset native input, and suppress saved-frame restoration while hidden. The framebuffer mapping and render context may remain allocated. |
| `resume(&error)` | Reopen and baseline input, discard stale edges, suppress held keys until release, and request a full redraw. Failure leaves the runtime suspended. |
| `abandon_foreground()` | Terminal ownership loss: close native descriptors without restoring saved pixels. A later resume fails. |
| `suspended()` / `foreground()` | Report the local runtime ownership state. They do not discover an external display owner. |
| `request_redraw()` / `redraw_requested()` | Invalidate the complete app-owned frame. A successful `end_frame()` clears the request. |
| `time()` / `elapsed()` | Retain monotonic elapsed time across suspension; resume drops pacing debt, not elapsed clock debt. |

Suspended input queries return no actions, and a new frame cannot begin until
ownership resumes successfully. `closed()` remains usable without polling
hidden input. The native adapter also resynchronizes after `SYN_DROPPED` without
turning a held key into a new action.

[`OfflineLifecycle`](../packages/tyrell-chess/src/offline_lifecycle.h) and
[`SessionHooks`](../packages/tyrell-chess/src/main.cpp) supply Chess policy.
Pause releases runtime input/rendering before settling RAM state and queuing
`paused`; it does not wait for AI completion or write files. Repeated pause is
idempotent and may repeat the acknowledgment. Redraw only marks invalidation.
Resume requires a successful hook; EOF, service failure or failed reacquisition
ends the session. Hidden EOF never grants foreground ownership.

Receive work is limited to 32 packets per batch. A full batch is followed by
another immediate drain before input or drawing, so unknown traffic ahead of
pause is not stranded behind a render or wait. Acknowledgments under socket
backpressure remain pending while hidden; resume cancels an obsolete unsent
acknowledgment. These bounds describe loop work, not execution time.

## Admission and startup gates

Opt-in builds use `-DCHESS_BACKEND=NATIVE -DTYRELL_ENABLE_SHELL_LINK=ON` with
the existing verified dependency inputs. Only the Linux native GUI adopts
`GH0ST_SHELL_FD`; desktop, headless, help and version paths remain independent.
An absent or invalid descriptor retains ordinary M5 launching. An adopted
endpoint that has already closed exits safely rather than falling back to an
uncontrolled native display session.

The first gate completes the versioned hello and drains queued lifecycle
traffic before constructing App, loading state or opening devices. A queued
startup pause is acknowledged and waits without native device acquisition.
Missing hello has a 100 ms software timeout. That timeout is not a guaranteed
wall-clock startup or handoff deadline.

App loading and optional automation setup can perform filesystem work. A
second App-aware gate drains the same handshaken link immediately before
Runtime construction. It does not require a second hello packet. Pause or
loss queued during loading therefore prevents subsequent device acquisition.
Valid linked launches use the inherited framebuffer/keyboard environment
paths; unrelated M5 launches retain normal path selection.

Linked sessions admit local games, computer opponents, puzzles and offline
archives. LAN selection, host/join setup, game start, address connection,
archive load and active-game reload are gated. A linked session cannot switch
into LAN after startup. This avoids promising peer continuity or network-clock
authority while Gh0st may stop the process. Ordinary unlinked LAN behavior
retains its existing path.

## Clocks, input and resumed play

OS lifecycle pause is separate from the existing explicit Chess clock pause.
A running match clock continues through hidden time and process `SIGSTOP`.
Pause settles its current value in RAM. Resume settles it again before input
is reopened or any move is accepted, so flag fall wins over a queued move.
Existing explicit pauses, including the saved-game menu state, remain paused;
the lifecycle adapter does not call `ChessClock::pause()` or `resume()`.

The engine retains its uint32 millisecond timestamp interface and unsigned
subtraction across a wrap for intervals shorter than `2^32` ms. The adapter
also records a uint64 monotonic suspension time. An observed running-clock
suspension spanning at least a full wrap, about 49.7 days, forces expiry using
the remaining time recorded at suspension instead of granting time through
modulo arithmetic. An explicitly paused clock does not expire from that gap.
This guard concerns an observed lifecycle suspension; it does not turn a saved
file or an unobserved full-wrap process stop into uninterrupted match timing.

Suspension clears controller repeat state, hints and active piece animation.
Native resume discards queued edges and baselines physical holds before a full
redraw. A held Q cannot become a fresh exit action. A pending puzzle opponent
reply is rescheduled relative to resumed UI time, avoiding a burst of overdue
replies. Runtime elapsed time remains truthful; Chess cancels local animation
and puzzle catch-up rather than changing the shared clock.

## AI cancellation and stale results

Each AI worker owns a fresh shared atomic cancellation token captured by value
with its board copy. Pause increments the App generation and requests
cancellation without destroying, joining or waiting on its future. The worker
has no input or framebuffer access, so it may finish cooperative search work
after `paused` without drawing or changing the live match.

[`ChessAI::findBestMove`](../packages/tyrell-chess/engine/chess_ai.h) samples
cancellation before work, through opening-book replay/sort, at search nodes and
between move-ordering/child steps. Observed cancellation returns an empty move
after unwinding the copied board; a partial opening book is not published.
The longest unchecked engine work is a finite legal-move generation/evaluation
followed by at most twelve move reversals. Search/book randomness avoids fresh
worker initialization through OS entropy. See the
[engine contract](../packages/tyrell-chess/engine/README.md).

A result completed just before cancellation may still be legal. Harvesting
therefore checks the worker generation, board-position hash, permitted active
screen/game context and unfinished match before applying it. Hidden completion
cannot mutate the match; resume discards an obsolete generation. The engine
still allows only one search at a time because search/book state is global.

Exit requests cancellation, destroys Runtime before waiting for the remaining
worker, then joins through App cleanup. No worker is detached. Cooperative
search units bound engine CPU work; OS scheduling, runtime initialization and
device/filesystem calls prevent a universal wall-clock cancellation or exit
guarantee. Software cancellation-to-join measurements are not a hard deadline.

## Restoration, saving and exit

Every linked Runtime sets `restore_framebuffer_on_close=false` before native
construction. This immutable policy covers partial-construction failures as
well as successful sessions. An exit guard additionally abandons ownership on
every linked exit, including foreground Q, a signal with pause still queued,
and exceptions. Cleanup cannot replay prelaunch pixels over a newer owner.
Unlinked M5 callers retain the default foreground restoration policy.

The loop observes shutdown after lifecycle processing, native input polling,
App update and command/input handling before beginning another frame. These
checks prevent a signal observed during polling from applying another command
or drawing another frame. They do not establish race-free arbitration with an
external owner or interrupt a kernel call already in progress.

Pause performs no disk I/O, even if settling the clock ends the match. It marks
save/archive work pending in RAM and acknowledges after input/render release.
The first resumed update attempts the deferred save; failures remain visible
and retries are throttled using the existing five-second save interval. A
failed or blocked save is not a prerequisite for pause acknowledgment.
Foreground saves remain synchronous, so a filesystem operation already in
progress can still delay the next lifecycle drain.

Observed link loss or linked signal termination skips exit persistence and
report output. After the loop, Runtime releases devices before the main
function's final save or worker join. The last successful atomic save is
preserved, but RAM-only moves, newer clock values, puzzle progress or settings
may be lost. Loading that
file uses existing recovery/menu behavior; it is not uninterrupted match
continuation. Normal foreground Q retains its existing save-and-exit behavior,
including keeping a save failure visible instead of treating it as success.

## Automated evidence and physical limits

The [local validation record](../research/game-chess-lifecycle-validation.json)
binds the frozen app/dependency digests, tool versions, native package digest
and test outcomes. Debian 13 ARM64 validation passed 8 framework, 14 Sandline,
1 desktop Runtime, 22 headless Chess, 22 default-native Chess and 2 enabled
native Chess CTest executions. The actual desktop Chess app rendered two frames
under Xvfb. All 507 required Linux Python tests passed without skips. These are
development checks; hosted CI independently records its own clean source and
artifact identities. The local package was inspected without installation.

`chess-offline-lifecycle` exercises policy ordering, repeated/out-of-order
events, resume failure, deterministic flag-fall boundaries, uint32 wrap and a
full-wrap hidden gap, explicit pause, failed deferred saves, LAN gates and an AI
result completed while hidden. `chess-ai-cancellation` exercises the actual
engine's cold cancellation, subsequent recovery, active-search cancellation,
board reversal and token ownership; it emits a software timing record.

Linux opt-in `chess-shell-lifecycle` uses real sequenced-packet sockets and the
actual GUI entry/loop. It tests startup pause before App/device initialization,
the post-load gate, native environment-path adoption, packet flood and output
backpressure, repeated pause/resume, held-Q suppression, hidden flag fall and
EOF, and signal/exception cleanup. A signal raised during a synthetic native
keyboard read produces no subsequent App update, scripted/input action or
frame commit.

These native checks link the production Runtime and native adapter with a
test-only syscall fixture for framebuffer/evdev operations. Another simulated
owner writes pixels before cleanup, allowing checks that hidden EOF, queued
pause plus signal, exceptions and normal linked exit do not restore an old
screen. Separate Runtime tests cover open-frame cancellation, failed resume,
construction failure, monotonic elapsed time and ordinary unlinked restoration;
desktop smoke coverage uses an isolated display.

Fixtures open no hardware device nodes and run no actual Gh0st shell. They
establish software ordering and cleanup, not panel timing, keyboard scan
behavior, power savings or physical M5/Gh0st acceptance. The reviewed shell may
send `SIGSTOP` after approximately 100 ms regardless of acknowledgment. No test
here proves a hard 100 ms pause, shutdown, filesystem or driver deadline, nor
guarantees that a late acknowledgment receives a later resume. Exact new
binaries still require physical M5 launch/input/exit regression and pause,
resume, signal/loss and foreground-pixel checks against the pinned Gh0st build.
