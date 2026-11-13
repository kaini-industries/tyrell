# Building Tyrell Status

The build workflow produces an ARM64 Debian application package. It does not
connect to or deploy onto the CardputerZero. Its installation test runs in the
project's dedicated Linux VM, followed by package removal. A separately
authorized SSH installation and live test has now been completed; see
[device validation](device-validation.md).

## Environment

- Host: Apple Silicon macOS, Lima 2.2.0, Python 3.12+ and `curl`.
- Guest: pinned Debian 13 ARM64 cloud image, VZ virtualization, 4 CPUs, 6 GiB RAM,
  64 GiB sparse virtual disk; native GCC 14 toolchain and CMake.
- VM name: `tyrell-build`; state lives under ignored `build/lima/` through a
  project-specific `LIMA_HOME`. The unrelated `nmt-build` VM is not used.
- No host filesystem mounts, SSH agent forwarding, or automatic guest service
  port forwarding. Lima's local SSH connection is used for copying and building.
- Sources and artifacts are built on the guest's Linux filesystem, rather than
  a macOS shared mount. Only the package, builder and lock directories plus the
  verified LVGL archive are copied in. `.env` is not a build input.

The [Lima configuration](../builder/lima.yaml) fixes the Debian image URL and
SHA-512. Lima verifies the download. [Build inputs](../locks/build-inputs.json)
pin LVGL 9.5.0 to a full source commit and a locally verified archive SHA-256.
The CardputerZero Template commit is retained as the reference for fbdev/evdev
and launcher packaging conventions; this lean app does not require its BSP,
cross-compiler, audio libraries or full UI framework.

## Commands

Run from the project root:

```sh
make builder-up     # Download/provision on first use; start on subsequent use
make package        # Clean native ARM64 build, tests, packaging and VM smoke test
make builder-info   # List only this project's Lima instances
make builder-stop   # Release VM CPU/RAM while retaining its disk and toolchain
```

`make package` also starts the VM if necessary. Initial setup needs Internet
access for the Debian cloud image, APT packages and locked LVGL archive.
Subsequent builds use the cached, checksum-verified archive and installed
toolchain. Nothing is published or deployed automatically.

New exports are checksum-verified before replacing `artifacts/`. The previous
export is preserved under `build/previous-artifacts-*`, so older files do not
silently mix with the current build's manifest.

For read-only inspection of the builder or further development:

```sh
python3 scripts/builder shell uname -m
python3 scripts/builder shell cmake --version
python3 scripts/builder shell bash
```

Always use this wrapper or set `LIMA_HOME` explicitly to the project's
`build/lima` directory. Plain global `limactl` commands use a different inventory.
Changing the checked-in VM template does not automatically migrate an existing
instance; update it deliberately while stopped or create a separately named
instance. Do not delete a VM that holds work you have not exported.

## Build and package behavior

The host helper verifies the LVGL archive and copies an allowlisted source
bundle into `/var/tmp/tyrell-input` in the VM. The guest build script recreates
its own generated build/dependency/artifact directories, compiles for ARMv8-A
with Cortex-A53 tuning, and records a source-content revision. It never uses
`-march=native`, which could require instructions absent from the CM0.

The UI uses a statically linked LVGL with built-in fonts. Runtime dependencies
are normal Debian libraries: `libc6`, `libgcc-s1` and `libstdc++6`, determined by
`dpkg-shlibdeps`. The first package requires libc >=2.38 and libstdc++ >=13.1,
within the Debian Trixie baseline. This is ABI evidence, not a physical-device
acceptance test.

The package installs `/usr/bin/tyrell-status`, unique APPLaunch and desktop
entries, its icon, and license/documentation files. It has no services,
maintainer scripts, autostart entries, boot files, kernel modules or configuration
replacements. Hardware mode opens the existing framebuffer and keyboard using
normal user permissions. It neither grabs input exclusively nor modifies
persistent configuration. ESC/Q exits the process for APPLaunch to resume.

## Validation and outputs

The build runs four CTests: telemetry fixture scenarios, CLI version, CLI help,
and real LVGL headless rendering. Fixture scenarios exercise valid, missing,
malformed, overflow and zero-valued telemetry. Package checks verify ARM64
metadata and expected files, reject maintainer scripts and firmware/config
payloads, install only in the dedicated VM, run JSON/render smoke tests, remove
the package, and check that its launcher entry and binary are gone.

Generated files are under ignored `artifacts/`:

| File | Purpose |
| --- | --- |
| `tyrell-status_0.1.0-1_arm64.deb` | First custom package |
| `manifest.json`, `source-manifest.json`, `build-inputs.json` | Source hashes, revisions, environment and proof status |
| `SHA256SUMS` | Checksums for the exported files |
| `tests.txt`, `install-test.txt` | Automated test and VM install/remove results |
| `package-info.txt`, `package-contents.txt` | Package metadata and file inventory |
| `elf-file.txt`, `elf-report.txt` | AArch64 ELF, runtime dependencies and symbol requirements |
| `builder-packages.tsv` | Exact installed package/toolchain versions |
| `status-system.png`, `status-network.png`, `status-installed.png` | Software-rendered 320×170 previews, plus original PPMs |
| `builder-status.json`, `installed-status.json` | Read-only telemetry from the VM |

Verify exported checksums on the host:

```sh
cd artifacts
shasum -a 256 -c SHA256SUMS
```

Previews and JSON from this build describe the **VM**, including an unavailable
battery. They are not measurements of the CardputerZero. `--proc-root` and
`--sys-root` allow offline fixtures. Rendering does not validate the actual LCD,
keyboard, battery, charging or launcher handoff.

Source and cloud-image inputs are pinned; APT remains a recorded moving input.
The per-build package inventory supports comparison, but byte-identical builds
and a fully frozen toolchain are not yet claimed. Hardware installation belongs
on a spare development microSD after backup/recovery preparation. Building and
testing a full factory-compatible OS image remains the next image milestone.
