# Video handoff participants

A2-D2 adds separate VT and normal-user audio backends, durable participant
records and a disposable worker harness to the
[handoff foundation](video-handoff-foundation.md). The backends are actual
Linux ioctl and asynchronous libpulse implementations. They are standalone,
uninstalled and disconnected from Video, its existing helpers and every image
recipe. Physical testing remains pending.
The later [D3 display increment](video-managed-display.md) adds separate native
KMS/LCD ownership APIs and durable dependencies; it does not open live admission.
The [D4 media increment](video-media-actor.md) adds a single-owner graph actor,
borrowed UI control pilot and durable media permits. The D2 helper backends
remain separate from that LCD/fake-audio pilot.

## Admission and trusted bootstrap

`Authority.live_hardware_admission` remains `False`. An inert D1 grant cannot
authorize a device operation. D2 requires a newly initialized, explicitly
selected `disposable-v2` authority (`Authority.create(..., disposable=True)`)
and `grant_disposable()`. Its admitted profiles
are a private null audio sink and controlled VT ioctls. The production HDMI and
manager-console backends are implemented for later integration; the disposable
harness does not turn them into hardware-qualified participants.

The coordinator selects a fixed domain/profile and registers the participant
before launching its worker. Trusted launch registration, per-message kernel
credentials and a pidfd bind that exact worker's process lifetime. An app packet
cannot register a helper, choose a console or server, supply a module command,
or certify a helper's cleanup. Correlation tokens and evidence are not peer
credentials. D5 still needs qualified service/cgroup identities and manager
bootstrap on the target.

[`ParticipantWorker`](../packages/tyrell-video-handoff/handoff_participants.py)
currently admits only same-UID disposable workers. Its separate `TVP2` grammar
has a 2,048-byte bound, fixed verbs, canonical flat JSON evidence and exact
authority-incarnation/lease/actor/generation plus operation bindings. The app's
`TVH2` codec keeps its original 512-byte bound. Explicit socket-creator identity
support is bootstrap groundwork, not installed cross-UID service qualification.

No legacy socket, helper executable, service privilege, audio default or M5
launch path changes. The VT service's existing dedicated nonroot identity and
manager-opened console boundary remain the intended privilege split. D2 adds
no global root daemon, service installation, environment adoption or automatic
downgrade to legacy helpers.

## Durable worker and stop-post barriers

[`Participants`](../packages/tyrell-video-handoff/handoff_participant_state.py)
uses the same serialized authority and SQLite ledger as the app handoff.
Registration atomically creates two independent actors: the worker and its
future stop-post obligation. It also journals a launch permit, including the
already accepted acquisition obligation, before a process can start. Binding
the authenticated worker completes launch and records its acquisition operation
before invoking the backend. Binding after sealing continues that preaccepted
obligation; it does not admit an unregistered new acquisition.
The worker prepares its exact backend/server identity before loading a module;
that provenance is committed with the acquisition permit before dispatch.

The release order is:

1. Seal the epoch and record drain intent. A pending acquisition remains held;
   cancellation waits for its original backend result before cleanup.
2. Record a cleanup permit, execute cleanup, confirm backend completion and
   readback, and persist that evidence. Cleanup alone cannot release the lease.
3. Persist the worker's fence acknowledgment, then reap that exact process.
   Only the recorded fence followed by reaping retires the worker actor.
4. Durably suppress the separate stop-post actor. Before the worker is fenced,
   a stop-post request is held and performs no resource operation. After
   suppression, replay cannot start an old-epoch cleanup or restore a console.
5. Issue a composite receipt only after these obligations and every app and
   other participant barrier are complete.

