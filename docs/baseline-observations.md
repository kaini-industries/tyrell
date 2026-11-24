# Baseline observations and physical acceptance

F2 preparation, November 24, 2026. The observation tools prepare plans, collect
bounded read-only Linux observations and compare matched runs. Their output is
not a Z4 hardware acceptance result, a boot-disk attestation or a battery claim.
They are opt-in: no service, timer, device access or tuning is enabled by merging
this implementation.

The original comparison pair remains the exact Z3 Zero baseline and full-source
control in the [image validation record](tyrell-zero-image-validation.md) and
[machine-readable evidence](../research/tyrell-zero-image-validation.json).
Both contain the retained Status `0.1.0-1`, SHA-256 `15471a8f…`; current app
development binaries cannot replace that input in the base-system experiment.
Z4 physical acceptance and Z5 settled-memory observations remain pending.

## Prepare a run without contacting a device

From the repository root:

```sh
python3 scripts/observe/records.py prepare --profile zero \
  --run-id zero-idle-01 --output /private/tmp/zero-idle-01.plan.json
python3 scripts/observe/records.py prepare --profile control \
  --run-id control-idle-01 --output /private/tmp/control-idle-01.plan.json
```

Fill the generated conditions before collection: device and battery identity,
supply, workload, display, audio, network, peripherals, effective policy and OS
settings. Record all connections and which device supplies power. Specify the
intended process IDs, settle period, sample interval and duration. A PID is
boot-specific; select it again for the next run. Keep logging cadence and process
selection equivalent across the pair. Collection is capped at 30 minutes after
at most 10 minutes of settling; it does not cover longer maintenance events.
Intervals are 1–60 seconds, with at most 181 samples and 16 selected process IDs.
Choose interval/duration together to remain inside those bounds.

Keep the plan's image/app/kernel/BSP identities tied to the retained evidence.
The raw image hash describes the selected file export. It does not prove that
the running filesystem is an unchanged copy of that image, identify a physical
card or replace write verification and boot observations. Do not hash an active
root filesystem and present it as the original raw-image digest.

```sh
python3 scripts/observe/records.py validate \
  --plan /private/tmp/zero-idle-01.plan.json
```

Plan validation checks structure and identity consistency. It cannot verify that
the declared setup exists or that a human observed a functioning peripheral.
Keep private provisioned backups, credentials, Wi-Fi secrets and private keys
outside committed run plans and distributable evidence.

## Collect only on the selected test system

After selecting a spare-card target and preparing the matched conditions, copy
the tools and filled plan to that Linux system. Preserve the repository-relative
layout of `scripts/observe/{collect,records}.py`,
`research/tyrell-zero-image-validation.json` and
`image/contracts/tyrell-zero-hardware.json`; plan validation reads those exact
reference files. This does not require copying build trees or private settings.
Use Python 3.11 or newer. Start collection explicitly:

```sh
python3 scripts/observe/collect.py --plan zero-idle-01.plan.json \
  --output zero-idle-01
```

Use a new output directory for each attempt. The collector uses fixed read
allowlists and retains raw observations. It does not install packages, tune
governors or autosuspend, change brightness, start/stop services, mount disks,
run input tests or perform network probes. Missing, inaccessible or unsupported
sources remain missing evidence. Run with the intended normal-user permissions;
do not elevate merely to turn unavailable PSS or counters into a complete row.

The bundle contains `plan.json`, `samples.jsonl` and `result.json`. Readings carry
explicit status; a missing, denied, unsafe, oversized, timed-out or failed read
is not a zero. Sources cover bounded procfs memory/pressure/CPU/interrupt/disk
counters, selected-process stat/PSS, and available sysfs CPU controls, thermal,
backlight, battery/raw gauge, USB power, network and SPI statistics. Each sample
has a bounded collection deadline; a missed deadline remains failed evidence.

Retain the complete bundle and its plan. Validate it after copying it off:

```sh
python3 scripts/observe/records.py validate --bundle zero-idle-01
```

Bundle integrity and sample completeness are separate. An intact interrupted
run is still incomplete; missing required sources or identity mismatches cannot
be repaired by editing the saved observations. Preserve the failed attempt and
use a new run ID and directory after correcting the setup.

The collector itself consumes CPU, reads procfs/sysfs and writes its output.
Record its timing and overhead, output location, cadence and remote-session
activity. Use equivalent instrumentation for both runs. Frequent SSH traffic,
storage writes and peripheral queries can change the state under observation.
Keep instrumentation outside battery energy windows where practical and use the
separate [battery protocol](tyrell-zero-battery-validation.md) for energy work.

