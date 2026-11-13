# Tyrell Video validation

Implementation and validation date: November 13, 2026. This report separates
automated checks, live observations, and physical acceptance. A successful build
or codec probe does not establish smooth playback on the handheld.

## Live starting inventory

The read-only `scripts/video-inventory.py` probe used the existing SSH account.
Its raw, device-specific evidence is retained locally in the ignored
`device-results/video-inventory.json`.

- Internal display: `card1-SPI-1`, connected/enabled, 320 × 170.
- HDMI: `card0-HDMI-A-1`, disconnected; no monitor modes reported.
- Broadcom `bcm2835-codec-decode` device exists at `/dev/video10`. This alone
  does not prove GStreamer hardware decoding.
- The onboard ES8389 audio output is exposed through PipeWire's PulseAudio
  interface. Its system sink volume was zero; this probe did not change it.
- LightDM was active; factory precheck was inactive. HDMI ownership with an
  attached monitor remains untested.
- APPLaunch was active with PID 1064 and zero restarts.
- GStreamer libraries/plugins were installed; the `gst-inspect-1.0` CLI was
  absent. The app's diagnostics enumerate plugins without requiring that CLI.

The user confirmed that no HDMI monitor is available for this implementation.
HDMI hardware acceptance is therefore explicitly pending.

## Automated and playback results

The ARM64 package is `tyrell-video 0.1.0-1`, source revision
`src-f54e9682780f`, SHA-256
`3ce44363550217017fd905b3bdeb4f06457b744c64d7fe7b9b64454bdaed2c30`.
The artifact manifest records exact dependency versions and source-file hashes.

- All **18 CTests passed**: generated media, real two-audio-track MKV switching,
  embedded/external caption pixel comparisons, paused seek and caption changes,
  EOS/replay, invalid media, HDMI absence, mode selection, browser, frame
  ownership/cache invalidation, audio-route recovery, report-write failure,
  CLI validation and five software-rendered pages.
- Package inspection, installation, CLI/render smoke checks and removal passed
  inside the isolated Debian ARM64 builder.
- The default Status build passed its four CTests and install/remove checks,
  retaining `artifacts/video/` intact.
- Twenty builder tests and six host-tool tests passed. Conversion tests checked
  H.264/yuv420p, 24 fps, AAC stereo, even-sized aspect fitting (including
  anamorphic input), and refusal to overwrite existing files.
- The optional SDL-enabled build and frame test passed in the ARM64 VM. A dummy
  SDL driver ran 20 browser iterations and exited without errors, at 17 MiB peak
  RSS. A visible desktop window and simulated media/audio remain unqualified.

### Live decoder delivery

The final release probe completed all 17 decoder/control checks using the real
PulseAudio session. Inputs were four-second, 24 fps, progressive 8-bit synthetic
clips, with 48 kHz audio where present. The table measures RGB565 sample delivery
to the probe; physical panel refresh and audible lip sync need separate checks.

| Profile | Decoder | Samples | Peak RSS | Result |
| --- | --- | --- | --- | --- |
| MP4 / H.264 360p / AAC | `v4l2h264dec` | 97 | 55 MiB | Decoded |
| MP4 / H.264 720p / AAC | `v4l2h264dec` | 96 | 57 MiB | Decoded |
| MP4 / H.264 1080p / AAC | `v4l2h264dec` | 96 | 58 MiB | Decoded |
| MKV / HEVC 360p / AAC | `avdec_h265` | 96 | 69 MiB | Decoded |
| MKV / HEVC 720p / AAC | `avdec_h265` | 96 | 78 MiB | Decoded |
| MKV / HEVC 1080p / AAC | `avdec_h265` | 95 | 118 MiB | Decoded; heavy memory/CPU use |
| MKV / AV1 360p / Opus | `av1dec` | 94 | 34 MiB | Decoded |
| MKV / AV1 720p / Opus | `av1dec` | 8 | 54 MiB | Degraded; convert first |
| MKV / AV1 1080p / Opus | `av1dec` | 4 | 79 MiB | Degraded; convert first |
| AVI / MPEG-4 360p / MP3 | `v4l2mpeg4dec` | 96 | 23 MiB | Decoded |
| WebM / VP8 360p / Vorbis | `vp8dec` | 97 | 26 MiB | Decoded |
| WebM / VP9 360p / Opus | `vp9dec` | 97 | 27 MiB | Decoded |
| WebM / VP9 720p / Opus | `vp9dec` | 97 | 37 MiB | Decoded |
| WebM / VP9 1080p / Opus | `vp9dec` | 97 | 53 MiB | Decoded |
| MP4 / silent H.264 360p | `v4l2h264dec` | 97 | 22 MiB | Decoded |

