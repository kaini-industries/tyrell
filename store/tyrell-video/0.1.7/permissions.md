# Permission and runtime declarations

These are the exact seven flags for the 0.1.7 candidate. They describe app
behavior and requirements, not a permission sandbox.

| Key | Declared | Reason |
| --- | --- | --- |
| `camera` | false | No camera capture. |
| `microphone` | false | Audio output only; no microphone recording. |
| `imu` | false | No inertial-sensor access. |
| `network` | false | Local files, no streaming, downloads or telemetry; the normal audio connection is the local user session. Installation can fetch Debian dependencies. |
| `additional_hardware` | false | Native-screen playback requires no accessory. Optional HDMI has its own declaration below. This follows the Store's separate external-display flag. |
| `background_service` | true | A socket-activated system console-lease helper provides HDMI handoff and crash recovery; a user-session audio helper can manage temporary TV audio. |
| `external_display` | true | Supports dedicated HDMI video with controls on the handheld. |

The player and audio helper remain normal-user processes. The dedicated locked
nonlogin console user receives only `CAP_SYS_TTY_CONFIG` and a read-only console
descriptor supplied by systemd. Clients in the local `video` group receive one
fixed connection-lifetime lease, not a command interface. The 0.1.7 software
retry changes this app's decode graph, not system decoder ranks or permissions.
See `reviewer-notes.md` for the recovery scope, completed hardware qualification and its measurement limits.
