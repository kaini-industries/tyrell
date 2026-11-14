# Permission and runtime declarations

These are the exact seven Store flags for the 0.1.6 package. They describe app
behavior and requirements; they do not configure a permission sandbox.

| Key | Declared | Reason |
| --- | --- | --- |
| `camera` | false | No camera capture. |
| `microphone` | false | Audio output only; no microphone recording. |
| `imu` | false | No inertial-sensor access. |
| `network` | false | Local-file playback; no streaming, downloads or telemetry. PulseAudio uses the local user session. Installation can fetch normal Debian dependencies. |
| `additional_hardware` | false | Native-screen playback needs no external accessory. Optional HDMI is declared separately below. This interpretation follows the Store's separate external-display flag. |
| `background_service` | true | Installs a socket-activated system HDMI console-lease helper, including recovery after the player exits or crashes. The socket listens while installed; helper instances run on demand. A user-session audio helper manages a temporary HDMI sink when required. |
| `external_display` | true | Supports a dedicated HDMI display, with controls on the handheld. |

The main app and audio helper run as the normal user. The system console helper
runs as a locked, dedicated nonlogin user, with only `CAP_SYS_TTY_CONFIG` and a
systemd-supplied read-only console descriptor. The local `video` group is the
authorization boundary. No caller-supplied commands, paths or VT numbers are
accepted. Neither the firmware, boot configuration, desktop services nor system
audio defaults are changed. See `reviewer-notes.md` for recovery evidence and
the pending physical HDMI test.
