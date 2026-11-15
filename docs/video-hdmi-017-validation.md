# Tyrell Video 0.1.7 HDMI recovery qualification

This report distinguishes the completed
[0.1.6 TV observations](video-hdmi-016-validation.md) from tests of 0.1.7's new
software-decoder recovery path. Controlled fault checks and the sustained
software-fallback run below passed on the exact package, followed by connected
crash recovery, normal APPLaunch acceptance and the final end-state audit.
These results qualify the tested hardware, H.264 profile and recovery cases;
they do not establish universal codec performance or Store maintainer approval.

## Candidate identity and completed build checks

The installed candidate is `tyrell-video_0.1.7-1_arm64.deb`, application version
0.1.7, source `src-03a5a269b185`, build commit
`8cba407dcce3675844acc4b203fa78a664044dc3`. Its SHA-256 is
`7ddf4ead5f9d27e44d92ab510b8cca503847af36762d60b315fea62de4450d79`;
the package is 686346 bytes. This qualification does not rebuild or alter it.

All **35 ARM64 CTests** passed, followed by isolated-builder package
installation, diagnostics/render and removal checks. The new policy test checks
the exact eligible KMS error, hardware-video delivery requirement, one-attempt
budget and per-playbin software-decoder flag. Unit checks alone do not exercise
a real HDMI sink's pool failure or establish software-fallback performance.

The motion fixture for the candidate checks is generated geometric video,
1920 × 816 at 24 fps, square pixels, H.264 Constrained Baseline level 4.0 with
AAC mono audio. It lasts 720 seconds, with white flashes and quiet 880 Hz beeps.
The fixture is separate from the app package and contains no personal movie
footage. The television and user audio session remain the physical test outputs.

## Why a new candidate is required

On 0.1.6, the user confirmed smooth TV motion, matching TV sound, no desktop
cursor, readable captions, correct geometry and unplug/reconnect recovery.
The paused controller captures also preserved the nonzero 0:53 position across
caption activation. A separate 0.1.6 run completed 603.713 seconds of media
progression. Those results remain valid for that package and those conditions.

An earlier 0.1.6 automated startup nevertheless rendered no HDMI buffers and
reported `failed to activate buffer pool; paused on LCD`. Free CMA was low
around that attempt, but the observation did not establish a root cause or
prove that a later successful run resolved it.

Version 0.1.7 adds one software-video retry per file load when the HDMI video
sink emits that exact KMS pool-activation error after hardware video has
produced output. The retry retains console/audio leases and the intended media
position, selected tracks and play/pause state. A second failure takes the
ordinary paused LCD fallback rather than creating a retry loop. This is a
specific recovery path, not a catch-all retry for corrupt media, missing
codecs, permission failures or unrelated allocation errors. It does not change
firmware, CMA configuration or other applications' decoder preferences.

## Natural-pressure startup observations

Five 0.1.7 starts reached HDMI across three cases with 4 MiB temporarily reserved,
one with 12 MiB and one with 16 MiB (3 + 1 + 1). The input variants included
startup without early keys and early track-menu actions through the test FIFO
or normal input route. All five reached HDMI; none triggered the software retry.

The reservation used normal-user, unshown DRM dumb buffers, released after each
group. It did not alter display modes, DRM mastership, boot settings or CMA
configuration. These successful starts did not reproduce the specific failure,
so they do not establish how often it occurs or validate the recovery branch
by themselves. The controlled tests below supply that separate evidence.

## Completed controlled KMS failure checks

A temporary, process-local qualification interposer made kmssink decline the
DMA-BUF direct-import identification path and failed selected activations of its
own `GstKMSBufferPool`. Guards limited the failure budget to distinct matching
pools and callers in `libgstkms.so`; separate strict-compilation and mock-guard
smoke checks passed. The interposer was not part of the installed app package.

