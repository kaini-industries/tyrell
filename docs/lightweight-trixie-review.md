# Base-system review and additional power options

Reviewed November 15, 2026 using ten fresh independent agents, exact retained
packages/driver sources, official upstream sources, and passive SSH readings.
This follow-up narrows the [original research](lightweight-trixie-research.md)
to the user's current scope: **base-system usage only**. Native application
rendering, sound-engine changes, game traffic and game saves are outside this
recommendation.

The subsequent [Tyrell Zero implementation plan](tyrell-zero-implementation-plan.md)
selects a concrete native-session and M5 integration design for this work.

The later [live power audit](cardputer-zero-power-audit.md) tests the Ethernet
policy directly: allowing the unused leaf to autosuspend also suspends its hub
and root bus, eliminating approximately 8,000 USB interrupts per second in two
repetitions. It also revises the relative priority of IMU tuning. Electrical
savings remain unmeasured.

All native hardware, native-app compatibility, Wi-Fi and SSH remain required.
No software, settings, radio state, calibration, brightness or firmware was
changed on the device. No new image was built. SSH authentication may generate
normal audit logs.

## Verification result: retain the profile, fix its first-boot contract

A separate source-built stage0–2 `lean-native` profile remains a credible way
to remove the desktop payload while retaining the vendor hardware layer.
Keep the current kernel/BSP, full firmware/module trees, audio/media stack,
camera, IMU, Bluetooth, Ethernet, USB modes, RTC, IR and expansion support.
Keep the stock-derived image and its strict preservation verifier unchanged.

Fresh verification found a **real first-boot blocker**, beyond the original
`graphical.target` concern:

1. The exact APPLaunch 0.6.105 release source calls
   `configure_desktop_startup()` as a mandatory wizard step.
2. It invokes `raspi-config nonint do_boot_behaviour B4`.
3. The retained source-build `raspi-config-core 20261026+2` requires
   `/etc/init.d/lightdm` for B3/B4 and fails when it is absent.
4. This happens before the wizard enables APPLaunch for the configured user.

