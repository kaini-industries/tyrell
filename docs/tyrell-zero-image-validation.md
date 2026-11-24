# Tyrell Zero baseline image validation

Opened November 24, 2026. The baseline image build and matching full-source
control are **in progress**. Storage preparation, the first-boot configuration correction and fresh runtime
qualification passed. Build, raw-image verification and export outcomes remain
pending in this record.

The machine-readable [validation record](../research/tyrell-zero-image-validation.json)
keeps the input comparison separate from the two construction results. The
existing [pipeline qualification](../research/tyrell-zero-pipeline.json) remains
the current November 24 pre-image receipt; it does not certify a completed
image. The [November 23 receipt](../research/tyrell-zero-pipeline-20261123.json)
is preserved separately.

## Verified input relationship

The November 24 read-only comparison of the checked-in locks found:

| Comparison | Observed result |
| --- | --- |
| Seed and Zero stage0 | All 209 package/version/architecture rows identical |
| Seed and Zero stage1 | All 239 rows identical |
| Seed stage2 within Zero | All 950 identities retained unchanged in Zero's 955-package OS closure |
| Zero additions to seed stage2 | `greetd`, `liblua5.4-0`, `libwireplumber-0.5-0`, `pkexec`, `wireplumber` |
| Full-source stage4 and Zero | 954 common identities, with identical versions, architectures and DEB SHA-256 values |
| Full-source-only OS identities | 725 |
| Zero-only OS identity versus full-source stage4 | `greetd` |

The compared seed APT lock is
`7df91bba2940d04aaa56179ab7320d08107f4dd18dcb420364dc946d9dc5ea8e`;
the Zero APT lock is
`553a1c4c106835c0f9498d43b385ff5535ab4f252ebf472ab2d6f66a50ff5761`.
These are lock relationships, not observations of newly constructed images.
The record preserves this snapshot; final job captures must identify the locks
and package bytes they actually use.

The intended common inputs are vendor pi-gen
`5f4c44f3431bf1dfb968c10f7228ad6fa0c66cf4`, BSP
`75518549a2640486f5d13ea72ca4935b198d7c20`, kernel
`6.18.39+rpt-rpi-v8`, APPLaunch `0.6.105`, and unchanged baseline power settings.
Both images must use the Zero-selected Status DEB SHA-256
`15471a8f605e826d6f81daf3260602042b70b9c19330cb03b0bf839039533b2b`.
The ordinary legacy artifacts currently contain another Status build with the
same `0.1.0-1` version and SHA-256 `86b0f912…`; the control's captured package
must therefore be checked by digest. Full-source desktop packages and Zero's
native session/Store integration are intended differences.

## Storage and first-boot preparation

A complete stopped-state 64 GiB disk backup and instance metadata are preserved
on `garage`, with matching streamed/copy SHA-256. The existing disk was expanded
to 160 GiB; its symlink and inode were preserved. The guest filesystem grew to
168,881,938,432 bytes with 101,385,093,120 bytes free at the capacity check.
No historical root or artifact was deleted. Backup and live package-status
bytes match exactly. The older BSP receipt predates automatic builder updates
on November 23, so its live-builder hash is not a current before/after baseline.

Bootstrap/default package provisioning is suppressed for this existing builder.
Automatic update units are temporarily masked during the jobs and will be
unmasked before the VM returns to Stopped; target-image security maintenance
policy is unchanged.

Vendor export would enable a competing `userconfig.service` dialog on tty8.
The corrected Zero recipe gives native Wizard sole first-boot ownership while
keeping the factory account disabled. Vendor stage2 already locks root; the
final custom stage reasserts that invariant before activation. Fresh selected-lock
qualification passed 24,461 checks, all 23 native Linux activation tests passed,
and 415 host tests passed with two documented platform skips. All selected
package and runtime identities remain unchanged.

## First cold-build correction

The first job, `20261124T165428867682Z-ef80d584`, completed stage1 and failed
naturally during stage2 before any controlled interruption. The vendor requests
`firmware-marvell-prestera-`: the suffix denotes removal, while the frozen index
contains neither that absent package nor references to its name. APT could not
resolve the otherwise unnecessary removal. All positive package requests are
represented by their stage candidates.

Shared frozen preparation now normalizes only that exact pinned removal token,
records its disposition and refuses an unexpectedly installed target. Source
drift or a lock containing the target fails closed. Both Zero and the control
use this preparation. No firmware or selected package bytes changed. An actual
isolated APT simulation reproduced exit 100 before and exit 0 after, preserving
all 4,348 APT/dpkg state entries on the corrected probe. The initial probe changed
only an APT planner log; both probe records disclose it. The failed capture,
result and work remain preserved; the corrected recipe requires a new job.

## Construction and export evidence

Each image entry will bind its job/attempt, captured archive and metadata,
durable result, independent verifier report, installed inventory and exported
manifests/checksums. Record raw size and unchanged before/after inspection hash,
XZ size/hash, and the decompressed payload's equality to the verified raw image.
Record cleanup and final builder state alongside the actual capacity and storage
preparation results. Preserve prior work, artifacts and historical records.

The Zero verifier must report scope `offline-read-only-image` for the actual
raw export. The detached-root check count cannot substitute for that result.
Stage2 recovery using actual pi-gen work has its own pending outcome; the
previous failure-only fixtures retain their recorded scope.

The matched full-source control uses the retained five-stage frozen seed
repository and a fresh preserved work namespace. Its captured metadata must
report frozen APT, the same pinned vendor/native inputs and the exact common
Status payload. Confirm common installed package/native/BSP bytes after both
builds, recording any difference before drawing comparison claims. Frozen
package selection does not by itself establish an isolated network namespace.

Measure rootfs apparent and allocated usage with the method and exclusions
recorded. Raw/XZ sizes describe distribution footprint. The historical November
13 source image used recorded, unfrozen APT and remains a historical reference;
the reviewed November 14 stock export remains the compatibility/recovery
reference. Neither replaces the new matched control.

## Remaining acceptance

The [plan's Z3 gate](tyrell-zero-implementation-plan.md#work-packages-and-release-gates)
requires both new image results. Z4 remains pending spare-card first boot,
provisioning, normal-user PAM/seat/VT and launcher/Store behavior, plus all 15
categories in the [hardware contract](../image/contracts/tyrell-zero-hardware.json).
Unavailable accessories remain untested. Preserve the original card and
controller firmware; identify the intended spare medium before any write,
following the [recovery procedure](hardware-and-recovery.md#preserve-and-restore-using-microsd).

Settled memory/PSS and pressure/swap behavior belong to Z5's matched physical
comparison. Battery qualification follows the separate
[battery protocol](tyrell-zero-battery-validation.md). Baseline remains selected;
extra power-policy promotion, battery savings and runtime improvement remain
unqualified. File-image verification and headless tests do not establish these
physical results.
