# Video managed display ownership

A2-D3 adds explicit managed KMS and LCD lifecycle APIs, independent producer
barriers and durable display-to-VT dependencies to the
[handoff foundation](video-handoff-foundation.md) and
[participants](video-handoff-participants.md). The C++ implementations are
exercised through controlled native workers and the real Python authority and
SQLite ledger. They do not enable Video's live managed launch path. Physical
testing remains pending.

## Compatibility and admission

The normal M5 player still selects the existing `HdmiSession`, LVGL fbdev driver
and `FramebufferCommit` cadence helper. Its existing foreground restoration
policy is preserved. Managed callers must explicitly select the separate
`ManagedHdmiSession` or `ManagedLcd` API. Neither class falls back to the legacy
restoration path after accepting managed work.

`Authority.live_hardware_admission` remains `False`, and Video's shell admission
guard remains closed. D3's display profiles admit only controlled KMS/LCD
fixtures. A normal inert grant cannot authorize a display operation. The
existing helper endpoints, privileges, image inputs, retained packages and
historical approvals are unchanged. Source changes and current software checks
do not extend the old binaries' physical qualification to a new build.

## KMS ownership and terminal completion

[`ManagedHdmiSession`](../packages/tyrell-video/src/managed_hdmi.h) accepts an
explicit trusted primary-DRM descriptor, a fixed connector/CRTC/plane scope and
an epoch/resource/generation binding. It opens no device path and does not
automatically acquire DRM master. The trusted coordinator journals each
operation before invoking the worker-side API; correlation IDs are not peer
credentials or grants by themselves.

The managed session has no saved desktop snapshot. Register every graph borrower
before sealing. Each borrowed DRM descriptor is an owned close-on-exec duplicate
associated with a specific borrower and use operation. Closing that descriptor,
returning from a graph destructor, or app `local-done` does not retire the
borrower. A separately bound completion proof must confirm that its graph and
backend work have drained. D3 uses controlled graph fixtures. The
[D4 media actor](video-media-actor.md) now connects actual GStreamer callback,
object and finality facts to an independent graph borrower for LCD/fake-audio
fixtures. A real KMS sink borrowing the managed DRM handle remains D5 work;
D4 refuses managed HDMI before legacy helper acquisition.

After the registry is sealed and borrowers have completed, cleanup performs a
test-only atomic neutral transaction, then an asynchronous atomic commit. It
disables only the configured planes and route/CRTC, waits for a successful
sync-file out-fence, and reads the effective scoped state back. Unexpected
identity, routing, fence status or readback causes unknown state. It never
restores an old CRTC, cursor, overlay or foreground snapshot.