Therefore, removing LightDM while keeping that wizard unchanged can block
normal provisioning. Supply a profile-aware native setup/session path, or a
separately verified provisioning route. Do not silently ignore the failure or
retain a desktop merely to satisfy it. A native path must establish the user
manager/runtime directory, D-Bus/audio, device groups and permissions, SSH
configuration, and removable-media authorization. Linger alone does not create
an active local session, and blind console autologin can leave an interactive
shell competing with the application keyboard.
[Exact wizard function](https://github.com/CardputerZero/launcher/blob/e7f60859fcc8bd867f3b8c4b3acd45dfa82ca39f/projects/LaunchWizard/main/ui/wizard_service.cpp#L749-L787),
[mandatory step](https://github.com/CardputerZero/launcher/blob/e7f60859fcc8bd867f3b8c4b3acd45dfa82ca39f/projects/LaunchWizard/main/ui/wizard_service.cpp#L1036-L1040),
[retained raspi-config package](https://archive.raspberrypi.com/debian/pool/main/r/raspi-config/raspi-config-core_20261026+2_all.deb).

The package's declared dependencies alone are not a complete runtime contract:
APPLaunch declares only `xdg-user-dirs`, while its executables and dynamically
loaded backends require additional libraries and commands. Audit those alongside
every selected native package. Keep the generic HDMI VT helper and qualify
desktop-free operation before assuming it needs a rewrite.

## Fresh base-system observations

A fifteen-second sample at about 18:19 America/Chicago, after 850 seconds
uptime, and subsequent read-only checks established:

| Observation | What it supports |
| --- | --- |
| 5.64% aggregate CPU, with application/client-driven audio activity present | A warm whole-product sample; not a measurement of pure OS overhead |
| Effective Wi-Fi power saving is **on**; NetworkManager profile says default | Preserve the effective policy; enabling it again is not a new saving |
| Ethernet is `r8152 v1.12.13`, link down, USB autosuspend supported, device forced `on` | A concrete per-device idle-PM experiment |
| Ethernet EEE is enabled but inactive without a link partner | Do not claim that enabling EEE is a new improvement |
| BMI270 reports 100/200 Hz, IIO buffers off; class-device runtime PM says unsupported | Investigate driver/physical-parent PM; buffer inactivity does not prove sensor low power |
| Swap-in one page, swap-out zero, no allocation stalls; about 16 KiB SD writes | No observed swap thrashing or heavy storage activity in this interval |
| zram uses zstd, no recorded backing I/O, generated writeback timer is 180 minutes then 24 hours | Keep the initial swap policy rather than treating it as continuous writeback |
| Battery charging at 18%, with unresolved average-power inconsistency | No base-system energy or runtime saving can be claimed |

The current launcher executable hash matches the locked 0.6.105 release binary,
whose embedded revision is `e7f6085`. This binds the first-boot source analysis
to the actual version, rather than assuming a cached checkout is equivalent.
The later cached checkout is 37 commits ahead. It is not the source of the
installed binary.

## Ranked base-system changes

### 1. Remove unneeded OS services with an explicit native-session profile

Remove stage3/4 desktop metapackages, display manager, desktop shell and unused
desktop helper clients. Audit optional PackageKit/GUI update clients, CUPS,
Pi Connect and network-filesystem services. The live census included
`nfs-blkmap` and `rpcbind`; these are not required for Ethernet or Wi-Fi hardware,
but should remain if NFS is a product requirement.

Keep NetworkManager and its Wi-Fi backend, OpenSSH, BlueZ, Avahi where `.local`
discovery is expected, time synchronization, udev, D-Bus/logind, UDisks2/Polkit,
audio/RTKit and all vendor hardware integration. Removing GVFS/portal components
needs a native mounting/media-access dependency test. Package exclusion must
account for D-Bus/socket activation; a disabled service may still be activated.

The direct, defensible benefit is less installed payload and memory pressure.
Power benefit depends on the work actually removed. Dormant package files do
not establish energy savings. Preserve security maintenance and bounded logs;
do not replace the desktop with unrestricted root applications or broad Polkit
allow rules. [UDisks authorization model](https://storaged.org/doc/udisks2-api/latest/udisks-polkit-actions.html).

### 2. Qualify idle Ethernet autosuspend

The identified USB Ethernet leaf supports autosuspend but is currently forced
on with no cable carrier. Test allowing **that device** to enter runtime idle;
record actual residency and power. Preserve automatic cable-insertion detection,
link recovery, traffic and switching between USB modes. Check shared hub/input
behavior. Leave the hub and accessory power controls alone.

Dynamic USB PM does not require whole-system suspend. The matching r8152 driver
has a distinct automatic-suspend path. However, advertised support does not prove
that the device will suspend or resume correctly in this board integration.
[Linux USB PM](https://docs.kernel.org/driver-api/usb/power-management.html),
[matching r8152 source](https://raw.githubusercontent.com/torvalds/linux/v6.18/drivers/net/usb/r8152.c).

### 3. Add coordinated IMU idle PM in a later BSP variant

The pinned BMI270 driver enables accelerometer, gyro, AUX and temperature at
initialization, configures 100/200 Hz, and disables advanced power saving. Its
runtime callbacks suspend IIO triggering; the inspected core/I2C wrapper lacks
the runtime usage accounting needed for hardware idle power management.

This is a driver opportunity, not an `auto` sysfs switch. Coordinate the
BMM150's AUX parent and preserve direct reads, buffered capture, configuration,
firmware initialization and automatic resume. Test all motion/compass channels
both separately and together through repeated idle/reopen cycles. Inspect the
physical I2C parent as well as the IIO class device: they are different PM layers.
[Pinned BMI270 initialization](https://github.com/m5stack/m5stack-linux-dtoverlays/blob/75518549a2640486f5d13ea72ca4935b198d7c20/modules/imu-1.0/bmi270/bmi270_core.c#L1204-L1237),
[Bosch power-mode requirements](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bmi270-ds000.pdf).

### 4. Investigate real panel-controller sleep

Brightness zero powers down the backlight path; it does not establish that the
LCD controller or its rail is asleep. The pinned panel disable callback delegates
to `mipi_dbi_pipe_disable`, whose inspected implementation does not issue an
explicit controller sleep command. The board node does not establish separately
switchable panel supplies.

A driver-level sleep/resume sequence may offer further idle savings. It needs
correct command timing, state restoration and full display/wake/HDMI testing;
no benefit is measured yet. Keep this separate from application frame-rate or
redraw optimizations, which are outside the current scope.
[Kernel helper](https://github.com/torvalds/linux/blob/v6.18/drivers/gpu/drm/drm_mipi_dbi.c#L405-L426).

### 5. Verify true hardware standby where drivers already support it

- **Camera:** IMX219 already has upstream runtime PM and a one-second
  autosuspend delay. Check the actual running driver, clocks, regulators and
  parent-device residency before adding a new policy. A suspended PipeWire
  node is not proof that a physical sensor rail is off. Do not apply the old
  board's GPIO16 camera-power overlay to v5.
  [IMX219 driver](https://raw.githubusercontent.com/torvalds/linux/v6.18/drivers/media/i2c/imx219.c).
- **Codec/amplifier:** the amplifier is stream-gated, while the inspected codec
  has `idle_bias_on=1` and an empty BIAS_OFF handler. Review hardware standby
  only with a controlled idle/no-PCM state. Preserve jack detection, recording,
  headset routes, pops and resume latency. Do not force standby underneath active
  clients or attribute their processing to base-system overhead.
- **Bluetooth:** preserve powered availability, pairing and reconnect. Audit
  normal discovery/connectability defaults; BlueZ warns that forced
  `FastConnectable` increases consumption. A default already off is not a new
  saving. [BlueZ 5.82 configuration](https://raw.githubusercontent.com/bluez/bluez/5.82/src/main.conf).

### 6. Keep later CPU/memory/maintenance experiments controlled

Keep ondemand, the existing firmware GPU reservation and 64 MiB CMA for the first
lean control. Later compare supported CPU policies using equal completed work,
and test 128→64 MiB GPU reservation only with the full camera/HDMI/graphics
matrix. Lower reserved memory can improve RAM headroom without proving lower
power. Do not use 16 MiB cut-down firmware, disable CPU cores or infer x86-style
power features on this hardware. Empty exported CPU-idle metrics do not prove
that ordinary architectural idle is absent.

Retain the initial zram/writeback and filesystem durability policy. The warm
sample supplies no evidence for disabling swap, increasing dirty-writeback
delays, removing journaling or moving logs into scarce RAM. Keep update
maintenance, with coalesced scheduling where appropriate. A battery-only supply
object without AC/USB `online` data does not establish reliable
`ConditionACPower` behavior; qualify external-power detection separately.
[CPUFreq](https://docs.kernel.org/admin-guide/pm/cpufreq.html),
[zram](https://docs.kernel.org/admin-guide/blockdev/zram.html),
[systemd conditions](https://www.freedesktop.org/software/systemd/man/257/systemd.unit.html).

The GPIO LED subsystem also exposes `grove_fun`, `ext_usb_gpio_fun`,
`ext_5v_out` and `grove_5v_out`: these are functional routing/power controls,
not a list of cosmetic LEDs to turn off.

## Base-only measurement and implementation sequence

1. Complete the lean profile's native setup/session contract, explicit runtime
   dependencies, profile-specific APT lock, stage2 export handling and distinct
   verifier/workspace. Keep every native hardware feature available.
2. Build a full-source control and lean image with matched common package,
   kernel/BSP and native-client versions/settings. The stock-derived image is
   a compatibility reference; some packages/boot defaults differ, including
   raspi-config-core, so it is not automatically a clean causal control.
3. Boot only a spare development card. Test first boot, reboot, user permissions,
   USB storage authorization, SSH and the entire native hardware matrix.
4. For the main base-stack comparison, hold client processes and audio states
   identical. An optional diagnostic boot without foreground clients must be
   applied equally to both images while retaining all hardware services and
   connectivity. Do not stop clients on the current device merely to create a
   cleaner number.
5. Measure settled memory pressure, CPU/IRQ activity, I/O and physical-device PM
   residency, then controlled discharge energy. Do not subtract process CPU
   shares from aggregate usage and call the remainder base-system electrical
   power: shared hardware states and energy costs are not additive that way.
6. Test one base-only PM change at a time, starting with the identified Ethernet
   leaf. Driver changes to IMU/panel/codec need a separately pinned BSP variant,
   automatic-resume tests and complete capability revalidation.

Current charging readings still show roughly 0.21 W from `power_avg` versus
about 2.09 W from voltage × current. The remaining-charge slope agrees internally
with reported current, but is not independent calibration. Resolve the mismatch
before integrating that field into an energy claim; do not silently multiply it
by ten. Charging current is net battery charge flow, not total OS load.
[TI BQ27220 reference](https://www.ti.com/lit/ug/sluubd4a/sluubd4a.pdf).

No base-system power reduction or runtime extension has been measured. The
deliverable is a verified implementation plan and a ranked set of experiments,
with [sanitized evidence](../research/lightweight-trixie-review.json). Raw local
observations and inspected source/package evidence remain under ignored
`build/lean-research-round2/`.
