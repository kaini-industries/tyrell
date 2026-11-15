# Tyrell Video 0.1.6 final HDMI qualification

Historical baseline: the startup failure recorded here led to the replacement
[0.1.7 package and completed recovery qualification](video-hdmi-017-validation.md).

November 14, 2026, on the existing Debian 13 ARM64 CardputerZero connected to a
television. This follows the [nonroot helper policy work](video-store-policy-validation.md)
and tests the installed package on the external display. It does not submit the
app to the Store or establish maintainer approval.

The tested package is `tyrell-video_0.1.6-1_arm64.deb`, application version 0.1.6,
source `src-11a4e420adee`, SHA-256
`3e6b8f50e0f076e783943e77f61e2b57b856a7168f44886dd7d7646013fb239f`.
The package bytes and original build provenance are unchanged.

## Test conditions

| Item | Observed condition |
| --- | --- |
| Kernel | `6.18.39+rpt-rpi-v8` |
| Before-test desktop | labwc PID 1211; foreground tty7; 1920 × 1080 at 60 Hz |
| APPLaunch | PID 1065; zero restarts at the baseline |
| Main player | Normal user, UID 1000 |
| Console helper | Real, effective, saved and filesystem UIDs all 111 |
| Helper capabilities | Effective, bounding and ambient masks all `0000000004000000`: only `CAP_SYS_TTY_CONFIG` |
| Main motion source | Synthetic continuous 720-second H.264 Constrained Baseline level-4/AAC flash-and-beep clip; 1920 × 816, 24 fps, about 1.19 Mbit/s |
| Geometry source | Synthetic 640 × 480 square-pixel calibration card |
| HDMI video mode | 1280 × 720 at 60 Hz during playback |

The 720-second source was stopped before EOF after more than ten minutes of
media progression in the completed sustained run below. No personal movie
frames are used for this qualification.

## Completed visual and control checks

The user observed the physical television and handheld, separately from the
programmatic output and resource checks:

| Check | Result |
| --- | --- |
| HDMI desktop handoff | The TV showed moving video and the handheld showed the controller. |
| Motion and cursor | The user confirmed smooth motion with no desktop cursor. |
| Automatic TV sound | Beeps came from the TV and matched the white flashes without an obvious delay. |
| External SRT captions | The user confirmed readable TV captions after enabling the track. |
| Pause and caption selection | The paused elapsed time stayed at 0:53 of 12:00 before and after enabling SRT; the user confirmed resumed playback preserved its time. |
| Unplug recovery | After HDMI was unplugged for at least 15 seconds, the user confirmed a paused handheld picture at the same point. |
| Reconnect recovery | After reconnecting, selecting O and resuming with Space, the user confirmed both TV picture and automatic TV sound returned. |
| Geometry card | The user confirmed the cyan circle and pink square were correctly shaped, with all four white border edges visible. |

Actual `/dev/fb_lcd` captures made immediately before and after caption activation
while paused were identical, including the controller's nonzero 0:53 elapsed
time and 12:00 duration. These captures support the pause-position check; the
separate user observation establishes TV-caption readability and continued
playback after resuming. They do not measure subframe timing or lip sync.

## Scaling and video path

During the 1920 × 816 motion source, the observed decoder was `v4l2h264dec`.
The video was I420 (DRM YU12), with square pixels. The initial interactive run
reported a DMA-BUF-backed first video buffer. The completed sustained retry
instead reported `SystemMemory` for its first delivered video buffer; KMS can
copy source data into a display buffer object. These observations do not
establish that every run or frame uses direct DMA import, or that playback is
always zero-copy.

KMS reported the full 1920 × 816 source mapped to a 1280 × 544 destination at
(0,88), inside the 1280 × 720 display mode. This preserves the source aspect
ratio with 88-pixel bars above and below; the television is receiving a 720p
signal rather than a full-HD scanout mode.

The 640 × 480 calibration source mapped to 960 × 720 at (160,0), with 160-pixel
bars on each side. The KMS rectangles establish the intended fitting geometry;
the user's circle, square and border observation checks the resulting physical
TV picture, including visible edges.

The cursor plane, ID 659, had no framebuffer and no assigned CRTC during the
captured playback and geometry checks. This agrees with the user's observation
that no desktop cursor remained over the video.

## Audio routing and normal cleanup

The app's own PulseAudio-compatible stream selected its temporary
`tyrell_video_hdmi_…` output automatically. The system default remained the
onboard `alsa_output.platform-sound.stereo-fallback` sink. This is stream-local
routing, not a permanent change to the default audio device.

