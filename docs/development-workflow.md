# Proposed development workflow

Original research date: 2026-11-13. The application and dedicated Linux ARM64
package builder have since been implemented; see [current build instructions](building.md).
The broader image/recovery workflow below remains a proposal. No OS image or
firmware replacement has been made. A separately authorized live installation
and device test of the app is now complete; see [device validation](device-validation.md).
Builder installation/removal tests and the original proposed recovery work are
distinct from that later app-only deployment.

## Starting point

The SSH inspection found Debian 13.7 (trixie), kernel 6.18.39, a preloaded
2026-09-29 stage4 image, and APPLaunch revision ending `.105`. Linux reported
352 MiB usable RAM and 91 MiB swap in use. These observations describe the
running installation, not the capacity available to a build machine.

The development host is macOS arm64. Docker CLI has a `desktop-linux` context;
that configuration does not establish that image builds will work. Lima has an
unrelated, stopped `nmt-build` VM with 4 CPUs, 8 GiB RAM and a 64 GiB disk. Leave
that VM alone; provision a dedicated builder if this plan is implemented.

## Separate the development loops

| Work | Proposed environment | Deliverable |
| --- | --- | --- |
| Edit and preview the interface | Native macOS SDL/LVGL simulator | Desktop executable and screenshots |
| Compile and package device applications | Pinned Debian Linux builder | arm64 `.deb` |
| Assemble the operating system | Dedicated Debian arm64 Linux VM or Linux host | Versioned `.img.xz` |
| Accept a release | Physical CardputerZero with a development microSD | Recorded hardware results |

