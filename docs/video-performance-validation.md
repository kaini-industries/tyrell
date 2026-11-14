# Local file playback performance

November 13, 2026. A fresh multi-agent performance review followed the
[0.1.3 HDMI/audio correction](video-av-validation.md). Measurements used the
attached CardputerZero: BCM2837, four Cortex-A53 cores at 1 GHz, about 352 MiB
Linux-visible RAM and 64 MiB CMA. Firmware, boot configuration and system-wide
audio defaults were preserved.

## Workload and measurement

A short stream-copy sample of the user's H.264 High level 4.1 movie stayed on
the device's SD-backed `/var/tmp`: 1920 × 816, square pixels, 24000/1001 fps,
AAC with six channels. The original was unchanged. Its compressed bitrate is
about 2.64 Mb/s, so the evidence does not support speculative SD read-ahead.
Only one decoder benchmark ran at a time. Test media and private probes are
removed after testing; raw JSON and logs remain in ignored local
`device-results/`.

The uninstalled pipeline benchmark now records every buffer's allocator
signature, startup time, sink render/drop statistics, page faults, block-input
operations and peak RSS. It takes immutable snapshots before teardown;
per-stage timestamps and snapshot duration expose sequential sampling skew.
Allocator histogram totals must equal the corresponding pad-buffer totals.
Sink render calls, pad arrivals and LVGL assignments do not measure physical
panel refresh or audiovisual synchronization. CPU percentages refer to one
core and exclude work done in other processes.

## LCD conversion

The old LCD path converted full-resolution YUV to RGB565 before reducing the
picture to 320 × 170. Explicit size caps before conversion substantially reduce
that work. Two maximum-throughput runs gave:

| Path | Frames / wall time | Approximate throughput |
| --- | --- | ---: |
| Native hardware decode, fake sink | 484 / 6.60–6.75 s | 72–73 fps |
| Convert full HD, then scale | 136–139 / 35.03–35.08 s | 3.9–4.0 fps |
| Scale YUV first, then convert | 484 / 14.17–14.58 s | 33–34 fps |
| Combined `videoconvertscale` | 421–423 / 35.06–35.10 s | 12 fps |
| Experimental V4L2 ISP conversion | 484 / 7.32 s | 66 fps |

These unsynchronized runs isolate throughput; they are not a TV or LCD refresh
measurement. The ISP experiment sampled only about 4 MiB CMA free. Its color,
failure recovery and memory margin need qualification before use in the app.
The simpler scale-first path is retained. HDMI keeps its native YUV/DMA-buffer
path, with VC4 aspect fitting.

An eight-second clocked playback-engine window with captions off advanced more
than six seconds of media, delivered 24.07 render calls per media second, and
used about 81% of one core. With HD captions blended before scaling, delivery
fell to 6.50 fps and CPU rose to 129% of one core. This isolated the next LCD
bottleneck: per-frame full-resolution subtitle blending.

Pango now supplies its standard timed composition metadata to the LCD sink.
The scaler reduces the picture without bars, RGB565 conversion follows, and
the app blends captions into that small fitted view before centering it in the
fixed 320 × 170 frame. Both explicit SystemMemory and overlay metadata features
are negotiated, and the appsink allocation callback advertises consumption.
Plain video remains supported; small sources retain upscaling. HDMI continues
to use ordinary Pango rendering because its KMS sink does not consume this
metadata.

Final clocked eight-second windows on the Zero gave:

| LCD captions | Render cadence / media second | Application CPU, one core | Sink drops |
| --- | ---: | ---: | ---: |
| Off | 23.95 fps | 78.6% | 0 |
| On, after small-frame blend | 24.07 fps | 83.1% | 0 |

Both windows advanced more than six seconds of media in eight wall seconds;
the caption-on run recorded actual small-frame compositions. Captions therefore
recover full source cadence for this profile. These playback-engine probes use
fake audio and no physical LCD updates; normal UI/audio require separate checks.
Software-decoded wide, anamorphic and small-source fixture tests on the physical
Zero measured caption heights of 16, 15 and 17 pixels, respectively, with
correct centering and no painting into the black bars.