Normal player exit restored tty7 and the desktop's previous 1920 × 1080,
60 Hz mode. The app's temporary audio modules were removed and the onboard
default remained unchanged. An end-of-run process and protected-file audit is
still part of the final cleanup check.

## Completed continuous playback check

The clean retry used the normal input path, with no early injected keys.
Captions were enabled after HDMI became ready. The app completed 603.713 seconds
of media progression in a 625.689-second app run, including startup, with one
pipeline build. Caption selection did not seek or rebuild the pipeline. The
final output was HDMI, with no playback error or notice. The user confirmed
smooth picture and beeps matching the flashes during this run.

| Full-run measurement | Result |
| --- | ---: |
| App elapsed wall time, including startup | 625.689 s |
| Media progression | 603.713 s |
| Pipeline builds | 1 |
| GStreamer sink render calls | 14,462 |
| Sink-reported drops | 2 |
| Render calls per media second | 23.955 |
| Maximum observed per-element QoS drop statistic | 19 |
| Average app CPU, one-core scale | 88.105% |
| App-reported peak RSS | 65,704 KiB |

The two sink drops and the QoS statistic are different observations, not counts
to add together. Approximately 24 render calls per media second is consistent
with the 24 fps source, but is not a physical scanout count. This is not a
zero-drop result.

Resource sampling covered 484.996 seconds of steady-state playback after
readiness and the late caption selection, not the entire ten-minute media
window. The full-run CPU, peak RSS, media position and sink totals above come
from the app's final report. Three samples during shutdown were excluded from
the steady-state resource summary.

| Sampled steady-state measurement | Result |
| --- | --- |
| RSS range | 51,756–52,932 KiB |
| First / last RSS sample | 52,928 / 51,968 KiB |
| Open descriptors | 32 in every included sample |
| Threads | 14 in every included sample |
| Minimum free CMA | 21,892 KiB |
| Maximum temperature | 57,996 m°C, about 58.0°C |
| HDMI and foreground console | Connected and tty1 throughout the included samples |

The samples show no sustained RSS growth within that measured interval. They
do not establish a sampled resource history for startup or every second of the
full run. The completed summary is retained as `endurance-summary.json` with
the raw reports and samples.

The earlier interactive report includes caption changes, pause/resume and hotplug
recovery across multiple pipeline builds. Its counters may reset at those
transitions and do not define a continuous frame-rate or memory-stability
benchmark. In particular, a source duration of 12 minutes or a successful short
render interval is not proof of a ten-minute run.

## Earlier startup failure — unresolved

An earlier automated stability attempt did not begin HDMI rendering. Its report
recorded `failed to activate buffer pool; paused on LCD`, zero sink render calls
and no first HDMI render. It fell back safely to paused LCD playback at position
0. Free CMA was approximately 4 MiB around that attempt; this observation does
not establish the cause of the allocation failure.

That failed attempt is retained separately in the raw evidence rather than
discarded or included in the successful run's statistics. A later retry using
the normal input path and enabling captions only after readiness completed the
sustained check above. Controlled startup comparisons are still in progress;
the failure's cause and repeatability are not yet established. The successful
retry must not be described as resolving that earlier failure or proving fully
reliable publication readiness.

## Connected-display crash checks — pending

Player-crash and helper-SIGKILL recovery with the television attached have not
yet been completed in this final qualification. Earlier disconnected-console
recovery checks remain recorded in the
[helper policy report](video-store-policy-validation.md); they are not relabeled
as connected-display tests.

## Evidence and limits

Raw captures, KMS state, routing details, reports and recorded user observations
are retained locally under ignored `device-results/video-hdmi-016-final/`.
The initial device inventory was recorded at `2026-11-15T04:20:30Z`, which is
November 14 in the client's America/Chicago time zone.

KMS geometry, DMA-BUF observations and sink render calls describe the selected
pipeline and submitted buffers. They do not count physical TV scanouts or
instrumentally measure audio/video delay. The physical observations above are
the user's qualitative confirmation. This source profile and TV do not establish
smooth playback of every codec, bitrate or resolution.

The user reports that a phone recording of the television and handheld has
been captured and is ready. The file has not been received, reviewed or attached
to a publication PR. The synthetic source, controller captures and rendered
Store previews retain their separate labels; none is automatically treated as
that recording or maintainer approval. The
[recording guide](../store/tyrell-video/0.1.6/recording-guide.md) describes filming
the app on the physical device. Connected crash checks, the unresolved startup
failure and final cleanup audit still prevent treating this draft as complete
publication clearance.
