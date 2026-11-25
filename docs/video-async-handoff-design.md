# Asynchronous Video handoff and helper ownership

A2 design, revision 1, November 24, 2026. Status: **D1 foundation, D2 participants,
D3 managed display APIs and D4 media actor/control pilot implemented; live
integration and qualification pending**. The
[decision record](../research/video-async-handoff-design.json) binds the inspected
source, design invariants and future acceptance cases. It is not a runnable
protocol, a release approval or a claim that those cases passed.

The separate [D1 implementation](video-handoff-foundation.md) supplies the
channel, trusted-launcher session, authority and durable ledger. Its adapters
are inert and it is not installed into Video. The separate
[D2 implementation](video-handoff-participants.md) adds actual VT/libpulse
backends and a disposable worker/stop-post harness; it does not admit live
hardware or install a service. The separate
[D3 display implementation](video-managed-display.md) adds explicit KMS/LCD
policies and independent producer barriers, without selecting a managed app
launch path. The [D4 media implementation](video-media-actor.md) adds the actual
single-owner graph, borrowed UI adapter and durable native-producer accounting
for LCD/fake-audio fixtures. The historical decision record's 19 complete
integration cases have not all passed; per-increment software coverage is
recorded separately. D1–D4 software checks do not open live admission.

## Decision and compatibility boundary

Use an explicitly negotiated asynchronous handoff contract and a shared resource
authority. Seal the outgoing ownership epoch before draining work. Keep the app
scheduled until it and its helpers actually finish. Grant a successor only
after the authority commits a complete release receipt. Unknown completion
quarantines the affected resources.

This requires changes to a future shell as well as Tyrell. It cannot be added
transparently to the [pinned Gh0st protocol 1](shell-link-status.md), whose roughly
100 ms pause window may end in `SIGSTOP`. Video's existing admission guard stays
closed. The design record itself installs no service or package. D1–D4's
separate implementation adds no environment adoption or live launch path.
Normal M5 launching and current helpers retain their behavior.

Use separate v2 endpoints and units for the managed path. Do not send commands
on the legacy HDMI socket: it deliberately rejects every client byte. Do not
fall back to legacy helpers after managed resources have been acquired. A
transition back to M5 ownership needs the same verified empty barrier as another
managed grant. Original M5 executables need no protocol modification.

## What the current implementation establishes

