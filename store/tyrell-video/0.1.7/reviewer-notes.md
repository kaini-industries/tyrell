# First publication: Tyrell Video 0.1.7

Prepared review text and evidence; not posted anywhere.

Package: `tyrell-video_0.1.7-1_arm64.deb`, 686346 bytes, SHA-256
`7ddf4ead5f9d27e44d92ab510b8cca503847af36762d60b315fea62de4450d79`.
Build: `src-03a5a269b185`, commit `8cba407dcce3675844acc4b203fa78a664044dc3`.

Tyrell Video is a C++17/LVGL/GStreamer local-file player with a keyboard browser,
common text subtitles, multiple audio tracks, native-screen playback and
HDMI video while the handheld remains the controller. Listing images are
rendered UI previews. `recording/Tyrell-Video-0.1.7.mp4` is the supplied authentic
17.1-second phone recording, independently reviewed and ready to attach. It
shows the real TV's moving pattern, readable 0.1.7 captions and the handheld;
recorded 880 Hz pulses occur roughly once per second. It is not a calibrated
latency/frame-rate measurement or a demonstration of every control.

## Recovery and measured limits

0.1.6 passed TV geometry/audio/caption/hotplug checks and a ten-minute run, but
also intermittently hit kmssink's `failed to activate buffer pool` at startup.
That draft is superseded. 0.1.7 retries that exact video-sink error only after
hardware video has produced output, once per file load, using software decoding.
It retains intended position, selected tracks and play/pause state. A second
failure takes the ordinary paused LCD fallback. Missing codecs, corrupt media,
permission errors and unrelated failures do not trigger a broad retry loop.

Controlled process-local KMS fault injection exercised the actual sink error,
with DMA import deliberately declined and one/two distinct pool activations
failed. It synthesizes no bus messages. One failure recovered to software HDMI;
two failures produced exactly one retry then paused LCD at 0:20. Captures before
and after enabling SRT both show 0:20. Five separate starts under temporary
buffer pressure used hardware decoding without entering the retry. These are
different evidence cases; injection is not natural CMA-exhaustion reproduction.

The software-recovered run advanced 615.097 seconds, with captions, using a
1920×816 24 fps H.264 Constrained Baseline/AAC fixture and 1280×720 HDMI output.
There were 14,755 sink render calls and three sink drops; separate max-per-element
QoS drop observation was five. Full-run CPU was 90.6255% of one core, peak RSS
93496 KiB; steady-state sampling covered 613.995 seconds, RSS 65420–76788 KiB,
17 threads and 22–23 descriptors. These counters do not measure physical scanout.
The user confirmed smooth TV motion, matching TV sound/flashes, readable captions
and handheld controls. Other profiles, particularly demanding newer codecs,
can require computer-side conversion; there is no universal smooth-HD promise.

Both connected player-SIGKILL and helper-SIGKILL recovery checks passed. The
initial helper check observed the LCD graph while loading two seconds after
console return and was not accepted; the settled retest verified paused LCD,
no error, desktop restoration and audio cleanup. The user also confirmed normal
APPLaunch playback with TV sound and Q returning to the launcher.

## Service and install policy

The player and audio helper are normal-user processes. The socket-activated
console helper uses the locked `tyrell-video-hdmi` account with only
`CAP_SYS_TTY_CONFIG`, a systemd-provided read-only console descriptor and a
closed device policy. The local `video` group is the authorization boundary.
It accepts a fixed connection-lifetime lease, no caller commands, paths or VT
numbers. There is no root command prefix, setuid bit, SYS_ADMIN, SYS_PTRACE or
DAC-bypass capability. Both helpers install under `/usr/lib/tyrell-video/`.

The desktop continues running while its VT is temporarily inactive. Dedicated
playback does not share HDMI with an active desktop. Recovery retains the
exclusive lock, private write-ahead marker and nonroot ExecStopPost. TV audio
moves only this app's stream, without changing the system default. Software
preference is per file load; global decoder ranks, firmware, boot settings and
CMA remain unchanged. All temporary test buffers, fixtures and injector were
removed after qualification.

All 35 ARM64 CTests, builder install/render/diagnostics/remove checks, current
Store path/root-service/dangerous-hook checks and metadata validation passed.
Final device audit was clean: all 12 protected files/kernel unchanged, original
launcher and desktop processes retained, zero launcher restarts, original tty7
and 1920×1080 desktop mode restored, original audio default unchanged, no owned
audio modules or active test player, and `dpkg -V` clean. Sanitized evidence is
in `qualification/`; exact package/build checks are in `package-checks/` in the
handoff archive. Original project code/artwork are MIT licensed; library notices
are included and media/font libraries remain system dependencies.

## Submission

Attach the supplied recording with these factual notes to the generated PR.
The package/title/proposed TYVD code were unused in the pinned published-pool
snapshot; recheck before submission. Omit the source URL and keep email hidden.
No public upload or Store approval has occurred. The intended uploader chooses
the owning account, and maintainers decide first-app acceptance.
