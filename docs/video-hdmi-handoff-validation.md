# Tyrell Video desktop HDMI handoff

November 13, 2026. Version 0.1.2 fixes the stock Raspbian desktop retaining HDMI
when Tyrell Video tries to play a movie. Version 0.1.1 required manually releasing
the desktop during its [TV qualification](video-hdmi-validation.md); that result
did not establish a usable automatic desktop handoff.

Version 0.1.3 adds automatic TV audio and native-buffer HD playback; see the
[audio/video follow-up](video-av-validation.md). The results below retain the
0.1.2 qualification conditions.

## Change

The normal user player opens a restricted, socket-activated console lease.
The helper remembers the active virtual console, reserves an unused console,
and switches to it. On the tested LightDM/labwc installation, logind then releases
the desktop's DRM master. The player waits asynchronously on LCD until the helper
confirms the handoff and DRM becomes available. It then switches the existing
movie to HDMI, preserving position, pause state and selected tracks.

The desktop stays logged in. No desktop services, boot configuration, firmware
or system-wide audio defaults are changed. The original console returns after
LCD selection, browser return, normal exit, or a player crash. A dead helper
forces paused LCD recovery. Connecting HDMI while playing remains an explicit
O action; unplugging active HDMI pauses on LCD.

The helper accepts only the installed root-owned player executable and local
video-group credentials. It uses fixed kernel operations and one exclusive lease,
with no caller-provided commands or paths. A root-owned write-ahead record and
systemd ExecStopPost recovery cover abnormal helper termination. Pre-acquisition
failure rolls back the console without waiting for an unrelated DRM owner.
A bounded recovery wait cannot indefinitely strand the user on a blank console.

The app remains unprivileged. Its pending handshake has an eight-second limit
and a 512-byte response limit; LCD playback and keys remain available during
setup. Diagnostics, software previews, the SDL simulator and the playback probe
do not request a privileged console lease. The browser alone does not take HDMI.

## Fresh reviews and automated checks

Three fresh agents separately implemented the console helper, integrated player
state handling, and reviewed the result adversarially. Review caught and fixed
failed-acquisition rollback that could strand a blank console and a forced LCD
fallback that could otherwise retry HDMI after losing its lease.

The ARM64 build passed 21 CTests, including real GStreamer playback controls,
track changes, deterministic UI renders, a nonblocking socket protocol test, and
11 helper lifecycle/security cases. All 26 host tool/builder tests passed.
Both Video and the unchanged default Status packages passed fresh builder-only
install, JSON/render smoke, and removal checks. Status passed all four CTests.

## Device acceptance

Tested the installed `/usr/bin/tyrell-video`, version 0.1.2,
source `src-f4c0b3983ccd`, with HDMI connected and the normal desktop running.
The ARM64 package SHA-256 is
`1f7031f31cd00ee85dbbd8a88fec09693984624d0954063fb290bf7b94b62d6f`.

Before playback the foreground console was tty7. The lease used unused tty1,
and every completed test returned to tty7. The desktop compositor retained PID
1210 throughout; APPLaunch retained PID 1064 with zero restarts. The automated
harness paused APPLaunch while drawing the test UI and resumed it in cleanup;
it did not replace or restart the launcher.

Completed checks:

- Automatic HDMI playback with LightDM/labwc still running: 350 sink buffers
  over 14.56 seconds of media, no reported QoS drops or playback errors.
- O from HDMI to LCD and back, then Esc to the browser: original console restored.
- SIGKILL of the player: systemd lease cleanup restored the desktop.
- A second player was rejected without disturbing the first lease.
- SIGKILL of the helper: player paused on LCD at 3.78 seconds and original
  desktop console returned, with no playback error.

An initial visual run used a remote harness while APPLaunch was already asleep.
Its paused launcher left panel brightness at zero despite a correct framebuffer
image. That was a test-setup error: the normal APPLaunch external-app handoff
restores the saved backlight. Repeating launch through the unmodified launcher
restored brightness to 100%, with the synthetic movie visible on the LCD. No
backlight or console-mode workaround was added to the app. The user then
confirmed both the TV movie and handheld controller were visible together
after reconnecting HDMI and selecting O through the normal launcher session.
The user also confirmed unplugging HDMI moved the movie to the handheld and
paused it. The panel remained at brightness 100% and the desktop console returned.
Reconnecting HDMI, selecting O and resuming with Space returned playback to the
TV; the user confirmed that cycle worked.

These short tests exercise the new ownership path. They do not replace the
previous release's ten-minute codec/scaling qualification. The native hardware
scaling path is unchanged. The user-visible native-launch check is recorded above. A sink-buffer report
alone is not a visual observation.

TV sound remains a separate audio-session condition. The stock image exposes
only its onboard PulseAudio-compatible sink, so the tests selected onboard
sound with an explicit HDMI-audio-unavailable notice. This patch does not add
a persistent HDMI audio configuration or change the system default.

Raw local evidence is retained in ignored `device-results/` and the build
artifacts. Generated test media and temporary acceptance tools are not package
contents.

## References

- [Linux virtual-console interface](https://github.com/torvalds/linux/blob/master/include/uapi/linux/vt.h)
- [systemd socket activation](https://www.freedesktop.org/software/systemd/man/latest/systemd.socket.html)
