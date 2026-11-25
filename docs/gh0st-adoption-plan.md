# Gh0st ideas adoption plan

Plan revision 1, updated November 25, 2026. Status: **F1/U1/R1/C1 implemented; L1/A1 and A3 offline Chess/Sandline/Beacon source increments implemented with physical gates pending; A3-LAN-1 Chess controller primitives implemented with linked admission closed and A3-LAN-2 native/loop integration pending; A2 checkpoint/timing and D1–D4 source increments implemented, D5 software shell/package candidate added with live/device qualification pending; F2/H1 observation preparation, H2 offline policy rehearsal and H3 source/experiment preparation implemented; physical testing, live power activation and experimental BSP builds pending**.

Adopt Gh0st's useful application, UI and delivery patterns while keeping Tyrell's
apps, existing renderers, original M5 APPLaunch behavior and protected hardware
stack. This is an incremental integration plan, not a replacement-shell project.

The reviewed starting points are Tyrell `45bf9fbc2e3cffd1e30bb3613f67fd321d843b7a`
(merged PR #5) and Gh0st `a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5`.
Gh0st's public v0.2.0 alpha targets the earlier
`46653d2295250d385a7706c544d7eb02a2276428`; current-main source is not evidence
that every feature ships in that release.

This plan supplements the [Zero implementation plan](tyrell-zero-implementation-plan.md).
That plan's exclusion of application power optimization still applies to its
base-system experiment. App changes below have separate package versions and
matched comparisons. Existing Z3 exports and qualification records remain
historical evidence; new changes cannot inherit their acceptance automatically.

## Decisions and boundaries

| Adopt | Preserve | Defer |
| --- | --- | --- |
| Shared action/help definitions and deterministic UI checks | LVGL/raylib renderers, artwork, controls and app-specific behavior | A new shell, a Rust rewrite or a second UI stack |
| Optional shell lifecycle integration and event-driven idle screens | Original M5 launch paths and operation without Gh0st | Universal Gh0st compatibility or uninterrupted LAN hosting claims |
| Scoped CI, immutable candidates and explicit release promotion | Exact-input capture, image verification and package qualification | Automatic base OTA, migrations or moving stable/edge channels |
| Cached, signed discovery for Tyrell-qualified packages | Root-owned exact-byte Store authorization and preserved vendor UI | Trusting every package in an upstream signed catalog |
| Focused backlight, USB and display experiments | Known BSP and all native hardware for the baseline | Default governor/GPU/CMA/swap changes or an automatic kernel upgrade |

Compatibility rules apply to every work package:

1. Original M5 launcher and preserved vendor Store bytes remain intact. Keep
   application desktop paths, working directories, user/group permissions and
   install/remove behavior compatible. Optional Gh0st metadata cannot change
   normal M5 launching.
2. Keep Status, Video, Chess, Sandline and Beacon Arena independently buildable.
   Utility helpers must not pull GStreamer, LVGL or raylib into unrelated apps.
   Game simulation, network schemas and chess rules remain app-owned.
3. Preserve normal-user operation. Shell IPC never authorizes root actions.
   Maintain the guarded Store's immutable staging, protected paths and
   app-scoped transaction recovery.
4. Version changed app packages and retain their exact bytes and manifests.
   Source/recipe/runtime changes invalidate affected source, compatibility,
   activation and image receipts. Requalify through existing tooling; do not
   edit receipt hashes to make a changed implementation appear accepted.
5. Adding Chess or games to a Zero image is an explicit, separately qualified
   package-selection change. They are not part of the current default payload.
6. Record implemented, automated-tested, device-tested, failed and not-tested
   states separately. Skips and missing hardware are not passes.
7. Pin upstream references and record attribution/licenses for reused code.
   Panel-driver ports require their GPL source/distribution obligations; the
   Rust SDK's license does not cover every upstream asset or driver.
8. Continue integrating official M5 updates through reviewed baseline refreshes.
   Zero's source/BSP/launcher/package locks must move coherently; the existing
   stock-image refresh does not update Zero automatically. Preserve old inputs
   and qualify a new candidate through the [Zero update path](stock-image-updates.md#tyrell-zero-base-updates).

## Starting evidence

| Artifact or capability | Current state | Consequence |
| --- | --- | --- |
| Zero and matched full-source control | Z3 file construction and verification passed | Preserve exports; use them as the original comparison pair |
| Zero hardware, settled memory and battery | Z4/Z5/Z6 physical acceptance pending | Prepare tooling now; qualify on an identified spare target later |
| Tyrell applications | Different versions have different device qualification | Inventory exact binaries; do not treat current source as a qualified package |
| Video HDMI/LCD/audio | Existing stock-device evidence for specific packages/profiles | Recheck after app changes or native-session/BSP changes |
| Power-policy package | Baseline no-op; optimized activation unimplemented | Complete existing Z6 design rather than copying Gh0st defaults |
| Gh0st interoperability | Source-compatible launch conventions; no Tyrell device acceptance | Start with Status and claim compatibility per app/build/scenario |

See [image evidence](tyrell-zero-image-validation.md),
[progress](tyrell-zero-progress.md), [Video qualification](video-hdmi-017-validation.md),
[Chess version boundary](chess-clock-validation.md) and
[power package](../packages/tyrell-zero-power-policy/README.md).

## Delivery order

Each ID is a work package, split into the reviewable PRs described below. All
new paths and interfaces in this plan are **proposed**. No deadlines are assumed
for physical work before a test card, device and required accessories are selected.

| Order / ID | Deliverable | Dependencies | Completion gate |
| --- | --- | --- | --- |
| 1 / F1 | Compatibility ledger and scoped CI | None | Existing portable checks run; exact candidate and evidence scopes are explicit |
| 2 / F2 | Baseline acceptance/measurement tooling; Z4 then Z5 | F1; physical portion needs an identified spare target | Unchanged baseline accepted; matched memory/pressure observations recorded |
| 2 / U1 | Shared commands/help and deterministic UI checks | F1; parallel with F2 | Pilot actions, dispatch and help agree without changing rendering stacks |
| 3 / L1 | Optional shell-link library and Status pilot | F1; protocol/loss policy settled | M5 regression checks plus real pause/resume and hidden-state ownership checks |
| 4 / A1 | Event-driven Status/Video idle states | L1 for Status; A2 for Video | Fewer idle wakeups/CPU with unchanged responsiveness and active playback |
| 4 / A2 | Video checkpoint and bounded lifecycle integration | L1; timing feasibility spike | Correct resource handoff within the reviewed shell's pause window |
| 4 / A3 | GameRuntime hooks and per-game lifecycle policy | L1; offline Chess first | No stale input, clock advantage, lost state or false LAN-continuity claim |
| 5 / R1 | Immutable candidate packaging and release promotion | F1; evidence for selected payload | Exact tested bytes, declared hardware status and retained recovery artifact |
| 6 / C1 | Signed qualified catalog and separate cached frontend | F1, U1, R1 signing/delivery conventions | Metadata cannot expand root authorization or replace preserved M5 UI |
| Later / H1 | Backlight investigation and conditional correction | F2 baseline identities; separate intervention | Reproduced fault and proven ownership before any fix |
| Later / H2 | Existing Z6 guarded power qualification | Accepted Z4/Z5 baseline | Functional/performance and paired battery evidence, or baseline fallback |
| Optional / H3 | TE/present panel experiment | Accepted baseline; separate BSP profile | Measured benefit and complete application/hardware requalification |

Offline implementation/fixtures for U1, L1 and H2 may proceed while physical
F2 is pending. Their changed binaries must not replace the apps in the original
Z4/Z5 control experiment. Hardware acceptance is required for release claims,
not for opening a small code PR or running host tests.

### F1 — Compatibility ledger and scoped CI

Implemented interfaces and limits are recorded in the
[compatibility matrix](compatibility-matrix.md) and
[CI workflow guide](continuous-integration.md). Candidate dispatch performs
captured-input preflight only; image construction stays in the existing frozen
pipeline, and release promotion remains R1. F1 does not complete hardware gates.

First PR: add `docs/compatibility-matrix.md`, a versioned machine-readable
qualification record under `research/`, and reusable check entry points under
`scripts/ci/`. Record app source/package identity, original M5/Zero/Gh0st target,
scenario, toolchain, result, evidence and known limitation. Include stock-derived
and full-source build modes so Zero work cannot silently break them.

Add `.github/workflows/checks.yml` for affected portable Python suites under
`builder/tests` and `image/tests`, app CTest groups and existing game/chess
headless checks. Use disposable Linux ARM64 workers for native package and
isolated install/integration checks. LVGL/GStreamer platform prerequisites
must be declared, not silently skipped. Reuse existing tests where they cover
the behavior; add tests for new boundaries rather than asserting internal layout.

Separate a manually dispatched image-candidate workflow from ordinary PR checks.
It consumes an explicitly identified retained input bundle with verified hashes.
Missing generated inputs fail clearly; CI must not rebuild a moving equivalent
and call it the old input. Documentation-only PRs run documentation checks, not
full image builds. Trigger coverage must include all consumed source, assets,
recipes, locks and toolchain definitions.

**Gate:** no CI job installs base-integration packages into the persistent live
toolchain or touches a device. Required jobs are tied to the exact candidate
commit/input identity, and failure or an unresolved required skip blocks promotion.

### F2 — Preserve and measure the unchanged baseline

**Implemented preparation:** [bounded baseline observations](baseline-observations.md)
provides opt-in read-only collection, exact retained-reference plans, bundle
integrity checks and a conservative descriptive memory comparison. The tools
never write a device or satisfy hardware acceptance. Z4 execution and Z5
settled-memory/pressure evidence remain pending on an identified spare target.

Add an opt-in, bounded collector and run-record format for exact image/app/BSP
identities, effective power controls, MemAvailable/PSS, pressure, CPU/wakeups,
thermal/throttle state, transfer activity and hardware idle return. Record
sampling overhead, start/settle/duration and raw observations. Collectors are
inactive by default and do not tune the device.

Execute existing Z4 on the exact exported Z3 baseline: provisioning/reboot,
PAM/seat/VT, M5 launcher/settings/guarded Store, native app return and all fifteen
[hardware categories](../image/contracts/tyrell-zero-hardware.json). Preserve the
original reference card. Build a concrete write/backup/recovery procedure with
the image checksum and identified spare reader/card before any device write.
The earlier file-validation instruction is not authorization to choose a disk.

Then execute Z5 with the matched full-source control and identical app versions,
governor/USB policy, GPU/CMA/swap, display/audio and network conditions. Retain
the existing targets and memory/pressure requirements in the Zero plan. Keep
base, app and tuning interventions separate. Report SPI transfer equivalents
separately from rendered frames and optical scanout; report short-window gauge
current separately from energy or usable runtime.

**Gate:** no provisioning, input-ownership, media restoration or peripheral
regression. Partial accessory coverage stays partial. No new lighter-RAM or
battery claim follows from footprint checks alone.

### U1 — Shared commands and deterministic UI checks

The [shared UI implementation](shared-commands-ui.md) records the command model,
repeat boundaries, UI-only test clocks, renderer checks and package qualification
limits. Source is Video 0.1.8 and Chess 0.1.3; original app packages/image captures
retain their earlier exact-byte evidence.

Pilot a small command descriptor in Video: action ID, keys, label/help,
availability, repeat policy and confirmation behavior. Derive dispatch, help and
footer actions from the same definitions. Apply it to Chess contextual help,
then extract only the proven common model into proposed
`packages/tyrell-app-common/` as `Tyrell::AppCommands`. Preserve current key
bindings and art; keep LVGL and raylib rendering in their current apps.

Extend existing headless tests with scripted input and fake time. Check enabled
actions against visible help, repeat-safe confirmations and semantic bounds at
320×170: long filenames/errors, network text, track names and chess choices.
Use representative rendered frames for review rather than making every pixel
an immutable golden image. Apply deterministic time to UI behavior; production
match clocks retain their existing authoritative time semantics.

**Gate:** existing keyboard behavior and navigation pass, including rapid taps,
held keys and `SYN_DROPPED`. No game rules or network protocol changes. This can
ship independently of Gh0st support.

### L1 — Optional shell link, piloted in Status

Implementation: [optional shell link and Status pilot](shell-link-status.md).
The default M5 build keeps integration disabled. Automated fake-shell checks
exercise the pilot; physical M5 and pinned-Gh0st acceptance remain pending.

First document a pinned supported protocol subset, then add a dependency-light
C++17 `Tyrell::ShellLink` target in `packages/tyrell-app-common/`, separate from
the command model. Expose a pollable descriptor, bounded nonblocking messages,
pending lifecycle events and explicit acknowledgment. Apps own their policy;
the transport does not pause a match or stop a media pipeline on its own.

Support only demonstrated `hello`, `pause`, `paused`, `resume`, `redraw` and
`awake` behavior initially. Use message boundaries, the 512-byte limit and
`MSG_NOSIGNAL`; reject truncation/malformed packets. Do not depend on proposed
shell-owned bars, themes or content rectangles.

Initialize before app worker threads. Validate `GH0ST_SHELL_FD` text, open FD,
socket family/type and ownership before adopting it. Prevent inheritance into
helpers and do not close/alter an unrelated FD on validation failure. With no
valid link, normal M5 operation continues. Keep native Gh0st integration behind
an explicit build option until each app is qualified; desktop/headless builds
must remain usable without Linux device IPC.

Pilot Status: pause stops display writes and input reads, releases/reset input
ownership, then acknowledges. Resume reacquires input, discards stale edges and
redraws the complete frame. Exercise repeated/out-of-order events, unknown verbs,
backpressure, malformed FD values including 0–2, regular-file/wrong-socket FDs,
peer close, descriptor inheritance and shutdown using a fake shell/socketpair.

**Important constraints:** reviewed Gh0st waits only about 100 ms before it may
SIGSTOP an app; it offers no pause-refusal or extension negotiation. `awake`
prevents idle dark, not switching or locking. Link loss while hidden does not
establish that the app owns the foreground: do not automatically reopen input
and draw on EOF. Specify and test a recovery policy before shipping. A hidden
or ownership-lost exit must suppress restoration of a saved framebuffer over
another app; preserve existing foreground restoration on normal M5 exit.
Existing GameRuntime destruction restores the prelaunch frame, so existing
cleanup alone is not sufficient. Worker/helper cancellation and exit deadlines
need explicit implementation before describing this recovery as bounded.

**Gate:** Status passes unchanged M5 launch/exit/input checks, fake-shell failure
checks and separate physical tests against a pinned Gh0st image/build. If no
Gh0st device test is available, mark integration experimental, not compatible.

### A1/A2 — Idle loops and Video lifecycle

A1 Status implementation and comparison gates: [event-driven Status idle loop](status-idle-loop.md).
Video checkpoint, lifecycle and idle changes remain separate A2 work.

A2's [checkpoint and timing increment](video-checkpoint-lifecycle.md) supplies
the snapshot/rebuild API and a fail-closed qualification guard. Blocking graph
teardown and asynchronous HDMI/audio/VT restoration prevent qualifying live
media against the current 100 ms protocol. Active integration and Video idle
optimization remain gated by a revised completion/ownership design.

The follow-up [asynchronous handoff design](video-async-handoff-design.md)
specifies a separate opt-in protocol, shared operation barrier and observable
helper completion. It preserves the closed guard. The separate
[A2-D1 channel/authority/ledger foundation](video-handoff-foundation.md) is
implemented and tested with inert device adapters. The separate
[A2-D2 VT/audio participants](video-handoff-participants.md) add actual backends,
disposable worker validation and durable stop-post fencing. The separate
[A2-D3 managed display APIs](video-managed-display.md) supply explicit KMS/LCD
policies, independent producer barriers and exact VT dependencies.
[A2-D4 media actor/control integration](video-media-actor.md) now owns the actual
graph lifecycle, connects native graph/fence evidence to durable media permits
and exercises a borrowed UI adapter with LCD/fake-audio fixtures.
[A2-D5's software integration candidate](video-qualified-integration.md) connects
the actual App to a credential-bound asynchronous shell controller and builds a
separate inactive ARM64 qualification package. Live admission stays closed.
**A2-D5 device qualification remains incomplete:** the deployed asynchronous
shell, legacy-writer exclusion, qualified driver finality and exact device
acceptance are still required before idle work. Physical acceptance stays
pending. D1–D5 software checks do not
complete the design's integrated acceptance
cases, which must exercise the eventual production resource paths.

Split this into a Status idle-loop PR, a Video checkpoint/timing PR and a Video
integration/idle-loop PR. Status waits on keyboard, link, shutdown and its real
telemetry/UI deadlines. Video needs explicit wake sources for GStreamer bus/new
frames, input, link, UI timers, notices and HDMI/audio recovery. Streaming
callbacks signal work; they do not render the UI or wait for it.

Video needs a suspend checkpoint distinct from user pause and destructive stop.
Capture file identity, fresh position, play/pause intent, tracks, volume/mute,
output preference and decoder-recovery state. Define release/reacquisition of
LCD/HDMI/VT/audio/helper resources. Reuse bounded decoder retry and LCD fallback
behavior instead of resetting its retry budget on every resume.

Run an early timing spike: existing user pause retains resources, and pipeline
teardown may block. Acknowledge `paused` only after the defined safe state is
actually reached. If required handoff cannot reliably finish before the shell's
100 ms limit, keep that scenario unsupported and resolve the protocol or
ownership design first. A helper outside the stopped group requires a separate
reviewed lifecycle design; it is not an assumed workaround.

**Gate:** playing/paused LCD and HDMI, preroll/seek/output transitions, captions,
track selection, missing HDMI on resume, shell exit, SIGTERM/crash and audio/VT
restoration pass. Measure idle wakeups/CPU, input latency, playback drops and
audio underruns against the unchanged app on the same OS. Declare tolerances
before trials; require lower idle work with no material responsiveness/media
regression. Preserve active frame pacing and avoid new periodic daemons.

### A3 — GameRuntime and per-game lifecycle

**Implemented source increments:** GameRuntime 0.1.1 adds ownership/input/redraw
hooks and Chess 0.1.4 adds the optional native offline policy. See the
[Chess implementation boundary](game-chess-lifecycle.md). Sandline 0.1.1 now adds
its own [offline Driving Range/menu policy](sandline-offline-lifecycle.md), with
state preservation, first-interval simulation-debt discard and native input/render
ownership checks. Framework/example 0.1.2 adds Beacon's independent
[offline session policy](beacon-offline-lifecycle.md), preserving actors and the
fixed-step remainder across suspension without resetting its Session. Normal
builds disable shell support. Controlled checks cover pause/resume and failure
paths; physical M5/Gh0st acceptance remains pending. Linked LAN is gated in all
three app pilots. The [LAN policy design](game-lan-lifecycle-design.md) now selects
local session termination on observed shell suspension, with separate host/client
authority and future acceptance cases. The subsequent
[A3-LAN-1 controller increment](chess-lan-terminal.md) adds irreversible local
sealing, host elapsed-time settlement, confirmed joiner snapshots, pending
request evidence and separate transport cleanup. Its Chess-only 10-second gap
and stale-authority policy requires explicit controller opt-in. Linked admission
stays closed, and physical qualification remains pending. The next source
increment is **A3-LAN-2: Chess production loop and optional native adapter**.
The A3 offline source
increments are implemented, with the full device gate below still open.
Video D5 and idle gates are unchanged.

First add native input suspend/reopen/reset and full-redraw hooks to
`packages/tyrell-game/`; keep shell transport and match policy outside simulation
and networking modules. Integrate offline Chess before LAN games. Settle pending
state and invalidate obsolete AI results without waiting for a long search.
Resume must clear held keys/stale events and avoid simulation catch-up bursts.
Keep real elapsed time for peer/input/snapshot expiry; do not globally reset or
clamp `Runtime::elapsed()` to solve simulation catch-up. Sandline and Beacon
consume it for liveness independently of their bounded simulation clocks.
Add an ownership-aware native close path so hidden/lost-link cleanup releases
descriptors without repainting the prelaunch frame over another foreground app.
Bound cooperative AI cancellation/shutdown explicitly; current Chess exit waits
for its worker, so stale-result invalidation alone does not bound process exit.

OS switching must not grant extra time in a clocked match: retain authoritative
monotonic elapsed time and resolve expiry before accepting a move after resume.
An explicit existing in-game pause remains a separate action. Test flag-fall
boundaries, AI completion while hidden, failed saves and repeated lifecycle events.

Choose and document a live-LAN policy before enabling it: coordinated peer-aware
suspension, separately qualified background operation, or a visible ended/lost
connection outcome. Neither `awake` nor a desktop background preference
guarantees continued scheduling. Preserve saves and present an honest match
state after a long stop; do not freeze one participant's clock or conceal timeout.
Apply the same transport/ownership hooks to Sandline and Beacon independently.

**Gate:** existing native/desktop/headless builds, chess rules/clocks and network
tests pass. Qualify each new package on M5 first, then each advertised Gh0st
scenario. Offline support can ship while live-LAN support remains experimental.
Long-suspension tests must expire stale peers/inputs while avoiding a burst of
simulation steps, and hidden-exit tests must leave the foreground pixels intact.

### R1 — Immutable candidates and explicit promotion

**Implemented source increment:** deterministic candidate assembly and
independent archive verification, authenticated source/candidate checks and a
separate reviewed promotion request are described in
[immutable candidates](immutable-candidates.md). Publication is a manual,
serialized default-branch workflow with immutable release protection, new tags
and verified draft assets. Development artifacts retain missing device gates;
hardware alpha requires exact-artifact Z4/app acceptance and optimized power
additionally requires Z6. No release or hardware promotion follows from merging
this implementation.

Add a release-manifest generator and candidate workflow using existing export
verification. Bundle the image/app DEBs as applicable, SHA-256, inventories,
source/input manifests, qualification matrix, known issues, license notices and
optional bmap. Bind compressed bytes to the verified raw image and exact input
capture. Never overwrite an earlier accepted asset/version with changed bytes.

Publication must depend on required passing checks for that exact candidate,
not independent push/tag jobs. Document retained-input restoration; do not claim
a fresh checkout can reconstruct unavailable local inputs. A file-validated
candidate may be retained as a clearly labeled development artifact. Promoting
a hardware-supported alpha requires its advertised Z4/app acceptance; optimized
power claims additionally require Z6. Missing tests remain visible in the manifest.

Keep initial base updates as manually selected reviewed images with recovery
guidance. No automatic channel movement, in-place base migration or device flash
is introduced by this work package. Source changes require new candidates;
resume remains bound to the original captured inputs.

### C1 — Qualified catalog and separate cached frontend

**Implemented source increment:** the optional Catalog 0.1.0 frontend, signed
offline metadata generator, pinned trust/base/sequence policy and bounded
normal-user cache are described in [qualified catalog](qualified-catalog.md).
Discovery, explicit refresh and verified downloads have automated/native
coverage; install/remove/repair remain with the original Store. The bundled seed
has no live endpoint or continuing signing key. New root approvals, live feed
deployment, image inclusion and physical acceptance remain separate gates.

This is lower priority than app/UI and baseline acceptance, and can be deferred
if the small qualified catalog does not justify another frontend.

First generate signed discovery metadata from qualified `dist/` and `store/`
manifests: exact package version/SHA-256/size, compatible base identity, artwork
hashes, sequence, validity interval and signing-key identity. Pin the verification
key in a reviewed package and specify rotation/revocation and clock-unknown
behavior. Test wrong signatures/base, malformed/oversized data, replay/expiry,
redirect/download limits and interrupted atomic refresh. A sequence floor used
as a security guarantee must live in protected state; a writable user cache alone
cannot provide tamper-resistant rollback protection.

A valid signature authenticates discovery metadata; it never adds a package,
version, dependency or maintainer script to root authorization. New approvals
still require the existing qualification and reviewed root policy/image update.
Stale cached metadata may be labeled and browsed offline, but cannot authorize
a new installation or satisfy a current release-validity gate.

Then add a separate normal-user Tyrell Catalog entry, using existing UI patterns,
bounded cached metadata and small hash-addressed artwork. Open without network
access; refresh explicitly and retain the last verified catalog on failure.
Preserve the original M5 Store UI/dispatcher path. This improves the new
frontend's startup, not the preserved vendor Store's startup. Patching that
binary instead requires a separate Store source-closure qualification.

Start with discovery and verified downloads. Wire install/remove/repair only
after qualifying its caller path against the existing fixed helper protocol;
do not broaden trusted callers or root verbs incidentally. If transactions move
to a service, use fixed authenticated operations, existing immutable staging
and app-scoped journals. UI closure must not interrupt mutation; reconcile after
restart without replaying completed operations. Cancellation stops at safe
boundaries. Exclude arbitrary commands and global pending-dpkg recovery.

**Gate:** offline launch is network-silent, quotas/concurrent refresh/cancellation
work, untrusted cache cannot change root policy, and all original M5 Store
install/remove/repair/rollback and protected-base tests still pass.

### H1/H2/H3 — Hardware ideas with separate evidence

**H1 preparation implemented:** the [backlight investigation](backlight-investigation.md)
uses F2's read-only observations and separate owner/visual/electrical records.
No Tyrell false-off fault or correction is established. Physical reproduction,
baseline acceptance and any justified caller/BSP intervention remain separate.

**H1: backlight.** Reproduce the reported brightness-zero false-off behavior on
Tyrell's exact BSP/controller, checking visible light, dark/dim/wake behavior
and controlled discharge observations. Do not add a global sysfs watcher that
fights M5 ownership. If confirmed, correct Tyrell-owned dark callers or qualify
a separately versioned BSP fix after locating the fault. Preserve original M5
executables. Give the intervention its own before/after identity, outside the
original base-system savings comparison.

**H2: power.** Follow existing Z6, including inert installation, integrated
Ethernet topology matching, eligibility before writes, bounded event-driven
application, concurrent invalidation and live/boot rollback. Qualify Ethernet
and ondemand-factor candidates independently, then combined: automatic cable
wake, traffic, USB modes, peripheral hotplug, latency/media and at least the
initial three paired counterbalanced battery trials. Keep baseline as a valid
fallback. Do not import schedutil, ID-only USB rules or different GPU/CMA/swap
settings into this experiment.

**H2 offline increment:** [guarded power rehearsal](guarded-power-rehearsal.md)
models exact preset intents, topology/identity checks and generation-bound
apply/invalidation/recovery using copied synthetic snapshots. It provides no
live hardware backend or runtime override and does not change the retained
power package, its closed activation gate or the accepted image inputs.
Unverified observations remain blocked; real observer/ordering/maintenance
integration and Z4/Z5/Z6 acceptance remain required before any live activation.

**H3: panel.** Study TE synchronization, fast copy and present ioctl in a separate
experimental BSP profile after baseline acceptance. Keep kernel, headers,
modules, device tree and identities coherent. Preserve the existing fbdev/fsync
fallback. Requalify M5 apps, Tyrell media/games, dirty/full redraws, tearing,
idle IRQs, brightness, HDMI handoff and crash recovery before promotion. A higher
transfer count alone does not establish visible benefit or justify changing the
protected baseline.

**H3 preparation:** the [pinned source audit](panel-source-audit.md),
[inactive experiment and counter analysis](panel-experiment.md) bind the ABI,
license and kernel/BSP differences without changing current applications or
active image profiles. Recorded SPI rates use parent capture envelopes and
remain separate from optical refresh. Accepted baseline, a coherent candidate
build and all physical H3 gates remain open.

## First implementation increment

**F1: compatibility ledger and scoped CI** supplies the initial records,
reusable checks and portable CI without rebuilding images or changing device
behavior. **U1** adds shared app commands and UI checks. **L1's experimental
Status pilot** adds an optional shell protocol with explicit ownership and loss
policy. Its device gates remain open before compatibility can be claimed.
Prepare F2 collectors/fixtures in parallel; leave physical execution explicitly
pending until a concrete spare target is identified.

This first increment is complete when required existing checks run on the
affected code, package/image/source evidence scopes are explicit, and future
promotion cannot mistake a skipped hardware test or stale receipt for acceptance.
It does not claim that later app, catalog, maintenance or hardware ideas are built.

## Pinned upstream references

- [Shell-link implementation](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/crates/gh0st-core/src/link.rs) and [pause/stop handoff](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/crates/gh0st-shell/src/run.rs#L2959-L2973).
- [Command/page API](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/crates/gh0st-app/src/page.rs), [deterministic runtime tests](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/crates/gh0st-app/src/testing.rs) and [layout checks](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/crates/gh0st-ui/src/lint.rs).
- [Image publication workflow](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/.github/workflows/os.yml) and [reviewed CI failure](https://github.com/therezor/CardPuterZeroOS/actions/runs/45420496682); publication should be gated more tightly than this upstream snapshot.
- [Signed catalog validation](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/crates/gh0st-core/src/store.rs#L817-L857).
- [Backlight observation](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/docs/hardware-facts.md#L72-L77), [USB measurement conditions](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/docs/hardware-facts.md#L224-L253) and [panel design](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/docs/panel-driver.md).

These references supply ideas and inspected mechanisms, not Tyrell hardware
acceptance or a dependency on a moving upstream main branch.
