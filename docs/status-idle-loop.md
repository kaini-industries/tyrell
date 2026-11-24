# Event-driven Status idle loop

A1 changes the native Status 0.1.2 loop to wait for keyboard input, shell-link
messages, shutdown, or a real telemetry/LVGL deadline. It introduces no daemon
and does not change Video. Shell integration remains a separate default-off
option, with the [L1 ownership and link-loss policy](shell-link-status.md).

The native wait uses nonblocking evdev, the optional shell socket, a shutdown
self-pipe and a monotonic timeout. The signal handler sets the shutdown flag
and wakes the pipe, closing the race between checking the flag and blocking.
Shell output readiness is requested only while output is pending. Bounded
input and message batches preserve responsiveness; remaining work gets another
immediate opportunity rather than an unconditional sleep.

Foreground telemetry still refreshes once per second, with manual refresh
handled promptly. LVGL's returned delay supplies the next UI deadline. The
pinned LVGL version pauses its display timer when no redraw is pending and
resumes it on invalidation, so its existing rendering behavior can be retained.
While hidden, Status waits on the shell and shutdown alone: no keyboard reads,
telemetry refreshes or LVGL timers run. Resume resets input and redraws fully.
Peer loss never becomes a foreground grant.

Headless screenshots retain their finite frame loop. The SDL simulator retains
its desktop timer path. Native M5 operation needs no shell service. Synchronous
device/telemetry operations and OS scheduling can still delay execution; this
does not establish a hard 100 ms lifecycle guarantee.

## Comparison declared before trials

The frozen baseline is merged PR #8,
`b5481666da138c4b477ef70e52cca447cf96cfb7`. Its source archive SHA-256 is
`9d0bf4d5ff0fb86bbfad385ec3aa93d1db46464db8a275eb4e4e0d01dbdbb22f`.
Both versions use the same harness, Debian 13 ARM64 worker, compiler, pinned
LVGL, fixture telemetry and memory display. The harness executes each version's
production GUI loop with fake input/shell devices; it does not open device nodes.

Three paired trials alternate baseline/candidate, candidate/baseline,
baseline/candidate. Each measures three seconds of foreground idle and two
seconds hidden, plus 20 samples per input/manual-refresh/pause/resume/shutdown
action. The gates were chosen before measurements:

| Metric | Required result |
| --- | --- |
| Foreground idle wait entries | At least 80% fewer than baseline |
| Hidden idle wait entries | At least 95% fewer than baseline |
| Median foreground process CPU | Increase no greater than 5 ms per three-second trial |
| Action latency p95 | At most 100 ms, and increase no greater than 20 ms from baseline |

Wait-entry counts describe application loop work, not system-wide interrupts.
Short process CPU trials are noisy, so CPU time is reported separately from
the reduction in periodic work. Memory-display latency excludes physical
keyboard scan, framebuffer transfer, shell scheduling and battery behavior.
The hidden trial keeps the app scheduled to expose cooperative-loop work. The
reviewed Gh0st often uses `SIGSTOP` for hidden apps; both stopped versions would
already have no process wakeups. This hidden fixture does not establish a
physical power or background scheduling gain. Passing the comparison cannot
replace device acceptance on exact new binaries.

## Recorded software result

All declared gates passed in the [three-pair comparison](../research/status-idle-comparison.json).
Foreground process CPU over each three-second window had medians of 25.30 ms
for PR #8 and 1.43 ms for A1. These are worker CPU-time measurements, not device
CPU or power estimates.

| Action | PR #8 p95 | A1 p95 |
| --- | --- | --- |
| Keyboard to GUI flush | 43.34 ms | 34.33 ms |
| Manual refresh to GUI flush | 42.14 ms | 33.83 ms |
| Pause acknowledgment | 14.96 ms | 2.36 ms |
| Resume to GUI flush | 13.98 ms | 2.80 ms |
| Hidden shutdown | 13.92 ms | 0.50 ms |

Each action contains 60 samples per version, 20 from each paired trial. Raw
samples, wait counts, process/GUI CPU, declared gates, source hashes and worker
identity are retained in the comparison record; the
[worker package inventory](../research/status-idle-worker-packages.tsv) identifies
the recorded toolchain. No physical device was accessed.

Median application wait entries were 245 versus 2 during foreground idle
(three seconds), and 163 versus 0 during cooperative hidden idle (two seconds).
Wake reductions passed separately in every paired trial. CPU uses the median
across trials; latency uses nearest-rank p95 across all 60 samples per action.

## Checks and scope

Status CI requires native event-wait and production GUI-loop tests in both
default and shell-enabled configurations, alongside the existing model, input,
CLI, renderer and L1 lifecycle checks. Tests exercise real pipe/socket polling,
queued events, pending output, shutdown, deadlines and idle behavior. The manual
matched benchmark can build a second target against an explicitly supplied,
frozen PR #8 `main.cpp`; normal builds do not fetch or execute a baseline.

For a manual comparison, configure the enabled build with
`-DTYRELL_STATUS_BENCHMARK_BASELINE=/path/to/frozen/pr8/src/main.cpp`, then build
`status-idle-baseline` and `status-idle-benchmark`. Run each with
`--idle-ms 3000 --hidden-ms 2000 --samples 20 --report OUTPUT.json` in the paired
order above. Use the same fixture harness and verified shared inputs for both.

Physical M5, pinned-Gh0st lifecycle and battery comparisons remain pending.
Retained packages, Store approvals and Zero image payloads keep their original
bytes and receipts; the new source version requires separate acceptance.
