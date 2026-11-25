# Chess LAN terminal controller

A3-LAN-1, November 25, 2026. Chess 0.1.5 adds explicit controller and transport
primitives for the [selected LAN lifecycle policy](game-lan-lifecycle-design.md).
An interruption ends one local session instance. It does not pause a shared
match, decide an unresolved winner or establish that a remote player received
a notification.

The native shell adapter does not call these primitives yet. Ordinary M5 LAN
play retains its existing behavior, shell support defaults off, and all linked
Host/Join admission gates remain closed. A3-LAN-2 owns the production lifecycle
loop and native adapter. Physical testing remains pending.

## Session and admission

`App::beginLanSession(role)` explicitly starts a controller-policy instance
before opening its transport. It rejects a linked App, an active instance,
unfinished cleanup or an open old transport. A fresh instance gets a new local
identity and clears pairing, pending requests and old authority observations.
The identity is local bookkeeping; the protocol-1 wire format is unchanged.

`sealLanSession(reason, observedAtMs)` irreversibly closes application admission
before settling state. It retains interruption reason/time independently of the
game result and preserves an ended position. `Lan::seal()` immediately rejects
send, poll and receive work and reports connected/listening as false. It leaves
the actual descriptors and buffers for the separate cleanup operation.

`completeLanTeardown()` closes local descriptors and discards transport buffers.
Neither operation flushes pending application messages, waits for a peer or
writes a save. Already transmitted data may still arrive remotely. Cleanup can
remain pending across a process stop; a new instance requires completed cleanup
and an explicit caller action. Repeated seal, cleanup or resume cannot revive
the old session.

Application admission checks guard network processing, move/command entry and
outbound work for an explicitly opted-in controller. The production shell loop
still needs its separate post-poll lifecycle drains and ownership-release
integration. These primitives alone do not establish a pause acknowledgment or
a scheduling/teardown deadline.

## Clock and result authority

| Role | Retained state at the terminal boundary |
| --- | --- |
| Host | Current authoritative position; real elapsed clock time settled through `Game::update()` at the latched boundary; any resulting flag fall or previously established result |
| Joiner | Last fully accepted host position, exact host clock sample and confirmed result; any outstanding request is retained as uncertain evidence |
| Setup/pairing | No established LAN match; a previously loaded offline game and its save are preserved |

Host checkpoints use uint64 monotonic observations around the existing uint32
chess clock. An expired checkpoint or a gap spanning one or more complete
uint32 cycles still resolves the actual flag through the normal Game result
path. A queued move cannot displace an expiry already due at the terminal
boundary. A backward observation seals admission using the last trustworthy
clock boundary.

For a joiner, PLY confirms the validated position and clock; END confirms a
result. An invalid partially applied PLY cannot replace the last confirmed
position. A locally projected clock reaching zero does not invent the host's
result. Matching move/ply acknowledgment clears the pending request; an accepted
different host move retains the original request as unconfirmed evidence.
Interruption itself means no resignation, forfeit, draw or victory.

## Chess service-gap and authority policy

The controller policy selects two Chess-specific, inclusive limits:

| Observation | Limit and action |
| --- | --- |
| Time since the last admitted controller service | At 10,000 ms, or on backward time, seal before accepting buffered work |
| Active joiner time since the last fully accepted host START/PLY/SYNC/END | At 10,000 ms, seal before queued traffic can refresh authority |

Ten seconds matches the existing pending-move budget and permits twenty missed
nominal 500 ms host clock updates. It is a conservative local interruption
policy, not a negotiated peer timeout. It is inactive in ordinary unlinked LAN
play until the controller is explicitly opted in.

The current one-way SYNC message has a ply number, without a freshness sequence
or sender timestamp. A newly accepted valid receipt therefore cannot establish
wire age, prove host health or detect every brief stop. No heartbeat or result
message is added. An idle host still has no general heartbeat proving that its
joiner is scheduled. Future liveness/protocol extensions require separate
compatibility review. Existing pairing and ordinary-peer behavior remain intact.

## Ended view and persistence

An explicitly sealed controller exposes a read-only ended view. Essential text
fits the existing 320×170 renderer: connection ended, local reason, remote state
unconfirmed and read-only status. Both clock rows remain inactive, including
untimed games. No play, resume, menu or next-move prompt advertises an unavailable
action. Native ended-view navigation remains part of A3-LAN-2.

The seal does no filesystem work or AI join. It invalidates pending command/AI
generations and ordinary archive/save work for the interrupted LAN instance.
The existing offline save and last successful files remain untouched. There is
no interrupted-LAN save format or automatic offline continuation. A later
non-resumable diagnostic/export flow needs explicit foreground authority and
failure handling. RAM retention does not promise durability across process exit.

## Validation boundary

`chess-lan-terminal` exercises the actual App, Game and TCP transport with an
injected monotonic clock. It covers setup and both roles, real queued packets,
expiry and multiple wraps, confirmed-state rollback, pending request authority,
stale service/host observations, idempotence, deferred cleanup, fresh pairing,
unchanged saved files and read-only rendering. CMake registers it with the
existing fixed-port serial network tests. Both portable and Debian ARM64 CI
require it; the headless/native software renderer also checks essential text
bounds and inactive clock pixels.

The [validation record](../research/chess-lan-terminal-validation.json) binds the
implemented source and actual software checks. Its scope excludes physical M5
launch/input/exit, real Gh0st pause/resume, native LAN ownership integration and
remote delivery guarantees. The original app packages, Store approvals,
November Zero image and prior image/control records retain their own evidence.
