# Video handoff foundation

A2-D1, November 24, 2026. This page records the foundation increment. The separate `TVH2` channel, ownership authority and
durable ledger are implemented in
[`packages/tyrell-video-handoff`](../packages/tyrell-video-handoff/README.md).
These are actual reusable Python modules exercised with SQLite, Unix sockets
and processes. Resource adapters remain inert. Nothing is installed, activated
or connected to Video by this increment, and physical testing remains pending.
The later [D2 participant implementation](video-handoff-participants.md) adds
standalone VT/libpulse backends and a disposable worker harness. Its required
profile, schema and recovery boundaries are separate from D1's inert grants.

## Deployment and trust boundary

The foundation follows the
[asynchronous handoff design](video-async-handoff-design.md). It does not extend
Gh0st protocol 1 or change `GH0ST_SHELL_FD`. There is no default endpoint,
environment-based adoption, service unit, daemon, device operation or automatic
fallback. `Authority.live_hardware_admission` is `False`, and its persisted mode
is `inert` on the D1 control path. D2 adds an explicitly selected
`disposable-v2` profile and grant for disposable backend validation. A successful inert handoff is not a
hardware ownership grant or an approval to enable Video playback.

A trusted normal-user coordinator constructs `Session` with an app instance,
expected UID/PID and an exclusively owned descriptor. The connected endpoint
must be an `AF_UNIX` `SOCK_SEQPACKET` FD above stderr. The coordinator must adopt
receivers before allowing peers to send. Linux `SO_PEERCRED` checks the socket
creator's user, while `SO_PASSCRED`/`SCM_CREDENTIALS` check the actual sender's UID
and PID on every packet. This distinction matters for socketpairs inherited by
a child: the socket creator's PID does not identify that child.

`Session` also holds a Linux pidfd for the registered process and checks its
lifetime before grants and while pumping packets. A retained socket copy or a
later process with the same PID cannot inherit the dead registration's rights.
Packets queued without sender credentials, descriptor payloads, truncation,
malformed grammar and unexpected ancillary data close the channel. Received
descriptors are closed even when their packet is rejected, with close-on-exec
set at receive time. The adopted control FD and pidfd are noninheritable.

The expected identity comes from the coordinator's trusted registration, not a
wire field or an environment variable. App IDs, epochs and receipts correlate
transactions; they are not credentials. `App` and `Actor` objects are opaque
in-process coordinator capabilities. The app channel cannot register helpers,
complete their operations or issue an authority release receipt. Service/cgroup
qualification and installed helper authentication remain D5 work; D2 binds its
disposable worker identities separately. This cooperating
client contract does not confine arbitrary same-user code or serialize root
and administrator device changes.

## Durable ownership decisions

The authority serializes decisions and persists them before returning the
corresponding permit or receipt:

1. A negotiated app may receive one inert lease only while the composite
   ownership barrier is empty. Register actors and operations before allowing
   any represented work to start.
2. `begin_operation()` durably records a pending permit. Its actor retains that
   permit through backend completion. The D1 tests use controllable work;
   future adapters must bind the same ordering to their actual side effects.
3. Quiesce closes acquisition and actor registration in one durable seal. Only
   existing actors can request cleanup permits afterward; those permits remain
   in the barrier too. The persisted state changes from `OWNED` to `DRAINING`.
4. `local-done` can complete only the app actor, after its own operations are
   done. Independently held actor work still blocks release. Failure, actor
   loss, EOF and process death cannot stand in for completion.
5. The authority records `RELEASED`, its unique receipt and the empty active
   barrier atomically only after local completion, every actor's release and
   every operation's successful terminal result. The trusted coordinator reads
   that durable receipt through `release_receipt()` before issuing `commit` or
   requesting a successor grant.

Cancellation withdraws intent and continues the drain; it does not reopen old
rights. Duplicate progress, failure and terminal messages do not extend a
deadline or rewrite the winning result. Incarnation, app, epoch, request and
receipt mismatches are rejected. The serialized authority's monotonic clock
orders expiry against release. If expiry wins, the lease is quarantined and
late completion cannot turn that request into a release. If release wins,
the recorded receipt remains replayable after its deadline.

## Storage and restart behavior

