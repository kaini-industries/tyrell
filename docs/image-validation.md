# First custom Debian image validation

Completed November 13, 2026 using the existing native ARM64 Lima builder. The
CardputerZero was not contacted, booted, flashed or written to during this work.

## Built artifact

| Item | Result |
| --- | --- |
| Compressed image | Local `image-artifacts/tyrell-trixie-arm64.img.xz` |
| Compressed size | 2,001,517,920 bytes (approximately 1.86 GiB) |
| Compressed SHA-256 | `f35b289ab5b84fbd7a38a642f60a36a8585b48073d51381a787905b84fec3a96` |
| Raw image size | 7,516,192,768 bytes (7.00 GiB) |
| Raw / decompressed payload SHA-256 | `d7dc6fb4b540e19bf9b7ecd3890238188764ebdb3544fd7264dedf7cca97ae76` |
| Vendor pi-gen | `5f4c44f3431bf1dfb968c10f7228ad6fa0c66cf4` |
| Kernel | `6.18.39+rpt-rpi-v8`, package `1:6.18.39-1+rpt1` |
| Board drivers | `75518549a2640486f5d13ea72ca4935b198d7c20` |
| APPLaunch | `0.6.105` |
| Tyrell Status | `0.1.0-1`, source `src-aafb85a69389` |
| App package SHA-256 | `9f8ad2f3562c251ee9cb2c3c1b1292bb2d3fce069e257a55fb60cf656c1396bb` |

The compressed image is a source-built vendor stages0–4 desktop profile with
Tyrell Status installed in the final stage. It is not a repackaged factory-image
download. The original verified application package was reused unchanged.

## Validation

All **134 offline checks passed**: partition table, FAT/ext4 filesystems, root
identifiers, exact application payload, launcher entry, pinned kernel/headers,
module ABI and overlays, selected boot hashes, first-boot wizard, disabled SSH
and unprovisioned identity. App version, JSON and headless rendering passed in
a read-only-mounted image chroot without host procfs/sysfs or hardware binds.

The raw image hash was unchanged by inspection. The XZ stream decompresses to
the same verified raw-image hash. Exported artifact checksums were verified on
the host. All captured recipe files match their recorded hashes in the retained
input bundle. The installed package inventory matches all 24 selected
camera/audio versions recorded by the image.

## Compatibility and environment

The initial attempt exposed current-repository libcamera/PipeWire dependency
drift against the older vendor FactoryTest package. The recipe now retains 39
verified factory-compatible camera/audio packages in a local APT repository,
with 24 selected packages installed and held. Other APT state is recorded rather
than entirely snapshot-frozen; byte-identical rebuilds are not claimed.

Completed stages were reused only after checking revision, source preparation,
stage-completion logs and matching configuration. The final job ran detached
under systemd to survive SSH/output interruptions.

The existing VM disk was moved to dedicated external storage, byte-compared and
boot-checked before its redundant internal copy was removed. Its identity and
toolchain were retained, and its RAM allocation was reduced to 4 GiB. Only
redundant project-generated outputs and its original Debian download cache were
removed to recover internal working space.

The captured image recipe is authoritative for this artifact. Later working-tree
edits can differ from that snapshot; the retained `image-input.tar` and recorded
recipe hashes preserve the exact inputs used.

## Limits and evidence

This image has **not been physically booted**. Display, keyboard, networking,
battery, audio, camera and sensor behavior under the full image still require
a spare development card and hardware acceptance checks. The current device's
firmware and SD card remain untouched.

Evidence is local under `image-artifacts/`: `image-manifest.json`,
`image-verification.json`, `image-packages.tsv`, `SHA256SUMS`, image previews,
`pi-gen-build.log`, configuration and input/package manifests. Generated files
are ignored by Git. See [image build instructions](image-building.md).
