# Tyrell

Custom applications and firmware-development tooling for the **M5Stack CardputerZero**, a Raspberry Pi Compute Module 0 Linux handheld.

**Tyrell Status** is a small system-monitor app packaged for ARM64 Debian and tested on the stock firmware through APPLaunch. The project also builds custom Debian images from the pinned vendor `pi-gen` baseline, with Tyrell Status preinstalled.

The first image passed 134 offline checks. The image workflow now includes frozen package inputs, recoverable build jobs, and archived provenance. Physical boot on a spare card remains pending; the original firmware and SD card are preserved.

## Tyrell Status

- Two keyboard-controlled pages for uptime, used/total memory, battery state, and network links.
- Native 320 × 170 framebuffer interface with a clear return to APPLaunch.
- Read-only telemetry from Linux procfs/sysfs; no network requests or root requirement.
- JSON output, offline fixtures, and headless LVGL screenshots for testing.
- A Debian package containing only the app, launcher entries, icon, and documentation.

## Tyrell Video

Tyrell Video adds a local-file video player with a keyboard-controlled browser,
audio/subtitle track selection, and a dedicated HDMI movie output with controls
on the handheld. Build its separate ARM64 package with `make package APP=video`;
outputs are under `artifacts/video/`. The default package command still builds
Tyrell Status.

Version 0.1.3 adds automatic TV audio, removes stale desktop cursor planes, and
keeps decoder-native DMA buffers for HDMI playback. The tested 1920 × 816 H.264
source reached about 24 fps with zero sink drops, including two-line SRT captions.
The user confirmed smooth TV video, matching flash/beep timing, and automatic
video/audio recovery after reconnecting HDMI. Other source profiles and codecs
still need individual qualification.

The HDMI console lease preserves the running desktop and restores it afterward.
A temporary user-session HDMI audio sink supplies TV sound when the stock audio
session lacks one, without changing the system default. See the
[player guide](docs/video-player.md), [audio/video validation](docs/video-av-validation.md),
[desktop handoff validation](docs/video-hdmi-handoff-validation.md), and
[earlier attached-TV validation](docs/video-hdmi-validation.md).

