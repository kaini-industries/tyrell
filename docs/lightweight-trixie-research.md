# Hardware-complete lightweight Trixie image

Research date: November 15, 2026. Nine fresh agents investigated package size,
kernel power support, native rendering, services, hardware compatibility,
measurement, build design, an adversarial review, and the live baseline.

A subsequent [fresh verification and additional power review](lightweight-trixie-review.md)
found a concrete first-boot blocker and version-matched upstream audio fixes.
The user then narrowed the active recommendation to **base-system usage only**.
That follow-up is the current plan; application-level observations below remain
historical research, not proposed work in the base-system scope.

The target is **native Tyrell apps, Wi-Fi and SSH, with every native
CardputerZero hardware capability retained**. This is a research and build
proposal. No firmware, packages, services, radio settings, brightness, controller
firmware, or SD contents were deliberately changed. Passive SSH observations
were collected after the device became reachable; authentication may produce
the normal system logs. No builder was started or new image produced.

## Recommended first profile

Add a separate source-built `lean-native` profile using vendor stages0–2,
retaining the complete stage2 hardware stack and vendor applications, plus an
explicit selection of verified Tyrell packages. Omit the desktop/application
metapackages introduced in stages3/4. Keep the existing stock-derived image and
its strict preservation verifier as the comparison/recovery reference.
The unchanged APPLaunch 0.6.105 setup wizard requires desktop autologin; a
verified native provisioning/session path is required before removing LightDM.

Initially retain ARM64, the current kernel/BSP, full module and firmware trees,
`gpu_mem=128`, 64 MiB CMA, ondemand CPU policy, Wi-Fi/Bluetooth availability,
and the qualified audio/media stack. Development-tool cleanup, font reduction,
and power-policy changes should be independently testable later increments.