The real kmssink path emitted `ensure_internal_pool: failed to activate buffer
pool`, and the app recorded the matching recovery reason. No bus messages were
synthesized. The exact GError domain/code eligibility guards were unit tested;
they were not independently captured as device bus messages by the interposer.
This is controlled fault injection, not a reproduction or measurement of
natural kernel DMA-import rejection or CMA exhaustion.

| Case | Observed result |
| --- | --- |
| One eligible pool failure | Exactly one software retry selected `avdec_h264`; the recovered HDMI session continued and completed the sustained run below. |
| Position, pause and subtitle preservation | The paused elapsed position was 20 seconds before and after external SRT selection; captured controller images and the selected `* Subs: Subtitles 1` menu support the check. |
| Two eligible pool failures | Two distinct pool failures occurred at 24.641 and 28.459 seconds of the test. Retry count stayed one; three pipeline builds ended on LCD, paused at 20 seconds, with no app error and the expected fallback notice. |
| Audio during recovered playback | The app selected its own temporary HDMI sink while software-decoded video continued; the user confirmed TV sound. The final audit confirmed default-audio preservation and module removal. |

The two-failure case ended with `failed to activate buffer pool; paused on LCD`;
no unbounded retry loop was observed. The injection count, decoder and app
retry telemetry establish branch coverage rather than merely showing normal
HDMI playback. Injected success does not establish the original natural
failure's occurrence rate or cover every possible allocation error.

Sanitized results and the before/after paused captures are in
[`startup-recovery.json`](../store/tyrell-video/0.1.7/qualification/startup-recovery.json)
and its referenced qualification images. The retained interposer source and
library hashes bind the test mechanism to that result.

## Completed ten-minute software-fallback run

The controlled single-failure session selected `avdec_h264`, retained one retry
and two pipeline builds, and stayed on HDMI with external SRT captions. After
the paused setup at 20 seconds, media progressed to 635.097 seconds: **615.097
seconds of advancement** in a 670.166-second app run that includes startup,
fault recovery and the paused control checks. It did not silently return to
hardware decoding. The app's own temporary HDMI audio sink was selected.

| Full-run measurement | Result |
| --- | ---: |
| App elapsed wall time, including setup | 670.166 s |
| Media advancement, 20 to 635.097 seconds | 615.097 s |
| Pipeline builds / software retries | 2 / 1 |
| GStreamer sink render calls | 14,755 |
| Sink-reported drops | 3 |
| Render calls per media second | 23.988 |
| Maximum observed per-element QoS drop statistic | 5 |
| App CPU, one core as 100%, full run | 90.6255% |
| App-reported peak RSS, including startup | 93,496 KiB |

Three sink drops and the separate QoS value of five must not be added together.
The approximately 24 render calls per media second are submission statistics,
not physical TV scanout counts or a zero-drop result. The user independently
confirmed smooth TV motion, matching flash/beep sound, readable captions and
the visible handheld controller during this final 0.1.7 recovered session.

The steady-state resource summary covers 615 included samples spanning 613.995
seconds after pause/caption setup. Three teardown samples were excluded and
retained separately. CPU is based on the platform's 100 clock ticks per second
and expressed on a one-core scale.

| Included steady-state samples | Result |
| --- | --- |
| Sampled CPU, one core as 100% | 96.7434% |
| RSS range | 65,420–76,788 KiB |
| First / last sampled RSS | 76,788 / 66,520 KiB |
| Open descriptors | 22–23 |
| Threads | 17 throughout |
| Free CMA range | 28,976–29,380 KiB |
| Free swap range | 184,484–206,108 KiB |
| Maximum temperature | 59,610 m°C, about 59.6°C |
| HDMI | Connected throughout the included samples |

The sampled RSS ended below its first sample; no sustained growth is indicated
within that interval. Sampling does not establish every instant's memory state,
and the full-run peak includes the initial hardware graph and recovery. Software
fallback consumes substantial CPU even for this tested low-bitrate H.264 profile;
other bitrates, codecs and resolutions can still need conversion.

