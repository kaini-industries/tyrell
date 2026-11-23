# Tyrell

Native applications, a C++17 3D multiplayer game framework, and firmware-development tooling for the **M5Stack CardputerZero**, a Raspberry Pi Compute Module 0 Linux handheld.

**Tyrell Status** provides system monitoring, **Tyrell Video** plays local media,
and the **Tyrell Game Framework** supplies shared rendering, input, timing, and
networking for **Sandline Tanks** and **Beacon Arena**. Status and Video have
verified ARM64 Debian packages; Sandline has a native test package integrated
with the stock APPLaunch launcher. The project also builds custom Debian images
from the pinned vendor `pi-gen` baseline, with Tyrell Status preinstalled.

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

Version 0.1.3 added automatic TV audio, removed stale desktop cursor planes, and
retained decoder-native DMA buffers for HDMI playback. The tested 1920 × 816 H.264
source reached about 24 fps with zero sink drops, including two-line SRT captions.
The user confirmed smooth TV video, matching flash/beep timing, and automatic
video/audio recovery after reconnecting HDMI. Other source profiles and codecs
still need individual qualification.

Version 0.1.4 scales handheld video before RGB565 conversion and blends captions
after scaling. The tested HD movie now delivers 24 fps with captions on the LCD
renderer, compared with about 6.5 fps before deferred blending. See the
[hardware performance report](docs/video-performance-validation.md) for CPU use,
measurement limits and the known anamorphic V4L2 decoder limitation.

Version 0.1.5 raises the tested native screen's physical transfer cadence from
about 12 to 24 full-frame equivalents per second, with user-confirmed smooth
handheld playback. It also preserves fresh playback time when pausing before a
caption change. See the [native display report](docs/video-lcd-refresh-validation.md).

Version 0.1.6 replaced the root HDMI helper with a dedicated nonroot service
and Store-compatible install paths; see the
[security and package checks](docs/video-store-policy-validation.md). Its later
[TV qualification](docs/video-hdmi-016-validation.md) confirmed sound, captions,
aspect fitting and hotplug recovery, and completed a ten-minute HD run. It also
exposed a separate KMS buffer-pool startup failure.

Version 0.1.7 adds one software-video retry for that specific HDMI allocation
failure after hardware decoding has produced output. Controlled tests verified
position/pause preservation, a bounded retry and safe LCD fallback after a second
failure. The software-decoded 1920 × 816 H.264/AAC profile completed more than
ten minutes of media with user-confirmed smooth TV motion and matching sound.
The exact package passed 35 ARM64 tests, install/remove checks, connected crash
recovery and normal APPLaunch launch/exit. See the
[0.1.7 report](docs/video-hdmi-017-validation.md) for measured limits; these results
do not promise smooth playback of every source profile.