## Output startup

HDMI display and TV-audio leases are now prepared together before opening the
first file pipeline. Pending pause, resume, seek, volume and mute requests are
preserved; O cancels into LCD and Esc/Q release pending leases. Failed handoff
or audio preparation retains an actionable fallback.

Cold process runs reduced pipeline constructions from three to one. Observed
first-HDMI-render timing overlapped: 8.8–10.5 seconds for the instrumented old
engine and 8.7–9.5 seconds for the first candidate. These short runs establish
fewer decoder/preroll cycles, rather than a significant startup-latency gain.
The timing counter is sampled by polling and can describe a provisional old
pipeline; it is not a physical TV/audio readiness measurement.

## Verification

The ARM64 suite includes real GStreamer tests for pending-output cancellation,
queued seeks and pauses, helper ERROR/EOF, file replacement, direct KMS failure,
and cleanup. Wide square-pixel and anamorphic 4:3 white-video fixtures test
aspect fitting, centering and black bars from decoded RGB565 pixels. Existing
caption pixel, paused-seek, track, EOS/replay and frame-ownership checks remain
required. Automatic audio and physical HDMI behavior also require hardware
checks; fake-audio unit tests alone do not establish them.

## Anamorphic decoder limitation

The physical decoder test found a pre-existing metadata problem in the installed
GStreamer/V4L2 stack. For a generated 720 × 576 H.264 file, FFprobe and
`h264parse` report SAR 16:15 (display aspect 4:3), while `v4l2h264dec` reports
SAR 1:1. The error is present before Tyrell's scaler: hardware output fits to
about 212 × 170 instead of 227 × 170. The software decoder retains 16:15 and
the same LCD geometry test passes on the device when V4L2 is excluded.

GStreamer 1.26.2 replaces the input-derived output caps with driver-acquired
caps in its [V4L2 decoder](https://github.com/GStreamer/gstreamer/blob/1.26.2/subprojects/gst-plugins-good/sys/v4l2/gstv4l2videodec.c#L490-L495).
This report does not qualify anamorphic hardware playback as correctly shaped.
The computer-side conversion preset accounts for display aspect ratio and
normalizes to square pixels; use it for affected files. The user's tested
1920 × 816 square-pixel movie is unaffected. No driver or OS-package change
was made to work around this limitation.


## Release and preservation

The final ARM64 package is `tyrell-video 0.1.4-1`, source
`src-372569a5a49a`, SHA-256
`6c248b4f86bd0532dfdee295f2bd9613e21ded36a6e6077ad09d2502712919cd`.
Its source manifest matches the selected working-tree inputs. All **30 ARM64
CTests** and package installation/render/removal checks passed. The six host
media-tool tests and twenty builder tests passed. The bounded LCD renderer also
passed AddressSanitizer/UndefinedBehaviorSanitizer checks with process-global
leak detection disabled.

The physical device passed RGB565 overlay color/position, padded-stride,
upscale, input-immutability and frame-bound tests. Real-audio playback tests
passed captions on/off, paused seeking, EOS/replay, bounded snapshots and cleanup.
The final package was installed and `dpkg --verify tyrell-video` was clean.
APPLaunch remained PID 1064 with zero restarts.

The initial scale-first-only hardware probe had a private stale-header ABI
mismatch and was discarded; matching headers and library were rebuilt before
all reported clocked results. Initial visual-only caption tests were also
insufficient: a positive composition-count assertion exposed continued HD
blending until the caps feature negotiation was corrected. Final tests require
both visible caption changes and a nonzero composition count.

The final before/after audit contains exactly one package-version change:
`tyrell-video 0.1.3-1` to `0.1.4-1`. All twelve protected boot, firmware, overlay,
launcher and provenance hashes match, and the kernel is unchanged. The final
movie is restored for observation. User confirmation of normal APPLaunch launch
and physical LCD/TV appearance for 0.1.4 remains pending; the earlier 0.1.3
physical HDMI/audio acceptance is recorded in the linked report.
