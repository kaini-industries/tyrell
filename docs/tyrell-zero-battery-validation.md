# Tyrell Zero battery validation protocol

Revision 3 planning contract, November 15, 2026. Five fresh agents reviewed the
battery strategy. These are future acceptance requirements, not completed
measurements. Preserve all native hardware, Wi-Fi/SSH, the original M5 launcher
and unchanged application packages/settings.

## Evidence needed to choose a battery preset

USB input measurements can screen candidates after charging settles. They
cannot alone select a battery-saving default: the published schematic provides
different USB and battery supply paths. Require either validated battery-side
discharge energy or repeated controlled usable-runtime improvement. For runtime
tests, hold completed work, availability and usable endpoints equivalent.
[Power-path evidence](cardputer-zero-power-audit.md#power-telemetry-likely-units-problem-still-not-a-system-meter).

Compare the four initial Zero presets independently. If the bounded follow-up
CPU-factor or Ethernet-delay trials become necessary, capture each selected
parameter and configuration identity. Compare the final image against both
Zero baseline and the matched full-source image on battery. The original stock
image remains the compatibility/recovery reference.

Keep the claim references distinct: candidate versus Zero baseline establishes
an additional policy effect; selected Zero versus matched full-source establishes
the whole-image effect. Isolated hardware/performance passes permit testing the
planned combination even when standalone energy is inconclusive. The combined
state still needs its own battery proof and cannot hide functional failures.

## Declare the experiment before seeing results

Record a versioned trial specification containing:

| Area | Required record |
| --- | --- |
| Software | Image/preset/contract hashes; common package versions; native clients/settings/audio state; enabled services and maintenance state |
| Battery | Same battery identity/health/cycle information where available; starting charge procedure and declared SoC/voltage window; ambient and battery temperatures |
| Supply | All charging, Ethernet, USB, HDMI and accessory connections; which device powers each peripheral; meter/logger connections and burden |
| Display/network | Fixed recorded nonzero brightness for screen-on, fixed screen-off state, AP/network conditions, effective Wi-Fi power saving, reconnect procedure |
| Work | Defined tasks, output validation/completion counts, latency/throughput budgets, work-plus-return-to-idle cycle duration |
| Endpoints | Equal elapsed windows for idle energy; equal work and common cycle duration for burst energy; unchanged useful-start and safe shutdown thresholds for runtime |
| Instrumentation | Validated units/calibration/uncertainty or runtime timing method, cadence, monotonic timestamps, buffered logging and rejected-run rules |
| Analysis | At least three independent paired trials initially, counterbalanced order, declared usage mix/weights, uncertainty method and material-regression limits |

Battery percentage endpoints alone are not an energy comparison. Lower energy
over a shorter or less useful discharge does not prove improvement. Preserve
calibration, low-battery thresholds and power-fail safeguards. TI's `power_avg`
unit contradiction remains unresolved; do not silently multiply values by ten.
Validated voltage/current integration or controlled runtime are separate routes
from that field. Preserve raw samples and instrument metadata.

Use brief counter/profiling observations outside energy windows where possible.
Keep logging cadence identical and buffered. Frequent SSH, I2C or `ethtool`
queries can change the measured states. A battery trial must have no unintended
charging/back-power, and instrumentation must not keep the USB tree active.
Record and account for unavoidable observer burden.

## Required operating conditions

| Condition | What it tests |
| --- | --- |
| Connected awake idle, screen on | Typical availability at a fixed representative brightness |
| Connected awake idle, screen off | Base draw with the same clients/audio/network state and verified display state |
| Intermittent SSH/network and equal-work bursts | Energy per useful cycle, responsiveness and return to idle |
| Legitimate wired Ethernet and USB use | Cable insertion, traffic, accessories/modes and resume churn; zero USB IRQs is not expected here |
| Camera/media/HDMI use followed by release | Functional use, permissions, reopening and physical-device idle return |
| Cold boot and normal shutdown | Energy/time to usable native UI and SSH; policy apply/reapply overhead; orderly shutdown behavior |
| Longer representative session | Actual maintenance, boot catch-up, memory pressure and swap/writeback events encountered in use |
| Software halt versus physical off | Actual storage/off-state behavior, separately from awake operation and charging |

Report one-time provisioning/OOBE, including its deliberate reboot, separately
from ordinary configured cold boots. Compare equally configured systems for
normal boot/session accounting.

Keep applications unchanged; these are base-policy workload tests. Publish each
condition's result. Define any usage weights in advance. A weighted idle win
must not hide a material energy or performance regression in a required case.

Long-session tests must include or explicitly account for scheduled events.
The retained zram writeback is scheduled after 180 minutes, then every 24 hours;
APT/man-db persistent calendar work can catch up after boot. If battery runtime
ends before an event, report that exposure limit. A separate plugged-in soak or
forced-job functional test must be labeled with its actual scope; it cannot be
presented as battery energy for an unobserved event. Do not change clocks or
disable maintenance to manufacture a clean representative result.

## Preserve return to idle and maintenance

After legitimate consumers release resources, compare physical camera/CSI,
V3D, HDMI and USB residency against the known idle baseline, using recorded
autosuspend deadlines and bounded margins. An active required client is not a
driver leak; a software PM state is not a physical rail measurement. Record
unexpected lingering activity for targeted investigation before adopting a
preset. Do not force hardware off or change latency constraints underneath use.

Preserve effective Wi-Fi power saving across provisioning, reboot, reconnect,
roaming, lost-AP recovery and NetworkManager restart, respecting explicit user
choices. Verify the exact retained version and effective state; a profile's
`default` value alone is insufficient.

Keep the initial APT/security, swap and durability behavior. Any proposed
nonurgent maintenance deferral must have a measured reason, a per-job finite
deadline/retry/manual/freshness contract, and tests for skipped/failed work,
network loss, locks, reboot and deadline fallback. Unknown external-power
detection cannot justify automatic security deferral. Track successful job
completion rather than timer triggers.

## Promotion and reproducible release

Require a benefit while battery-powered beyond instrument/run uncertainty,
passing per-condition performance and material-regression limits, and the full
native hardware matrix. Inconclusive results remain unproven. Extend trials only
to resolve a specific uncertainty; correlated samples within one run are not
independent repetitions. Choose the combined or single qualified policy, rather
than assuming the individual effects add.

Freeze the selected parameters and policy machinery into a new immutable image
job. Verify boot, effective settings, idle return, supported maintenance,
transactional invalidation/rollback and the exported artifact. If no policy
passes, retain the lighter baseline with no additional-policy claim. If that
baseline demonstrates a battery benefit over the matched full-source image,
it can fulfill the whole-image objective. Otherwise the battery-performance
objective remains open. Publish no battery-saving or runtime claim from USB readings,
IRQ counts or CPU-frequency residency.

See the [implementation plan](tyrell-zero-implementation-plan.md) and
[review evidence](../research/tyrell-zero-battery-review.json).
