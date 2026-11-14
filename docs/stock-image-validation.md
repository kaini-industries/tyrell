# Stock-image update validation

This records the **initial pre-review build**. The later
[adversarial review](stock-image-adversarial-review.md) tightened evidence,
firmware-route, credential, and capacity checks. Its automated results and
fresh-VM rebuild limit are documented separately. The historical artifact below
remains paired with its original captured code; its 580-check result does not
certify the later stricter verifier.

Validated November 14, 2026 using fresh independent design, upstream, and
validation agents, followed by implementation review and a real build in the
existing native ARM64 Linux VM. No CardputerZero connection, SD card write,
or physical boot occurred.

## Selected design

The new stock-derived mode pins the complete official image and adds Tyrell
Status offline. This preserves the stock kernel, drivers, firmware, launcher,
applications, camera/audio libraries, and Debian package set together. It
avoids reconstructing the stock image's mutable source-build inputs from
incomplete release metadata. The existing source/frozen-APT mode remains
available with its original locks.

The release selector defaults to stable. At validation time, the latest stable
official image is `20260929-070146-5f4c44f`; November 13 is a prerelease. The
release build-info and asset hashes were checked against the official release
metadata. The real `stock-image.py check` reported `unchanged` for the
checked-in stable lock. Running `refresh` with
`--output build/stock-validation-candidate.json` created a separate candidate
equal to the stable lock without changing the active lock. Its SHA-256 is
`4f978b5ac927ca5daae7f94ee115cf9770ea0db73cf918125a97c6f9e47d7565`.

## Initial automated checks

The complete image host-test suite passed **92 tests**. Coverage includes
release selection and pagination, explicit prerelease selection, metadata
checksum and identity mismatches, atomic lock publication, captured input
tampering, in-tree candidate locks, unsafe archive members, partition bounds,
decompression races, offline installation, package preservation, provisioning
cleanup, failed-unmount retention, and source-build lifecycle regression.
Python compilation, shell syntax, and `git diff --check` also passed.

## Initial real image build

Job: `20261114T175157262974Z-de3d406f`.

| Input | Verified identity |
| --- | --- |
| Official stock image | `2026-09-29-cardputerzero-trixie-arm64.img.xz` |
| Compressed base bytes | `1786957924` |
| Compressed base SHA-256 | `b37a702aea8b5d75015b48c99b8c969efc2636b82757282fb27cf03056788c91` |
| Pristine raw base SHA-256 | `cc95c131e8f77d96d016fac22579fd0a08009e4b821fc4c543fa122fdda84f0f` |
| Kernel | `6.18.39+rpt-rpi-v8`, package `1:6.18.39-1+rpt1` |
| APPLaunch | `0.6.105` |
| Drivers | `75518549a2640486f5d13ea72ca4935b198d7c20` |
| Added package | `tyrell-status_0.1.0-1_arm64.deb` |

The official compressed image was downloaded to the existing external build
cache and verified before capture. The job retained its selected lock, input
archive, and source hashes. Construction used a new regular image file in an
isolated guest output directory; the original downloaded image and existing
pi-gen work were preserved.

Offline validation passed **580 checks**. The final image preserves **7,145
protected stock paths** and all **1,685 original package identities and states**;
Tyrell Status is the only package addition. The protected snapshot was recorded
before installation and compared again after installation and during independent
read-only verification. Module ABI, launcher integration, exact application
payload, first-boot sanitation, and filesystem checks passed. Cleanup reported
no errors.

Raw derived image size: `7415529472` bytes. Its SHA-256 remained unchanged
through verification:

```text
bb303ee47119814f5f7ea8a51885e9e5f293795544613135d55da0d8e58b0b37
```

The output manifest additionally compares the decompressed exported artifact
against this verified raw digest. Checksums, the pristine stock identity,
preservation evidence, installed-package inventory, and the exact captured
inputs accompany the output.

Export completed successfully at
`/Volumes/garage/tyrell-build/images/20261114T175157262974Z-de3d406f/`;
the workspace's `image-artifacts` link selects it. The prior source-built
export remains in its original directory.

| Output | Verified value |
| --- | --- |
| Compressed image bytes | `1942915112` |
| Compressed image SHA-256 | `9f2ab0fc17c71350b0c468989761541a73a15a9a0a7909ba0f4d7d97bab54136` |
| Captured input archive SHA-256 | `48fd66c666dd7101c64fc906285de847152ae05117525f7af9cbc5f7473835e2` |

The compressed artifact's payload matched the verified raw-image digest.
The builder was shut down after export.

The first export attempt correctly stopped when the Mac's internal volume fell
below the existing 4.25 GiB combined reserve. To retain that guard, the generated
`build/downloads`, `build/game-deps`, and `build/tanks-deps` caches were copied to
`/Volumes/garage/tyrell-build/host-caches/20261114-stock-validation/`, verified
file by file, and replaced at their original paths with symlinks. About 387 MiB
of file contents were preserved; the relocation manifest is retained beside
the caches. These cache paths now require that external volume to be mounted,
as the existing build VM already does. No source files or prior artifacts were
removed.

## Remaining limits

This is an offline validation result. Cold boot, display/input, audio, networking,
camera/sensors, power handling, and Tyrell launch/exit still need spare-card
acceptance for each newly adopted stock release. The workflow supports the
current Trixie ARM64/v5 stock layout and rejects unsupported layouts, provisioning
seeds, missing dependencies, or insufficient filesystem space. It does not
silently expand partitions or fetch replacement packages from live APT.

Use the [stock-image update workflow](stock-image-updates.md) to check, stage,
build, and adopt later stock release locks.
