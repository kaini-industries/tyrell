# Tyrell Zero November 19 baseline refresh

File-image validation passed November 25, 2026. This refresh selects the official M5 release
[`20261119-094529-3b0e6f6`](https://github.com/CardputerZero/pi-gen/releases/tag/20261119-094529-3b0e6f6)
for the separate Tyrell Zero profile. Physical testing remains pending. The
retained September baseline, full-source control, stock-image selection and
their historical acceptance records remain separate.

The [file-image validation record](../research/tyrell-zero-nov19-image-validation.json)
binds the successful fresh attempt, captured inputs, independent host audit,
CI evidence and builder restoration. Physical acceptance remains pending by
the user's request.

## Selected inputs

| Input | Selection |
| --- | --- |
| pi-gen | `3b0e6f61520afb59598b188c803c1b9e8d63737f` |
| Cardputer BSP | `a3fd3e83c0247e16e194e7e40f7fc1f5be933dd4` |
| Kernel | `6.18.39+rpt-rpi-v8`, package `1:6.18.39-1+rpt1` |
| APPLaunch | `0.6.111`, source `fd71a2dc2e30737cadebd47fb0a2ba68406ecfb8` |
| SDK | `1846503d46fa74685b6df4735aad6391d1624a96` |
| libcamera | `0.8.0+rpt20261104-1` |
| Custom packages | Status `0.1.0-1`, session `0.3.1-1`, Store `0.1.1-1`, power policy `0.1.1-1` |

The Zero app and camera manifests are independently pinned. The frozen seed
preserves available stage candidates and records the final installed stage0–2
inventories separately. Build-only packages may leave the final root; their
verified bytes remain available for replay. New locks require actual source
assembly, native package tests, runtime capture and independent verification.

The official compressed image has SHA-256
`27e35401ccb8f46356f45c59f16a4cfaed8423bf61862a3fe449e55405e788b9`.
Its package inventory and signed APT metadata were inspected read-only to select
the refreshed OS packages. This image supplies a comparison baseline; Tyrell
Zero is assembled from the pinned source stages.

The [input audit](../research/tyrell-zero-nov19-input-audit.json) records the
authenticated package/source inputs and fresh source replay. The separate
[pre-image qualification](../research/tyrell-zero-nov19-pipeline.json) binds
459 Linux tests with no skips, 30 actual runner failure/interruption checks,
24,383 independent detached-root checks and two identical BSP rebuilds
(16 modules and 391 overlays each). The runtime has 948 OS packages and four
custom packages. Those results enable construction; exported-image acceptance
is recorded separately.

## Exported file image

Job `20261125T054827270121Z-dbd95ae2`, attempt 1, completed from clean source
commit `9b2f09c9bdf8418558ca5e04d9f4dad0f8291fda`. All 24,402 read-only raw-image
checks passed. A separate host audit rechecked the source/archive/manifests,
copied raw image, XZ bytes and streaming decompressed payload. The raw hashes
before and after verification and the decompressed payload hash agree.

| Output | Bytes | SHA-256 |
| --- | ---: | --- |
| Raw image | 4,294,967,296 | `167943270e90f2f5f8bf125362449181bfa31ce52f5ee91adfdde08284e59455` |
| XZ image | 1,192,730,608 | `d7e6782fe7de4bb00a5d27cc6035497331f0613de44aaae2c1d447ef57d480cb` |

The local export is
`image-artifacts-zero-nov19/tyrell-zero-trixie-arm64-lite.img.xz`, resolving to
the dated export on `/Volumes/garage`. The separate verified raw copy also lives
on that drive. The original three image pointers retain their prior targets.

All 952 installed package identities match the qualified runtime locks. Of
these, 945 match the official image's versions and architectures exactly. The
seven explicit additions are greetd, libcamera-dev, rpi-connect-lite and the
four custom packages. The audit checked all 15 M5 package archives, 40 BSP/camera
input archives and 948 frozen OS DEBs. The three approved Store package payloads
retain their original hashes.

All nine hosted CI suites and the compatibility gate passed for the image's
source commit. Downloaded evidence review checked 952 Python tests and 190
CTest cases with no skips, including the optional Catalog package's signed seed.
These CI counts are separate from the image and pre-image checks above.

The builder returned to `Stopped`; its full package-status bytes match the
stopped backup. Update-unit enablement was restored and no temporary mounts or
loop devices remained. Completed native build caches were checksum-verified
and about 2.5 GB of completed files moved to `/Volumes/garage`. Required source
archives and writable build namespaces remain regular local paths; fresh Wizard
input and generated-job path validation passed after the move.

Vendor finalization may fetch changelogs or optional metadata after restoring
runtime APT sources. The frozen contract covers installed package bytes;
whole-image byte reproducibility remains unqualified.

## Integration behavior

The native Wizard retains the vendor one-shot expansion before committing
readiness. Its unit requires disk-ID setup to complete first. Preparation,
activation and final verification refuse completed disk-ID/Wizard expansion
state or a queued legacy resize, so a previously booted root cannot qualify as
a fresh image. The initial resize enablement is verified in the image; only
its expected self-removing link is excluded from long-lived Store invariants.

The vendor Store UI and original launcher bytes remain pinned. Its guarded
helper retains the exact approved Tyrell package entries. The bulk vendor app
updater is masked because it bypasses that transaction boundary; the reviewed
APT metadata-only service remains available. Normal authenticated maintenance
and the reviewed baseline-refresh workflow remain distinct.

The optional Catalog package advances to `0.1.1` with a newly signed offline
seed bound to the refreshed Store base. The bootstrap key and sequence floor
rotate, retaining the same three approved package/artwork entries. No live
endpoint is configured and Catalog receives no install authority or default
image inclusion.

Large input caches, disposable captures and image outputs live on `/Volumes/garage`.
The builder disk was backed up before expansion from 160 to 224 GiB. Builds use
private roots and frozen repositories; they do not install the Zero packages
into the live builder.

## Acceptance boundaries

File-image qualification and physical acceptance are separate. A successful
file-image report does not establish boot, setup, display, input, networking,
audio, camera, Store GUI, expansion or battery behavior on a Cardputer Zero.
The November BSP changes battery-gauge behavior, so earlier gauge, power and
battery observations do not transfer to this candidate. Baseline power policy
remains selected and additional tuning remains disabled.

A later physical phase requires an identified spare card and the exact exported
candidate. Preserve the original card and firmware. New performance or battery
comparisons also require a matched control using this baseline; the older
control cannot establish gains for the refreshed BSP.
