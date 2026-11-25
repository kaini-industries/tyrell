# Video handoff foundation

A2-D1 implements the [asynchronous handoff design](../../docs/video-async-handoff-design.md)'s
control channel, ownership authority and durable ledger as reusable Python 3.11+
standard-library modules. The [implementation notes](../../docs/video-handoff-foundation.md)
describe their trust boundary and validation. A2-D2 adds separately versioned
[participants](../../docs/video-handoff-participants.md), actual VT/libpulse
backends and an explicitly disposable worker harness.
The [D3 display increment](../../docs/video-managed-display.md) adds native
managed KMS/LCD APIs and independent durable display borrowers and VT dependencies.
The [D4 media increment](../../docs/video-media-actor.md) connects the actual
media actor's graph facts to an independent borrower and durable command permits.

The [D5 software integration candidate](../../docs/video-qualified-integration.md)
connects the actual App and asynchronous shell controller. A separate inactive
ARM64 qualification package contains these modules and a fixed memory-LVGL/
LCD-fake-audio native peer. It is not a normal device app or a replacement launcher.
The modules are not installed into a launcher or helper service. D4's explicitly
injected Video control pilot uses a trusted coordinator; it is not a live launch path.
`Authority.live_hardware_admission` remains `False`. An inert `grant` exercises
only the control contract; it cannot authorize
LCD, input, DRM, VT, GStreamer or audio access. Video's existing playback guard,
legacy helper endpoints and normal M5 launch path retain their behavior.
The separate `disposable-v2` profile admits only private null audio and
controlled VT/display fixtures. Production HDMI/manager-console admission stays gated.

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
| `handoff_display_state.py` | Independent graph/flush borrowers and exact display-to-VT completion dependencies. |
| `handoff_media_state.py` | Exact media permits, native graph evidence, parked epoch fences and final producer fencing/reaping. |
| `handoff_shell.py` | Credential-bound fixed-profile shell transitions; durable receipt, commit and fresh-grant ordering. |
| `handoff_candidate.py` | Read-only exact qualification-payload inspection and unconditional live-admission refusal. |

The trusted coordinator supplies the registered app instance, UID, PID and
exclusively owned inherited descriptor. A wire packet or environment variable
cannot select that identity. `App` and `Actor` objects are in-process coordinator
capabilities, not peer credentials. Helper registration and completion APIs must
not be exposed as app commands.

Use `Authority.create(new_directory)` only for explicit first initialization;
use `Authority.open(existing_directory)` to reopen it. Schema 4 refuses earlier
schema-1 through schema-3 snapshots without rewriting or automatic upgrade. Reopening creates a new
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

D2's Linux group passed 119 tests with zero skips. Current required-suite and
native display results are retained separately in CI evidence.
This evidence does not qualify physical resource cleanup, a maximum device teardown
time or power-cut durability. The 19 full integration cases in the original
design record have not all passed. D3 supplies separate managed display policies;
D4 adds actual graph/actor/control and native-authority fixtures with LCD frame
capture and fake audio. Every dispatched media permit remains held until graph
drain plus a positive parked epoch fence, or final process fencing and reaping.
The D5 candidate adds an actual App/`TVH2` shell trial and repeats it using the
extracted package's peer and modules. `build-package.py` requires `--native-peer`,
the exact clean checkout's `--source-revision`, captured `SOURCE_DATE_EPOCH` and
a fresh `--output-dir`; see the [candidate guide](../../docs/video-qualified-integration.md)
for the disposable ARM64 workflow. The archive has no maintainer scripts,
services, launcher entries or activation. Its payload/source hashes detect
drift relative to its manifest; they are not deployment attestation or hardware
approval. Qualified live D5 shell/service deployment, legacy-writer exclusion,
driver finality and physical testing remain pending. The pinned Gh0st protocol-1
shell is not a qualified peer, and Video idle optimization remains gated.
