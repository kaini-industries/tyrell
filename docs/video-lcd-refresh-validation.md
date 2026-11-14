# Native LCD refresh and playback-time validation

November 13, 2026. This follows the [file-playback performance work](video-performance-validation.md).
The user confirmed 0.1.4 HDMI video/audio, then reported that the native screen
still looked slow. More fresh agents reviewed the UI, framebuffer driver,
frame pacing, callback lifetime and test coverage. The native display correction
preserves the existing firmware, driver, SPI settings and boot configuration.

## Physical transfer bottleneck

The 0.1.4 playback engine delivered the tested 1920 × 816 H.264 source at 24 fps,
but the native screen's SPI traffic represented only about 12 complete frames
per second. The UI thread used 4.3% of one CPU core, and the process could use all
four cores. No stale-frame or image-cache bug was found.

LVGL's fbdev backend copies partial draw buffers into mapped framebuffer memory.
The installed Linux DRM framebuffer helper batches mapped writes with a
50 ms deferred-I/O delay. The vendor overlay requests 60 MHz SPI and RGB565;
a 320 × 170 full frame contains 108,800 bytes. TE waits are disabled in the
installed panel driver. Sources: [vendor overlay](https://github.com/m5stack/m5stack-linux-dtoverlays/blob/75518549a2640486f5d13ea72ca4935b198d7c20/modules/CardputerZero/cardputerzero-v5-overlay.dts#L448-L473),
[Linux deferred framebuffer update](https://github.com/torvalds/linux/blob/v6.18/drivers/gpu/drm/drm_fbdev_dma.c#L229-L259),
[deferred-I/O fsync](https://github.com/torvalds/linux/blob/v6.18/drivers/video/fbdev/core/fb_defio.c#L145-L163).

A reversible external process called fsync on a second framebuffer descriptor
at 24 Hz while the unchanged app played. Repeated ten-second windows gave:

| Window | Full-frame equivalents from SPI bytes / second |
| --- | ---: |
| Normal mapped updates | 12.001 |
| Explicit pending-update commits | 23.953 |
| Return to normal mapped updates | 12.001 |

The forced window issued 240 calls, averaging 0.616 ms and peaking at 5.76 ms.
This isolated the deferred update scheduling as the limiting stage.

## App correction

Version 0.1.5 commits pending framebuffer updates once after the last partial
flush of each completed LVGL redraw. It uses the configured native framebuffer
path and excludes headless/SDL displays. It does not enable a refresh ioctl on
every strip, run a periodic background pump, or change system configuration.
The extra descriptor is close-on-exec. An open or fsync failure records a
measurement error and retains the original mapped display behavior. Callback
removal, display deletion and descriptor cleanup are tested.

New report fields count completed LVGL redraws, fsync requests, request durations
and errors. Those counts describe software redraws and kernel requests; they do
not claim panel scanout or vertical-sync timing.

## Integrated hardware results

The full application played the same H.264 source with real PulseAudio output.
After controls hid, two twenty-second windows measured:

| Captions | SPI full-frame equivalents/s | Application CPU, one core | SPI faults/timeouts |
| --- | ---: | ---: | ---: |
| Off | 24.022 | 89.0% | 0 / 0 |
| On | 23.972 | 89.2% | 0 / 0 |

The source rate is 24000/1001 fps. A complete SPI pixel update appeared as a
65,543-byte leading increment and a 43,268-byte trailing increment. Sampling at
5 ms without captions and 10 ms with captions gave frame-start proxies:

| Captions | Median interval | 95th percentile | Maximum | Gaps over 83 ms |
| --- | ---: | ---: | ---: | ---: |
| Off | 40.04 ms | 45.07 ms | 55.04 ms | 0 |
| On | 40.00 ms | 50.08 ms | 60.02 ms | 0 |

These counters timestamp SPI submission/transfer activity, not panel vsync.
The first parser incorrectly expected an entire frame in one counter increment;
its zero large-update count is discarded. The retained raw samples establish
the split-transfer boundaries used above.

The user confirmed that handheld playback is now smooth, without tearing or
repeated stutters. Across the measured windows descriptors stayed at 33; RSS
fell from about 44.5 MiB to 37.7 MiB. The bounded full-app run recorded 4,586
completed redraws and exactly 4,586 fsync calls, with no commit error. It included
menus, the short clip reaching EOF, and replay. That full-run average is not
used as a steady-playback frame-rate measurement.

## Caption and pause clock continuity

The user requested that enabling captions and pausing preserve the video time.
A new regression at nonzero positions reproduced a roughly quarter-second
rewind when pause was immediately followed by caption enabling: the paused
caption redraw reused an old 250 ms status sample. The baseline test expected
37.922–38.234 seconds and observed 37.667 seconds.

The correction captures the fresh media position when pausing a stable running
pipeline. Initial output preparation, queued preroll and unsettled seek targets
retain their existing intent. Caption changes keep the existing file/pipeline.
Replay after EOF remains an explicit separate action.

The new regression exercises external SRT and embedded MKV/SRT at 30.5 seconds,
queued paused seeks to 36.5 seconds, and playing seek/pause/caption batches at
42.5 seconds. Every poll checks both media time and decoded-frame PTS, with an
unchanged pipeline count. It also verifies paused caption pixel changes and
restoration, rapid controls and a deliberately stale status sample.

The fixed test passed on the physical Zero with real PulseAudio for both forms
of subtitle. The stale status lag was 0.384 and 0.396 seconds in that run; pause
and caption selection retained the fresh paused position. The test allows a
one-second forward-progress observation window for hardware audio/decoder
resume while keeping all no-reset bounds active on every poll. The embedded
fixture uses ordinary two-second cues and non-boundary seek positions; a single
90-second cue was unsuitable for validating caption pixels after random MKV
seeks in the installed stack.

Space/Enter now delegates to the playback engine's actual end-of-stream state.
The old UI shortcut guessed that playback had ended within 0.1 seconds of the
duration, which could restart a still-playing video when the user meant to
pause. Actual replay after EOF remains available; ordinary pause and caption
changes do not restart the file.

## Final package and checks

The final package is `tyrell-video 0.1.5-1`, source `src-44de5147f659`, SHA-256
`0acf088b12dcdd397ed9862f482d14839a4aafb2a38e5611ce6cc1e14ee0b122`.
All **34 ARM64 CTests** and package install/render/remove checks passed. The
source manifest matches the selected source files. The native refresh helper
has real LVGL partial-buffer tests for one commit per redraw, no repeated
unchanged redraw commits, failure fallback, descriptor cleanup and callback
lifetime. The pause fix and UI dispatch received independent fresh reviews.

Caption selection after EOF now retains the finished state and time, so a later
replay request still starts correctly. The final engine's real-audio device test
passed paused caption pixels, seeking, EOF/caption selection/replay and cleanup.

An initial package-validation attempt was interrupted by an automatic APT job
inside the isolated builder. After the job finished, only the generated Video
test package left by that interruption was removed; a complete fresh build and
install/remove validation passed. No package-manager locks were removed.

The final installed build passed LCD → HDMI → LCD switching. Its app stream
routed automatically to the temporary TV sink, while the system default stayed
onboard. Returned LCD playback measured 23.6 full-frame equivalents per second
in a short five-second check, and the original movie is left playing on the
handheld. Package verification is clean. The kernel and all twelve protected
firmware/boot/launcher hashes match the starting audit; only Tyrell Video's
package version changed. APPLaunch remains PID 1064 with zero restarts.
