# Stock-image update validation

## Reviewed build — November 14, 2026

Fresh job `20261114T182824879955Z-b309a99d` uses reviewed commit
`e4933fa63a638cc1d8041de2039223323c43dacc`. The complete host image suite passed
**114 tests**, and the fresh ARM64 image passed **582 offline checks** with no
cleanup errors. This exercises the stricter checks from the
[adversarial review](stock-image-adversarial-review.md).

An independent archive audit compared all **29 captured recipe files** with
their Git blobs at the reviewed commit and with the archive's recorded hashes.
All matched; no files were missing or different. The build therefore used the
reviewed recipe, rather than relying only on its reported commit identifier.

The final image preserves **7,279 protected paths**, including mode, UID, GID,
symlink targets, and `/etc/alternatives` firmware routing. All **1,685 original
package identities and states** are unchanged; Tyrell Status is the only added
package. The verifier accepted the real-directory protected roots, locked stock
credentials, exact raw-size binding, and module/kernel ABI. No handheld or SD
card was accessed.

### Safe internal-space recovery

The Mac initially had `4090925056` bytes free (about **3.81 GiB**), below the
existing 4.25 GiB combined internal reserve. Downloaded inactive Playwright 1243
browser bundles, Prisma/pnpm download caches, and historical generated Tyrell
test outputs were copied to the existing external build volume. All 27 original
paths were retained as symlinks to verified external copies.

Native `ditto` preserved macOS resource data, extended attributes, quarantine
metadata, and ACLs. File SHA-256, mode, UID/GID, and symlink identities were
checked before removing redundant internal copies. An independent path audit
found no dangling or mismatched links and confirmed that tracked source,
current artifacts, active UV/Codex runtimes, and Playwright 1223 were excluded.

The relocated files total `1168871886` bytes. Observed internal free space rose
to `5270159360` bytes (**4.91 GiB**), an increase of `1179234304` bytes. The
relocation manifest and per-directory identity records remain at:

```text
/Volumes/garage/tyrell-build/host-caches/20261114-reviewed-stock-build/
```

These caches and historical output paths now require the external volume to be
mounted, as the build VM already does. Their contents remain recoverable at the
manifested destinations. The storage guards were not reduced or bypassed.

### Capacity and locked inputs

The new before-transfer guest check required **19,480,699,515 bytes** and found
**24,715,722,752 bytes** available (about 18.1 GiB required versus 23.0 GiB free).
This includes the input archive, extracted inputs, raw image, a raw-equivalent
compressed output, and the 1 GiB guest reserve. The guest also rechecked output
capacity and bounded decompression to exactly **7,415,529,472 bytes**.

| Input | Verified value |
| --- | --- |
| Stock release | `20260929-070146-5f4c44f` |
| Stock lock SHA-256 | `4f978b5ac927ca5daae7f94ee115cf9770ea0db73cf918125a97c6f9e47d7565` |
| Compressed stock SHA-256 | `b37a702aea8b5d75015b48c99b8c969efc2636b82757282fb27cf03056788c91` |
| Pristine raw stock SHA-256 | `cc95c131e8f77d96d016fac22579fd0a08009e4b821fc4c543fa122fdda84f0f` |
| Captured input archive SHA-256 | `82c5a0364401838f9316140fd5220a5f21f0f6dd0aeae63cd8e76a4516c637de` |
| Tyrell Status package SHA-256 | `86b0f912c39f63eade505596e25db34c289fc776b8cdd32b82b73eed546c78e3` |
| Tyrell package source revision | `src-c1322e3aa9ad` |

The raw derived image is `7415529472` bytes. Its SHA-256 was identical before
and after read-only verification:

```text
a1c25eac2a7c3989773666bdd3f883a06d9f455c47ad5bc2312f73650afedff5
```

### Exported reviewed artifact

The complete reviewed build, compressed-payload check, and host export all
succeeded. The compressed file expands to the exact raw digest above.

```text
/Volumes/garage/tyrell-build/images/20261114T182824879955Z-b309a99d/
```

| Output | Verified value |
| --- | --- |
| Compressed file | `tyrell-trixie-arm64.img.xz` |
| Compressed bytes | `1942911472` |
| Compressed SHA-256 | `8acab9a62b18e40ca198406d30d174f2de1442eecb539147a4c7d37b6e71a108` |
| Decompressed payload SHA-256 | `a1c25eac2a7c3989773666bdd3f883a06d9f455c47ad5bc2312f73650afedff5` |

The workspace's `image-artifacts` link now selects this reviewed export.
`image-input.tar`, `image-manifest.json`, `SHA256SUMS`,
`stock-base-verification.json`, `image-verification.json`, and the installed
package inventory accompany it. All exported file hashes were verified; the
historical source and pre-review exports remain in their original directories.
The builder was shut down after export.

Physical cold boot and peripheral acceptance on a spare card remain pending.
Offline verification does not establish that hardware acceptance.

## Historical pre-review build

This records the **initial pre-review build**. The later
[adversarial review](stock-image-adversarial-review.md) tightened evidence,
firmware-route, credential, and capacity checks. The reviewed build above
supersedes the earlier rebuild limit. The historical artifact below
remains paired with its original captured code; its 580-check result does not
certify the later stricter verifier.

Validated November 14, 2026 using fresh independent design, upstream, and
validation agents, followed by implementation review and a real build in the
existing native ARM64 Linux VM. No CardputerZero connection, SD card write,
or physical boot occurred.

### Selected design

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

### Initial automated checks

The complete image host-test suite passed **92 tests**. Coverage includes
release selection and pagination, explicit prerelease selection, metadata
checksum and identity mismatches, atomic lock publication, captured input
tampering, in-tree candidate locks, unsafe archive members, partition bounds,
decompression races, offline installation, package preservation, provisioning
cleanup, failed-unmount retention, and source-build lifecycle regression.
Python compilation, shell syntax, and `git diff --check` also passed.

### Initial real image build

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
at that initial export, the workspace's `image-artifacts` link selected it.
The historical image and prior source-built export remain in their original
directories.

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

### Initial limits

This is an offline validation result. Cold boot, display/input, audio, networking,
camera/sensors, power handling, and Tyrell launch/exit still need spare-card
acceptance for each newly adopted stock release. The workflow supports the
current Trixie ARM64/v5 stock layout and rejects unsupported layouts, provisioning
seeds, missing dependencies, or insufficient filesystem space. It does not
silently expand partitions or fetch replacement packages from live APT.

Use the [stock-image update workflow](stock-image-updates.md) to check, stage,
build, and adopt later stock release locks.