An out-fence and neutral readback are still insufficient to prove every driver
cleanup stage has finished. The [DRM commit documentation](https://docs.kernel.org/gpu/drm-kms.html#c.drm_crtc_commit)
distinguishes flip completion from later cleanup. The managed session drops master and closes its
owned DRM descriptor before invoking an independent terminal-completion hook.
Only a valid positive hook result can produce terminal native evidence. Missing
or unqualified hooks refuse completion. The evidence binds the exact resource,
generation, epoch and cleanup operation; it is not the authority's composite
release receipt.

Release and destructor replay cannot begin another restoration. Destruction
before successful release is abandonment: it closes local handles and leaves
completion unknown. The authority retains the corresponding barrier. It must
not interpret descriptor closure or worker exit as successful backend cleanup.

## LCD submissions, callbacks and driver work

[`ManagedLcd`](../packages/tyrell-video/src/managed_lcd.h) owns a separate
320 × 170 RGB565 LVGL display with partial buffers. Its refresh timer stays
paused; only an explicitly accepted operation ticket can submit a framebuffer
write. The gate runs inside the real flush callback before copying pixels,
rather than observing `LV_EVENT_FLUSH_FINISH` after the effect.

One ticket covers an entire redraw, including all partial strips. Submission
records a queued operation before rendering. Sealing refuses new submissions
and positively suppresses tickets that have not begun; started callbacks and
backend writes retain their original operations. A callback finishing or
`lv_display_flush_ready` returning releases LVGL's temporary buffer borrowing,
not the independent driver obligation. The coordinator can distinguish
`suppressed-before-start` from positive backend completion using each ticket's
scoped operation evidence.

`poll()` processes backend completion facts and disables future invalidation
and callbacks after sealing. A queued LVGL refresh after the gate closes cannot
write pixels. Release requires no queued or in-flight work, future callbacks
disabled, and a separate positive driver-finality receipt. The session has no
destructor redraw, restoration, reopen or implicit resume.

LVGL operations and destruction belong to one UI owner thread. Only `seal()`
and evidence reads may run from the separate control thread. No asynchronous
driver callback retains a raw pointer to a destroyed `ManagedLcd`: driver work
is polled by operation ID. Deleting the display removes its synchronous flush
callback; an abandoned backend's later result cannot create release evidence.
The owner must still retain the object while an accepted synchronous method is
executing, rather than deleting it concurrently with a held callback.

The production `LinuxManagedFramebuffer` adapter validates a manager-provided
framebuffer descriptor, takes a close-on-exec duplicate and requires fixed
geometry, pixel format and stable descriptor/readback identity. It writes through
the actual framebuffer adapter and records sync/readback results. It does not
open a path or change a mode. Known identity loss or failed writes permanently
poison that session.

Generic fbdev supplies no portable completion proof for deferred panel work.
The [kernel deferred-I/O documentation](https://docs.kernel.org/fb/deferred_io.html)
describes work queued after memory updates. Consequently, the default adapter
refuses managed submissions without an explicitly qualified completion
provider. `fsync`, a completed userspace copy, unmapping and a blank memory
buffer do not certify physical panel completion or privacy cutoff.

The provider must confirm each frame and, independently, all release-side work.
For finality the adapter closes its owned framebuffer descriptor first, then
polls a provider that outlives that descriptor. A pending result retains the
barrier. Abandonment before a positive final receipt permanently refuses late
resurrection; successful terminal replay is cached and performs no new write.
The controlled provider used in tests is not a qualified physical-panel driver.

## Durable producers and VT dependencies

[`handoff_display_state.py`](../packages/tyrell-video-handoff/handoff_display_state.py)
extends the same serialized SQLite authority. Display registration records the
resource and ownership epoch. A graph or flush borrower receives its own actor
and durable use operation before submission. App completion cannot certify
another actor's work, and a display cleanup permit cannot bypass unresolved
borrowers.

Completion facts bind the actor, operation, resource, generation and epoch. Only
a flush ticket positively removed before starting may report
`suppressed-before-start`; graph borrowers must actually drain. Worker identity
and native identity/evidence digests bind the controlled native producer. A
stale or foreign result, lost worker, failed storage commit or restart preserves
unknown work and quarantine.

The new `vt-display` fixture profile requires exactly two explicitly identified
participants from the same lease: one KMS and one LCD. Empty dependencies,
duplicate domains, unrelated displays and a caller boolean are rejected.
Before permitting VT cleanup, the authority requires both displays' terminal
native evidence, completed worker fences/reaping and suppressed stop-post
obligations. It journals their exact receipts with the VT cleanup permit.
The earlier standalone D2 VT fixture profile remains a separate test boundary;
it is not an alternative live route around these display requirements.

Authority snapshots now use schema 4, including D4 media-producer evidence.
Earlier schema-1 through schema-3 ledgers are
refused without rewriting or automatic migration. Do not delete a ledger to
clear an ownership barrier. D3 adds no reset, pruning or quarantine-clear API;
uncertain backend work still requires separately qualified reconciliation.

## Validation and remaining gates

The native LCD tests exercise real LVGL partial flushes, held synchronous writes,
queued suppression, driver-finality gates, descriptor and geometry checks, and
the production pwrite/fsync adapter with controlled admission and driver facts.
The KMS tests call the production DRM implementation through wrapped hardware
boundaries, including held completion, failed readback and destructor behavior.
No physical framebuffer or DRM device is opened by these fixtures.

The native display worker and Python integration tests use real processes,
pidfds and SQLite to connect producer facts to the durable authority. Their
checks include independent borrowers, partial display completion, exact VT
dependencies, expiry, crashes and successful composite release. Required CTest
groups and per-group evidence are retained by CI. Local Linux validation passed
all 129 handoff Python tests with zero skips, both native adapter test groups
and four native authority scenarios, including the actual VT worker. A separate
run verified normal-user execution beneath a root-owned `0700` build directory.
Hosted outcomes are recorded in source-bound reports. A portable state-model-only result would
not establish this production-path coverage.

These checks do not globally complete the original 19 cases in the
[design decision record](../research/video-async-handoff-design.json). D4 adds
[actual GStreamer drain and control integration](video-media-actor.md) under an
explicit LCD/fake-audio policy. App key/render gates do not complete an accepted
LCD flush; the independent D3 display barrier still applies. Qualified driver
finality, service/manager identities, legacy-writer exclusion, asynchronous
shell behavior and exact package/device scenarios remain later gates. KMS
fences and controlled memory callbacks do not qualify physical scanout, HDMI
blanking, LCD transfer completion, sound or privacy locking.

D5 qualified shell/package/device integration is next. Video's live guard stays
closed and physical LCD/HDMI, launch/return, audio/VT restoration and
responsiveness testing remain pending.
