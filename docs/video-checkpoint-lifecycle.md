# Video checkpoints and lifecycle admission

A2's first increment adds Video 0.1.9 checkpoints, resource diagnostics and a
timing gate. **Active Gh0st playback remains unsupported.** The reviewed shell's
approximately 100 ms pause window cannot be treated as a completed media,
HDMI, audio and VT handoff merely because a local stop call returned quickly.
The normal M5 package retains its launch behavior and existing playback loop.

## Checkpoint contract

[`Playback`](../packages/tyrell-video/src/playback.h) now supplies
`capture_checkpoint()`, `suspend()`, `resume()`, `checkpoint()` and `resources()`.
Capture is nondestructive; successful suspend stores an authoritative checkpoint and
releases local playback resources. User pause retains the pipeline. Stop and a
new accepted load invalidate the saved checkpoint. Repeated suspend/resume calls
are idempotent, and playback controls are frozen while suspended.

The checkpoint retains:

- Media and external-caption metadata identity, including resolved path,
  device/inode, size and modification/change timestamps.
- A freshly queried position, a still-pending preroll/seek target, or end-of-file
  position, with explicit provenance and play/pause intent.
- Audio/subtitle selection, external SRT, volume/mute, requested sound route and
  output preference.
- Software-decoder fallback state, the spent retry flag/counter/reason and audio
  recovery budgets, so rebuilding does not grant another recovery attempt.

Identity is recorded at load and checked again before capture and resume.
Changed or unverifiable files are refused. This is metadata identity, not a
content hash or an atomic seal against concurrent writers. A failed replacement
load does not discard a valid suspended checkpoint.

Track selections use playbin indices. Tests verify the concrete fixture's
mapping; semantic language/track identity across a different enumeration order
is not guaranteed by this API.

Seek completion uses the matching downstream segment and stable pipeline state.
An unresolved immediate seek retains its target; an ambiguous settled seek is
refused rather than saving a stale target indefinitely. Resume reuses the
existing preroll and track-selection path. An unavailable preferred HDMI output
falls back to paused LCD playback instead of silently changing play intent.
Preroll applies captions and the saved seek before changing the audio selector,
then enters the requested playing/paused state without a playback warmup.

These operations are synchronous and **may block** in filesystem, GStreamer or
driver calls. They are not a bounded shell-pause API. `local_handles_released()`
describes the graph, DRM fd and app-side lease handles only. The separate
`external_cleanup_unconfirmed` flag remains conservative after external helper
activation attempts: closing a socket does not prove that its service finished restoring
the desktop or unloading a sound sink.

## Why live media admission stays closed

The [pinned shell contract](shell-link-status.md) allows no pause refusal or
deadline extension and may stop the process after about 100 ms. Video's
[`destroy_pipeline()`](../packages/tyrell-video/src/playback.cpp) joins streaming
work during `GST_STATE_NULL`. A controlled real GStreamer callback can hold that
transition beyond the shell window. Fast fake-sink measurements therefore cannot
establish a universal bound for decoders, sinks or kernel operations.

Release also includes ownership effects outside that call:

- [`HdmiSession::release()`](../packages/tyrell-video/src/outputs.cpp) restores
  CRTC/plane state before dropping DRM master.
- The [desktop/VT helper](../packages/tyrell-video/system/tyrell-video-hdmi-session)
  restores ownership asynchronously after lease EOF; it can wait for DRM and
  reactivate a VT. Its service stop timeout is measured in seconds.
- The [audio session](../packages/tyrell-video/src/hdmi_audio.cpp) closes its
  lease and reaps the launcher without waiting for service cleanup. Temporary
  sink removal can continue after that return.

A delayed restoration can affect a newer foreground owner. Generic stop or
destructor cleanup consequently does not establish a safe hidden-exit policy.
No `paused` acknowledgment is wired to these blocking calls, and no helper is
moved outside the shell's stopped process group as a workaround.