The [0.1.7 submission snapshot](store/tyrell-video/0.1.7/README.md) includes the
listing, icon, UI previews, permission declarations, qualification records,
review notes and a local handoff archive builder. The exact package was
[accepted through Store PR #324](https://github.com/CardputerZero/packages/pull/324),
merged on November 15, 2026, and promoted to the official
[`apt-pool` release](https://github.com/CardputerZero/packages/releases/tag/apt-pool).
The [current publication record](store/tyrell-video/README.md) verifies its approved
generated catalog entry and exact package identity. The reviewed real-device
recording is linked in the
[PR conversation](https://github.com/CardputerZero/packages/pull/324#issuecomment-6890010512).

The HDMI console lease preserves the running desktop and restores it afterward.
A temporary user-session HDMI audio sink supplies TV sound when the stock audio
session lacks one, without changing the system default. See the
[player guide](docs/video-player.md), [audio/video validation](docs/video-av-validation.md),
[desktop handoff validation](docs/video-hdmi-handoff-validation.md), and
[earlier attached-TV validation](docs/video-hdmi-validation.md).

ARM64 package snapshots are retained in [`dist/`](dist/README.md), with checksums,
source provenance and test results. Video 0.1.7 is qualified for the tested
hardware and source profile:

- [Tyrell Video 0.1.7 `.deb`](dist/tyrell-video/0.1.7/tyrell-video_0.1.7-1_arm64.deb)
- [Tyrell Status 0.1.0 `.deb`](dist/tyrell-status/0.1.0/tyrell-status_0.1.0-1_arm64.deb)

The earlier Video 0.1.5 is also available in the
[Tyrell Video 0.1.5 release](https://github.com/kaini-industries/tyrell/releases/tag/tyrell-video-v0.1.5).

## Tyrell Game Framework

[Tyrell Game Framework](packages/tyrell-game/README.md) provides reusable C++17
runtime, 3D rendering, fixed-step timing, input, and ENet transport modules.
It supports desktop, native CardputerZero LCD, and headless software builds,
using pinned raylib 6.2 and ENet 1.3.18 sources. Sandline consumes these modules;
the independent Beacon Arena example has its own simulation and packet schema.
The following commands build, test, and run Beacon Arena on the desktop:

```sh
make game-deps
make game
make game-test
python3 scripts/game run
```

Make targets accept `GAME_BACKEND=headless`, `network`, or `core` for smaller
builds and tests. Direct script commands use `--backend`; for example:

```sh
python3 scripts/game deps --backend headless
python3 scripts/game build --backend headless
python3 scripts/game test --backend headless
python3 scripts/game run --backend headless -- --autoplay --frames 30
```

The `native` backend builds on Linux for the Zero's 320 × 170 LCD and keyboard.
Public targets are `Tyrell::GameCore`, `Tyrell::GameRender`,
`Tyrell::GameRuntime`, and `Tyrell::GameNetwork`. See the
[architecture and ownership guide](docs/game-framework.md).

Desktop, headless, native ARM64, real UDP, sanitizer, and native package checks
passed. The [validation record](docs/game-framework-validation.md) and
[adversarial review](docs/game-framework-adversarial-review.md) document the
test matrix, confirmed defects and fixes, and hardware acceptance limits.

## Sandline / Tyrell Tanks prototype

The [tank game](packages/tyrell-tanks/README.md) is a C++17 framework consumer
supporting desktop, native LCD, and headless builds: four-player LAN
hosting/joining, a low-poly arena, independent turrets, shells, damage and
respawning. Build the desktop game with
`python3 scripts/tanks deps` followed by `make tanks`; run with
`python3 scripts/tanks run`. It includes separate native Linux software/GLES2
[display benchmarks](docs/tanks-display-benchmark.md) for the CardputerZero.
The native-LCD game is packaged for ARM64 as **Sandline Tanks** in APPLaunch.
The [installation validation](docs/tanks-install-validation.md) covers the
earlier installed test binary. The reviewed framework build passed ARM64
memory-rendering tests and still needs its own physical hardware acceptance.

## Tyrell Chess

[Tyrell Chess](packages/tyrell-chess/README.md) ports Cardputer ADV Chess to the
Zero's native Linux display, keyboard and stock launcher. It adds a colorful
pixel-art interface, asynchronous computer play, Chess960, clocked local/LAN
matches, puzzle categories, checked saves, full offline replay and PGN export.
Ordered keyboard input preserves rapid taps across display frames.
The installed [0.1.1 update](docs/chess-ui-input-validation.md) adds spatial and
held-key navigation, safe LAN confirmations and clearer game-state feedback.
Current [0.1.2 source](docs/chess-clock-validation.md) corrects clock-expiry
handling; ARM64 package and device qualification remain pending for that version.

Build/test locally with `make chess` and `make chess-test`; `make chess-package`
creates an ARM64 app-only Debian package in the isolated builder. See the
[validation report](docs/chess-validation.md) for hardware tests, independent
rules comparisons and adversarial fixes.

## Requirements

Game and framework development builds directly on macOS or Linux with CMake
3.25+, a C/C++ toolchain, Python 3.12+, and curl. Linux desktop builds also need
GLFW's development dependencies; see the [game build guide](packages/tyrell-tanks/README.md#build-on-macos-or-linux).

The isolated Debian package/image builder currently supports
**Apple Silicon macOS 13 or newer** with:

- [Lima](https://lima-vm.io/) 2.x; tested with 2.2.0.
- Python 3.12 or newer, `make`, and `curl`; stock-image builds also require `xz` on the host (`brew install xz`).
- Internet access for the initial Debian image, toolchain packages, and LVGL source download.

The dedicated Debian ARM64 VM defaults to 4 CPUs, 4 GiB RAM, and a 64 GiB sparse virtual disk. Its state lives in the ignored `build/lima/` directory. It has no shared host folders or forwarded SSH agent. Only selected build inputs are copied into it. Existing VMs retain their configuration; set `TYRELL_BUILDER_MEMORY=4GiB` while the VM is stopped to resize it explicitly.

The Status device package targets **Debian 13 ARM64 / ARMv8-A**, with Cortex-A53 tuning. Status also supports direct CMake builds and an optional SDL simulator; see the [app documentation](packages/tyrell-status/README.md).

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

Revision 3 **Tyrell Zero** has separate package, native integration and frozen
stage0–2 pipeline commands:

```sh
make zero-check       # Read-only structure and remaining image-build gates
make zero-packages    # ARM64 foundation packages; disposable install-test roots
make zero-native-deps
make zero-native-wizard
make zero-integration WIZARD_ARTIFACTS=artifacts/tyrell-zero-native/WIZARD_JOB
make zero-prepare     # Capture verified profile inputs; no image build or VM start
# After the input and implementation qualification gates pass:
make image-zero      # Launch the first Zero file-image build in the ARM64 VM
```

These commands keep the handheld and its SD card untouched. The profile gates
image builds on exact dependency/custom-package locks, native runtime receipts
and qualified implementation hashes. The native build uses the actual patched
Wizard GUI and a guarded dispatcher preserving the vendor Store UI. Offline
activation, independent verification and attempt-bound stage2 resume have their
own [frozen pipeline workflow](docs/tyrell-zero-pipeline.md).
See the [native integration workflow](docs/tyrell-zero-native-integration.md) and
[implementation progress](docs/tyrell-zero-progress.md) for package behavior,
test boundaries and next gates.
The first full Zero image build and spare-card hardware acceptance remain
separate milestones.

Generated working exports under `artifacts/` remain local. Verified package
snapshots are committed separately under [`dist/`](dist/README.md).

| Output | Purpose |
| --- | --- |
| `artifacts/tyrell-status_0.1.0-1_arm64.deb` | Installable package |
| `artifacts/manifest.json` | Source revision, environment, checksums, and test status |
| `artifacts/SHA256SUMS` | Exported-file checksums |
| `artifacts/status-system.png`, `artifacts/status-network.png` | Software-rendered previews |
| `artifacts/tests.txt`, `artifacts/install-test.txt` | Test results |

See [build instructions](docs/building.md) for dependency locking and the full artifact inventory. Application source and VM-image inputs are pinned. The builder's APT toolchain is recorded per build; image APT packages use the separate frozen repository described below. Byte-identical rebuilds are not claimed.

## Build a Debian image

Two image modes are available. **Stock-derived builds** carry the official
kernel, drivers, firmware, launcher, apps, and Debian packages forward together,
then install Tyrell Status offline. **Source builds** retain the existing
pi-gen recipe and frozen APT inputs for deeper OS customization.

### Build from an official stock image

After building or retaining a verified Tyrell Status package, provision the
image tools once and build using the checked-in stock lock:

```sh
python3 scripts/builder image-deps
make stock-check
make image-stock
```

`stock-check` reports the latest official stable release without changing the
active lock or downloading the large image. To adopt a later release, create
and review a separate candidate, then select it explicitly:

```sh
python3 image/stock-image.py refresh --output build/stock-candidate.json
make image-stock STOCK_LOCK=build/stock-candidate.json
```

Keep the selected lock in version control for later rebuilds. Prereleases
require `--include-prereleases`. An unsupported newer stock release fails
explicitly instead of silently selecting an older image. Normal builds consume
their saved lock and never resolve “latest.”

Stock builds verify the official image's size and SHA-256, install Tyrell
without live APT, preserve protected vendor files (including permissions,
ownership, and firmware alternative routes) and all stock package versions,
and apply first-boot provisioning with SSH and bootstrap passwords disabled.
Both the host volume and the VM's own free space are checked
before large writes. Each job retains independent inputs and preservation
evidence; it does not reuse or overwrite source-build work.

See the [stock-image workflow](docs/stock-image-updates.md),
[validation results](docs/stock-image-validation.md), and
[adversarial review](docs/stock-image-adversarial-review.md). Physical boot and
peripheral acceptance on a spare card remain required for a newly adopted stock
release. No build command flashes a card or updates the handheld.

### Build from source with frozen APT inputs

After building or retaining the verified Tyrell Status package, select an
existing storage directory with enough free space. Replace the example drive
path with your own. Materialize the checked-in lock once into a new directory:

```sh
export TYRELL_IMAGE_STORAGE=/Volumes/BUILD_DRIVE/tyrell-build
python3 image/apt-lock.py materialize --lock locks/image-apt.json \
  --output "$TYRELL_IMAGE_STORAGE/apt-repository"
make image
```

The source-build profile verifies **1,679 OS package identities and their bytes**.
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

### Manage image jobs

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

Raw device evidence, captures, credentials, VM state and intermediate build
exports remain local and ignored by Git. Curated packages and their build/test
provenance under `dist/` are tracked.

## Project layout

```text
builder/                 Debian ARM64 VM configuration, provisioning, and checks
scripts/builder          Host-side VM, package, and image orchestration
image/                   Vendor image recipe, APT replay, job recovery, and verifier
packages/tyrell-status/  C++ application, CMake/CPack files, assets, and tests
packages/tyrell-video/   Video player, native display/HDMI support, and tests
packages/tyrell-game/    Shared 3D/runtime/network modules and Beacon Arena example
packages/tyrell-tanks/   Sandline game consuming the shared framework
dist/                    Verified versioned ARM64 packages, checksums, and provenance
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

Tyrell's original code and documentation are licensed under the [MIT License](LICENSE).
LVGL, raylib, ENet, and other third-party components retain their own licenses;
see the [Status notices](packages/tyrell-status/THIRD_PARTY.md) and
[game framework notices](packages/tyrell-game/THIRD_PARTY.md).