The vendor fork already contains a Lite CI path that skips stages3–5. This is
evidence for a smaller build path, not proof of physical hardware operation.
[Vendor workflow](https://github.com/CardputerZero/pi-gen/blob/5f4c44f3431bf1dfb968c10f7228ad6fa0c66cf4/.github/workflows/build.yml),
[upstream stage anatomy](https://github.com/RPi-Distro/pi-gen#stage-anatomy).

## What the package evidence supports

The currently selected reviewed stock-derived image has 1,685 original package
identities plus Tyrell Status, a 7,415,529,472-byte raw image, and a
1,942,911,472-byte compressed image. It has not been physically booted. See
[stock-image validation](stock-image-validation.md).

Analysis of the separately retained source-build APT lock gives:

| Selection | Locally derived result | Interpretation |
| --- | --- | --- |
| Stage4 entries absent from stage2 | 729 identities; 2,278.8 MiB `Installed-Size` | Candidate payload associated with later stages; some native dependencies must be added back |
| Stage2 CJK font packages | 298.9 MiB | Later locale/font experiment; verify vendor text and video subtitle fallback first |
| Chromium/common/localization and Firefox | 781.9 MiB | Overlaps the later-stage total; do not add it again |

These are package metadata totals, not measured filesystem shrink, compressed
image savings, RAM savings, or energy savings. The source lock retains transient
build packages and differs from current stock in some versions. Its stage2
selection of 950 identities is not a promised lean runtime package count.
Reproducible calculations and their limitations are recorded in
[research evidence](../research/lightweight-trixie.json).

For the first build, keep stage2's camera, audio, BlueZ, GPIO/I2C/SPI bindings,
USB/gadget tools, and hardware applications. Later remove an exact build-only
set after module installation and ABI verification, with a simulated dependency
transaction that protects runtime packages. Do not apply broad `autoremove` or
remove Python indiscriminately. Removing an unused browser or font primarily
reclaims storage; reduced battery drain requires less actual activity.

## Live observations and their limits

On November 15 at approximately 18:06–18:08 America/Chicago, passive SSH showed:

- Linux-visible RAM about 353 MiB; about 118 MiB available in the first sample.
  About 100 MiB of swap was occupied. Occupied swap does not prove active
  swapping; measure swap-in/out and memory pressure separately.
- LightDM, labwc, the desktop panel and file manager, GVFS monitors, desktop
  portals, and PackageKit were present as running processes/services while
  backlight brightness and actual brightness were zero.
- The CPU governor was already ondemand, with a 600–1000 MHz range.
- Initial aggregate CPU utilization was 28.75% over eight seconds. A follow-up
  at 184 seconds uptime measured 5.36% over fifteen seconds. The initial value
  must not be presented as settled idle consumption.
- In the follow-up, the launcher used about 7.4% of one CPU core, PipeWire/Pulse
  together about 8.2%, and the desktop panel about 0.2%. These are interval
  process deltas, not `ps` lifetime percentages. The desktop is a clear footprint
  opportunity, but it was not the dominant CPU user in that later interval.
- At approximately 18:09, PipeWire reported a running APPLaunch output stream
  and running onboard audio sink; microphone and camera nodes were suspended.
  This identifies an audio-idle investigation, not proof that the stream was
  silent or a measurement of its energy cost.
- The battery was charging at 9–10%. These observations are not a discharge
  benchmark and cannot establish battery runtime or savings.

The short intervals, recent boot, SSH observer activity and uncontrolled user
workload limit interpretation. Preserve the observations, then establish a
settled control before changing the image or power policy.
A later fifteen-second observation at 850 seconds uptime measured 5.64%
aggregate CPU, with similar launcher/audio activity. Wi-Fi power saving was
already on; swap-out was zero and SD writes totaled about 16 KiB. See the
follow-up review for its provenance and limits.

### Battery telemetry needs a consistency check

The gauge exposed `current_now`, `voltage_now`, `power_avg` and charge/status
fields, but no `power_now` or `energy_now`. During the follow-up it reported
approximately 3.717 V and +0.559 A, implying about +2.08 W, while `power_avg`
represented about +0.208 W. The discrepancy persisted across four five-second
samples. An instantaneous product and a hardware average need not coincide,
but this approximately tenfold difference needs investigation before energy
integration.

TI specifies the BQ27220 AveragePower register in **mW**, signed negative during
discharge and positive during charge. The pinned vendor driver converts this
to micro-watts using a factor of 1,000. The documentation does not justify an
automatic tenfold correction. Check the running driver, averaging behavior and
gauge configuration against a suitable independent measurement; preserve the
raw values and calibration. [TI technical reference, section 2.18](https://www.ti.com/lit/ug/sluubd4a/sluubd4a.pdf),
[Linux power-supply conventions](https://www.kernel.org/doc/html/latest/power/power_supply_class.html).

## Earlier cross-system hypotheses

Application sound-engine, rendering, game and UI changes in this section are
outside the current base-system-only scope. Use the follow-up review's ranked
OS/driver options for the implementation plan.

1. **Idle audio and launcher activity.** Preserve PipeWire/Pulse compatibility,
   WirePlumber, RTKit and all audio routes. Investigate which nodes/clients keep
   processing when the screen is off, especially the observed APPLaunch stream.
   Test releasing/inactivating a genuinely idle stream and opening it for real
   sounds before changing the audio-server policy. Exact release-source and
   binary verification now identifies an upstream idle-engine-close fix suitable
   for a separately tested backport; see the follow-up review. Verify idle device suspension, clean
   resume, launcher sounds, microphone capture, TRRS and HDMI sound. Do not
   disable the audio stack to improve an idle number.
   [WirePlumber ALSA idle-suspension controls](https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/alsa.html).
2. **Event-driven native applications.** Status currently sleeps 10 ms despite
   one-second telemetry; Video loops at 8 ms even in its browser. Wait for input
   or the next real LVGL/application deadline. Video already caches unchanged
   labels and frame assignments: loop frequency is not full-screen transfer
   frequency. Add dirty-frame/idle-wait support to the game framework so Chess
   can stop continuously presenting a static board. Preserve active game frame
   pacing, chess clocks, search and network deadlines.
   [Status loop](../packages/tyrell-status/src/main.cpp),
   [Video loop](../packages/tyrell-video/src/main.cpp),
   [game runtime](../packages/tyrell-game/src/runtime.cpp),
   [LVGL 9.5 integration](https://lvgl.io/docs/open/9.5/integration/overview).
3. **One coordinated display policy.** Reuse or extend the installed launcher's
   existing backlight/screen-timeout behavior after verifying its exact version.
   Source/binary binding now confirms that cached commit 0590e73 is later than
   installed 0.6.105; select specific reviewed changes rather than assuming the
   cached checkout describes the installed program. The
   live brightness-zero state shows that simply adding another blanking daemon
   is not a sufficient plan. Coordinate brightness, dim/off, input wake and
   app inhibition. Consume a wake key before forwarding it to a game. Active
   LCD video/games must not unexpectedly blank; HDMI can dim its controller.
   The pinned driver supports brightness-zero backlight power-down, which is
   different from drawing black pixels.
   [Pinned backlight driver](https://github.com/m5stack/m5stack-linux-dtoverlays/blob/75518549a2640486f5d13ea72ca4935b198d7c20/modules/st7789v-1.0/pwm_bl_m5stack.c).
4. **Connected Wi-Fi power saving.** Keep NetworkManager and SSH. The later
   read-only check confirmed effective Wi-Fi power saving is already on, so
   enabling it is not a new gain. Preserve and test that policy under weak signal,
   reconnect, interactive SSH, throughput and network games. Retain Bluetooth
   support and current availability in the first image. Radio-off or peripheral
   disablement is not the target profile.
   [NetworkManager wireless settings](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/settings-802-11-wireless.html).
5. **Maintenance and runtime PM.** Omit desktop-only update/remote-access helpers
   when dependencies permit, but keep a deliberate security-maintenance path.
   APT's locked services already use `ConditionACPower=true`; a battery-only
   sysfs supply without an AC/USB `online` object cannot be assumed to make that
   condition reliable. Verify it under manual cable tests later. Use relaxed
   timer accuracy to coalesce eligible work. Test runtime PM one device at a
   time, preserving Bluetooth, camera, sensors, Ethernet, USB and input behavior.
   [systemd 257 conditions](https://www.freedesktop.org/software/systemd/man/257/systemd.unit.html),
   [timers](https://www.freedesktop.org/software/systemd/man/257/systemd.timer.html),
   [Linux runtime PM](https://docs.kernel.org/power/runtime_pm.html).

The exact pinned kernel has `CONFIG_SUSPEND` disabled; the live
`/sys/power/state` was empty. Screen-off is therefore **awake Linux**, not
system suspend. Keep ondemand as the control: generic `powersave` requests the
minimum CPU frequency and may extend work or degrade media. GPU reservation
changes, CMA changes, a new kernel, and alternate CPU policies should remain
separate experiments. In particular, reducing firmware GPU memory to 16 MiB
can select cut-down firmware with missing features.
[Linux sleep prerequisites](https://docs.kernel.org/admin-guide/pm/sleep-states.html),
[CPU frequency policies](https://docs.kernel.org/admin-guide/pm/cpufreq.html),
[Raspberry Pi firmware selection](https://www.raspberrypi.com/documentation/computers/config_txt.html).

## Full native hardware is a release contract

Keep the entire pinned BSP/firmware/module set for the first lean image. The
existing twelve-custom-module check is not a complete hardware test.

| Capability | Retain and physically accept on a spare card |
| --- | --- |
| Boot and microSD | Boot firmware, kernel/initramfs, matching modules/overlays, filesystem expansion and clean reboot/shutdown |
| LCD and keyboard | Panel firmware, backlight driver, keymap, udev permissions, all keys/modifiers/repeat, dim/off/wake and launcher return |
| Battery and power | M5IOE1, BQ27220, `powerfail_suo`, regulators/GPIO; charging, low battery and orderly physical-switch shutdown |
| Audio | ES8389/board modules, ALSA, PipeWire/Pulse, WirePlumber/RTKit; speaker, headset/jack routes, built-in and headset microphones |
| HDMI | VC4 KMS/DRM, Mesa/media runtimes, CMA, HDMI audio and VT helper; hotplug, playback and abnormal-exit recovery |
| Camera | IMX219, matching libcamera/IPA/tuning/rpicam/Picamera2; capture, preview/record and clean reopening |
| Motion and compass | BMI270/BMM150 modules/overlay and `bmi270-init-data.fw`; accelerometer, gyro and magnetometer after idle |
| RTC and IR | RX8130 support/time sync, IR receive/transmit overlays and tools; retained time and known-code tests |
| Wi-Fi and Bluetooth | Original drivers/firmware, NetworkManager and BlueZ; SSH, pairing, reconnect, actual peripheral use |
| Ethernet and USB | Host/hub/network/gadget/modeswitch support; Ethernet traffic, USB devices and right-port host/slave behavior |
| Grove, expansion and LEDs | I2C/UART/SPI/GPIO routing and bindings, M5IOE1, key/LED tmpfiles and udev rules; safe accessory tests |

The [M5 specification/pin map](https://docs.m5stack.com/en/CardputerZero),
[pinned board Makefile](https://github.com/m5stack/m5stack-linux-dtoverlays/blob/75518549a2640486f5d13ea72ca4935b198d7c20/modules/CardputerZero/Makefile)
and [v5 overlay](https://github.com/m5stack/m5stack-linux-dtoverlays/blob/75518549a2640486f5d13ea72ca4935b198d7c20/modules/CardputerZero/cardputerzero-v5-overlay.dts)
define the retained native layer. Optional external accessories are tested where
available; their absence must not be reported as a passing hardware test.

LaunchWizard wants `graphical.target`, which does not itself install or run a
desktop. However, exact 0.6.105 source verification found a separate mandatory
`configure_desktop_startup()` step that calls `raspi-config ... B4`; the locked
raspi-config fails without LightDM. This step precedes enabling APPLaunch.
Provide a profile-aware native setup path or verified provisioning alternative,
including normal-user/PAM/logind, audio, device access and removable-media
authorization. Merely changing the target or blindly selecting console autologin
does not establish that contract.
Likewise, the current [HDMI helper](../packages/tyrell-video/system/tyrell-video-hdmi-session)
leases an unused VT generically. It may already work without LightDM/labwc.
Qualify unowned DRM, getty collisions, hotplug and crash recovery first.
[systemd target semantics](https://www.freedesktop.org/software/systemd/man/257/systemd.special.html).

## Build work required

| Area | Required implementation |
| --- | --- |
| Profile manifest | Record selected stages/apps, native provisioning/session behavior, all hardware requirements, runtime packages, APT lock and distinct output/work names; hash them into captured metadata |
| Native packages | Capture each selected Tyrell `.deb` version/source/hash explicitly; the existing source-image path installs Status only |
| Stage/export handling | Select stages0–2; place final Tyrell setup in stage2; stop suppressing that export and handle the vendor `-lite` suffix explicitly |
| Frozen APT | Resolve a clean stage2 runtime plus every selected app against retained compatible bytes; generate a distinct profile lock/stage selection, not an unconstrained mixed-version pool |
| Resume | Bind reuse to profile, hardware contract, sources and dependencies; never reuse full-image work as lean work |
| Verification | Separate hardware-complete lean checks from strict stock preservation; check firmware, modules/ABI, keymaps, permissions, apps, sessions, provisioning and cleanup |

Relevant implementation files are [host orchestration](../image/build-image.py),
[guest build](../builder/build-image.sh), [vendor preparation](../image/prepare-vendor.py),
[APT replay](../image/apt-lock.py), [resume guards](../image/resume.py), and
[offline verification](../image/verify-image.py).

Simply shortening `STAGE_LIST` is insufficient: the current source path fixes
stages0–4, installs Tyrell at stage4, suppresses stage2 export, expects an
unsuffixed filename, and requires five frozen APT stage selections. Stage2's
existing repository may also lack dependencies for Video or other native apps
previously introduced by later stages. Keep normal-user privileges, first-boot
credential safeguards and explicit SSH provisioning; no default shared password.

## Measurement and implementation order

1. Build the desktop-free, hardware-complete control using explicit native app
   inputs. Validate dependency closure and offline contents; preserve reference
   artifacts and the current card.
2. Boot only a spare development card and pass the complete hardware matrix,
   first-boot/reboot, native apps, SSH, HDMI and recovery tests.
3. Record actual filesystem usage, settled `MemAvailable`/PSS, swap I/O, CPU
   deltas/residency and workload latency. Report compressed/raw sizes separately.
4. In the current base-only scope, test the identified Ethernet leaf's runtime
   PM, then separately qualified OS/driver idle policies. Keep all hardware
   functional after each change and its idle/wake transitions. Application power
   optimizations are deferred.
5. Resolve the gauge consistency issue before energy claims. Use full-source
   control and lean images with identical common package/app versions for causal
   comparisons; stock remains a compatibility/product reference, not an otherwise
   identical control. Compare repeated,
   counterbalanced A/B discharge runs with matched brightness, signal, radios,
   accessories, app workload, charge range and temperature. Buffer compact
   telemetry in RAM and retrieve afterward to limit SSH/SD observer effects.
6. Compare independent per-run energy and responsiveness, with a pilot-defined
   practical threshold and uncertainty interval. Do not count correlated gauge
   samples as independent experiments. A controlled full-discharge run is needed
   for a runtime claim; charging measurements or battery-percent snapshots are
   insufficient. [Paired comparison guidance](https://www.itl.nist.gov/div898/handbook/eda/section3/eda353.htm).

The expected opportunity is a materially smaller OS plus lower unnecessary
activity. No percentage battery improvement, runtime extension, final lean image
size, or complete hardware acceptance is claimed by this research.
