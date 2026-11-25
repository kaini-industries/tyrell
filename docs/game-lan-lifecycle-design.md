# LAN game lifecycle policy

A3 design, revision 1, November 24, 2026. This selects a policy for future linked
LAN builds of Chess, Sandline and Beacon: **an observed shell pause ends the
local network session**. It does not pause a shared match or promise background
hosting. A resumed UI may show the ended session; it cannot continue that session.

The [decision record](../research/game-lan-lifecycle-design.json) binds the
November 24 inspected source and future acceptance cases. That increment was
design only. The separate [A3-LAN-1 controller increment](chess-lan-terminal.md)
now implements Chess terminal-state APIs and controlled App/TCP acceptance
checks. Production lifecycle-loop/native integration, protocol changes and
physical acceptance remain pending. The original decision record's future
cases are historical requirements, not updated runtime qualification. All
current linked-LAN admission gates stay closed.
Existing [Chess](game-chess-lifecycle.md), [Sandline](sandline-offline-lifecycle.md)
and [Beacon](beacon-offline-lifecycle.md) offline policies retain their behavior.

## Local termination and foreground ownership

An observed pause seals the current session before another local command,
inbound application packet, move or simulation update can be accepted. The seal
also prevents new outbound application work. It is irreversible for that session
instance, including during pairing, connect, already-finished play or a repeated
pause/resume sequence. Shell loss or linked termination seals it too, then exits
without acquiring foreground ownership.

The future production loop must drain lifecycle traffic after potentially
blocking input/network polls and before admitting packets, commands or rendering.
A pause found behind a full bounded receive batch wins before progress resumes.
Chess currently drains before input polling; copying that loop unchanged would
leave a post-poll race. Sandline and Beacon's offline loop checks provide useful
ordering examples, without supplying a LAN terminal primitive.

Local input/render release takes priority over network cleanup and persistence:

1. Latch terminal intent and its monotonic observation time; close application
   admission. Preserve the existing game state in RAM without serializing it.
2. Suspend Runtime input/rendering, or abandon ownership on loss/termination.
   Keep linked saved-frame restoration disabled before device construction.
3. Settle the authoritative Chess clock in RAM as described below, invalidate
   pending match commands and queue `paused` after local foreground release.
   Do not wait for disk, packet delivery, a peer response or an agreed result.
4. Finish local transport shutdown without processing more game traffic. Keep
   teardown pending if a stop prevents completion; complete it before any fresh
   connection. A future implementation must audit its actual close path rather
   than assume a flush or a reset helper satisfies this step.

`paused` describes local foreground release only. It is not proof that the
socket closed, a remote player received a notification, a final result was agreed
or a save became durable. The reviewed protocol-1 shell may stop the process
after roughly 100 ms, even without an acknowledgement. A stop before local
handling or cleanup can leave remote peers waiting; there is no guaranteed
network teardown, pause or exit deadline.

Successful resume may reopen and baseline input, suppress held keys and redraw
an ended-session view. Failed reopening remains closed and exits. Redraw, a
duplicate resume, EOF or queued packets cannot unseal a session. Starting another
host/join operation requires an explicit user action, completed old transport
cleanup and a fresh session identity. It must not reuse old peer IDs, queued
messages, pending moves or old terminal state. There is no automatic reconnect
or conversion of a LAN snapshot into a live offline game.

## Role and authority boundaries

| App / role | Local outcome | What remote participants can establish |
| --- | --- | --- |
| Chess host | Settle authoritative elapsed time, retain confirmed board/history/result, end local connection | Ordinary disconnect or later transport error; no guaranteed receipt of a final result |
| Chess joiner | Retain the last host-confirmed position and clock snapshot; distinguish a pending move request | Only the host can confirm that move or match result; local interruption does not decide a winner |
| Sandline or Beacon host | Retain a readonly last-local world/tick and end hosting | Each client sees ordinary disconnect/timeout; no agreed final score or shared freeze |
| Sandline or Beacon client | Retain the last accepted host snapshot and end this client session | The host and remaining players may continue and remove the departed player |
| Any app during setup/pairing | End the pending attempt without creating an active match | No successful pairing, accepted slot or remote acknowledgement is inferred |

Do not add a terminal-result wire message in this design. Current game protocols
do not establish a shared suspension outcome. TCP `Lan::send()` queues bytes;
`close()` discards its buffers. ENet send queues data, and disconnect/close do not
wait for peer acknowledgement. Previously sent packets can still arrive. A
future protocol extension needs its own version/capability and mixed-peer tests.

The ended view should say that the connection ended on shell suspension and
that remote state is unconfirmed. It may display the last observed position or
score, labeled accordingly. It must not present interruption as resignation,
forfeit, draw, victory, accepted move or an authoritative final world.

## Chess clocks and persistence

Host authority continues to use real monotonic elapsed time. At the terminal
boundary, settle expiry at the latched observation timestamp through the
game-result path before retaining its
result, without invoking synchronous archive/save work. Flag fall already due
at that boundary takes precedence over a queued move. Preserve an established
checkmate, draw or other result; interruption must not overwrite it. Once
terminal, the displayed clock is a historical observation, not a paused live
match that can later resume. Shell switching grants no continuation advantage.

