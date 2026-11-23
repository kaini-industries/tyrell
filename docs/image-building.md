# Building the Debian image

To incorporate a newer official stock OS as a complete vendor-tested base, use
the separate [stock-image update workflow](stock-image-updates.md):
`make stock-check`, then `make image-stock`. The source-build workflow below
continues to use its explicitly frozen baseline.

`make image` builds a new image file from the CardputerZero vendor `pi-gen`
source, with the verified Tyrell Status package preinstalled. All construction
and inspection happen in the existing native ARM64 Linux builder. The workflow
does not read `.env`, connect to the CardputerZero, flash a device, or access its
SD card.

The first build completed with 134 passing offline checks. Its artifact sizes,
checksums and remaining hardware-test limits are recorded in
[image validation](image-validation.md).

## Inputs and output profile

The first profile retains the vendor desktop stages 0–4 and board support:

| Input | Selected baseline |
| --- | --- |
| Vendor `pi-gen` | `5f4c44f3431bf1dfb968c10f7228ad6fa0c66cf4` |
| Debian release | Trixie, ARM64 |
| Kernel and headers | `1:6.18.39-1+rpt1`, running target `6.18.39+rpt-rpi-v8` |
| Board drivers | `75518549a2640486f5d13ea72ca4935b198d7c20` |
| Launcher | APPLaunch `0.6.105` |
| Custom package | Verified local `tyrell-status_0.1.0-1_arm64.deb` |

The vendor baseline already pins and checksums its kernel/header packages.
[The application lock](../image/baseline-apps.json) fixes the launcher, driver
archive, and 14 bundled application assets to versioned URLs and SHA-256 values.
The preparation helper verifies pristine source files before replacing mutable
downloads with those local inputs.

The [camera/audio package lock](../image/camera-packages.json) also retains a
compatible factory-era set: libcamera `0.7.2+rpt20260817-1`, rpicam `1.13.0-1`,
PipeWire `1.4.2-1+rpt3`, Picamera2 `0.3.37-1`, and TensorFlow Lite
`2.20.0-1+rpt1`. Current repository camera packages conflict with the pinned
FactoryTest application's older ABI. A local repository under
`/usr/local/share/tyrell/camera-repo` holds 39 checksum-verified packages; 24
selected packages are installed and held, while related optional packages remain
available at the matching versions. Exact APT preferences prevent mixing in the
newer camera ABI. This compatibility lock remains necessary even when the
rest of the image's APT inputs are frozen.

The default build profile requires a complete local APT repository with an
`apt-lock.json` package/version/architecture/SHA-256 manifest. Every package
and repository index is checked before it enters the input bundle. Frozen
image construction uses that repository through guest loopback HTTP for
debootstrap and stage APT operations, and fails when a needed package is absent.
Normal runtime APT sources are restored in the image after construction; this
does not promise that a future device upgrade will preserve the locked stack.
The builder's toolchain APT environment is recorded separately and is not frozen.

Repository location defaults to `apt-repository/` under image storage, or can
be specified with `--apt-repository PATH`. The repository bytes are local
generated inputs, not files committed to Git. The portable
[image APT lock](../locks/image-apt.json) retains the 1,679 OS package identities,
official/versioned HTTPS locations, byte sizes and SHA-256 values captured from
the first build. Tyrell Status is a separate verified per-job input, so rebuilding
the app does not require reproducing its original package bytes to bootstrap APT.

Materialize the checked-in lock on the host, selecting a new output directory:

```sh
python3 image/apt-lock.py materialize --lock locks/image-apt.json \
  --output /Volumes/BUILD_DRIVE/tyrell-build/apt-repository
python3 image/apt-lock.py verify /Volumes/BUILD_DRIVE/tyrell-build/apt-repository
```

Use the existing storage location appropriate to the machine. Downloaded bytes
are checked against the lock; unavailable historical packages fail explicitly.
Optional repeated `--assets-dir PATH` arguments reuse matching local cached
packages. Materialization preserves an existing repository rather than replacing
it. To discover a changed package
set, explicitly select `--discovery`; this uses moving repositories and marks
the resulting build accordingly. It does not silently fall back from frozen
mode when a lock or package is unavailable.

Tyrell Status is installed in a final `stage4/99-tyrell` substage. Its exact
payload checksum and source revision come from the existing package manifest.
Image metadata is written to `/etc/tyrell-image.json`. Seven selected boot
configuration/assets are hashed before export and checked afterward. The
root partition identifier and initramfs are handled by the vendor export path.

## Storage requirements