The official [application template](https://github.com/CardputerZero/Template)
documents macOS simulator presets, an aarch64 Linux cross-build with a BSP sysroot,
and `cmake --workflow --preset cp0-cross-package`. Its maintainers recommend a
Debian-based Linux host for reliable CPack dependency discovery. The simulator
previews application behavior; it does not validate physical peripherals.

Start image work from the [vendor pi-gen fork](https://github.com/CardputerZero/pi-gen),
branch `cardputerzero_v1.0`, pinned to a reviewed commit. Its Docker workflow needs
Linux kernel facilities including loop devices and binfmt. Use a Linux filesystem
inside the dedicated builder for build work; validate the builder there before
claiming macOS Docker compatibility. A Linux arm64 builder is the proposed path,
not a verified build configuration.

## Two outputs, shared customization

Package custom applications and system defaults independently, then install the
same versioned packages into images. This makes an application change testable
without rebuilding the entire OS and prevents manual device edits from becoming
the only record of the intended system.

Proposed profiles:

- **stock-compatible:** retain the vendor kernel, overlays, firmware, launcher and
  hardware services; add project metadata and selected custom packages. Establish
  this baseline before trimming the existing stage4-derived environment.
- **minimal-debian:** later produce a smaller userspace while retaining every
  required hardware driver, configuration and service. Measure each removal on
  the device; do not assume a generic Raspberry Pi Lite image supports the board.
- **appliance:** optional later profile that starts a custom shell/application
  and keeps a documented maintenance and recovery path.

## Milestones and acceptance

1. **Baseline and recovery assets.** Save sanitized package versions, boot
   configuration, device-tree choices, loaded modules, service configuration and
   peripheral interfaces. Preserve the original card and prepare a spare card.
   Acquire an offline full-card backup through a reader; do not treat a raw copy
   of a mounted, changing root filesystem as a verified recovery image.
2. **Package-first proof of concept.** Build a small `tyrell-status` application
   from the official template: build revision, uptime, network and battery state,
   keyboard navigation, and a clear exit back to APPLaunch. Use only interfaces
   confirmed by inspection. Preview on macOS, package on Linux, and test on the
   development card. Acceptance: launch, input, display, exit, and package
   installation/removal work without breaking the existing launcher.
3. **Stock-compatible image.** Build the vendor-derived image with that exact
   package and project version metadata. Acceptance: it boots from the spare
   card, passes the physical checklist, and has a usable recovery procedure.
4. **Profile customization.** Add packages, services, defaults or a custom shell
   through recorded build inputs. Compare memory, swap and boot time after each
   meaningful change. Keep the stock-compatible profile available for comparison.
5. **Release process.** Rebuild from a clean builder, inspect the image, publish
   versioned artifacts and record physical test results. Introduce unattended
   updates only after installation and recovery are dependable.

## Lock inputs and preserve provenance

A proposed `locks/sources.json` should record:

- Exact pi-gen, application, launcher and driver source commit IDs.
- Builder OS/image digest, architecture and toolchain versions.
- Kernel, modules, DTBs/overlays, GPU/wireless firmware and BSP versions/hashes.
- Debian, Raspberry Pi and vendor package versions and repository origins.
- Immutable archive locations, SHA-256 values, and redistribution/license notes.

[Debian Snapshot](https://snapshot.debian.org/) can preserve date-specific Debian
package inputs. Raspberry Pi and vendor artifacts need their own pinned or
archived inputs; a Debian snapshot alone does not lock those repositories.

The [M5 Imager repository](https://github.com/CardputerZero/m5stack-imager) points
at a mutable `latest.img.xz`. Archive an identified image with a computed SHA-256
instead of treating that URL as a release lock. Its checked-in
[manifest template](https://github.com/CardputerZero/m5stack-imager/blob/main/m5stack/os-list-template.json)
currently leaves the CardputerZero image checksum fields empty.

Use [SOURCE_DATE_EPOCH](https://reproducible-builds.org/docs/source-date-epoch/)
where supported, normalize build timestamps, and record remaining differences.
Claim byte-for-byte reproducibility only after comparing two clean builds.
Exclude `.env`, SSH host keys, personal files and network credentials from release
images; provide device-specific configuration separately.

Each release should contain `.deb` packages, `.img.xz`, SHA-256 checksums, source
revisions, package inventory, build logs, relevant source/license material, and a
hardware test record. Label artifacts untested until they pass on the device.

## Physical acceptance checklist

- Cold boot, project version, expected mounts, and persistent settings.
- Display orientation/backlight; every key, modifiers and repeat behavior.
- APPLaunch/custom application startup, exit and restart.
- Wi-Fi reconnect and SSH; Bluetooth and Ethernet if the profile uses them.
- Battery readings, charging, low-battery handling and orderly power-off.
- Speaker, microphone and headphone routing.
- RTC persistence, IR, expansion interfaces and USB host/slave modes as applicable.
- Camera and IMU on the full model; explicitly mark unavailable Lite features.
- Boot duration, idle RAM/swap, storage use, CPU load and responsiveness.
- Repeated clean shutdown/reboot, package upgrade/removal, and recovery boot.

Separate simulator/automated results from hardware observations. For every item,
record the image checksum, device model, pass/fail/not-tested, method and evidence.
The observed memory pressure makes resource measurements part of acceptance.

## Recovery and updates

Keep the factory card unchanged and perform experimental boots on a spare card.
Before flashing, identify the destination card by capacity and device identity;
verify the written image and retain the known-good backup and its checksum.

[M5's flashing guide](https://docs.m5stack.com/en/compute/cardputerzero/image)
recommends a card reader. Its USB fallback uses `raspberrypi/usbboot` commit
`61c482663c9b2a61ee86ba491f131ba4ca90e4ea`, the right USB-C port in slave mode,
and BOOT held through more than five seconds after power-on. Validate recovery
with the actual hardware before relying on it remotely.

Start with versioned application packages and complete image releases. Preserve
the matching kernel/modules/boot assets as a unit. Defer A/B updates until the
CM0 boot firmware, trial boot, health checks and rollback have been tested on the
device; [Raspberry Pi's A/B documentation](https://www.raspberrypi.com/documentation/computers/config_txt.html#the-autoboot-txt-file)
describes a mechanism, not a validation of this CardputerZero installation.

The PY32/IOE1 controller firmware is a separate maintenance concern: the official
[FactoryTest application](https://github.com/CardputerZero/FactoryTest) includes
an upgrade feature. Record its baseline version and leave controller updates out
of the initial Linux-image experiments.
