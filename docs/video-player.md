# Tyrell Video

Tyrell Video is a local-file player for the Linux CardputerZero, packaged
separately from Tyrell Status. It uses the stock APPLaunch handoff, the built-in
320 × 170 framebuffer and keyboard, GStreamer 1.26, and the existing audio
session. It does not replace the launcher, firmware, drivers, or boot settings.

## Build and install

```sh
make package APP=video
```

The existing isolated Debian ARM64 VM installs the media development libraries,
builds and tests the app, and checks package installation/removal. Results are
exported to `artifacts/video/`, including the `.deb`, screenshots, diagnostics,
dependency inventory, checksums and manifest. The normal `make package` still
builds Status and retains the video artifacts.

The current [0.1.7 ARM64 package](../dist/tyrell-video/0.1.7/tyrell-video_0.1.7-1_arm64.deb)
passed 35 ARM64 CTests, package install/remove checks and actual-device HDMI
recovery, sustained software playback and normal APPLaunch launch/exit checks.
See the [qualification report](video-hdmi-017-validation.md) for the tested
profile and measurement limits.
Package [checksums and provenance](../dist/README.md) identify the exact build.
On the device, from a repository checkout:

```sh
cd dist/tyrell-video/0.1.7
sha256sum -c SHA256SUMS
sudo apt install ./tyrell-video_0.1.7-1_arm64.deb
```

For a local build, copy the package from `artifacts/video/` and install it the
same way. Then select **Tyrell Video** in APPLaunch. APT resolves the declared
media plugin and font dependencies. No network connection is needed during playback.

Keep videos on SD-backed storage such as `~/Videos`. The tested stock image
mounts `/tmp` as tmpfs: a large clip there consumes the handheld's limited RAM
and swap rather than normal SD storage. Use `findmnt -T DIRECTORY` to check a
test-media location; `/var/tmp` was SD-backed on the tested installation.

Start graphics through APPLaunch so it hands over the screen and keyboard.
For a display-free inventory, run `tyrell-video --diagnostics`; for a read-only
system inventory, run `python3 scripts/video-inventory.py` on the device.
Remove only the app with `sudo apt remove tyrell-video`.

## Files and controls

The browser starts in `~/Videos` when present, otherwise Home. It navigates one
directory at a time, with mounted media available through the locations menu;
it does not recursively index the microSD card. `--media-root DIR` changes the
starting location, and a positional file starts playback directly.

| Context/action | Key |
| --- | --- |
| Browse/select menus | Arrows or W/A/S/D; Enter confirms |
| Pause/resume | Space |
| Seek backward/forward 10 seconds | Left/Right or A/D |
| Volume | Up/Down or W/S |
| Switch LCD/HDMI | O |
| Audio and subtitle tracks | T |
| Audio output | V |
| Mute | M |
| Return to browser | Esc |
| Exit to APPLaunch | Q |

The track menu lists embedded audio/text tracks and a matching external SRT
file. Subtitles start off and can be disabled again. Pango supplies timed text
rendering and font fallback; subtitle appearance is calibrated for the small
screen. Styled ASS/SSA and DVD/Blu-ray image subtitles are outside the required
compatibility set.

Playback controls fade after four seconds, including while paused, to uncover
the picture and captions. A keypress reveals them; H opens the key guide.
The HDMI controller and loading/error states keep their controls visible.

## Display and sound

`--output auto` prefers an already connected, available HDMI output at startup.
The LCD becomes the controller while HDMI shows the movie. Connecting a cable
during playback does not interrupt it; use O to switch. Removing active HDMI
pauses and returns video to the LCD at the same position. Space resumes.

HDMI uses DRM/KMS. When selecting HDMI, the packaged console lease temporarily
switches from the desktop to an unused virtual console. The stock LightDM/labwc
desktop releases the display while its session and applications remain running.
The original console returns after switching to LCD, returning to the browser,
exiting, or a player crash. Starting with 0.1.6, the socket-activated helper runs
as a dedicated nonroot account with only `CAP_SYS_TTY_CONFIG`; systemd supplies
its console descriptor. The video player runs as the normal user. No desktop service is stopped and no
boot setting changes. See the [desktop handoff validation](video-hdmi-handoff-validation.md)
for recovery tests and deployment details.

The helper socket is restricted to root and the local video group, verified
using kernel-captured socket credentials. It accepts a fixed connection-lifetime
lease, with no caller commands or paths. It grants one lease at a time. See the
[Store policy validation](video-store-policy-validation.md) for its security
boundary and nonroot recovery checks. Preparation is asynchronous:
LCD playback and controls remain available. Unsupported owners, missing helpers
and handoff failures keep playback on the LCD with an explanation. The helper
also recovers the console after an abnormal helper exit.

The original attached-TV test found that software upscaling to the HDMI mode
was too slow. HDMI uses an owned background and VC4 overlay scaling, retaining
native-size video where possible. The [0.1.6 TV report](video-hdmi-016-validation.md)
records physical sound, captions, aspect fitting and hotplug checks, together
with a successful ten-minute run and a separate startup allocation failure.