Transport acceptance, peer closure and service exit do not replace any step.
Potentially blocking calls stay in the registered worker while the coordinator
can serve status and authority deadlines. A timeout retains the operation;
it does not terminate backend work or authorize a successor.
The worker also pins the coordinator with a pidfd, so a reused coordinator PID
cannot issue later commands. This disposable fork harness must launch from a
single-threaded coordinator before starting threads. It closes unrelated
inherited descriptors, including the authority database and lock.
Real audio workers use `AudioWorkerConfig` and a fixed isolated Python entry in
a fresh process. Libpulse [rejects context use after fork without exec](https://www.freedesktop.org/software/pulseaudio/doxygen/def_8h.html);
closed parent contexts are insufficient. Only the control FD, launch gate and
coordinator pidfd survive exec. Trusted callback/VT fixtures may use the direct
fork factory path; no caller-selected executable or generic command is loaded.

Abandoning a worker requests termination and closes its channel without waiting
for a potentially blocked kernel call. The coordinator retains the supervisor
and exact pidfd, calling `pump()` or `poll_exit()` until nonblocking reaping
confirms exit. Failed constructors retain the same tracking in `reap_pending()`.
Supervisor calls belong to one serialized control loop. This never turns
unknown backend work into release.

## VT backend

[`VtBackend`](../packages/tyrell-video-handoff/handoff_vt.py) accepts only an
explicitly provided, exclusively manager-opened `tty0` descriptor. It validates
the character-device identity and owns a close-on-exec duplicate; it never
opens a path or searches the environment. Device type alone does not establish
manager provenance: the trusted launcher must supply the registered descriptor.

The trusted configuration fixes separate managed and neutral VTs. Acquisition
requires the neutral VT to be effective. Release requires the managed VT and
an explicit trusted display-drain prerequisite, then switches only to the
configured neutral target. It never restores an app-supplied or captured old
foreground VT. D3's separate dependent VT profile binds display drain to both
completed KMS/LCD barriers; a peer boolean cannot supply that proof.

Each transition uses `VT_ACTIVATE`, `VT_WAITACTIVE` and effective `VT_GETSTATE`
readback. Activation can queue a switch, and waiting may block; the permit stays
held throughout. The [Linux VT implementation](https://github.com/torvalds/linux/blob/master/drivers/tty/vt/vt_ioctl.c)
and [ABI definitions](https://github.com/torvalds/linux/blob/master/include/uapi/linux/vt.h)
describe this distinction. An unexpected external VT, ioctl failure or failed
readback leaves unknown state and no completion evidence. Close and terminal
replay never perform another restoration.

The evidence describes completion at that operation boundary, not continuous
ownership monitoring. Later external switches and real driver semantics still
need D3/D5 handling and target qualification. D2 does not open a physical VT in
its controlled tests.

## Audio backend

[`PulseAudioBackend`](../packages/tyrell-video-handoff/handoff_audio.py) uses
one original asynchronous libpulse context in a registered normal-user worker.
Its production profile fixes the VC4 HDMI module scope. The null-sink profile
is explicit disposable policy, never a fallback when HDMI fails. It does not
autospawn an audio server or select the user's default sink.

The coordinator supplies a trusted Unix audio endpoint. Socket ownership,
kernel peer UID/PID, a pidfd and endpoint identity bind the server process;
the original context, server cookie and random generation bind the accepted
module work. The cookie is correlation, not authentication or a sufficient
server identity by itself. Target user-session/service qualification remains
pending.

The credential probe and libpulse connection are separate connections. They
do not by themselves authenticate libpulse's internal transport against every
replacement race. Qualification therefore needs a private, controlled endpoint
and no conflicting module writers; arbitrary same-user or administrator changes
are outside this cooperating-client contract. Acquire evidence records the
observed server UID/PID and endpoint identity alongside the module identity.

An accepted load operation, callback and result storage remain alive until
positive callback completion on that context. Cancellation queues drain; it
does not call `pa_operation_cancel`, discard the pending load or scan on a new
connection. If creation succeeds, cleanup verifies the exact observed module
index, name, arguments and lease token on the original server generation,
unloads that owned module, confirms the unload callback and verifies absence.
An index or sink name alone cannot authorize removing a pre-existing or
replacement module.

Connection loss, server loss or callback uncertainty quarantines accepted work.
A fresh scan cannot prove that an old accepted load will never appear. There
is no reconnect-and-cleanup path, automatic server reset or broad module scan
that clears ownership. Abandoning a lost worker closes its local connection
without converting that abandonment into a release receipt. Reconciliation
needs qualified backend finality or a separately controlled baseline recovery.

## Schema, restart and validation

The D2 snapshot was schema 2, with participant generations, operation
bindings, evidence and stop-post state. D3 introduced schema 3 for display
borrowers and exact VT dependencies; D4 now uses schema 4 for media-producer
evidence as well. Existing schema-1 through schema-3 ledgers are refused
without rewriting or automatic upgrade. They must not be deleted or recreated
to bypass an outstanding barrier. New disposable tests initialize their own
private ledger; there is no live migration or quarantine-clear API.

Restart creates a new incarnation and preserves accepted unresolved work as
unknown and quarantined. A worker dying during load, transition, cleanup or
fencing cannot certify finality. Lost channel/process/server identity similarly
retains the barrier. Durable storage faults prevent successful permits,
fences and receipts. These are conservative software recovery states, not
automatic device recovery or power-cut durability qualification.

The required Linux suite exercises the actual modules with disposable processes,
SQLite, sockets, a private Pulse server and null sink, and controlled VT ioctls.
It includes pending-load cancellation, exact module identity, cleanup failures,
blocked VT work through expiry, worker/server loss, stop-post ordering and
restart. Host runs that skip Linux or libpulse coverage are incomplete.
The original D1 run of 65 tests remains historical evidence for that increment;
D2's Linux group passed 119 tests with zero skips, including 20 audio
backend cases, 12 VT cases, real worker lifecycle and coordinator-death tests.
Per-group results are retained in CI evidence. The local run used a disposable
container, a read-only repository mount and normal-user test execution;
PulseAudio/libpulse were installed only inside that container. No target device
or user's audio session was contacted.

The 19 full cases in the original
[design record](../research/video-async-handoff-design.json) remain pending for
complete integration. Disposable audio finality and wrapped VT calls do not
qualify HDMI sound, KMS/fbdev completion, graph teardown, privacy cutoff or
physical launch/return. D3's [managed display policies](video-managed-display.md)
now have separate native/authority coverage. D4's
[media actor/control pilot](video-media-actor.md) adds actual LCD/fake-audio
graph and durable producer coverage. D5 qualified shell/package/device
integration is next. Video's guard remains
closed and physical testing remains pending.