Prebuilt ARM64 packages and checksums are available in the
[Tyrell Video 0.1.3 release](https://github.com/kaini-industries/tyrell/releases/tag/tyrell-video-v0.1.3).

## Requirements

The automated builder currently supports **Apple Silicon macOS 13 or newer** with:

- [Lima](https://lima-vm.io/) 2.x; tested with 2.2.0.
- Python 3.12 or newer, `make`, and `curl`.
- Internet access for the initial Debian image, toolchain packages, and LVGL source download.

The dedicated Debian ARM64 VM defaults to 4 CPUs, 4 GiB RAM, and a 64 GiB sparse virtual disk. Its state lives in the ignored `build/lima/` directory. It has no shared host folders or forwarded SSH agent. Only selected build inputs are copied into it. Existing VMs retain their configuration; set `TYRELL_BUILDER_MEMORY=4GiB` while the VM is stopped to resize it explicitly.

The device package targets **Debian 13 ARM64 / ARMv8-A**, with Cortex-A53 tuning. The application also supports direct CMake builds and an optional SDL simulator; see the [app documentation](packages/tyrell-status/README.md).

## Build

```sh
git clone https://github.com/kaini-industries/tyrell.git
cd tyrell
make package
```

`make package` starts or creates the isolated VM, verifies the pinned LVGL archive, compiles the app, runs tests, creates the `.deb`, and tests installation/removal inside the VM. It does not connect to or deploy onto the CardputerZero.

Other builder commands:

```sh
make builder-up
make builder-info
make builder-stop
python3 scripts/builder image-deps --check
```

Stop the VM when finished to release its memory; the toolchain remains available for the next build.

Generated outputs are local and are not committed:

| Output | Purpose |
| --- | --- |
| `artifacts/tyrell-status_0.1.0-1_arm64.deb` | Installable package |
| `artifacts/manifest.json` | Source revision, environment, checksums, and test status |
| `artifacts/SHA256SUMS` | Exported-file checksums |
| `artifacts/status-system.png`, `artifacts/status-network.png` | Software-rendered previews |
| `artifacts/tests.txt`, `artifacts/install-test.txt` | Test results |

See [build instructions](docs/building.md) for dependency locking and the full artifact inventory. Application source and VM-image inputs are pinned. The builder's APT toolchain is recorded per build; image APT packages use the separate frozen repository described below. Byte-identical rebuilds are not claimed.

## Build a Debian image

After building or retaining the verified Tyrell Status package, select an
existing storage directory with enough free space. Replace the example drive
path with your own. Materialize the checked-in lock once into a new directory:

```sh
export TYRELL_IMAGE_STORAGE=/Volumes/BUILD_DRIVE/tyrell-build
python3 image/apt-lock.py materialize --lock locks/image-apt.json \
  --output "$TYRELL_IMAGE_STORAGE/apt-repository"
make image
```

The default profile verifies **1,679 OS package identities and their bytes**.
Tyrell Status is verified separately, so app iteration does not require the
original app binary. Later builds reuse the repository; missing or changed
inputs fail explicitly. New VMs receive the required image tools automatically.
For an existing package-only builder, run `python3 scripts/builder image-deps`
before its first image build.

This uses the same ARM64 builder to run pinned vendor `pi-gen` stages 0–4,
install Tyrell Status in the final stage, and inspect the exported image offline.
It produces an `.img.xz` file and validation/provenance records under the local,
ignored `image-artifacts` path. It does not connect to or flash the CardputerZero.

The first image has been built and passed 134 offline checks, including app
execution inside the image and a compressed/raw payload checksum comparison.
It has not been physically booted. See the [image validation report](docs/image-validation.md).
The subsequent [workflow hardening and adversarial review](docs/image-hardening-validation.md)
passed regression tests and real ARM64 builder checks. A complete image rebuild
using the hardened workflow has not yet been run.

Image jobs retain independent inputs and durable results. Inspect, reconnect,
or export completed output without starting another build:

```sh
python3 scripts/builder image status
python3 scripts/builder image logs
python3 scripts/builder image wait
python3 scripts/builder image export
```

Exports include the exact input archive and checksums. Package/image builds and
builder shutdown share locks. Status, logs, and wait require the builder to be
running; they do not start it automatically.

Image builds need substantially more storage than app builds: allow at least
35 GiB free on the VM backing volume, 15 GiB for inputs/exports, additional room
for the measured input bundle, and a 4 GiB internal host reserve. Requirements
are added when paths share a volume. See
[image-building instructions](docs/image-building.md) for external storage,
repository capture/discovery, stage recovery, first-boot provisioning, and the
limits of offline validation. Changing artifact storage does not move the VM
backing disk.

## Install and use

After building, replace `user@cardputer` below with your device's SSH target:

```sh
scp artifacts/tyrell-status_0.1.0-1_arm64.deb user@cardputer:/tmp/
ssh -t user@cardputer 'sudo dpkg --install /tmp/tyrell-status_0.1.0-1_arm64.deb'
```

This installs an additional app without replacing the firmware or launcher. Runtime libraries must satisfy the package's declared dependencies; the tested stock Debian 13 installation already supplied them.

Select **Tyrell Status** in APPLaunch and press Enter. Start graphical mode through the launcher so it can hand over display/input ownership and resume on exit.

| Action | Keys |
| --- | --- |
| System page | `1`, Left, or `Z` |
| Network page | `2`, Right, or `C` |
| Refresh | `R` |
| Return to APPLaunch | `ESC` or `Q` |

Inside an SSH session on the device, display-free commands are available:

```sh
tyrell-status --version
tyrell-status --json
```

To remove only the app:

```sh
sudo dpkg --remove tyrell-status
```

Local SSH details may be kept in `.env` using the names in [.env.example](.env.example). Credentials are not needed for building and are never included in the source bundle or VM.

## Validation

The first package passed four automated CTests, isolated VM installation/removal checks, and live tests on the CardputerZero:

- Correct rendering on the real 320 × 170 RGB565 display.
- Live battery, memory, uptime, and network telemetry.
- System/Network navigation and Q exit using synthetic events through the existing keyboard device.
- Return to the same APPLaunch process without a restart.
- Exactly one package added, with all 12 protected firmware/launcher file hashes unchanged.

Physical keyboard switches, long-running performance, and charging/discharging transitions have not been exercised. The detailed reports distinguish builder tests, live device observations, and remaining checks:

- [Builder validation](docs/build-validation.md)
- [Device installation and validation](docs/device-validation.md)
- [First image validation](docs/image-validation.md)
- [Image workflow hardening and adversarial review](docs/image-hardening-validation.md)

Run the host-side image and builder regression suites without starting the VM:

```sh
python3 -m unittest discover -s image/tests -p 'test_*.py'
python3 -m unittest discover -s builder/tests -p 'test_*.py'
```

Raw test evidence, device captures, credentials, VM state, and build artifacts remain local and are ignored by Git.

## Project layout

```text
builder/                 Debian ARM64 VM configuration, provisioning, and checks
scripts/builder          Host-side VM, package, and image orchestration
image/                   Vendor image recipe, APT replay, job recovery, and verifier
packages/tyrell-status/  C++ application, CMake/CPack files, assets, and tests
locks/                   Pinned application and image package inputs
research/                Firmware source and release research metadata
docs/                    Build guides, hardware notes, validation, and roadmap
```

## Firmware roadmap

The application/package loop is working, and the image recipe builds on the vendor's [pi-gen fork](https://github.com/CardputerZero/pi-gen). Next steps are a complete rebuild with frozen inputs, recovery-media preparation, and physical acceptance on a spare card. Kernel/driver updates, a minimal userspace, replacement launchers, and transactional updates are later work.

Preserve the factory microSD and test OS images on a spare card. Keep the separate M5IOE1/PY32 controller firmware at its working version during initial Linux image work.

Further reading:

- [Development workflow](docs/development-workflow.md)
- [Hardware and recovery](docs/hardware-and-recovery.md)
- [Build-system research](docs/build-system-research.md)
- [Original device baseline](docs/device-baseline.md)
- [Official CardputerZero documentation](https://docs.m5stack.com/en/CardputerZero)

## License

Tyrell's original code and documentation are licensed under the [MIT License](LICENSE). LVGL and other third-party components retain their own licenses; see [third-party notes](packages/tyrell-status/THIRD_PARTY.md).
