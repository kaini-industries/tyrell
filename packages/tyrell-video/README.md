# Tyrell Video

Keyboard-controlled local video player for the Linux M5Stack CardputerZero.
Start **Tyrell Video** through APPLaunch so the launcher yields display/input
ownership and resumes after the player exits.

The built-in screen uses the existing 320×170 framebuffer. Dedicated HDMI
playback keeps controls on the handheld. On the stock LightDM/labwc desktop,
the installed HDMI lease helper temporarily switches to an unused virtual console
so the desktop releases HDMI without logging out. The desktop returns when HDMI
playback ends, including after a player crash. The player remains unprivileged;
it changes neither boot settings nor system-wide audio defaults. Other display
owners that do not release DRM ownership still produce a clear LCD fallback.

| Action | Keys |
| --- | --- |
| Browse / volume during playback | Up/Down or W/S |
| Parent folder / seek ±10 seconds during playback | Left/Right or A/D |
| Open file or folder / confirm menu | Enter |
| Pause / resume | Space |
| Switch LCD / HDMI | O |
| Select audio or subtitle track | T |
| Select audio output | V |
| Mute | M |
| Home, Videos and mounted-media roots | B |
| Key guide | H |
| Return to browser / close menu | Esc |
| Exit to APPLaunch | Q |

The browser starts in `~/Videos` if it exists, otherwise Home. Folder listings
include directories and common video extensions, with no recursive card scan.
The UI uses a compact Latin font: unsupported filename characters appear as
`?`, while actual UTF-8 paths are preserved. Subtitles use Pango's installed
font fallback rather than the compact UI font.

Audio and subtitle tracks are selected from T. Subtitles start off. A matching
external `.srt` file is discovered alongside the video, including case-insensitive
extensions. Styled ASS/SSA and bitmap subtitles are outside the required
compatibility set. Broad codec availability does not guarantee real-time
high-resolution decoding; demanding files may need computer-side conversion.

At startup automatic output selection prefers available HDMI, otherwise LCD.
HDMI plugged in during playback becomes selectable with O. Unplugging active
HDMI pauses and returns video to LCD at the same position. Press Space to resume.
HDMI sound is preferred for external playback when the user's PulseAudio-compatible
audio session exposes a working HDMI sink, with onboard output as fallback.
If the stock image exposes no HDMI sink, the app creates a temporary user-session
VC4 HDMI sink and moves only its own audio stream to the TV. It removes that sink
when no longer needed, including after a crash; an existing HDMI sink or explicit
audio choice takes precedence. If TV audio cannot open, playback keeps onboard
sound and reports the problem. No permanent audio configuration is installed.
The board's existing headphone detection controls speaker muting.

An attached-TV test confirmed ten minutes of smooth low-resolution H.264/AAC
playback, matching flash/beep timing, readable captions, correct aspect fitting,
and paused LCD fallback after unplugging HDMI. Hardware scaling fits the source
to a 1280 × 720 HDMI signal. Version 0.1.3 retains decoder-native I420/YU12 DMA
buffers when supported, avoiding the CPU conversion that made HD files slow.
Decoder, sink-render and buffer-memory diagnostics expose the actual path; see
the validation report for measured source profiles and subtitle limits.
Version 0.1.4 scales LCD pictures before RGB565 conversion and blends timed
Pango captions into the small fitted frame. HDMI display and audio preparation
share one initial decoder pipeline. Hardware-specific comparisons and remaining
limits are in the [performance report](../../docs/video-performance-validation.md).
The computer-side HDMI conversion
profile bounds output to 640 × 360. Setup and measured results are in the
[attached-TV validation report](../../docs/video-hdmi-validation.md).

```sh
tyrell-video /home/user/Videos/example.mp4
tyrell-video --media-root /media/user/Movies --output lcd
tyrell-video --diagnostics
tyrell-video --headless-screenshot /tmp/video.ppm --page player --frames 2
tyrell-video example.mp4 --run-seconds 1800 --report /tmp/playback-report.json
```

`--diagnostics` emits JSON without opening a framebuffer or keyboard. It
enumerates decoders, outputs and permissions; connected hardware and active
sessions determine what is available. Headless previews are deterministic
software-rendered LVGL illustrations, not evidence of actual playback.
Additional preview pages: `browser`, `hdmi`, `tracks`, and `error`.

`--run-seconds` provides a finite validation run and `--report` records playback
state, codec, frame delivery, CPU time and peak RSS. LCD frame assignments count
unique frames given to LVGL; they do not measure physical panel refresh or
audio/video timing. Report write failures return a nonzero exit status.

Build/package with `make package APP=video` from the repository root. An optional
SDL simulator is enabled by `-DTYRELL_ENABLE_SDL=ON`, then `tyrell-video
--simulator [FILE]`. Native simulator builds require development GStreamer
libraries and SDL2. Device builds also require libdrm and libpulse development
libraries. CMake downloads no dependencies and uses the verified LVGL source
selected by `LVGL_SOURCE_DIR`.

Simulator and package tests cannot establish physical display, audio or keyboard
behavior. The attached-TV report records the tested hardware and conditions;
other displays and source profiles need separate qualification. Current evidence
and conversion commands are in the
[player guide](../../docs/video-player.md) and
[validation report](../../docs/video-validation.md).
