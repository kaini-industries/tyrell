# CardputerZero build-system research

Researched November 13, 2026. This is a source-reviewed proposal: no image or package was built, booted, installed, or flashed during this research. The primary agent inspected the device over SSH; this agent reviewed upstream sources. Device inventory remains the authority for the supplied installation.

## Recommendation

Start with the CardputerZero fork of `pi-gen`, preserve the factory kernel and board support, and maintain custom applications as ARM64 Debian packages. Prove an application package first, then produce a versioned image containing it. Keep a separate Buildroot experiment for a later small appliance OS if needed.

This device is a Raspberry Pi CM0 Linux computer. ESP32 Cardputer firmware projects, Arduino, ESP-IDF, and M5Burner are not its OS build path. The vendor describes RP3A0/BCM2837, ARMv8-A, 512 MB RAM, and microSD storage. [Product specification](https://docs.m5stack.com/en/CardputerZero)

## Factory baseline versus current upstream

The SSH inspection reported the factory installation below. Do not replace it with the newest upstream release merely to establish the project.

| Input | Factory installation | Current upstream candidate |
|---|---|---|
| OS | Debian 13.7 / Trixie, ARM64 | Trixie ARM64 |
| Image builder | `5f4c44f3431bf1dfb968c10f7228ad6fa0c66cf4`, stage 4 | `e3b4e323782a59a426b92356248f5bc09a098be1` |
| Release | September 29, 2026 stable | November 13, 2026 prerelease |
| M5 driver source | `75518549a2640486f5d13ea72ca4935b198d7c20` | `4f7aeee9fc103ebb03d22cac476bb191ecfb321e` |
| APPLaunch | `0.6.105` | `0.6.106` |
| Running kernel / release package | `6.18.39+rpt-rpi-v8` | Release reports package `1:6.18.39-1+rpt1` |
| Board configuration | v5, `bq27220_v5`, `bmi270_bmm150_overlay` | Build selects `CONFIG_CARDPUTERO_V0_5=y` |

Sources: [factory builder commit](https://github.com/CardputerZero/pi-gen/commit/5f4c44f3431bf1dfb968c10f7228ad6fa0c66cf4), [September 29 stable release](https://github.com/CardputerZero/pi-gen/releases/tag/20260929-070146-5f4c44f), [current builder commit](https://github.com/CardputerZero/pi-gen/commit/e3b4e323782a59a426b92356248f5bc09a098be1), [November 13 prerelease](https://github.com/CardputerZero/pi-gen/releases/tag/20261113-033903-e3b4e32).

The current builder commit was verified through GitHub's commit endpoint; its Git tree is `221ec74c87e15c2242648b284f3de2791ab39cdc`. The current driver commit's tree is `f24623f91a20c8928bae073c04fcf285f5823ef6`. Use commit IDs when fetching source, not these tree IDs.

Versioned image assets provide a stronger baseline than a mutable `latest` URL. GitHub currently reports these compressed-asset digests; they have not been verified against downloaded bytes in this project:

| Release asset | SHA-256 |
|---|---|
| `2026-09-29-cardputerzero-trixie-arm64.img.xz` | `b37a702aea8b5d75015b48c99b8c969efc2636b82757282fb27cf03056788c91` |
| `2026-11-13-cardputerzero-trixie-arm64.img.xz` | `e1de0a3db4f0916dec4036b726530aa98e2d6b33949bdcfcbc970e3b012d83c7` |

Download from the release pages above, verify the bytes locally, and retain the artifact with its release metadata. The stock SD should remain the known-good reference. A downloaded vendor image is not a backup of the configured device.

## Existing image pipeline

The repository default branch is `cardputerzero_v1.0`; generic upstream instructions mentioning the `arm64` branch should not override the selected CardputerZero commit. The current fork's `build.sh` fixes `ARCH=arm64` and defaults to Trixie. [Builder implementation](https://github.com/CardputerZero/pi-gen/blob/e3b4e323782a59a426b92356248f5bc09a098be1/build.sh)

The current vendor workflow supplies a useful initial build recipe:

1. Run on native ARM64 Linux, currently `ubuntu-24.04-arm` in vendor CI.
2. Install the Debian build dependencies specified in that workflow.
3. Generate `config` with the image name, Trixie, XZ compression, provisioning policy, and locale.
4. For the desktop image, skip stage 5. The Lite verification job skips stages 3–5.
5. Run `sudo ./build.sh`; outputs are placed in `deploy/`.
6. Decompress the image and run `sudo ./tools/verify-image.sh <image.img>`.

[Exact current CI recipe](https://github.com/CardputerZero/pi-gen/blob/e3b4e323782a59a426b92356248f5bc09a098be1/.github/workflows/build.yml)

Use a Linux ARM64 VM or runner for full image construction from a Mac workstation. Application SDL previews can run on macOS. Upstream `pi-gen` describes emulated builds but states only native builds are supported; the CardputerZero CI also uses native ARM64. A container still needs Linux facilities such as chroot and mounts. Building directly on the 512 MB handheld is a poor default for full images. [Upstream build guidance](https://github.com/RPi-Distro/pi-gen)

The hardware stage installs `linux-headers-rpi-v8`, compiles M5 modules against the image kernel, installs overlays and configuration, runs `depmod`, and records the driver commit in `/etc/cardputerzero-dtoverlays.commit`. It sets 64 MiB CMA for the KMS display. [Current hardware stage](https://github.com/CardputerZero/pi-gen/blob/e3b4e323782a59a426b92356248f5bc09a098be1/stage2/05-cardputerzero/01-run.sh)

The driver build selects revision-specific overlays and integrates display/backlight, keyboard, audio, battery, M5IOE1, IMU, and board functions. Preserve the observed v5 configuration initially. [Current board Makefile](https://github.com/m5stack/m5stack-linux-dtoverlays/blob/4f7aeee9fc103ebb03d22cac476bb191ecfb321e/modules/CardputerZero/Makefile)

The launcher stage copies vendored `start.elf`, `fixup.dat`, and splash assets, sets `gpu_mem=128`, and creates LaunchWizard and APPLaunch service configuration. These boot assets are build inputs too. The image verifier rejects a U-Boot override and `u-boot.bin`; reproducing this pipeline does not require adding U-Boot. [Launcher stage](https://github.com/CardputerZero/pi-gen/blob/e3b4e323782a59a426b92356248f5bc09a098be1/stage2/05-cardputerzero/02-run.sh), [image verifier](https://github.com/CardputerZero/pi-gen/blob/e3b4e323782a59a426b92356248f5bc09a098be1/tools/verify-image.sh)

## Reproducibility and branch traps

Pinning `pi-gen` alone is insufficient. The reviewed current implementation has mutable inputs:

- **Driver tip:** `01-run.sh` clones the driver's current main branch. Recording the resulting commit provides provenance after a build; it does not select the same input for the next build. Patch the fetch to require a locked commit.
- **Application releases:** `03-run.sh` forces latest releases and explicitly ignores the per-app `DEB_URL` and `DEB_FILE` inputs. Patch that function to consume exact retained artifacts with required SHA-256 values. Merely setting those variables will not pin these applications.
- **Launcher:** `02-run.sh` accepts `APPLAUNCH_DEB_FILE` or `APPLAUNCH_VERSION`. Prefer an exact verified package; the version selector alone still resolves a release dynamically.
- **APT packages:** kernel, headers, userspace, and build dependencies depend on repository state. Retain exact package versions and bytes, or configure suitable package snapshots. Freeze kernel and headers together.
- **Build environment:** record the Linux architecture, base image digest, tools, configuration, source commits, package manifest, boot firmware, and resulting image hash. Pin CI action revisions for release builds.

Sources: [driver fetch](https://github.com/CardputerZero/pi-gen/blob/e3b4e323782a59a426b92356248f5bc09a098be1/stage2/05-cardputerzero/01-run.sh), [application download function](https://github.com/CardputerZero/pi-gen/blob/e3b4e323782a59a426b92356248f5bc09a098be1/stage2/05-cardputerzero/03-run.sh), [launcher input handling](https://github.com/CardputerZero/pi-gen/blob/e3b4e323782a59a426b92356248f5bc09a098be1/stage2/05-cardputerzero/02-run.sh).

Additional source-reviewed traps:

- The current workflow's push trigger still names `arm64` despite the default branch being `cardputerzero_v1.0`. Set the fork's intended branch explicitly; manual dispatch is separately available.
- CI publishes GitHub releases and uploads to Aliyun OSS. A personal project should first build local/CI artifacts without copying vendor publishing or credential assumptions.
- The sample workflow enables SSH with `pi`/`raspberry` and uses `Asia/Shanghai`. Define personal provisioning and locale deliberately; never put `.env` secrets into a public image or source manifest.
- The launcher stage appends `cfg80211.ieee80211_regdom=AE`. Configure the radio region for the device's actual country of operation. Account timezone is not evidence of where its radio will be used.
- The historical image-design document describes older module names, overlays, and service behavior. Current executable stages are stronger evidence for the reviewed revision.

[Workflow implementation](https://github.com/CardputerZero/pi-gen/blob/e3b4e323782a59a426b92356248f5bc09a098be1/.github/workflows/build.yml), [current configuration implementation](https://github.com/CardputerZero/pi-gen/blob/e3b4e323782a59a426b92356248f5bc09a098be1/stage2/05-cardputerzero/02-run.sh), [historical design notes](https://github.com/CardputerZero/pi-gen/blob/e3b4e323782a59a426b92356248f5bc09a098be1/docs/CARDPUTERZERO-IMAGE-DESIGN.md).

These observations describe the reviewed November commit. Apply and verify fixes against whichever factory-baseline or newer commit is actually selected; do not assume every revision has identical behavior. A repeatable recipe is the first target. Byte-identical output requires additional control of timestamps, filesystem metadata, and package tooling and has not been demonstrated here.

## Kernel ABI and hardware acceptance

Keep the kernel image, matching headers, `/lib/modules/<release>`, M5 module build, and overlays as one tested unit. A `.ko` copied from another kernel release can fail even if both kernels are described as 6.18. A successful compile or image-content check does not establish that the LCD, keys, sound, or power handling work.

First validate the factory stack plus one custom application. For later kernel changes, verify module version metadata and dependency resolution, boot a spare SD, inspect logs, and exercise the hardware. Avoid a broad package upgrade as an incidental part of app iteration. Preserve a complete known-good baseline before changing the kernel or graphics stack.

The vendor verifier checks required files, overlays, selected modules, package metadata, service configuration, and recorded provenance. Add runtime checks for SSH/networking, the 320×170 display, keyboard modifiers, backlight, battery reporting, speaker/microphone, camera, RTC, IMU, USB modes, Ethernet, and expansion hardware actually used. Recovery and cold-boot checks require the real unit. [Existing static checks](https://github.com/CardputerZero/pi-gen/blob/e3b4e323782a59a426b92356248f5bc09a098be1/tools/verify-image.sh)

## Application iteration

Use the vendor [Template](https://github.com/CardputerZero/Template) for a CMake/LVGL application with an SDL desktop preview and device build. The [AppBuilder](https://github.com/CardputerZero/AppBuilder) toolchain produces ARM64 `.deb` files and AppStore metadata. Publishing to the public store is optional and separate from building/installing a private package.

The launcher itself has its own SDK/SCons build and Debian packaging flow; changing the whole launcher is a larger maintenance commitment than adding an application. Keep application assets, launcher integration, and a reusable system-profile package separate from the image assembler. [Launcher implementation](https://github.com/CardputerZero/launcher)

## Recovery and image delivery

The official guide recommends a microSD card reader with M5 Imager. Keep the supplied SD intact and test new images on a spare. Physical restoration is a normal part of this workflow, not an OTA mechanism. [Official flashing guide](https://docs.m5stack.com/en/compute/cardputerzero/image)

The guide also documents USB recovery using the right USB-C port, USB selector, Boot button, and `rpiboot`. Its `usbboot` instructions pin commit `61c482663c9b2a61ee86ba491f131ba4ca90e4ea`; it warns USB flashing is slower and may have host compatibility issues. No recovery operation was performed during research.

The Imager template references a mutable `cardputerzero-trixie-arm64-latest.img.xz` and has empty checksum fields. Prefer the versioned release artifacts and independently verified hashes discussed above. [Reviewed Imager manifest](https://github.com/CardputerZero/m5stack-imager/blob/main/m5stack/os-list-template.json)

## Alternatives and their proof limits

| Approach | Useful when | Current evidence / remaining work |
|---|---|---|
| Stock image plus custom packages | Learning hardware and developing apps | Smallest first step; full image reproducibility still needs an assembler and locked inputs. |
| CardputerZero `pi-gen` fork | Maintaining a general Debian development handheld | Existing hardware integration and vendor CI; recommended first image path after dependency locking. |
| Raspberry Pi `rpi-image-gen` | A later declarative Debian image pipeline | Native Debian ARM64 host path, YAML layers and hooks; no ready CardputerZero layer was verified. Vendor board support must be ported. Its documentation excludes containers and emulated non-ARM64 hosts from formal support. |
| Generic Buildroot | Minimal, tightly controlled appliance | Upstream has a Pi Zero 2 W ARM64 configuration. Cardputer display, input, audio, battery, boot assets, and peripheral policy still need integration and real-device tests. |
| MSD/Linux | Studying a Cardputer-specific Buildroot external tree | Uses Buildroot, read-only SquashFS, persistent `/data`, and an A/B design. Author explicitly reports real-device boot and tryboot recovery as unverified. Does not target stock LVGL or `.deb` compatibility; SSH/VPN are not implemented. |
| Yocto | A maintained product distribution with more formal BSP/package layers | Generic Raspberry Pi BSP exists; no complete maintained CardputerZero machine target was established in this investigation. |
| Armbian | An alternative Debian-based board distribution | No complete maintained CardputerZero target was established; generic SoC support alone does not establish peripheral support. |
| Community `cardputer-zero-os` | Exploring replacement login/display/session policy on Pi OS | A system profile rather than a standalone reproducible image builder; integration needs separate review. |

Sources: [Raspberry Pi rpi-image-gen](https://github.com/raspberrypi/rpi-image-gen), [Buildroot Pi Zero 2 W ARM64 configuration](https://github.com/buildroot/buildroot/blob/master/configs/raspberrypizero2w_64_defconfig), [MSD/Linux](https://github.com/uist1idrju3i/MSD-Linux), [MSD/Linux reviewed source commit](https://github.com/uist1idrju3i/MSD-Linux/commit/733d27e66c8499aa306325cb4ce928c6ed2f1034), [Yocto Raspberry Pi BSP](https://github.com/agherzan/meta-raspberrypi), [community system profile](https://github.com/vicliu624/cardputer-zero-os).

Absence of an established target in this research is not proof that no such project exists. Generic Pi support and QEMU success do not establish CardputerZero hardware compatibility. A/B/OTA support should be a separate tested milestone, with actual boot-failure rollback, before it is relied on.

## Suggested implementation milestones

1. Preserve and document the factory baseline and a physical recovery path.
2. Build one custom ARM64 application package; preview on the workstation and test on the existing OS.
3. Assemble a factory-based image on ARM64 Linux with locked dependencies and the package included.
4. Verify the image offline, flash a spare SD, and record cold boot and hardware results.
5. Introduce smaller package profiles, launcher changes, or a kernel update one at a time.
6. Evaluate an immutable Buildroot/A/B branch only if its operational benefits justify the compatibility work.

First deliverables should include `.deb`, `.img.xz`, SHA-256 checksums, exact source/package/configuration manifests, retained matching inputs, and recorded device-test results. None of these build or boot outcomes are claimed by this research document.
