# Cardputer Zero: live base-system power investigation

Investigated November 15, 2026 with nine fresh agents, source and schematic
review, passive SSH observations, and reversible live comparisons. This follow-up
focuses on the base system; application power optimization remains out of scope.
All native hardware, Wi-Fi and SSH remain requirements.

## Confirmed: unused Ethernet keeps the USB tree active

The strongest new finding is a repeatable source of unnecessary kernel activity.
With Ethernet unplugged, its USB device had `power/control=on`. Changing only
that leaf to `auto` allowed the Ethernet device, internal hub and USB root bus
to suspend. USB-controller interrupts fell from approximately **8,000 per second
to zero**, in both repetitions. Returning the leaf to `on` restored the activity.

| Phase | Ethernet USB policy | USB interrupts/s | Leaf / hub / root at sample end | Aggregate CPU busy |
| --- | --- | ---: | --- | ---: |
| A1 | on | 7,999.55 | active / active / active | 5.47% |
| B1 | auto | 0 | suspended / suspended / suspended | 4.37% |
| A2 | on | 7,999.94 | active / active / active | 5.92% |
| B2 | auto | 0 | suspended / suspended / suspended | 4.60% |
| A3 | on | 8,000.08 | active / active / active | 3.41% |

Each phase had eight seconds to settle and approximately thirty seconds of
measurement. Suspended-time counters increased for the entire measured interval
in both B phases. Ethernet traffic counters remained zero. SSH used Wi-Fi;
the experiment did not change application processes/settings or parent USB
policies. Application workload was uncontrolled. Both parents already
allowed autosuspend. Independent timeout restoration supplemented `finally`.

This establishes a causal policy-to-activity relationship for the tested
topology. The original policy was restored after testing. CPU usage drifted,
and 1 GHz residency stayed approximately 76–77%; **do not claim a CPU reduction**
from these samples. Interrupt occurrences also are not necessarily CPU wakeups.
No wattage or battery-life improvement was measured.