Have at least **35 GiB free on the volume backing the VM**, **15 GiB free
where downloads and exported artifacts will be stored**, and a **4 GiB free
internal host reserve** plus 256 MiB for Lima/project metadata. The measured
input bundle, including a full APT repository, needs additional room on both
the output and VM backing volumes. Budgets are added when paths share a
filesystem; two individual free-space checks are not treated as independent
capacity on the same volume. The existing VM has a
64 GiB virtual disk. Full desktop stages, root filesystem copies, and the raw
export use substantially more space than application builds.

If the Mac's internal volume is short of space, placing only the final image on
an external drive is insufficient: the VM's backing disk must also have room.
In the initial build environment, the stopped VM disk was copied to dedicated
external storage, compared byte-for-byte, and boot-checked before the redundant
internal copy was removed. The existing VM identity and toolchain were retained.

Image cache/output location can be selected with `TYRELL_IMAGE_STORAGE`, or the
local ignored `build/image-storage.json` file containing a `path` property. The
configured directory must already exist. Example:

```sh
TYRELL_IMAGE_STORAGE=/Volumes/BUILD_DRIVE/tyrell-build make image
```

The default is `build/image-storage/`. Changing this location does not migrate
the VM backing disk. The preflight checks these volumes and the internal host
reserve. Export checks use measured output/input sizes. Mount external storage
before starting a VM whose disk resides there.
See [relocated-storage recovery](building.md#restore-relocated-build-storage)
when existing cache, VM or image-export symlinks become unavailable.

## Build

New builders receive all image tools during first-boot provisioning. For an
existing package-only builder, explicitly provision and verify the tools before
a frozen build:

```sh
python3 scripts/builder image-deps
python3 scripts/builder image-deps --check
```

This updates the builder tool environment under the shared locks; it does not
build an image. Frozen image jobs only check those tools and export their
package versions, avoiding an implicit global toolchain upgrade.

Use the existing verified package outputs and frozen APT repository, or build
a package first:

```sh
make package
make image
```

If `artifacts/tyrell-status_0.1.0-1_arm64.deb` and its matching `manifest.json`
already exist, run only `make image` to retain that exact tested package.

For a first package-set discovery when no frozen repository is available:

```sh
python3 scripts/builder image --discovery
```

After a discovery build completes, capture a repository inside the VM. The
capture helper requires all five retained stage root filesystems and gathers
both installed packages and transient cached build packages. The output must
be a new directory; missing exact-version package bytes fail capture. Substitute
the discovery job's input path printed by `image status` for `INPUT` below:

```sh
python3 scripts/builder shell sudo python3 INPUT/image/apt-lock.py capture \
  --work-dir /var/tmp/tyrell-image-work/pi-gen/work/tyrell-trixie-arm64 \
  --assets-dir INPUT/assets --output /var/tmp/tyrell-apt-repository
```

Export the repository to a new directory on the configured host image-storage
volume. Replace `/path/to/image-storage` with that existing directory:

```sh
python3 scripts/builder shell sudo tar -C /var/tmp/tyrell-apt-repository -cf - . \
  > /path/to/image-storage/apt-repository.tar
mkdir /path/to/image-storage/apt-repository
tar -xf /path/to/image-storage/apt-repository.tar \
  -C /path/to/image-storage/apt-repository
python3 image/apt-lock.py verify /path/to/image-storage/apt-repository
```

Package identities and file hashes make this a retained-input repository;
reproducible image bytes still require control of timestamps and build tools.

The host helper verifies assets and copies an allowlisted input bundle into the
VM. The guest installs image-building dependencies, prepares the vendor tree,
runs `pi-gen` as root, and validates the raw image. Root privileges are used
inside the builder for chroots and loop mounts; they are not used on the handheld.
The long-running guest job is supervised by a transient systemd unit, so it can
continue independently of an SSH output stream. The default builder RAM is
4 GiB, as used by the successful image build. Existing VM RAM is preserved
unless an explicit `TYRELL_BUILDER_MEMORY` override is applied while stopped;
see [builder configuration](building.md).

The host and guest locks are shared with package builds. A second invocation
checks for an active job before preparing or replacing inputs. Each job receives
a new input directory under `/var/tmp/tyrell-image-jobs/JOB_ID/input`; the host
retains its archive under `jobs/JOB_ID/` in image storage. Recipe hashes are
checked against the captured archive, so concurrent source edits cannot silently
change that job's inputs.

Observe or reconnect to a job without starting another build:

```sh
python3 scripts/builder image status
python3 scripts/builder image logs
python3 scripts/builder image wait
python3 scripts/builder image export
```

`--no-wait` submits a build and returns after launch. `--job JOB_ID` selects a
retained job; `--storage PATH` selects its storage directory. Status, logs and
wait do not start a stopped VM or prepare new inputs. `export` copies completed
verified output without rebuilding and preserves the exact input archive next
to the image. An interrupted wait can be repeated. A result file records the
job ID, archive hash and exit status independently of the transient systemd
unit; it remains available after a VM restart.

The build preserves its work tree at `/var/tmp/tyrell-image-work` in the guest.
It refuses to overwrite an existing tree or continue over leftover mounts.
Inspect a failed build before deliberately removing or renaming its generated
work directory. For the initial successful image, retaining this directory
also preserves the raw image and build logs for investigation.

For a failure during stage2 or stage4, explicit recovery paths are:

```sh
python3 scripts/builder image --resume-from stage2
python3 scripts/builder image --resume-from stage4
```

This verifies the retained vendor revision, unchanged stage0/1 source, completed
stage-completion log, matching build configuration and captured input identity
before reuse. Every source/preparation hash from earlier stages is compared;
per-job input-directory names are normalized before comparison. Both
paths refuse active mounts, preserve the previous prepared source/output, and
recreate generated work from the selected stage onward. Only earlier completed
filesystems are reused. Work created before the retained input-identity marker
was added cannot be resumed and must be preserved separately for a fresh build.
Neither applies changes to an installed device.

## First boot

The image retains the vendor LaunchWizard and keyboard-guide first-boot
markers. Defaults are hostname `tyrell`, English (US) locale/keyboard, and UTC.
The temporary `pi` account has no configured password, root is locked, and SSH
is disabled pending provisioning. There are no saved Wi-Fi profiles or personal
SSH keys. Wi-Fi starts unprovisioned; the vendor's fixed `AE` kernel regulatory
value is replaced with world domain `00`. Configure the intended country and
network during device setup.

No M5IOE1/PY32 controller firmware update is part of this build.

## Offline validation

The verifier accepts a regular image file, attaches a read-only loop device,
and mounts the ext4 root with `ro,noload` and the FAT boot partition read-only.
It does not create full partition copies. Cleanup unmounts children before
detaching the exact loop device, including on failures.

Checks cover:

- DOS partition table, 512 MiB FAT boot partition, ext4 root, valid boundaries
  and matching root/boot partition identifiers.
- Exact Tyrell Status package payload, launcher entry, icon, version and ARM64
  package metadata.
- Pinned kernel/header versions, driver source revision, required overlays,
  selected module filenames and matching module ABI metadata.
- Preserved boot assets, vendor launcher and first-boot wizard setup.
- Uninitialized machine identity, disabled SSH, no saved network profiles,
  authorized keys, shell histories, or provisioned account passwords.
- App version, JSON output and headless rendering inside the image's chroot,
  without binding the builder's `/proc`, `/sys` or hardware devices.
- Read-only filesystem checks and unchanged raw-image checksum after inspection.

Passing these checks establishes image contents and userspace execution. It does
not establish successful CM0 boot or physical display/input/peripheral support.
Those tests require a spare development card; the current card remains untouched.

## Artifacts

After a successful build, the ignored `image-artifacts` symlink points to a
timestamped output directory on the configured storage volume. Outputs include:

- `tyrell-trixie-arm64.img.xz`: compressed SD image.
- `image-manifest.json`, `SHA256SUMS`: inputs, proof status and checksums.
- `image-verification.json`, `image-packages.tsv`: verification evidence and
  the image's installed package inventory.
- `image-status-system.png`, `image-status-network.png` and PPM originals:
  chroot-rendered previews.
- `pi-gen-build.log`, `pi-gen-config.txt`, vendor preparation metadata and
  builder/package input manifests.
- `image-input.tar`, the exact recipe, vendor tree, app and APT inputs used for
  that job, with its checksum included in the export.
- Vendor `.info`, SBOM and block-map outputs when emitted by `pi-gen`.

The raw `.img` remains in the VM's vendor export work directory. The first
completed image used recorded moving APT state plus targeted camera locks;
future frozen-profile builds retain exact Debian/Raspberry Pi package bytes.
The toolchain and image metadata can still vary, and byte-identical rebuilds
have not been demonstrated. This is a source-built custom image, not a remaster of a
downloaded factory image or a byte-identical reproduction of the factory release.
The exported XZ stream is decompressed and its payload checksum compared with
the verified raw image before the final manifest is written.