A joiner retains host-confirmed state and uncertainty about unacknowledged work.
It cannot use a locally extrapolated clock to invent the host's final result.
Keep confirmed result, pending request and local interruption reason distinct.
The existing offline clock helper excludes online games, and existing
`disconnect()` pauses clocks without resolving a newly flagged Game result;
neither can become the LAN policy merely by removing admission checks.

Chess's active-save format deliberately excludes Host/Join, and its loader does
not admit them. Preserve the ordinary offline save. Any future interrupted-LAN
diagnostic or PGN is a separate non-resumable artifact, with explicit
confirmed-or-unresolved result authority and pending-request semantics.
Defer writes until permitted foreground
work, show failures and retain the last successful file. Pause/loss must not
wait for persistence or promise that newer RAM state survived process exit.

## Sandline and Beacon state

Their existing offline/reset and client-disconnect helpers clear or respawn the
world. A future terminal operation must instead preserve a detached final view
or an explicit terminal world, invalidate network/input state and forbid further
updates of that view. It must retain the inspected actors/tanks, shells/pulses,
scores and tick as applicable, without claiming they are globally final.

All host/connected/admission predicates must report terminal state honestly.
Sandline currently derives `is_host()` from mode alone; closing its transport
without a state change would still report hosting. A fresh join must not inherit
old local input, score ownership or pending packets. Ordinary unlinked slot
reuse, remaining-player state and app-owned wire schemas retain their behavior.

## Silent gaps and peer liveness

An observed pause is terminal even if immediately followed by resume. A stop
without an observed lifecycle event needs a separate service-gap guard before
buffered packets or moves are processed. Use a uint64 monotonic observation;
do not rely solely on uint32 clock subtraction across a full wrap. The host must
settle authoritative expiry; a client must preserve authority uncertainty.

Per-app service-gap limits and stale-authority behavior are prerequisites of
enablement. A3-LAN-1 selects separate inclusive 10-second Chess service-gap and
joiner-authority limits for explicitly opted-in controllers. Existing 500 ms
host clock updates provide receipt observations; they are not a negotiated or
bidirectional heartbeat and carry no wire-age proof. Ordinary unlinked behavior
is unchanged. Sandline and Beacon limits remain undecided. No common app timeout
or detection of every brief unobserved stop follows. Protocol/liveness changes
need separate compatibility review; already-expired authority wins over queued
old traffic.

Sandline and Beacon already separate wall-time expiry from bounded simulation.
Keep their existing handshake, stale-input and snapshot boundary semantics in
ordinary unlinked play. Never apply the offline first-resume interval discard
to LAN liveness, reset peer clocks or treat a post-stop backlog as fresh input.

## Implementation sequence and acceptance

| Increment | Deliverable | Required before advancing |
| --- | --- | --- |
| A3-LAN-1 | Implemented: Chess terminal/controller primitives, role-aware clocks and interruption state; reviewed gap/stale-authority policy | Controlled actual App/TCP and read-only render checks; linked LAN stays closed; see the separate validation record |
| A3-LAN-2 | Chess production loop and optional native adapter | Post-poll admission checks, real TCP peers, backpressure, hidden/lost exits and native I/O fixtures; default M5 regression |
| A3-LAN-3 | Sandline terminal transport/world policy and loop; decide its service-gap limit | Actual ENet host/client and surviving-player cases, immutable ended view, honest status and native ownership checks |
| A3-LAN-4 | Independent Beacon terminal Session policy and loop; decide its service-gap limit | Actual TYBC host/client, actor/pulse state, slot reuse and native ownership checks |
| A3-LAN-5 | Exact-artifact per-app qualification and reviewed enablement | Retained native/default packages as applicable, M5 first then pinned-Gh0st scenario acceptance; physical phase pending |

LAN-2 through LAN-5 remain future increments. LAN-1 exposes controller methods
for controlled callers, without a native adapter or new runtime enable option.
Do not expose linked LAN simply because a helper or one test exists. Keep
ordinary unlinked LAN coverage and default-off shell builds throughout. A
controlled opt-in fixture is separate from permission to advertise live support.

The decision record lists future cases for both roles: setup/active/finished
states, pause racing polls/packets/moves, repeated lifecycle events, backpressure,
lost notification, silent gaps/full wrap, authoritative expiry, pending requests,
save failure, foreign framebuffer pixels, failed reopen, explicit fresh sessions
and mixed/ordinary peer behavior. Every case remains not run for this design.
Future tests must exercise actual App/Session and transport paths; a policy-only
model cannot establish integrated behavior.

Physical M5 launch/input/exit and pinned-Gh0st pause/resume, termination, prolonged
stop and responsiveness remain pending for each exact new artifact. This design
changes no app/package version, protocol, image, Store approval, vendor input or
release. Video's [D5 qualification and idle gates](video-qualified-integration.md)
remain unchanged. Official M5 updates remain a separate
[reviewed Zero baseline refresh](stock-image-updates.md#tyrell-zero-base-updates).