`Ledger` stores the current snapshot and a hash-linked event history in one
SQLite transaction, using rollback journaling, `synchronous=FULL`, directory
sync and verification before acknowledging a commit. The private, caller-owned
directory has mode `0700`; its database and lifetime lock have mode `0600`.
A nonblocking exclusive flock prevents another authority writer. Symlinks,
hardlinks, unexpected files, unsupported schema, invalid history and replaced
inodes are rejected. The integrity hashes detect inconsistent bytes; they do
not authenticate data against a malicious process with the same user access.

Initialization and reopening are deliberately distinct. `create()` requires a
new directory. `open()` never initializes a missing or corrupt ledger. An
authority restart commits a new incarnation, invalidates old in-process
capabilities, quarantines outstanding leases and changes pending operations to
`UNKNOWN`. Previously released records remain terminal. Known storage errors
poison the running authority, including cached receipt queries; an uncertain
commit cannot return a new permit or successful release.

Current authority snapshots use schema 2 for D2 participant state. Earlier D1
schema-1 snapshots are refused without rewriting or automatic upgrade. The D1
test results below are historical evidence for that source increment, not a
live migration or approval to discard an old ledger.

There is no D1 reset, migration, pruning or quarantine-clear API. An unresolved
barrier stays blocked until a later qualified reconciliation design establishes
backend finality. Deleting the ledger or resetting counters would erase that
barrier and is not recovery. Keep output reports outside its dedicated
directory. Restart/process-kill tests verify atomic old-or-new ledger state;
they do not establish physical power-cut durability on target storage.

## Bounded work and timing

| Limit | D1 policy |
| --- | --- |
| Packet size and grammar | At most 512 ASCII bytes; exact fields, fixed verbs and capability set. |
| Channel output | At most 16 queued packets, at most 64 sends per flush. Queue acceptance is not peer delivery. |
| Session input | At most 64 received packets per pump. Socket calls use per-call nonblocking flags. |
| Negotiation | 1,000 ms; incomplete negotiation closes the session. |
| Handoff | Default 10,000 ms; accepted budgets from 1 through 30,000 ms; progress never resets the deadline. |
| Registry | At most 8 apps and 64 retained leases; at most 16 actors and 128 operations per lease. |
| Durable history | At most 2,048 commits; 256 KiB state, 16 KiB event and 16 MiB database. No silent eviction. |
| IDs | Lowercase 128-bit hexadecimal IDs and nonzero canonical uint64 counters; no counter wrap. |

The coordinator schedules `pump()`/`tick()` against its monotonic deadlines.
Bounded packet work is not a hard real-time guarantee: SQLite commits, fsync,
the filesystem and kernel scheduling may block. D1 does not call a device or
promise that a future GStreamer, KMS or audio operation ends within the policy
budget. A timeout retains uncertainty and ownership instead of reporting
successful cleanup.

## Validation and remaining work

At D1, all 65 foundation tests passed on Linux with zero skips: 7 protocol, 14 channel,
16 ledger, 19 authority and 9 session cases. The run used Python 3.11 in the
cached `node:24.21.0-bookworm` container, running as UID/GID 65534 with networking
disabled, all capabilities dropped, no-new-privileges, a read-only root and
repository mount, and disposable tmpfs storage. No device was opened.

The tests exercise the implemented modules rather than a separate state model.
They cover actual child-process credentials and pidfds, bounded backpressure,
rejected FD payloads, forged app receipts, held worker permits, worker death,
sealed registration, expiry/release ordering, duplicate terminal messages,
storage faults, writer exclusion, corrupt state and subprocess restart. Hosts
without the required Linux facilities skip channel/session cases; such a run
does not replace Linux execution.

From the repository root, run:

```sh
python3 -m unittest discover -s packages/tyrell-video-handoff/tests -v
```

The 19 cases in the original
[design decision record](../research/video-async-handoff-design.json) remain
pending for full integration. D1 evidence does not qualify real helper cleanup,
pending audio-load finality, KMS/fbdev completion, graph teardown, privacy cutoff,
legacy coexistence, shell integration or target storage power loss.

D2 now supplies separately versioned VT and normal-user audio backends,
disposable completion/cancellation checks and durable worker/stop-post fencing;
see its [implementation boundary](video-handoff-participants.md). D3 managed
display policies, the single-owner media actor and shell integration
follow before admission. Current Video, legacy helper privileges/endpoints,
M5 compatibility, image inputs and package approvals are unchanged. Physical
playing/paused, LCD/HDMI, audio/VT restoration and launch/return testing remain
pending.
