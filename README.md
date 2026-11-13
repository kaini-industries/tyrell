# Tyrell

Custom applications and firmware-development tooling for the **M5Stack CardputerZero**, a Raspberry Pi Compute Module 0 Linux handheld.

The first deliverable is **Tyrell Status**, a small system-monitor app built as an ARM64 Debian package. It has been installed and tested on the stock Debian firmware through the existing APPLaunch launcher. Full custom OS image generation is a planned next milestone.

## Tyrell Status

- Two keyboard-controlled pages for uptime, used/total memory, battery state, and network links.
- Native 320 × 170 framebuffer interface with a clear return to APPLaunch.
- Read-only telemetry from Linux procfs/sysfs; no network requests or root requirement.
- JSON output, offline fixtures, and headless LVGL screenshots for testing.
- A Debian package containing only the app, launcher entries, icon, and documentation.

## Requirements

The automated builder currently supports **Apple Silicon macOS 13 or newer** with:

- [Lima](https://lima-vm.io/) 2.x; tested with 2.2.0.
- Python 3.12 or newer, `make`, and `curl`.
- Internet access for the initial Debian image, toolchain packages, and LVGL source download.

The dedicated Debian ARM64 VM uses 4 CPUs, 6 GiB RAM, and a 64 GiB sparse virtual disk. Its state lives in the ignored `build/lima/` directory. It has no shared host folders or forwarded SSH agent. Only selected build inputs are copied into it.

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

See [build instructions](docs/building.md) for the full workflow, dependency locking, and artifact inventory. Source and VM-image inputs are pinned; APT package versions are recorded per build rather than snapshot-frozen. Byte-identical rebuilds are not yet claimed.

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

Raw test evidence, device captures, credentials, VM state, and build artifacts remain local and are ignored by Git.

## Project layout

```text
builder/                 Debian ARM64 VM configuration and package checks
scripts/builder          Host-side VM and build orchestration
packages/tyrell-status/  C++ application, CMake/CPack files, assets, and tests
locks/                   Pinned application build inputs
research/                Firmware source and release research metadata
docs/                    Build guides, hardware notes, validation, and roadmap
```

## Firmware roadmap

The application/package loop is working. The next steps are to prepare and verify recovery media, reproduce a factory-compatible image using the vendor's [pi-gen fork](https://github.com/CardputerZero/pi-gen), then include project packages in that image. Kernel/driver updates, a minimal userspace, replacement launchers, and transactional updates are separate later milestones.

Preserve the factory microSD and test OS images on a spare card. Keep the separate M5IOE1/PY32 controller firmware at its working version during initial Linux image work.

Further reading:

- [Development workflow](docs/development-workflow.md)
- [Hardware and recovery](docs/hardware-and-recovery.md)
- [Build-system research](docs/build-system-research.md)
- [Original device baseline](docs/device-baseline.md)
- [Official CardputerZero documentation](https://docs.m5stack.com/en/CardputerZero)

## License

Tyrell's original code and documentation are licensed under the [MIT License](LICENSE). LVGL and other third-party components retain their own licenses; see [third-party notes](packages/tyrell-status/THIRD_PARTY.md).