| Current boundary | Evidence and consequence |
| --- | --- |
| Playback teardown can block | The [A2 timing record](video-checkpoint-lifecycle.md#recorded-software-timing) contains a controlled 251.90 ms GStreamer NULL transition. A local timeout cannot make that transition finish. |
| HDMI EOF precedes cleanup | [`run_lease()`](../packages/tyrell-video/system/tyrell-video-hdmi-session) shuts down its peer before `restore()`. EOF revokes the client; it is not a restoration receipt. |
| VT recovery is a legacy policy | The same helper checks the active VT and can restore the original console after a DRM wait fails. Its marker has no ownership epoch. Its flock excludes other helper instances, not shell grants or app KMS writes. |
| KMS release has no completion result | [`HdmiSession::release()`](../packages/tyrell-video/src/outputs.cpp) restores planes/CRTCs synchronously, logs failures and clears local state. A later owner cannot safely race those calls. |
| Audio client release is local | [`HdmiAudioSession::release()`](../packages/tyrell-video/src/hdmi_audio.cpp) closes the lease and clears its state while the external service can still be cleaning up. |
| Audio creation can remain uncertain | The [audio helper](../packages/tyrell-video/tools/audio-session.cpp) cancels local operations on failure and uses later cleanup scans. This provides no positive proof that a previously accepted load cannot appear after a scan. |

The design must close these boundaries; an acknowledgment added to an existing
socket cannot close them by itself.

## Authority, participants and trust

A future managed-session authority owns the resource ledger and the decision to
grant foreground use. The shell requests transitions through that authority;
it cannot independently grant a resource that the ledger has quarantined.

Retain the existing privilege split. The player and a user-session coordinator
remain normal-user processes. A separately versioned VT participant uses the
dedicated nonroot service identity, manager-opened console descriptor and scoped
`CAP_SYS_TTY_CONFIG` boundary. Audio remains a participant of the user's audio
session. A dedicated VT account does not implicitly gain access to that session.
No new global root daemon or arbitrary-command helper is proposed.

Every participant and operation is registered before a side effect can begin.
Identify it with authority incarnation, app instance, ownership epoch, lease and
operation ID. A helper launch or accepted audio load is already outstanding work,
including when its result has not arrived. Registration must precede spawning
or sending the backend request.

Kernel peer credentials, approved service/cgroup identities and a fixed local
bootstrap bind channels to participants. PIDs alone are insufficient; use
pidfds or qualified manager identities where available. Correlation IDs and
receipts are not credentials. Retain fixed helper verbs and device scope;
clients supply no commands, paths, console numbers or service names.

This is an ordering contract for cooperating, qualified clients. It does not
confine arbitrary code with existing video-group access or serialize root and
administrator actions. Unexpected external VT/device/server changes invalidate
the affected epoch and require reconciliation. Managed admission must exclude
untracked legacy helpers, direct restoration code and conflicting launcher
writers, including their pending cleanup.

## Resource domains and the operation barrier

Track LCD/input, HDMI KMS/VT and owned HDMI audio separately, but release all
domains of a Video transition as one composite transaction initially. Partial
receipts do not authorize a conflicting successor or a completed privacy lock.
Per-domain admission is a later extension requiring a reviewed dependency graph.

An epoch has an acquisition gate and an outstanding-operation barrier:

1. Journal and seal its actor/resource registry atomically with closing new
   acquisitions. Reject queued requests that have not obtained a permit.
2. Preserve all previously accepted operations in the barrier. Give existing
   actors only fixed release/cleanup permits; those permits are counted too.
3. Hold each permit through the actual side effect, asynchronous backend
   completion and required readback. Checking an epoch immediately before an
   unlocked ioctl leaves a race and is insufficient.
4. Journal the terminal result before retiring a permit. Loss of its process,
   connection or reply leaves it outstanding/unknown.
5. Only a zero, verified barrier plus all local/participant release conditions
   can produce the durable release record. Persist that record before issuing
   its receipt or allocating a successor epoch.

Control handling stays separate from potentially blocked resource work.
Registered per-domain workers may block while the authority continues serving
status and deadlines. The worker retains its permit. Its failure or timeout
cannot decrement the barrier. A worker's service placement outside the app group
is an explicit part of this lifecycle design, not a workaround for protocol 1.

Existing app-side KMS restoration must join this barrier or be absent from the
managed path. In particular, a managed destructor must not begin an unpermitted
restoration after revocation. An already permitted operation may still finish
while its domain is quarantined; it retains the barrier throughout. No old
restoration may remain when the epoch is retired as released. The authority chooses the qualified
neutral/recovery target; it never accepts an app-supplied VT target or resurrects
old foreground snapshots. Legacy M5 restoration remains a separate policy.

GStreamer sinks borrowing a DRM descriptor hold a registered graph/resource
lease until tasks, callbacks, borrowed handles and relevant backend work have
actually drained. FD closure, a successful fsync, or a `GST_STATE_NULL` return
alone must not be assumed to cover outstanding driver work. Required fence and
completion semantics are implementation/qualification gates. The LCD barrier
likewise includes queued LVGL, fbdev and driver work, not just unmapping memory.

## Proposed control protocol

This is **Tyrell handoff draft 2**, not an implemented Gh0st version. It needs a
separate, explicitly provided endpoint, proposed as `TYRELL_VIDEO_HANDOFF_FD`.
The existing `GH0ST_SHELL_FD` transport is unchanged. Validate the new endpoint
before workers/device access: exclusive inherited FD above stderr, connected
Unix `SOCK_SEQPACKET`, expected credentials, noninheritance and bounded messages.
Never infer managed authority from an environment variable or UID check alone;
the qualified launcher must bind it to the authority's registered app instance.

Packets are proposed as strict ASCII tab-separated fields, at most 512 bytes,
one packet per message. Reject truncation, extra fields and unknown state-changing
verbs. No embedded commands, paths, arbitrary error text or FD payloads. Decode
IDs before use; no unchecked integer conversion.

`S` is a random 128-bit authority incarnation, `A` a registered 128-bit app
instance, `E` a nonzero uint64 ownership epoch, and `R` a nonzero uint64 request
ID. Hex identifiers use exactly 32 lowercase digits; decimal counters have no
leading zeroes and never wrap/reuse within an incarnation. Exhaustion requires
reconciled shutdown and a new incarnation. `T` below expands to `S A E R` as
four tab-separated fields. `L` and `C` are opaque 128-bit lease/receipt IDs
looked up on their authenticated authority channel.

| Packet after `TVH2` prefix | Direction | Meaning |
| --- | --- | --- |
| `hello A async-drain,epoch-barrier,helper-receipts` | App → shell | Request this exact capability set before acquisition. |
| `welcome S A async-drain,epoch-barrier,helper-receipts` | Shell → app | Confirm the bound authority and required capability set. No ownership yet. |
| `grant T L` | Shell → app | Forward an authority-issued fresh lease. Rebuild only within its registered domains. |
| `ready T L` | App → shell | Acquisition/rebuild completed; not a release receipt. |
| `quiesce T budget_ms` | Shell → app | Authority has sealed this epoch; begin asynchronous release. |
| `quiescing T phase` | App → shell | Progress only; does not extend the deadline or grant rights. |
| `local-done T checkpoint_id` | App → shell | Local release reported; the authority still requires independent participant completion. |
| `released T C` | Authority → shell | Durable composite release receipt on the separate authority channel. An app cannot self-certify it. |
| `commit T C` | Shell → app | Matching receipt committed; old epoch is hidden and may now be frozen. |
| `cancel T` | Shell → app | Withdraw user intent. Finish draining; no old rights return. |
| `failed T code` | Either control peer | Fixed failure code; query the authority's recovery state. |

The welcome must arrive before any managed resource acquisition. Missing or
incomplete negotiation, legacy protocol 1, malformed traffic or an adopted link
lost during negotiation keeps media admission closed. No automatic downgrade
is allowed after acquiring a resource. The exact parser and trusted app-channel
bootstrap are supplied by [D1](video-handoff-foundation.md); D2 independently
binds its disposable participant workers. Installed manager/service admission
still needs D5 qualification.

Progress phases are the fixed values `local`, `graph`, `display`, `audio` and
`stop-post`. Failure codes are `unsupported`, `malformed`, `stale`, `busy`,
`expired`, `identity-lost`, `backend-failed` and `completion-unknown`.
`checkpoint_id` is an opaque 128-bit correlation value or `none`; it never
contains a filename. Grants name a fixed registered resource set through `L`,
not caller-selected domains or device paths. `budget_ms` is decimal `1` through
`30000`, with no leading zeroes; zero, overflow and larger values are rejected.

Use a proposed 1,000 ms negotiation budget and a 10,000 ms handoff budget, with
an explicit 30,000 ms policy maximum. These are finite control-plane policy
choices to measure, not promised device timings or kernel deadlines. The
authority's monotonic clock, starting at request acceptance, orders expiry.
Peers use local timers for bounded reporting, never independent ownership
decisions. Progress does not reset the authority deadline. Process at most 64 packets per wake and bound queued
output. Backpressure cannot turn an unsent acknowledgment into completion.

## App and helper release sequence

```mermaid
sequenceDiagram
    participant S as Future shell
    participant O as Ownership authority
    participant V as Video control loop
    participant M as Media actor
    participant H as Registered helpers
    S->>O: Request quiesce(epoch, request)
    O->>O: Journal and seal acquisitions/actor registry
    O-->>S: Sealed transaction
    S->>V: quiesce(epoch, request, budget)
    V->>V: Stop input/UI submissions; clear stale edges
    V->>M: Capture checkpoint and drain graph
    M-->>O: Local and backend release evidence
    O->>H: Authorized cleanup under held permits
    H-->>O: Terminal completion and readback
    O->>O: Verify barrier; persist composite release
    O-->>S: released(receipt)
    S->>V: commit(receipt)
    S->>O: Request fresh successor grant
```

Keep all app tasks/cgroup members scheduled until the composite release. Move
blocking checkpoint/GStreamer lifecycle calls to one explicit media actor inside
that app lifecycle boundary. It owns graph access; the UI/control loop never
joins it synchronously to acknowledge pause. Queued controls, seeks, preroll and
output switches carry epochs and are rejected after sealing. An unresolved
capture or graph call retains ownership and the existing checkpoint's uncertainty.
Do not detach a worker and call it released.

After local submissions stop, drain graph/sinks and complete registered KMS
cleanup. Then complete the authority-selected VT transition and confirm its
effective state. Finalize audio and every stop-post obligation before composite
release. Resource-specific ordering must prevent VT restoration racing a still
active DRM client. Checkpoint retry budgets, tracks, position and play intent
retain the existing A2 semantics; ownership grants are an additional condition.

The audio participant records accepted load intent before submitting it and
keeps the original operation/connection alive through positive completion.
Cancellation requests draining and, if a module was created, exact owned unload
plus terminal confirmation. Bind ownership to server incarnation, lease token,
module ID and observed module identity/arguments; an index or sink name alone
does not authorize deleting a replacement or pre-existing sink.

A helper dying mid-load leaves an unknown accepted operation. A finite scan on
a new connection cannot prove that no late creation is possible. Keep the
domain quarantined until qualified server-side reconciliation/finality, a
controlled audio-session reset, or controlled baseline recovery proves it safe.
Resetting an audio server can affect other applications and is not an automatic
fallback in this design.

V2 `ExecStopPost` reports and reconciles through the same ledger. It must not
independently activate an old VT or scan/unload modules after the authority has
retired the epoch. A release receipt includes completion or positively verified
suppression of every such future cleanup path. Service exit alone is insufficient.

## State, cancellation and failure

The app progresses `UNGRANTED → FOREGROUND → QUIESCING → LOCAL_RELEASED → HIDDEN`.
The authority progresses `OWNED → SEALED → DRAINING → RELEASED`. Any uncertainty
can enter `QUARANTINED`; it cannot skip to `RELEASED` on time alone.

Duplicate requests return their recorded state without repeating side effects.
Stale incarnation/instance/epoch/request/receipt combinations are rejected.
Conflicting transitions are coalesced into the current drain, not overlapping
acquisitions. Resume or cancellation during drain finishes release first; a new
epoch/grant then rebuilds the checkpoint. Neither old `resume`, EOF nor pending
user intent reopens input or redraws a frame.

Release and expiry share the authority's serialized durable terminal decision.
If verified release wins first, replay that recorded receipt even if delivery
is delayed; a peer queries the authority instead of inventing another outcome.
If expiry wins, terminate the request as failed, reject attempts to resurrect
it and quarantine unresolved domains. Late operations remain registered and may finish
only under their existing authority; a separate recovery transaction must
reconcile them before a new grant. Killing/freezing a process is escalation,
not proof that its kernel/server operation completed. Do not retry indefinitely.

Journal accepted intent before side effects, and terminal state before retiring
a barrier or issuing a receipt. Authority restart creates a new incarnation
and reconstructs every unresolved permit as unknown. A dead PID, empty socket,
missing journal, or reset in-memory count is not an empty barrier. If durable
state is corrupt/unavailable, disable the managed domain pending controlled
recovery. Reconcile exact old cgroup/service and backend identities; never
adopt an old epoch because a PID or VT number happens to match.

Shell or authority loss revokes new work and prevents new, unpermitted old
foreground restoration. In-flight effects remain counted and may still finish;
reconnection cannot manufacture a grant. External administrator
changes similarly invalidate eligibility. Completed privacy locking needs
confirmed display/audio cutoff; a process signal, missing frame or timed-out
operation cannot establish that HDMI is blank or audio stopped. A target
without a qualified cutoff/recovery path cannot advertise this managed mode.

## Implementation and acceptance sequence

1. **D1 implemented with inert adapters:** the separate shell/authority channel, parser,
   authenticated bootstrap, durable ledger and operation barrier. Keep all
   device adapters inert. The [foundation checks](video-handoff-foundation.md)
   exercise adversarial ordering with real sockets, processes and held inert
   backend work. Actual device completion still requires the following steps.
2. **D2 standalone implementation:** separate v2 VT and normal-user audio
   backends, write-ahead participant registration and worker/stop-post fencing.
   [Disposable checks](video-handoff-participants.md) exercise completion,
   pending-load finality, cancellation and crash uncertainty. Live admission,
   installed service qualification and physical testing remain pending;
   legacy endpoints and privileges are preserved.
3. **D3 separate managed display APIs:** [KMS/LCD ownership and destructor
   policies](video-managed-display.md), independent graph/flush borrowers and
   backend completion evidence. The managed path has no old-epoch restoration;
   default M5 policy remains independent. Generic driver finality and live
   integration are still refused without qualification.
4. **D4 media actor/control pilot:** the [single-owner actor](video-media-actor.md)
   owns factory invocation, every graph call, polling and destruction. A borrowed
   UI adapter uses exact durable permits and immutable snapshots; production
   LCD/fake-audio graph fixtures exercise callback drain, finality refusal and
   fresh-grant checkpoint rebuild. Command completion alone does not retire a
   permit. This adds no universal teardown deadline or live HDMI qualification.
5. Integrate only with a shell that honors this asynchronous contract. Qualify
   exact new packages on M5 first, then each managed scenario. Enable Video's
   idle-loop work only after its ownership and wake-source design is qualified.

The [decision record](../research/video-async-handoff-design.json) lists concrete
future integrated cases and expected refusals. They remain **not run for the
complete design**. D1–D4 tests exercise their actual modules and disposable
backends; legacy checks and disposable fixtures cannot validate the future
integrated managed resource paths.

Before admission, require delayed/blocked graph teardown, input/render drain,
late helper creation, acquisition cancellation, pending audio load, cleanup
failure, stale/duplicate receipts, held-operation revocation, authority restart,
legacy coexistence rejection, external VT changes, KMS/fbdev work, privacy lock,
crash/SIGTERM and successful checkpoint rebuild tests. Inspect all actual
production call paths; a toy state model is insufficient.

Physical playing/paused LCD/HDMI, preroll/seek/track/caption/output transitions,
missing HDMI, audio/VT restoration, native launch/return and responsiveness
remain pending. The default Video path preserves M5 behavior; retained package
approvals, image inputs and historical evidence remain unchanged by the separate
D1–D4 implementation and do not qualify the new source additions on hardware.
