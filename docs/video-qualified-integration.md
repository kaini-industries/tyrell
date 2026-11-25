# Video shell and package integration candidate

A2-D5 adds a software qualification candidate connecting the actual Video App,
the D4 media actor and a credential-bound asynchronous shell controller. It also
builds a separate ARM64 qualification package and inspects its exact extracted
payload. **Live shell/device qualification is incomplete, and physical testing
remains pending.** The normal M5 player, legacy helpers, retained packages,
image inputs and Store approvals keep their existing policies and identities.

The candidate uses `TVH2`, a private memory LVGL display and production
LCD-frame capture with fake audio. It does not open the Cardputer framebuffer
or keyboard, an HDMI output, the manager console or a user's Pulse endpoint.
It is a fixed qualification peer, not a replacement launcher or a device app.
`Authority.live_hardware_admission` and the candidate's live-admission gate stay
false. No Video idle optimization is enabled by this increment.

## Shell and App ownership

[`ShellSession`](../packages/tyrell-video-handoff/handoff_shell.py) wraps the
production `Session`, authority and SQLite ledger. The trusted coordinator
provides the exclusively owned descriptor, registered app instance and expected
PID/UID. The fixed `inert` or `disposable-v2` profile must match the authority;
the disposable profile requires a normal user. No environment variable or app
packet supplies these rights. Linux per-message credentials and pidfds bind the
actual process lifetimes, including a child inheriting a socketpair endpoint.

The native [`Draft2Link`](../packages/tyrell-video/src/draft2_link.h) implements
the other side of that `TVH2` contract. Negotiation precedes a grant. A fresh
grant leaves the App gated until its coordinator has registered the graph
lifetime, supplied durable permits and completed readiness. The qualification
peer pumps lifecycle messages before its next App tick; a queued quiesce or
link loss closes input/render admission before that tick can act.

The shell journals quiesce before sending it and continues pumping while the
actor drains. Cancellation retains the current drain. App `local-done` reports
only the local seal: graph, display, driver and VT participants still complete
independently. The shell reads the authority's durable composite receipt,
queues its exact commit and drains its output before issuing a successor
grant. A queued commit is a transport fact, not proof that the App received it
or that physical output is private. The native peer accepts the bound commit
before entering Hidden, and a new epoch must rebuild before readiness reopens
the App.

App messages cannot invoke helper completion, mint a grant or manufacture a
release receipt. Expiry sends a fixed failure notice without signalling or
freezing a worker; unresolved work remains quarantined. The shell's fixed stop
reasons request durable disconnect before closing the bound session; a storage
failure propagates without reporting a successful transition. SIGTERM/SIGINT intent
does not certify graph drain, audio cutoff or display restoration. There is no
timeout-based quarantine reset or compatibility fallback to protocol 1.

## Separate qualification package

[`build-package.py`](../packages/tyrell-video-handoff/build-package.py) produces
`tyrell-video-handoff-candidate_0.1.0-1_arm64.deb`. It requires a clean exact Git
revision, a captured `SOURCE_DATE_EPOCH`, a regular ARM64 ELF native peer and a
fresh output directory. The peer comes from the Video CMake
`video-draft2-app-peer` target; it includes the actual App, memory LVGL adapter,
media actor and production LCD/fake-audio graph. The Python modules, fixed
[profile](../packages/tyrell-video-handoff/candidate-profile.json), documentation
and read-only inspector are packaged with it.

The private peer is installed under
`/usr/lib/tyrell-video-handoff/qualification/native-peer`. The package contains
no maintainer scripts, service/socket units, launcher entries, capabilities or
device-access rules. Installing or inspecting the kit cannot activate a managed
session or replace normal Video. CI assembles and extracts the package in a
disposable worker; it does not install it onto the host or a device.

Build in the disposable Debian ARM64 worker after building and checking the
native peer with the verified LVGL source and current Video prerequisites.
Use the actual native build path and a new output path:

```sh
export SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)"
python3 packages/tyrell-video-handoff/build-package.py \
  --native-peer /path/to/native-video-build/video-draft2-app-peer \
  --source-revision "$(git rev-parse HEAD)" \
  --output-dir /path/to/new-candidate-artifacts
```

The source manifest records source-file and dependency-lock hashes, the chosen
native ELF hash, payload sizes/modes, architecture and captured timestamp. The
builder validates the staged payload and then the bytes extracted from the
actual `.deb`, refusing extra private files, symlinks, missing modules or profile
changes. Its `a2-d5-candidate-package.json` report binds the resulting archive.

[`handoff_candidate.py`](../packages/tyrell-video-handoff/handoff_candidate.py)
can inspect an extracted payload without loading its modules or launching the
peer:

```sh
python3 -I packages/tyrell-video-handoff/handoff_candidate.py \
  --root /path/to/extracted-candidate
```

The installed `tyrell-video-handoff-candidate` command performs the same
read-only inspection. Adding `--check-live` deliberately refuses even an intact
candidate. Hashes establish integrity relative to the captured manifest, not a
trusted deployment attestation, a signature, compiler provenance or device
approval. Capturing a source revision and timestamp does not assert a frozen
toolchain or byte-identical rebuilding.

## Software checks and remaining qualification

The required `video-draft2-link` group exercises the native transport/state
machine. `video-draft2-integration` connects the Python shell controller and
native App/actor peer through real credential-bearing Linux sockets. Its
two-epoch trial holds an actual streaming callback and independent finality,
keeps App rendering gated, completes independent LCD/VT barriers, commits the
composite receipt and rebuilds the paused checkpoint after a fresh grant. A
separate link-loss trial keeps accepted work quarantined. These are software
scenarios, with controlled display and driver counterparts.

CI also reruns that App/shell trial using the native peer and Python modules
from the extracted candidate package, with no checkout-module fallback. The
package report and integration report retain their native-peer hash so their
identities can be compared. The shell-controller and package-inspection Python
checks remain separate from these native groups. Current source-bound results
are retained by CI; they do not upgrade historical device evidence.

The [pinned Gh0st shell](shell-link-status.md) still implements protocol 1,
including its short pause window. It is not this asynchronous shell and does
not become a qualified Video peer. Live D5 admission still requires an actual
shell/service deployment honoring this contract, exclusion of untracked legacy
writers and pending restoration, and qualified KMS/LCD/audio/VT finality.

The candidate profile explicitly retains device gates for an identified spare
target and image; exact new player/shell/package/dependency identities; playing,
paused, preroll, seek, track, caption and output transitions; native launch,
return, input and responsiveness; missing HDMI and external-owner conflicts;
and effective privacy cutoff plus crash/SIGTERM recovery. These tests remain
pending. The original design's 19 complete production acceptance cases are not
marked passed. D5 device qualification remains unfinished; Video idle work
follows only after the ownership and wake-source gates are qualified.
