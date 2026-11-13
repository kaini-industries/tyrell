# Tyrell Video adversarial review

The subsequent [attached-TV test](video-hdmi-validation.md) records HDMI
hardware results and the performance correction found during that test.

Review date: November 13, 2026. Three fresh agents independently reviewed HDMI,
playback and UI/tools; a fourth reviewed the fixes. Confirmed defects were fixed
and tested. No HDMI monitor was available, and the user explicitly chose to
leave physical HDMI testing pending.

## Findings and fixes

| Severity | Failure | Correction |
| --- | --- | --- |
| P2 | Repeated output requests during preroll replaced a queued seek/track target with the new pipeline's initial state. | Preserve the pending restart target during output changes and HDMI error recovery; accumulate seek keys against that target while loading. The old ARM64 engine reproduced a queued two-second seek returning to zero. |
| P2 | Selecting Automatic audio with no matching onboard/HDMI sink passed a null destination to PulseAudio's live stream-move API, terminating playback. | Use the shared routing policy, including onboard fallback, and resolve the system default through `@DEFAULT_SINK@`. A private PulseAudio null sink reproduced the old `Invalid argument` failure. |
| P2 | Active HDMI checks forced a connector/EDID reprobe every 500 ms on the UI thread. | Poll cached connector state with `drmModeGetConnectorCurrent()`; retain probing for discovery/acquisition. |
| P2 | Clearing the frame after a seek could erase a new preroll frame published before the seek call returned. | Retain the immutable previous snapshot until a replacement arrives. |
| P2 | Long filenames, paths and track labels wrapped into neighboring rows. | Give compact labels one font line of height and ellipsize them. Cache the original string to avoid repeatedly redrawing unchanged truncated text. Error bodies retain multiline wrapping. |
| P2 | Non-UTF-8 filename bytes could produce invalid JSON reports. | Share a UTF-8-safe JSON encoder; replace invalid bytes only in reports while preserving the byte-exact path used for playback. |
| P2 | HDMI control exercises required LCD appsink frames, which HDMI never produces; the probe also lacked video-delivery evidence for HDMI. | Gate HDMI exercises on buffers reaching its final video sink, require that evidence for a pass, and continue rejecting LCD fallback as an HDMI pass. |

The final-sink counter is atomic, bounded, and survives pause, seek, EOS and
output reconstruction. It counts buffers reaching the sink, including preroll
and buffers that the sink may later drop. It measures neither successful
scanout nor physical refresh, and is not an exact displayed-frame count.
An intermediate implementation based on GStreamer sink statistics was rejected
in the fourth review because those statistics reset on state/segment changes.

Linux's [DRM connector API guidance](https://raw.githubusercontent.com/torvalds/linux/v6.12/include/uapi/drm/drm_mode.h)
explains why routine forced probes can block and flicker. The HDMI review also
checked GStreamer 1.26's borrowed-fd lifetime, connector/mode matching, vc4 pixel
format support, and teardown before CRTC restoration. These code checks do not
establish attached-monitor compatibility.

## Verification

The reviewed ARM64 package is `tyrell-video 0.1.0-1`, source revision
`src-28d121163711`, SHA-256
`09426b662da2ecca2dfcab8360e9dd5ae0b64c18604d66c68c478deaa4848260`.
Its source manifest matches the working source files.

- All 19 CTests passed, including queued relative seek/output recovery,
  subtitle pixels, retained delivery evidence after pause/seek/EOS, JSON edge
  cases, frame ownership, CLI and deterministic UI previews.
- Package inspection, installation, smoke checks and removal passed in the
  isolated Debian ARM64 VM.
- All 20 builder tests and six host-tool tests passed. No shared builder code
  changed during this review.
- A private PulseAudio null sink reproduced the old Automatic routing failure;
  the corrected real-audio playback test passed. An existing test
  assumed a new frame within 300 ms of audio startup; it now uses a bounded
  condition wait and continues to verify snapshot immutability.
- A non-UTF-8 filename played successfully and emitted parseable UTF-8 JSON.
  The probe correctly rejected an audio-only file and disconnected HDMI.
  Final delivery evidence remained positive after normal EOS.

The final package is installed on the Zero. Its real-audio playback control
test, embedded multitrack/subtitle exercise, absent-HDMI probe, package
verification and diagnostics all passed. APPLaunch retained PID 1064 with zero
restarts; the packaged Video launcher entry remained unchanged. All 12 protected
firmware/launcher hashes and the kernel match the original inventory. System
audio volume and mute state also match the pre-review inventory. These audio
tests establish stream operation, not audible sound or observed lip sync.

Sanitized raw evidence remains local under ignored `device-results/`, including
`video-adversarial-results.json`, `video-adversarial-after.json`,
`video-adversarial-initial-results.json`, and the SRT/retry reports. Temporary
test media/binaries were removed from the device after collection.

The locked LVGL harness measured a normal long release filename at 30 px high
before the fix, overlapping the 18 px browser row spacing. The corrected label
was 15 px high, displayed an ellipsis, and caused no redraw for unchanged text;
changing the text did redraw. The multiline error body remained 88 px high.
The resulting long-browser screenshot was visually inspected.

On the real Zero, the first external-SRT control test missed its original
four-second startup deadline without a decoder error. A bounded 12-second
probe then delivered the complete four-second clip (96 sink buffers, 97
appsink samples) through `v4l2h264dec`, with no playback error. The full native
control test passed on retry. The test now allows 12 seconds for initial
decoder/font/audio setup, within the application's 15-second startup limit.
The initial failure is retained in the local evidence.

The original 30-minute LCD test was not repeated for this review. Its measured
results remain in [the implementation validation report](video-validation.md).

## HDMI acceptance still pending

The real Zero continues to report `card0-HDMI-A-1` disconnected, with no modes.
`card0` is accessible to the normal user; the separate SPI LCD remains on
`card1`. Factory precheck was inactive and LightDM active. The review did not
stop either service or change boot configuration, firmware, launcher files,
virtual terminals, system audio defaults or system volume.

With a monitor, the remaining acceptance work is:

1. Check EDID modes and DRM ownership, including the vendor factory precheck.
2. Play a known H.264/AAC baseline on HDMI and inspect aspect fit, captions,
   sound and lip sync; complete the 30-minute HDMI run.
3. Unplug/replug during normal playback and during an output switch, confirming
   paused LCD fallback preserves position, selected tracks and volume.
4. Confirm a busy display remains owned by its current session, and that the
   previous HDMI state is restored after normal exit and repeated launches.

HDMI playback, audio, hotplug and display restoration remain physically
unqualified. No simulated or disconnected-output test is reported as an HDMI
hardware pass.