## Z4 acceptance remains an observed hardware exercise

Before any write, prepare a concrete recovery record containing:

1. The original card's identity, safe storage location, offline whole-card backup
   size/hash and recovery evidence. Keep that card unchanged and available.
2. The intended **spare** card and reader, their capacity and current identifiers,
   and confirmation that the selected target contains no needed data. Recheck
   identification after reconnects; a remembered disk number is insufficient.
3. The exact chosen image export, raw/compressed hashes, source/input manifests
   and independent verification results from the retained Z3 evidence.
4. The write/readback verification procedure and clean shutdown/power-off/card
   swap sequence, using the [recovery guide](hardware-and-recovery.md).

No disk is selected by these tools and no write command is supplied. The
earlier file-image validation request did not identify or authorize a target
card. Complete this target-specific procedure before a separately authorized
image write; retain the original firmware and controller firmware.

On the exact spare-card image, record operator, time, artifact identity,
scenario, expected outcome, actual outcome and supporting evidence for:

- First boot/provisioning, its deliberate reboot, subsequent cold boot,
  expansion, repeated reboot, clean shutdown and recovery.
- PAM/session authentication, active seat/VT, normal-user device permissions,
  original M5 APPLaunch/settings and return from each tested native app.
- Guarded Store install/remove/repair/rollback with an explicitly approved test
  artifact; verify preserved vendor UI and protected-base identity afterward.
- All 15 categories in the [hardware contract](../image/contracts/tyrell-zero-hardware.json):
  boot/SD, LCD/backlight, keyboard, power/gauge, audio, HDMI, camera, IMU/compass,
  RTC, IR, Wi-Fi/SSH, Bluetooth, Ethernet, USB modes and expansion.
- Return to known idle after media, HDMI, camera, USB and network use, including
  abnormal-exit and restoration scenarios where required by the plan.

Use safe identified accessories and preserve connectivity needed for recovery.
An absent accessory, unobserved visual result or unavailable instrument is
`not-tested`. Software counters and successful file checks cannot fill those
rows. Keep manual evidence separate from the collector's machine observations.

## Z5: compare a matched pair

Run the unchanged full-source control and Zero baseline on the same device with
the same retained app bytes, settings, battery/supply, audio, display, networking,
peripherals, effective governor/USB policy and instrumentation. Record startup,
settle and sampling windows, maintenance state and any interventions. Separate
one-time provisioning from normal configured boot. Unmatched clients/settings
make a whole-product comparison, not a base-only result.

```sh
python3 scripts/observe/records.py compare --zero zero-idle-01 \
  --control control-idle-01 --output baseline-comparison.json
```

The comparison checks declared conditions, identities and collected effective
controls before describing samples. Missing readbacks block target interpretation.
CPU controls must retain ondemand, 600–1,000 MHz and all six original tunables;
the Ethernet leaf must retain `on` and 2,000 ms, with parents on `auto`. USB
VID/PID, driver and canonical ancestry are collected without choosing a bus
number. Review that ancestry against the integrated board topology; an external
lookalike is not automatically attested by these software fields. Effective
Wi-Fi power saving, EEE, GPU/CMA/swap and complete activation/timer inventories
still require independent evidence; the collector does not invoke their tools.
The comparator reports the plan's initial **32 MiB
additional settled `MemAvailable`** engineering target; it does not accept Z5,
certify steady state or approve a release. Inspect raw PSS, pressure, swap/I/O,
CPU and collection overhead alongside it. Repeated samples in one run are not
independent trials. The separate 1 GiB rootfs-footprint target uses exported
image measurements, not live memory counters.

Preserve failed/mismatched/incomplete runs and explain rejected comparisons.
Counter deltas describe their exposed interval; interrupts are not a direct
process-wakeup count, SPI transfers are not optical frames, and gauge current
or `power_avg` alone is not system energy. New app or backlight/power
interventions require separate before/after identities and cannot replace the
original base-system control.

For the next investigation, see [H1 backlight procedure](backlight-investigation.md).

## Automated validation

Run `python3 -m unittest discover -s scripts/observe/tests -v` for the 30
fixture checks. They cover read/output/time bounds, missing/malformed sources,
sysfs alias changes, PID reuse, cancellation, partial bundles and comparison
rejections. Their fake roots and clocks are explicit test inputs, unavailable
as live CLI options. The required Python CI group runs these checks; no fixture
result constitutes a physical backlight reproduction or baseline acceptance.
