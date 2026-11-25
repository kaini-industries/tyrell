# Video media actor and control integration

A2-D4 implements a single-owner media actor, a borrowed UI control adapter and
durable media-producer accounting. It builds on the
[checkpoint API](video-checkpoint-lifecycle.md),
[handoff authority](video-handoff-foundation.md) and
[managed display barriers](video-managed-display.md). Its native fixtures use
the production `Playback` graph with LCD frame capture and fake audio. Video's
live managed admission stays closed. Physical testing remains pending.

## Ownership and UI integration

[`MediaActor`](../packages/tyrell-video/src/media_actor.h) owns its backend from
factory invocation through destruction. One worker thread constructs `Playback`,
executes every command, polls it, captures checkpoints, drains graphs and destroys
the backend. Automatic preroll, decoder-retry and rebuild work performed by
`poll()` belongs to that same owner and the prejournaled epoch-lifetime operation.
There is no second thread allowed to call into that `Playback` instance.

The actual Video `App` consumes the
[`PlayerControl`](../packages/tyrell-video/src/player_control.h) interface.
Normal M5 launches construct `DirectPlayerControl`, retaining the existing
synchronous player policy. The explicitly injected actor pilot uses
[`ActorPlayerControl`](../packages/tyrell-video/src/actor_player_control.h).
It borrows a supervisor-owned actor; destroying the UI facade cannot destroy a
graph or join its worker.

UI actions enqueue bounded, typed intentions. A nonblocking mailbox supplies a
permit for the exact binding, request and command payload after the coordinator
has committed it off the UI thread. Queue acceptance does not report successful
playback. Immutable snapshots and later events report the result. Stale load
results cannot replace a newer browser/player intention, and asynchronous load
failure remains visible to the user. Subtitle discovery runs on the media worker.

The adapter gates app key handling and rendering after sealing or binding loss.
Those gates do not prove that an accepted LVGL flush or deferred panel write has
finished. A managed LCD still requires D3's independent flush borrower and
qualified driver completion. The pilot does not substitute UI gating for that
display barrier.

The supervisor must retain the actor while work is blocked. Shutdown requests
stop admission and let accepted work finish; they do not detach the worker.
`join_finished()` only joins after the actor publishes `Finished`. Actor
destruction can wait and therefore belongs outside the UI/control loop.

## The actual graph drain

Managed `Playback` requires an explicit epoch/resource/generation binding and
the LCD/fake-audio profile. It refuses HDMI output, user audio-sink selection,
desktop handoff and automatic HDMI audio before legacy helper acquisition.
Managed construction, error paths and destruction do not restore old HDMI,
audio or VT state. No environment variable selects this mode. The ordinary M5
path retains its existing helper policy.

The graph lifetime tracker covers the real appsink callbacks, pad probes,
element-setup and notification callbacks. GStreamer/GLib owns each callback
context until its destroy notification; counting only callbacks that have
already entered would miss a retained callback about to run. The tracker also
uses weak-object finalization and retains a strong appsink reference while its
callbacks are disconnected, including graph-construction failure paths.

Drain seals callback admission, removes probes and signal handlers, replaces
appsink callbacks, transitions the graph to NULL and releases owned references.
It waits for active callbacks and retained callback contexts to finish, and
requires every tracked graph object to finalize. A held capture or callback
continues under the old operation even if the caller requests cancellation.

These local facts still require an independent, trusted finality hook. A missing
or negative hook refuses a positive receipt. GStreamer's
[state transitions](https://gstreamer.freedesktop.org/documentation/additional/design/states.html)
describe streaming and device-state changes; a NULL result alone does not
qualify an external driver's complete cleanup. D4 fixtures supply a controlled
non-device hook. They do not qualify an HDMI sink, audio server or panel driver.

`GraphDrainEvidence` binds the epoch, resource, generation and drain operation,
graph serial, callback entry/completion counts, outstanding contexts and live
objects. It also records pipeline finalization, local handle release, absence
of legacy helper use and the separate finality receipt. An empty startup graph
can produce positive empty-graph facts without inventing a media checkpoint.

## Durable permits and retiring an epoch

[`handoff_media_state.py`](../packages/tyrell-video-handoff/handoff_media_state.py)
attaches a media producer to D3's independent graph borrower. Its schema-4 state
binds the trusted producer PID/UID, native identity, binding, lifetime operation
and exact command digest. A command result is not a composite release receipt.
The coordinator records each native event before acknowledging that event to
the actor; the UI may inspect an event without consuming this durable obligation.

Normal dispatched command permits remain held after execution. They retire only
after positive graph drain and one of two independently checked fences:

- A parked epoch fence proves that the sealed old actor has no active operation,
  queued command or unacknowledged event, and cannot poll, rebuild or accept more
  old-epoch work. Its CPU checkpoint may remain in the same process.
- Final shutdown requires a positive process fence, owner-thread completion and
  exact process reaping before retiring the producer and its commands.

An intention positively suppressed before dispatch may retire its own unused
permit. Cancellation cannot relabel dispatched work as suppressed. Missing,
foreign or malformed graph/fence facts retain unknown ownership. Restart keeps
unresolved native provenance and quarantines the operations; an uncertain
storage commit cannot return a dispatch permit or successful acknowledgement.

Authority snapshots now use schema 4. Schemas 1–3 are refused without rewriting
or automatic migration. There is no pruning, reset or quarantine-clear API;
deleting a ledger is not recovery.

After a positive parked epoch fence, resume needs a fresh authority grant,
strictly newer epoch, new resource/generation binding and new lifetime operation.
`Playback::rebind_managed_graph()` accepts that binding only while the old graph
is fully drained. Resume rebuilds the saved checkpoint, preserving media identity,
position and uncertainty, tracks, volume/mute, play intent and retry budgets.
Old resume, EOF, queued controls and old grants cannot reopen the graph. The LCD
fixture checks this continuity; it does not exercise or qualify HDMI fallback.

## Bounds and software evidence

The actor admits at most 32 outstanding ordinary controls, including queued,
executing and unacknowledged work. It retains a bounded 1,024-operation history
and accepts at most 16 epochs. The authority
admits 32 media command permits per producer with separate drain/shutdown
capacity. Exhaustion refuses new work; history is not silently pruned. These
bounds limit accepted work, not the duration of a GStreamer or driver call.

Native graph tests hold an actual production appsink callback during drain,
then independently hold finality. They exercise prebinding audio-sink failure,
empty-graph shutdown, default finality refusal and fresh checkpoint rebuild.
Actor tests cover factory/method/poll/destructor thread ownership, blocked calls,
bounded queues, event acknowledgement, sealing, shutdown and fresh regrant.
UI/control and native-authority fixtures connect the actual adapter and durable
media journal. The required Video groups are `video-media-graph`,
`video-media-actor`, `video-media-ui` and `video-media-authority`. The handoff
Python suite has 139 passing checks with zero skips, including 10 media-journal
cases covering permit bounds, payload binding, stale proofs, lost producers,
uncertain commits and restart. Required groups and source-bound results are retained by CI;
earlier D1–D3 counts remain historical evidence for those increments.

The actor's polling interval is not an idle-power result or a teardown deadline.
The original design's 19 complete integrated cases have not all passed.
[D5's software candidate](video-qualified-integration.md) now connects the actual
App and asynchronous shell controller and builds a separate inactive ARM64 kit.
Qualified shell/service deployment, driver finality, legacy-writer exclusion
and identified-device acceptance remain unfinished. Physical playing and
paused LCD/HDMI, audio/VT restoration, launch/return, privacy and responsiveness
remain pending. Video idle work follows the ownership and wake-source gates.
