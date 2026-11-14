# HDMI video, audio and cursor correction

November 13, 2026, on the attached Debian 13 ARM64 CardputerZero. This work
follows the 0.1.2 [desktop handoff](video-hdmi-handoff-validation.md). The user
reported poor HDMI frame rate, a retained desktop cursor, and TV sound requiring
manual setup.

## Profiling the actual workload

The user's slow source is H.264 High, level 4.1, 1920 × 816 at 24000/1001 fps,
with AAC audio. A short stream-copy sample stayed on the device for testing;
the original file was not changed. Reports identify its media profile without
including the movie itself.

The installed 0.1.2 player consumed approximately 110% of one CPU core while
playing this source. Stage-isolation benchmarks found the following:

| Path | Frames reaching sink | Wall time | CPU time | First-buffer allocator |
| --- | ---: | ---: | ---: | --- |
| Hardware decode only | 484 | 6.886 s | 1.015 s | DMA buffer |
| Old forced NV12 conversion | 136 | 30.007 s | 29.603 s | System memory |
| Native I420 through convert/scale passthrough | 484 | 6.969 s | 0.855 s | DMA buffer |
| Native DMA_DRM/YU12 passthrough | 484 | 6.671 s | 0.993 s | DMA buffer |

The 20.4-second source contains 484 frames. The old conversion path reached only
about 4.5 frames/s; hardware decoding alone exceeded 70 frames/s. Allocator labels in this table sample the first buffer; later buffers may use
different memory. These runs used an unsynchronized fake sink to isolate throughput. They do not establish physical
TV frame rate or audio synchronization. A first DMA-buffer experiment overlapped
an FFmpeg decoder and failed memory allocation; it was discarded and rerun with
exclusive decoder access. The subsequent DMA-buffer comparison used an explicit test lock.

The HDMI caps now use the chosen plane's concrete I420/YV12/NV12 layouts.
Compatible linear DMA buffers remain directly importable under those caps. Compatible converters pass those buffers through. KMS imports
them and VC4 performs aspect fitting instead of copying and converting every HD
frame on the CPU. In an actual app run, DRM reported imported YU12 at 1920 × 816
with a 1280 × 544 destination at (0,88) in a 1280 × 720 mode: exact aspect ratio
and 88-pixel bars above and below. All three YUV memory planes were imported into one video overlay.

New diagnostics distinguish decoder output, sink-pad arrivals and GstBaseSink
render/drop counters, and report negotiated caps and allocator memory. Sink render
calls are stronger evidence than pad arrivals but still do not measure physical
scanout or audiovisual synchronization. Their counters can reset on seeks/flushes.

## Cursor ownership

Before the fix, a 64 × 64 ARGB cursor framebuffer allocated by labwc remained
active above the movie on the HDMI CRTC. The player now snapshots and hides active
foreign cursor/overlay planes on that CRTC, restoring their exact framebuffers,
source crops and destination rectangles during cleanup. Primary planes and
unrelated displays are excluded. Preflight and partial-failure rollback are tested.
A hardware snapshot during playback confirmed the cursor plane had no framebuffer
or CRTC while the imported video plane was active.

## Automatic TV audio

The app prefers a working existing HDMI sink. If none exists, a normal-user
helper creates a temporary 48 kHz stereo VC4 HDMI sink. Its low priority preserves
the system default. Only this player's stream targets the new sink. A socket
lease and transient user service with ExecStopPost cleanup remove the owned
module after normal exit, player death, or helper death. Cleanup matches the full
unique module token, leaving other sinks alone. Explicit audio choices remain
selected; failure returns to onboard sound with a notice.

Isolated device tests passed module creation and removal after normal release
and helper SIGKILL, with the system default unchanged in both cases.

