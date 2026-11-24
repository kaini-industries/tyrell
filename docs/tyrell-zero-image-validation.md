# Tyrell Zero baseline image validation

Z3 file-image construction and validation passed on November 24, 2026. The Zero
baseline and a fresh full-source control were captured from clean commit
`5adaecb09066d09140db4161e0f45b352a1e63d4`, with the same exact Status, vendor,
kernel and BSP inputs. Both images passed read-only verification, compressed
payload binding and host export checks. The builder is stopped.

The [machine-readable record](../research/tyrell-zero-image-validation.json)
binds captures, durable outcomes, manifests, checksums, comparisons and cleanup.
Generated images remain local:

- `image-artifacts-zero/tyrell-zero-trixie-arm64-lite.img.xz`
- `image-artifacts-control/tyrell-trixie-arm64.img.xz`

The existing `image-artifacts` stock-image pointer is unchanged. No handheld,
firmware or SD card was accessed. The user explicitly selected file validation;
physical acceptance remains deferred.

## Measured results

| Measurement | Zero | Full-source control |
| --- | ---: | ---: |
| Read-only image checks passed | 24,478 | 134 |
| Installed packages | 959 | 1,679 |
| OS packages | 955 | 1,678 |
| Custom packages | 4 | 1 |
| Raw image bytes | 4,328,521,728 | 7,172,259,840 |
| XZ bytes | 1,201,240,412 | 1,935,646,992 |
| Rootfs allocated bytes | 3,106,742,272 | 5,635,518,464 |
| Rootfs apparent bytes | 2,937,382,746 | 5,305,789,644 |
| Boot allocated bytes | 78,512,640 | 91,470,848 |

Rootfs usage was measured on read-only mounts with GNU `du -x -B1 -s`, adding
`--apparent-size` for apparent bytes. Boot was measured separately. The allocated
rootfs is 44.87% smaller in this pair. This measures file footprint; settled RAM,
CPU/performance and battery behavior were not measured.

| Digest | SHA-256 |
| --- | --- |
| Zero raw, before/after inspection and decompressed payload | `207fcf45b222ec834468b6ecbe81cef663b0e709df282052b8450c5b5f85372a` |
| Zero XZ | `ec038dd34456668e3ab5b98bc4280cb9c8a7c5920a433a6b81621eae5369e388` |
| Control raw, before/after inspection and decompressed payload | `e4e0d73d4ec99377dab1c61e321a666a6c574ce519bdf9783a605999155704c0` |
| Control XZ | `ca3e15a91de242396372b8a78cabaac5b1c366e2ced7859577ae53a983b059f9` |

Zero job `20261124T171522036978Z-f7c1a8bc` passed on attempt 2. Control job
`20261124T172859067303Z-cfd88df2` completed one fresh full-source execution.
Both exports retain their captured archives, build inputs, manifests and logs.
Original guest manifests are preserved separately from host augmentation.

## What matched

All 954 common installed packages, including Status, have matching versions,
architectures and input DEB hashes. All 6,949 protected hardware identities match
the lock and each other, with no unlisted hardware entries. FAT's synthetic
ownership/mode is excluded from that comparison; content remains checked.
Original M5 main and the mapped original vendor Store match their pinned bytes
and metadata in both images. The protected boot configuration is identical;
Zero selects baseline policy and applies no optimized tuning.

The scoped custom/vendor payload comparison found 39 additional Zero files and
the intended public Store dispatcher replacement. The original Store survives
as the `.vendor` executable. This comparison does not assert equality of every
mutable configuration file.

The control has 725 installed packages absent from Zero. Zero has five absent
from the installed control: greetd, rpi-connect-lite, session, Store and power
policy. The control retains rpi-connect-lite only as residual configuration.
Its 1,680 inventory rows therefore contain 1,679 installed/held packages plus
one configuration-only record. Candidate counts are not final installed counts.

The seed/Zero locks still share all 209 stage0 and 239 stage1 rows. All 950 seed
stage2 identities remain in Zero's 955-package OS closure; the additions are
greetd, liblua5.4-0, libwireplumber-0.5-0, pkexec and wireplumber. The full-source
stage4 lock contains 1,679 candidates, including transients.

The control used the retained five-stage frozen repository and the exact
Zero-selected Status digest `15471a8f…`, rather than the different same-version
package in ordinary working artifacts. A detached worktree linked the committed
Status snapshot and existing builder state, with private exclusions for those
generated directory links. The capture's recipe hashes match the declared Git
commit. The original fixed guest source workspace was preserved under a dated
name before creating a fresh control.

## Recovery and corrections

A controlled SIGTERM interrupted Zero after completed stage1 at the start of
stage2. Attempt 1 recorded failure durably. Authorized stage2 resume reused the
same archive/configuration, skipped completed stages, preserved the interrupted
tree in quarantine, and produced the accepted attempt-2 image. Both result files,
the authorization receipt and manifest bind the same archive. No captured input
was changed.

Cold construction exposed gaps corrected in this increment:

- Vendor export enabled a competing tty8 userconfig dialog. Zero now gives
  native Wizard exclusive first-boot ownership while retaining the disabled
  factory account. Vendor stage2 already locks root; the final custom stage
  reasserts that invariant after package installation.
- The pinned `firmware-marvell-prestera-` token requests removal of an absent
  package. Frozen APT could not resolve its name. Shared preparation records
  only this exact no-op, verifies absence from the locks, and refuses an
  unexpectedly installed target. No firmware or selected package bytes changed.
- Resetting a failed transient service unloaded it before a redundant stop.
  Both launchers now choose state-appropriate cleanup; Zero durably reconciles
  every proven pre-start failure after attempt persistence.

The original failed jobs and probes remain preserved. The corrected real APT
simulation left all 4,348 APT/dpkg state entries unchanged; its initial probe's
planner-log update is disclosed in the evidence. Current qualification includes
421 host tests with two documented platform skips, 23 native Linux activation
checks, 30 native wrapper checks and 24,461 detached-root checks. The accepted
raw-image counts above are separate from those pre-image checks.

## Storage, cleanup and limits

A full stopped-state 64 GiB builder disk backup and instance metadata are retained
on `garage`. The existing disk was expanded to 160 GiB without replacing its
symlink/inode or deleting historical roots. Stopped-backup and final live package
status bytes match exactly. The older BSP receipt predates November 23 automatic
builder updates and is not used as today's live-state before/after baseline.

Comparison left both raw hashes unchanged and cleaned up all loops/mounts.
Normal update-unit enablement was restored, temporary masks removed, and the
builder returned to `Stopped`, with 44,998,352,896 guest bytes free before shutdown.

Frozen DEB resolution does not imply whole-build network isolation. Vendor
finalization can fetch APT changelog and optional GitHub metadata after runtime
sources are restored. These are not package installation/upgrade transactions;
byte-identical image rebuilds are not claimed.

Z4 spare-card boot, provisioning, PAM/seat/VT, launcher/Store behavior and all 15
[hardware categories](../image/contracts/tyrell-zero-hardware.json) remain
untested physically. Settled memory/PSS and pressure belong to Z5; battery
qualification follows the [separate protocol](tyrell-zero-battery-validation.md).
No runtime, battery-saving or release-hardware claim follows from these file
results. The [implementation plan](tyrell-zero-implementation-plan.md) retains
those later acceptance gates.
