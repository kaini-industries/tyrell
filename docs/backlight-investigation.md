# Backlight investigation before a correction

H1 preparation, November 24, 2026. No Tyrell brightness-zero fault has been
reproduced on the retained Zero baseline. No backlight correction, watcher or
BSP change is justified by the current evidence. This procedure prepares a
separate investigation after F2 identifies the baseline and test conditions;
physical results remain pending.

## What the evidence establishes

The [pinned Gh0st observation](gh0st-adoption-plan.md#pinned-upstream-references)
reports a brightness-zero false-off concern on its hardware/software setup. It
is a hypothesis for Tyrell's exact BSP/controller, not a diagnosis of this unit.

Tyrell's [existing power audit](cardputer-zero-power-audit.md) observed brightness
zero and cached PWM-disabled state without matching PWM/I2C boot-log errors.
The inspected vendor driver ignores apply errors and lacks hardware PWM
readback. Those observations do not establish that the panel emits no light or
that a physical supply is disabled.

A [Video handoff test](video-hdmi-handoff-validation.md) also saw brightness zero
while a harness had paused APPLaunch. Normal launcher handoff restored saved
brightness and visible video. That setup error is distinct from the reported
false-off concern and is not evidence for adding a Tyrell workaround.

## Preserve identity and ownership

Use the [F2 preparation procedure](baseline-observations.md) and select a spare
test target before device work. Create a separate observation plan:

```sh
python3 scripts/observe/records.py prepare --profile zero \
  --purpose h1-backlight --run-id backlight-baseline-01 \
  --output /private/tmp/backlight-baseline-01.plan.json
```

Fill conditions and validate the plan before copying it to the test system.
Record the raw/export image identity, running kernel, BSP commit, actual
overlay selection, board/controller identity available through supported
read-only interfaces, installed app bytes and original M5 launcher identity.
The retained contract selects kernel `6.18.39+rpt-rpi-v8` and BSP
`75518549a2640486f5d13ea72ca4935b198d7c20`; an unknown controller firmware
version stays unknown. The raw hardware ID alone does not select an overlay.

Identify the owner of each transition before changing code: original M5 idle
dim/dark/wake, external-app handoff, Tyrell app action, or another explicit
client. Record which process initiated it and whether input/display ownership
changed. Preserve original M5 executables. Use owner-provided controls manually;
the observation tool does not write brightness, PWM, GPIO, I2C or controller
registers and does not pause the launcher to manufacture a state.

Do not add a global sysfs watcher or repeated corrective writes. Such a process
can fight the launcher, change measurement conditions and conceal the caller
or driver that actually failed.

## Reproduce and observe

Declare brightness settings, ambient light, observation method, settle interval,
repeat count and expected dark/wake behavior before trials. Record supply,
battery state/temperature, audio, network, peripherals and all instrument
connections. Keep unrelated clients and settings fixed.

Exercise separately through normal owner interactions:

| Scenario | Observe and retain |
| --- | --- |
| Known nonzero brightness | Visible output and readable reference settings |
| M5 dim | Visible change, requested/read-back attributes, input ownership |
| M5 dark/brightness zero | Whether visible light remains after settling, without waking the screen through the observation method |
| Wake | Full intended brightness, fresh input and functioning native display |
| Normal app launch/return | Saved brightness restoration and which owner performs it |
| Repeated dark/wake and app return | Reproducibility, intermittent failures and final recovery |

Use the bounded collector only when explicitly started, retaining its full
bundle. Keep time-stamped manual visual observations, photographs or instrument
readings separately, with their own hashes and conditions. Note if the observer,
SSH session or camera lighting could trigger wake or obscure a dim glow.
Compare requested brightness, exposed actual-brightness/PWM attributes and
visible light without treating any one software field as physical proof.

If dark is visibly wrong, first reproduce it with the normal owner and record
whether the request reached the expected interface. Trace the narrow caller,
driver and controller chain from the pinned source/identity. Read-only cached
state cannot prove a successful electrical transition. Do not probe unknown
registers, export GPIOs, change controller firmware or force power rails as part
of this procedure.

## Electrical observations require a separate controlled method

Visible darkness is not a power measurement. A USB-powered or charging sample
cannot establish battery discharge savings; the published supply paths differ.
Follow the [battery validation protocol](tyrell-zero-battery-validation.md) for
independent, controlled discharge or usable-runtime trials. Declare validated
units, instrument uncertainty/burden, equal work, supply isolation and matched
temperatures/settings. Keep software and instrument sampling equivalent.

Preserve raw gauge fields. The unresolved `power_avg` mW/cW documentation
contradiction does not authorize multiplying values by ten, recalibrating the
gauge or claiming system watts from charging current. A cached disabled PWM
state or zero reported brightness also does not prove an unpowered rail.

## Decide whether a correction is warranted

Retain an explicit outcome: reproduced, not reproduced, inconclusive or
not-tested, with exact artifact/controller identities and limitations. A
software-only fixture can validate parsing and record integrity; it cannot
reproduce visible glow or qualify hardware behavior.

Only after reproducing and locating the fault should a separate change correct
a Tyrell-owned dark caller, or qualify a separately versioned BSP fix. Keep its
source/package/BSP identity and before/after observations separate from the
original Z3 control pair. Preserve the original M5 executables and existing
fallback. A driver change requires coherent kernel/headers/modules/overlays,
license/source obligations and application/hardware requalification.

Require correct dim/dark/wake, original M5 launch/return, input ownership,
media/HDMI restoration, abnormal exit and repeated recovery before promoting a
fix. Any energy claim needs its own controlled battery evidence. If the concern
does not reproduce, retain the baseline and report that finite result instead
of adding a speculative correction.