An integrated candidate exposed a startup stall consistent with an upstream
GStreamer 1.26.2 PulseAudio device-setter race. That setter can return without
unlocking the Pulse mainloop if the stream has not acquired an index. All runtime
route changes now coalesce into a pipeline restart, selecting the destination on
a fresh NULL-state sink while preserving current position, pause state and tracks.
This avoids depending on a timing-sensitive readiness check. [GStreamer source](https://github.com/GStreamer/gstreamer/blob/1.26.2/subprojects/gst-plugins-good/ext/pulse/pulsesink.c#L2595).

A separate timeout case rejected READY already queued while the UI was occupied
with pipeline startup. The audio client now drains its reply before applying the
deadline; a synchronized delayed-poll regression test covers it. A pending cleanup
or silent helper still has a bounded timeout.

## FFmpeg's role

GStreamer's libav plugin already provides FFmpeg-based software codecs, while the
tested H.264 source uses V4L2 hardware decoding. Replacing the player with FFmpeg
would still require efficient DMA-buffer presentation; it would not remove the
measured conversion bottleneck by itself. FFmpeg remains useful for codec tools
and optional computer-side conversion. [GStreamer libav decoder](https://gstreamer.freedesktop.org/documentation/libav/avdec_h264.html),
[DMA-buffer design](https://gstreamer.freedesktop.org/documentation/additional/design/dmabuf.html),
[FFmpeg hardware-acceleration options](https://ffmpeg.org/ffmpeg.html#Advanced-Video-options).

## Reviews and automated validation

Six fresh agents worked on audio lifecycle, performance, cursor ownership,
adversarial integration review, an independent FFmpeg assessment, and Pulse
threading review. Findings corrected explicit-speaker-to-Automatic routing,
retained audio-helper failure on LCD, delayed audio-error recovery, descriptor
handling, and the startup issues described above.

The ARM64 build includes 24 CTests, plus builder-only package install/remove,
JSON diagnostics and UI smoke checks. The unchanged Status default passed its
four CTests and package install/remove checks after the shared verifier change.
The six host media-tool tests and 20 builder tests passed. A clocked production-library probe of the same HD source measured 24.05 fps
without subtitles (19.64% of one CPU core) and 24.06 fps with two-line SRT
subtitles (103.34% of one core), with zero sink drops in both eight-second
steady-state windows. These tests use synchronized HDMI and fake audio to
isolate video; full-app audio and physical observations are recorded below.

Generated media, temporary diagnostics and raw evidence are retained locally in
ignored build/device-results directories; they are not shipped in the package.

An opaque DMA_DRM caps variant passed unclocked throughput tests but encountered
a clocked fallback allocation failure and subtitle negotiation failure. Concrete
raw formats passed both clocked cases while still importing DMA buffers. The
exact allocator error in the discarded variant was not established; the report
does not attribute it to an unknown pixel format or prove CMA exhaustion.

## Final physical observations

The user confirmed TV beeps matching the white flashes, smooth HD motion with no
desktop cursor, and TV video plus TV sound returning after unplugging for 15
seconds, reconnecting, selecting O and resuming with Space.

Native UI checks passed explicit onboard audio, returning to Automatic, retaining
an explicitly selected TV sink while video uses LCD, helper SIGKILL recovery to
onboard audio on LCD, and automatic reacquisition on HDMI. The desktop service
and launcher were preserved throughout.

The final release audit found no P1/P2 issues. One package-lifecycle follow-up
remains: removing the package while explicitly retaining TV audio on LCD can
leave that transient user service active until the player exits. Active-removal
cleanup should be strengthened separately; ordinary exit and helper-failure
cleanup were tested in this release.

## Package identity and device preservation

The qualified package is `tyrell-video_0.1.3-1_arm64.deb`, source
`src-08798b6c30ab`, SHA-256
`0e5e90d74669665c6ae4b15e407fc67d8ba0b5c65cb0c3a637245b810887bb0a`.
All packaged source inputs match the working tree. The final build passed all
24 CTests and package checks. Protected firmware, boot and launcher file hashes,
the kernel, and APPLaunch PID 1064 with zero restarts match the baseline. The
regular desktop and system audio default were retained. Diagnostic binaries
were copied temporarily rather than installed as OS packages.

The before/after package inventory records the Video upgrade and an additional
Screen installation during the test window. Screen is not a Video dependency;
it was left in place rather than treating it as an app-owned package.