Version 0.1.7 adds one software-video retry per file load for the specific KMS
buffer-pool activation failure after hardware video has produced output.
Controlled tests verified one recovery, a nonzero paused position surviving
caption selection, and paused LCD fallback after a second failure. The retry
retains the console and audio leases; it does not broadly retry corrupt files,
missing codecs, permission errors or unrelated failures, and it does not change
system decoder preferences or firmware. The tested 1920 × 816, 24 fps H.264/AAC
software session advanced 615.097 seconds with user-confirmed smooth motion,
TV sound and readable captions. Software decoding uses substantial CPU and
other profiles can still need conversion; the
[0.1.7 qualification](video-hdmi-017-validation.md) records the actual limits.

On the tested stock image, LightDM owns HDMI and its audio card does not expose
a working HDMI PulseAudio sink by default. The app releases the desktop
automatically for video playback and, when needed, creates a temporary HDMI
audio sink in the normal user audio session. Only its own stream moves to that
sink. It keeps explicit audio choices, prefers existing HDMI sinks, and removes
its temporary sink on exit or crash. A busy or disconnected audio device produces
a clear notice and onboard fallback. System sound defaults remain unchanged.

Audio selection changes the app's stream, not the system default. HDMI playback
prefers HDMI audio when exposed by the current audio session; otherwise it uses
the onboard output. The board driver handles speaker/headphone switching. If
the system sink is muted or at zero volume, the app's volume alone cannot make
it audible; use the existing system sound settings.

## Compatibility and conversion

Containers and codecs are handled by the installed Debian GStreamer plugins.
An MP4 or MKV extension does not determine how expensive its codec is. Reducing
display size does not eliminate the cost of decoding a high-resolution source.
See [validation results](video-validation.md) for what was actually measured.

Generate synthetic, redistributable test media on a computer with FFmpeg:

```sh
python3 scripts/video-fixtures.py generate build/video-fixtures
```

The set includes H.264/AAC MP4, MPEG-4/MP3 AVI, VP8/Vorbis and VP9/Opus WebM,
HEVC/AAC and AV1/Opus MKV when their encoders are installed, silent video,
multiple audio tracks, embedded/external SRT and intentionally invalid media.
Unavailable encoders are explicitly skipped. For a sustained baseline, generate
a separate directory with `--duration 1800 --baseline-only`.

On the test machine, run the uninstalled playback probe against those files:

```sh
python3 scripts/video-matrix.py /path/to/tyrell-video-probe build/video-fixtures --fake-audio --output build/video-matrix-001.json
```

Choose a new report path for each run; existing evidence is never overwritten.
The report is saved atomically after each file with `complete: false` until the
scan finishes, retaining completed rows if the run is interrupted. These probes
measure decoder delivery; `--fake-audio` also bypasses the physical sound path.

Prepare a demanding source without overwriting it:

```sh
python3 scripts/video-fixtures.py convert input.mkv output-handheld.mp4
python3 scripts/video-fixtures.py convert input.mkv output-hdmi.mp4 --profile hdmi
```

The presets use aspect-preserving, even-sized H.264/yuv420p at 24 fps with AAC
stereo. The handheld bounds are 320 × 170; HDMI bounds are 640 × 360, matching
the smooth attached-TV test. The TV mode remains 1280 × 720, with hardware
scaling. Earlier releases forced a slow HD frame conversion. Version 0.1.3 preserves
decoder-native DMA buffers for HDMI when the negotiated path supports them;
first-buffer memory and software-retry costs must be interpreted from the
current qualification rather than assumed to be identical on every run. The tool
refuses to overwrite an existing destination. It selects the first video and
audio tracks; subtitles remain separate rather than being burned in.

## References

- [GStreamer playbin](https://gstreamer.freedesktop.org/documentation/playback/playbin.html)
- [Bounded appsink](https://gstreamer.freedesktop.org/documentation/app/appsink.html)
- [KMS output](https://gstreamer.freedesktop.org/documentation/kms/index.html)
- [PulseAudio output](https://gstreamer.freedesktop.org/documentation/pulseaudio/pulsesink.html)

Hardware-specific playback measurements and the LCD conversion changes are in the
[local-file performance report](video-performance-validation.md).

The [native display report](video-lcd-refresh-validation.md) records the 0.1.5
framebuffer refresh correction and pause/caption clock-continuity tests.

The [0.1.7 Store preparation](../store/tyrell-video/0.1.7/README.md) includes the
completed hardware evidence and reviewed real-device recording. The package was
[submitted as Store PR #324](https://github.com/CardputerZero/packages/pull/324),
and the recording is linked in the
[review comment](https://github.com/CardputerZero/packages/pull/324#issuecomment-6890010512).
Automated submission checks passed. This first-app release awaits manual
maintainer review and merge before it becomes available in the Store.
