# Video handoff foundation

A2-D1 implements the [asynchronous handoff design](../../docs/video-async-handoff-design.md)'s
control channel, ownership authority and durable ledger as reusable Python 3.11+
standard-library modules. The [implementation notes](../../docs/video-handoff-foundation.md)
describe their trust boundary and validation. A2-D2 adds separately versioned
[participants](../../docs/video-handoff-participants.md), actual VT/libpulse
backends and an explicitly disposable worker harness.

The modules are not installed or connected to Video, a launcher or any helper.
`Authority.live_hardware_admission` remains `False`. An inert `grant` exercises
only the control contract; it cannot authorize
LCD, input, DRM, VT, GStreamer or audio access. Video's existing playback guard,
legacy helper endpoints and normal M5 launch path retain their behavior.
The separate `disposable-v2` profile admits only private null audio and
controlled VT fixtures. Production HDMI/manager-console admission stays gated.

| Module | Responsibility |
| --- | --- |
| `handoff_protocol.py` | Strict, bounded `TVH2` packet parsing and encoding. |
| `handoff_channel.py` | Linux Unix sequenced-packet transport with per-message sender credentials and bounded output. |
| `handoff_session.py` | Trusted-launcher app bootstrap, Linux pidfd lifetime checks, negotiation and bounded packet pumping. |
| `handoff_authority.py` | Serialized write-ahead permits, registry sealing, independent actor completion, terminal release decisions and quarantine. |
| `handoff_ledger.py` | Exclusive, verified SQLite state and append-only event history. |
| `handoff_participant_state.py` | Durable worker launch/acquire/cleanup permits and independent stop-post fencing. |
| `handoff_participants.py` | Separate `TVP2` codec, authenticated disposable workers, exact process reaping and supervisor hooks. |
| `handoff_participant_worker.py` | Fixed isolated fresh-process entry for the null-audio configuration. |
| `handoff_vt.py` | Fixed manager-console scope, effective VT completion and conservative refusal. |
| `handoff_audio.py` | Original-context libpulse load completion, exact owned unload and absence readback. |

The trusted coordinator supplies the registered app instance, UID, PID and
exclusively owned inherited descriptor. A wire packet or environment variable
cannot select that identity. `App` and `Actor` objects are in-process coordinator
capabilities, not peer credentials. Helper registration and completion APIs must
not be exposed as app commands.

Use `Authority.create(new_directory)` only for explicit first initialization;
use `Authority.open(existing_directory)` to reopen it. Schema 2 refuses earlier
D1 schema-1 snapshots without rewriting or automatic upgrade. Reopening creates a new
incarnation and retains unresolved ownership as quarantined, with pending
operations marked unknown. No API clears quarantine, discards history or
recreates missing/corrupt state. Do not delete a ledger to resolve a blocked
lease. The ledger directory is dedicated to its private database and lock;
write reports elsewhere.

Run the module tests from the repository root:

```sh
python3 -m unittest discover -s packages/tyrell-video-handoff/tests -v
```

Full transport/session/participant coverage requires Linux with `SOCK_SEQPACKET`,
`SO_PASSCRED`, `SCM_CREDENTIALS`, `MSG_CMSG_CLOEXEC` and pidfds. Unsupported hosts
skip those cases; a host-only pass does not establish Linux IPC coverage.
Current audio integration also needs libpulse and a disposable private Pulse
server. D1's historical 65 tests passed on Linux with zero skips, including real child processes,
blocked worker completion, process death, packet forgery, restart and storage
failure. The run used a disposable, nonroot, network-disabled container with a
read-only repository mount.

The current Linux group passed 119 tests with zero skips; required-suite results
are retained in CI evidence.
This evidence does not qualify physical resource cleanup, a maximum device teardown
time or power-cut durability. The 19 full integration cases in the original
design record remain pending. D3 supplies managed display policies; physical
testing remains pending.
