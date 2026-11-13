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

Copy `artifacts/video/tyrell-video_0.1.1-1_arm64.deb` to the device. Install with
`sudo apt install ./tyrell-video_0.1.1-1_arm64.deb` from its containing directory,
then select **Tyrell Video** in APPLaunch. APT resolves the declared media plugin
and font dependencies. No network connection is needed during playback.

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

HDMI is a dedicated display path using DRM/KMS. A desktop or factory precheck
can own it. The app reports an unavailable/busy output instead of stopping
services, switching virtual terminals, or requesting root. The previous HDMI
state is restored on normal cleanup. No active-desktop video backend is included.

The attached-TV test found that software upscaling to the HDMI mode was too
slow. HDMI now uses an owned background and VC4 overlay scaling, retaining
native-sized NV12 video where possible. See the [TV validation report](video-hdmi-validation.md)
for measured geometry, performance and the ten-minute stability result.

On the tested stock image, LightDM owns HDMI and its audio card does not expose
a working HDMI PulseAudio sink by default. Dedicated playback needs the desktop
temporarily released and a working HDMI sink. The TV validation report records
the temporary ALSA HDMI bridge used for testing; it leaves the system default
onboard. This setup is separate from package installation.

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
scaling. Tested 720p/1080p sources dropped frames in this release. The tool
refuses to overwrite an existing destination. It selects the first video and
audio tracks; subtitles remain separate rather than being burned in.

## References

- [GStreamer playbin](https://gstreamer.freedesktop.org/documentation/playback/playbin.html)
- [Bounded appsink](https://gstreamer.freedesktop.org/documentation/app/appsink.html)
- [KMS output](https://gstreamer.freedesktop.org/documentation/kms/index.html)
- [PulseAudio output](https://gstreamer.freedesktop.org/documentation/pulseaudio/pulsesink.html)