The normal fixture contains 96 video frames. Counts can include preroll or omit
a sample replaced before consumption. Each probe ran for ten seconds, including
startup and time after EOS. AV1 at 720p/1080p delivered very few samples and is
unsuitable for direct playback in these tested profiles; use the H.264 conversion
preset. HEVC 1080p used 12.1 CPU-seconds during its probe and needs qualification
with real files. These short samples do not qualify arbitrary high-bitrate,
10-bit/HDR, interlaced or long-form media as smooth.

The real two-audio-track/embedded-SRT MKV passed rapid pause, seek, audio track,
caption on/off and cleanup checks on the final engine. Its sample count was 113
because the control exercise replays part of the clip. The intentionally corrupt
MP4 produced the expected structured stream-type error.

Raw evidence is retained locally under ignored `device-results/`, especially
`video-decoder-matrix-release.json` and `video-release-matrix-start.json`; the
latter records the exact probe hash and release revision. An earlier rapid-track
timeout was corrected by removing unnecessary pipeline destruction on caption
changes. Earlier runs with the long clip in RAM-backed `/tmp` are excluded from
codec results; moving that clip to SD-backed `/var/tmp` restored normal memory
availability.

### Sustained LCD playback and native handoff

A full **30-minute H.264/AAC** clip at **320 × 170, 24 fps** completed on the real
framebuffer using `v4l2h264dec` and the onboard PulseAudio output. The tested
steady-playback revision was `src-04f19db4bee0`; the final release adds audio-route
recovery and report-write failure handling, with the steady playback path
unchanged. Final release codec/control tests are recorded above.

- Reported media position and duration both reached **1,800 seconds**, with no
  playback error. Wall time including startup and the Q check was 1,817.61 seconds.
- **43,201 unique frame assignments** were given to LVGL. These count software
  handoffs rather than physical SPI panel refreshes. The pipeline reported one
  QoS drop.
- Average application CPU was **34.3% of one core**; peak RSS was **53.7 MiB**.
  Sixty-one samples covered 1,800 seconds. Sampled RSS ranged from 24.1 to
  40.9 MiB and ended at 27.8 MiB, with no growing RSS trend. System available
  memory stayed above 90 MiB during sampling.
- Actual framebuffer captures show the clip advancing at startup, 15:16, and the
  paused 30:00 endpoint. Native track selection and text captions were also
  exercised with synthetic events through the existing keyboard device.
- Q was sent at 13:54:09 America/Chicago, before the bounded test's automatic
  timeout. The player exited and the **same APPLaunch PID 1064** resumed with
  **zero restarts**.

The system audio sink volume was zero, so no audible speaker/headphone or lip-sync
claim is made. Physical keyboard switches were not pressed. Raw reports and
captures remain local: `video-soak-finish.json`, `video-soak-resources.jsonl`,
`video-soak-summary.json`, and the `video-soak-*.png` images.

### Preservation

Before/after inventories show exactly one added package, `tyrell-video`; no
existing package versions changed or were removed. All **12 protected hashes**
match, covering selected boot assets/configuration, overlays, launcher binary and
service, image/driver provenance and kernel pin. The kernel is unchanged.
The original packaged Video launcher entry is restored after temporary bounded
test commands. `dpkg --verify tyrell-video` returned clean on the final release;
device diagnostics parsed successfully, and the `/dev/full` report test returned
exit 1 with the expected write-failure message. Generated test directories and
media were removed after downloading the evidence. No operating-system image or
controller firmware was flashed.

## Remaining physical release checks

- Press the physical keys, including navigation, pause, seek, menus and Q exit.
- Listen through speaker and headphones; check volume and headphone switching,
  and observe audio/video synchronization.
- Attach HDMI, check full-screen aspect fit, captions and HDMI sound, then perform
  the 30-minute HDMI baseline.
- Disconnect/reconnect HDMI during playing and paused states; confirm position,
  tracks and volume survive, with paused LCD fallback after disconnect.
- Check a busy HDMI display reports a useful message without interrupting its
  owner, and confirm prior HDMI state is restored on normal exit.
- Qualify representative real-world files against the measured short-fixture
  results, especially high-resolution AV1 and memory-intensive HEVC.

HDMI hardware, audible sound, physical switches and observed lip sync remain
unqualified. Automated sustained LCD playback and software checks are recorded
above; they do not substitute for those physical observations.