The optional Linux build flag `TYRELL_EXPERIMENTAL_SHELL_GUARD=ON` compiles a
qualification guard, **not playback support**. A valid inherited shell link,
including one already lost on adoption, makes native GUI launch exit with code
2 before App/GStreamer, input, framebuffer or helper creation. It sends no
`paused` acknowledgment. Missing/invalid descriptors retain the M5 path without
closing or changing unrelated FDs. Headless tools and SDL do not adopt the link.
The guard is off in the normal package.

## Verification and remaining gates

Tests use actual GStreamer playback with fake audio/LCD sinks to exercise
settings, fresh/pending positions, caption pixels, tracks, repeated suspension,
changed files, output preparation and fallback. Admission tests call the actual
GUI entry with a fake shell and inaccessible device paths. The controlled
blocking test uses an in-process fixture thread solely to release a held
streaming callback; it is not a lifecycle worker design.

The predeclared timing plan uses a 250 ms held callback, requiring a blocking
counterexample beyond 150 ms, plus five samples each for playing, user-paused,
immediate-preroll and queued-seek LCD checkpoints. Capture, suspend, resume call
and restore-ready times are separate measurements. No timing result alone
qualifies shell admission. CI retains the negative-spike JSON and requires the
checkpoint, blocking-spike and guarded-admission checks alongside existing
media/UI regressions.

An identical paused audio 0-to-1 switch followed immediately by a caption seek
blocked in both the unchanged 0.1.8 player and the candidate, before checkpoint
capture. Completed selections are covered by checkpoint tests; that in-flight
transition remains a known unsupported case. Existing rapid-track regression
checks are retained. This increment does not claim to resolve every transition.

Unused retry-budget cycles are tested through the public API; spent-budget
policy has existing unit coverage and checkpoint assignments are source-reviewed.
An actual exhausted hardware-decoder/audio recovery across suspend/resume is
**not tested**. Physical playing/paused LCD/HDMI, preroll/seek/output transitions,
caption/track continuity, missing HDMI, crash/SIGTERM and audio/VT restoration
remain unqualified on the new binaries. No device or card is accessed here.

Before live integration, the shell needs a handoff contract that can accommodate
asynchronous completion, and helpers need observable completion/cancellation
with an ownership-safe restoration policy. That design must be reviewed and
tested before enabling media, acknowledging pause or optimizing Video's idle
loop. Active frame pacing is preserved in this increment. Retained packages,
Store approvals and image payloads keep their original receipts.

## Recorded software timing

The [frozen-source timing record](../research/video-lifecycle-timing.json) contains
the predeclared plan, all twenty LCD samples, the controlled negative spike,
producer hashes and tool versions. The
[worker inventory](../research/video-lifecycle-worker-packages.tsv) records the
disposable Debian 13 ARM64 environment and GStreamer 1.26.2 toolchain.

The held callback produced a **251.90 ms** NULL transition. Ordinary fake-audio
LCD samples were faster, but neither result confirms external cleanup or a
device deadline:

| LCD scenario, five samples each | Suspend range | Resume-ready range |
| --- | --- | --- |
| Playing | 2.70–4.31 ms | 61.96–86.07 ms |
| User-paused | 3.10–7.01 ms | 63.45–80.80 ms |
| Immediate preroll | 0.26–0.68 ms | 51.20–68.76 ms |
| Queued seek | 3.37–4.89 ms | 62.45–70.28 ms |

The raw record retains separate capture, suspend, resume-call and ready samples.
`active_shell_supported` remains false.

## Asynchronous handoff design

The follow-up [A2 design](video-async-handoff-design.md) specifies a separate
negotiated protocol, a shared ownership barrier, observable helper completion
and conservative quarantine. It requires a future shell implementation; no
protocol, broker or managed helper is introduced by that design. Its five
implementation increments and unrun acceptance cases keep the current guard
and normal M5 behavior intact. Physical testing remains pending.