The mechanism is consistent with r8152 stopping its interrupt URB during runtime
suspend and DWC2 masking SOF interrupts when periodic scheduling becomes empty.
These upstream sources explain the behavior; they are not an integrity match
to the installed Raspberry Pi kernel source tree.
[r8152 suspend path](https://github.com/torvalds/linux/blob/v6.18/drivers/net/usb/r8152.c#L8086-L8139),
[DWC2 periodic scheduling](https://github.com/torvalds/linux/blob/v6.18/drivers/usb/dwc2/hcd_queue.c#L1579-L1622),
[USB runtime PM](https://docs.kernel.org/driver-api/usb/power-management.html).

The best next base-image change is a narrowly matched Ethernet autosuspend
policy, qualified before becoming a default. Verify cable insertion, negotiated
link and sustained traffic after idle, repeated resume, USB peripheral hotplug,
and host/gadget mode changes. A no-cable SSH experiment cannot certify those
physical functions. Keep the hub's existing policy and retain Ethernet support.
The leaf exposed no readable `power/wakeup` attribute in this inventory;
automatic cable-triggered wake has not been demonstrated.

## CPU governor comparison

With Ethernet restored to its original policy, a separate comparison found:

| Phase | Governor | Time at 600 MHz | Time at 1 GHz | Aggregate CPU busy |
| --- | --- | ---: | ---: | ---: |
| A1 | ondemand | 19.66% | 76.33% | 6.33% |
| B | schedutil | 54.37% | 44.07% | 8.14% |
| A2 | ondemand | 20.34% | 76.31% | 5.49% |
| C | conservative | 91.88% | 4.08% | 6.84% |
| A3 | ondemand | 17.06% | 80.25% | 5.95% |

Other time was spent at intermediate frequencies. Each measured interval was
approximately thirty seconds after eight seconds to settle. Limits stayed at
600–1,000 MHz. The experiment did not change application processes/settings;
workload was uncontrolled. PCM playback remained running at sample boundaries.
Higher busy percentages at lower clocks do not by themselves mean more work or
more energy. `schedutil` also had more context switches and interrupts in its
single trial. Neither alternative has energy or responsiveness qualification.

The observed ondemand settings were `up_threshold=50`, `sampling_rate=100000`
microseconds and `sampling_down_factor=50`. The retained Raspberry Pi
`raspberrypi-sys-mods` package supplies these settings through tmpfiles and
selects ondemand through udev. A final live check confirmed version `1:20260914`
and matching policy files on the device. They are distribution policy, rather than evidence
of an M5-specific change. The factor permits a nominal five-second next-sample
delay after an above-threshold transition to maximum frequency; it is not a
fixed five-second boost duration. Shared-policy load uses the busiest core,
which can differ greatly from aggregate utilization.
[Retained policy package](https://archive.raspberrypi.com/debian/pool/main/r/raspberrypi-sys-mods/raspberrypi-sys-mods_20260914_arm64.deb),
[governor implementation](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/drivers/cpufreq/cpufreq_ondemand.c),
[CPUFreq behavior](https://www.kernel.org/doc/html/latest/admin-guide/pm/cpufreq.html).

A separate repeated comparison isolated only `sampling_down_factor`, retaining
ondemand, its threshold and frequency limits:

| Phase | sampling_down_factor | Time at 600 MHz | Time at 1 GHz | Aggregate CPU busy |
| --- | ---: | ---: | ---: | ---: |
| A1 | 50 | 19.44% | 78.57% | 5.73% |
| B1 | 1 | 78.59% | 5.96% | 7.48% |
| A2 | 50 | 18.45% | 79.07% | 5.04% |
| B2 | 1 | 80.12% | 6.51% | 7.64% |
| A3 | 50 | 19.49% | 79.02% | 6.27% |

Each phase had eight seconds to settle and approximately twenty seconds of
measurement. Reducing the factor repeatedly cut maximum-frequency residency
from about **79% to 6%**. Intermediate frequencies account for the remaining
time. USB activity stayed near 8,000 interrupts/s, separating this policy effect
from Ethernet autosuspend. The original factor and every other tunable were
restored after testing.

This is a second strong base-policy candidate, with a narrower change than
switching governors. Lower frequency can increase busy time, and more frequent
sampling/transitions have their own cost. These short, uncontrolled-workload
trials do not prove energy savings, equal completed work, smooth interaction,
or underrun-free audio. Qualify responsiveness, sustained compute, audio/video,
camera and timing-sensitive hardware before selecting a persistent policy.
Measure each policy separately and then together; no new default was applied.

## Findings that change the priorities

- **There is a substantial hardware baseline.** M5 specifies 2.5 W whole-device
  idle without test conditions. Raspberry Pi's CM0 datasheet gives 225 mA in
  section 3.3 and 250 mA in Table 8 on its 5 V supply, about 1.1–1.25 W excluding
  external peripherals. Neither is a measurement or floor for our unit, and the
  two references cannot be subtracted to obtain peripheral power.
  [M5 specifications](https://docs.m5stack.com/en/CardputerZero),
  [CM0 datasheet](https://pip-assets.raspberrypi.com/categories/1286-raspberry-pi-compute-module-zero/documents/RP-009251-DS-3-cm0-datasheet.pdf).
- **IMU power management is lower priority than its earlier ranking implied.**
  Bosch lists approximately 1.2–1.7 mW for the BMI270 IC in its stated normal and
  performance conditions. This is not a measured board contribution or upper
  bound, but makes a watt-scale saving from this sensor alone unlikely. The
  pinned driver's missing coordinated idle PM remains a valid cleanup.
  [Bosch datasheet](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bmi270-ds000.pdf).
- **The camera already reaches driver-managed standby.** The physical IMX219
  device was runtime-suspended; its camera regulator reported zero users and
  GPIO40 was low. This is stronger evidence than a suspended PipeWire node,
  though it does not measure leakage or prove every physical rail is off.
- **Wi-Fi power saving and Ethernet EEE are already enabled.** Preserve them;
  enabling them again would not be a new improvement. Ethernet EEE was inactive
  without a link partner, which is distinct from USB runtime suspend.
- **IR is another source of background work.** The receiver generated about
  37 interrupts/s in the passive sample and every Ethernet phase. Ambient interference is plausible,
  unproven. The inspected GPIO IR driver's optional runtime PM changes latency
  constraints, rather than disabling the receiver's IRQs. Disabling protocols
  does not remove those edges; an `auto` setting is not a solution by itself.
  [GPIO IR driver](https://github.com/torvalds/linux/blob/v6.18/drivers/media/rc/gpio-ir-recv.c).
- **Display and amplifier observations need careful interpretation.** Brightness
  zero and cached PWM-disabled state were reported, with no matching PWM/I2C
  errors found in the boot log. The vendor backlight driver ignores apply
  errors and lacks hardware PWM readback, so these do not prove electrical
  shutdown. The amplifier enable GPIO was high alongside active PCM playback;
  that does not establish an idle base-driver leak. No audio client was stopped.
- **UART and storage polling need compatibility work before tuning.** Mini-UART
  is enabled and the measured VPU core clock was 400 MHz; do not assume the
  generic 250 MHz UART example describes this unit. The root SD card uses
  `sdhost-bcm2835`, whose card-detect polling can create work without block I/O.
  Preserve UART, Bluetooth and card detection rather than disabling hardware
  for an unmeasured saving.
  [UART clock requirements](https://www.raspberrypi.com/documentation/computers/configuration.html#mini-uart-and-core-frequency),
  [SD host implementation](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/drivers/mmc/host/bcm2835.c).

## Power telemetry: likely units problem, still not a system meter

The new sample again showed roughly 0.214 W in `power_avg` versus approximately
2.14 W from battery voltage × charging current. Two agents independently
visually verified a previously missed inconsistency in TI's own BQ27220 manual:
section 2.18 says mW, while Figure 8-3, page 78, displays **cW**. Its -25 cW agrees
with 3.694 V × -68 mA, about -0.251 W. Web text extraction obscured this detail.

This strengthens a units/conversion explanation. The figure shows software
output, not the raw register word, so it does not establish a driver correction.
Preserve raw telemetry; validate the conversion electrically or with TI before
changing calibration, multiplying values, or reporting energy.
[TI technical reference](https://www.ti.com/lit/ug/sluubd4a/sluubd4a.pdf).

The exact BSP reads `current_now` directly; the 360-second background polling
interval is not a current cache. Read-only `current_instant` and
`voltage_instant` also read the registers directly and were sampled every two
seconds during comparisons. Charging current stayed near 538–540 mA during
the Ethernet phases. That does **not** show zero system saving: USB can absorb
a load change while the charger maintains battery current.

The official schematic places the gauge across the battery shunt and gives USB
an independent path to system power. The audit found no supported software
input-current meter or way to force battery-only operation while charging USB
remains connected. Main power-hold controls must not be used to simulate
unplugging. Actual watts need an external input meter after charging settles,
or validated battery-side measurements during controlled discharge.
[Official schematic, pages 3–4 and 6](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/1243/C154-CardputerZERO_SCH_V0.6.1_20260702.pdf).

## Evidence and disposition

The installed kernel was `6.18.39+rpt-rpi-v8`; Ethernet used r8152. The physical
camera and IMU drivers were identified rather than inferred from device IDs.
Firmware, launcher, boot configuration and package installation were unchanged.
No service or radio was disabled. Research artifacts are summarized in
[sanitized evidence](../research/cardputer-zero-power-audit.json); local raw
observations and experiment scripts are under ignored `build/power-audit-round3/`.

The final independent SSH inventory verified the original Ethernet policy,
ondemand governor, all six original governor tunables, unchanged 600–1,000 MHz
limits, Wi-Fi power saving on, brightness zero and continued camera standby.
The original Ethernet UP flag was retained. No matching USB/DWC2/r8152,
PWM/I2C or inspected sensor/codec errors were found in the boot log after the
tests; firmware throttle flags were zero. These checks do not replace physical
cable, peripheral and full hardware qualification.

This supplements the [earlier review](lightweight-trixie-review.md) and
[Tyrell Zero image plan](tyrell-zero-implementation-plan.md). It supports
prioritizing the proven idle USB activity over speculative sensor savings,
while keeping installed footprint, kernel activity and electrical energy as
separate outcomes.