[`endurance.json`](../store/tyrell-video/0.1.7/qualification/endurance.json) records
the completed result, timing/resource scope and user observation. There was no
app error; the remaining notice, `HDMI video memory is tight; trying software
decoding`, records the exercised recovery. The historical 0.1.6 ten-minute run
is not substituted for this new software-decoded result.

## Completed connected recovery and normal launch

With HDMI connected, killing the player restored tty7, removed the app-owned
audio modules, kept the default audio unchanged and preserved the desktop PID.
The helper-SIGKILL retest also passed: after allowing up to 20 seconds for the
asynchronous transition to settle, LCD playback was paused at 4.43712 seconds
with no app error. The expected notice was `HDMI desktop handoff ended
unexpectedly; paused on LCD`. The desktop console, original audio default and
temporary-module cleanup were restored.

The first helper check observed the LCD graph only two seconds after console
return, while it was still loading. That observation was inconclusive and was
not accepted as a pass. Its raw evidence is retained separately from the
settled retest. The allowed observation window is not a measured guarantee that
every recovery finishes in a particular number of seconds.

The user then launched Tyrell Video normally through APPLaunch, played a movie
with TV sound and used Q to return to the launcher, confirming that the normal
launch/exit path worked. This acceptance is separate from remotely driven
controls and the temporary fault-injection tests.

## Final installed-state audit

The final audit was recorded at `2026-11-15T05:40:45Z` (November 14 in
America/Chicago). It confirmed:

| Check | Final state |
| --- | --- |
| Installed app | 0.1.7, source `src-03a5a269b185` |
| Package verification | `dpkg -V` clean |
| Protected firmware, boot and launcher files | All 12 hashes unchanged |
| Kernel | Unchanged, `6.18.39+rpt-rpi-v8` |
| APPLaunch | PID 1065, zero restarts, freezer state `running` |
| Desktop | labwc PID 1211 unchanged |
| Foreground console | tty7 |
| Restored desktop HDMI mode | 1920 × 1080 at 60 Hz |
| Audio default | Original onboard sink unchanged |
| App-owned temporary audio modules | Removed |
| Active player | None |
| HDMI | Connected |

The [lifecycle record](../store/tyrell-video/0.1.7/qualification/lifecycle.json)
retains the connected crash outcomes, normal-launch acceptance and final audit.
The original package bytes and build identity remain unchanged by these later
qualification records.

## Store evidence boundary

The user supplied a 17.108-second phone recording of the current
0.1.7 run. The full original and prepared derivative were reviewed: the physical
television, handheld controller and readable 0.1.7 test captions are visible,
and the content is the generic fixture rather than a personal movie. Recorded
audio contains 880 Hz pulses roughly one second apart; this is not a calibrated
measurement of audio/video latency.

The prepared [SDR BT.709 H.264/AAC sharing copy](../store/tyrell-video/0.1.7/recording/Tyrell-Video-0.1.7.mp4)
has source metadata removed, with conversion/provenance details in its
[recording manifest](../store/tyrell-video/0.1.7/recording/manifest.json). It is
ready to attach to the eventual first-app PR and has not been publicly uploaded.
The older unreceived 0.1.6 recording is no longer a missing-evidence dependency.

The Store asks for a short real-device demonstration, not a 45-second minimum
or a recording of every control test. The supplied clip can support that review;
it is not automatically proof of the recovery branch, every qualification case
or maintainer approval. Synthetic fixtures and rendered previews retain their
separate labels.

The [0.1.7 Store preparation](../store/tyrell-video/0.1.7/README.md) includes the
completed hardware qualification and reviewed recording, ready to accompany
the eventual first-app PR. A completed package build, an injected recovery
test and an authentic device recording remain distinct evidence items. No
Store upload or maintainer approval has occurred, and source-repository
visibility remains unchanged.
