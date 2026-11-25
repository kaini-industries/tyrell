# Tyrell Zero frozen image pipeline

The Zero source profile uses vendor pi-gen stages 0–2 and the native Wizard,
PAM session and guarded Store packages. Its baseline power policy remains
unchanged. The build tools create files in the existing ARM64 builder; they do
not connect to the handheld or select an SD card.

## Frozen inputs

| Lock | Purpose |
| --- | --- |
| `tyrell-zero-apt.json` | Profile-specific OS package bytes, independent stage candidates, exact final installed closure, repository indexes and authenticated greetd provenance |
| `tyrell-zero-apps.json` | Independent Status/session/Store/power-policy DEBs and source/qualification evidence |
| `tyrell-zero-native-compat.json` | Exact native executables, reachable ELF dependencies, resources, configuration, hardware trees and supported Store compatibility |
| `tyrell-zero-session-source.json` | Pinned recursive Wizard sources, patch/configuration identities and bound native/runtime qualification |
| `tyrell-zero-build-inputs.json` | Custom package versions and the implementation/test qualification receipt |
| `tyrell-zero-seed-apt.json` | Selected five-stage seed candidates and the separately observed stage0–2 final inventories used to qualify a disposable Zero root |

The original full-image seed lock and repository remain intact. Zero selects
its own seed lock and later captures its own three-stage runtime repository.
Each candidate stage selects one version per package and architecture. The
optional seed `installed_stages` map records exact final inventories for
stages 0–2; each identity must also be a candidate for that stage. This permits
the recipe to remove build-only dependencies without adding unused packages
back into the finished root. Locks without this field retain their existing
behavior. If the field is present, Zero's dependency capture requires an
explicit stage2 final list and compares the retained root against it exactly
before copying any files. Failed source builds and their observations remain
retained separately; adding final-inventory metadata does not qualify a failed
build. Custom packages remain separate from the OS APT pool.

The reviewed November baseline uses profile version 2, the Zero-specific
`image/zero-baseline-apps.json` and `image/zero-camera-packages.json`, and the
separate `tyrell-zero-hardware-nov19.json` contract. The stock/full-image locks
and earlier hardware contract keep their original bytes. Zero caches pristine
vendor source under `sources/pi-gen-COMMIT`; preparation rejects a dirty cache,
wrong commit or redirected directory.

The capture command uses a disposable copy of an existing, pinned stage2 root
inside the ARM64 builder. It authenticates greetd through Debian's signed
Release/index chain, resolves exact retained package bytes, checks APT/dpkg
consistency, installs the custom packages with the explicit Store diversion,
and inventories the complete native runtime. It preserves the builder's prior
running/stopped state and does not install these packages in its live root.

Inspect the available inputs and choose fresh output paths before recapture:

```sh
python3 builder/zero-lock.py capture --help
python3 builder/zero-lock.py capture \
  --integration-artifacts artifacts/tyrell-zero-native/NATIVE_JOB \
  --power-deb artifacts/tyrell-zero/POWER_JOB/tyrell-zero-power-policy/tyrell-zero-power-policy_0.1.1-1_arm64.deb \
  --seed-repository /Volumes/garage/tyrell-build/zero-nov19/seed-apt-repository-v2 \
  --guest-seed-repository /var/tmp/ZERO_SOURCE_JOB/input/apt-repository \
  --retained-root /var/tmp/ZERO_SOURCE_JOB/work/pi-gen/work/SOURCE_IMAGE/stage2/rootfs \
  --repository-output /Volumes/garage/tyrell-build/zero/NEW_REPOSITORY
```

The printed artifacts include candidate locks and the disposable guest-root
path. `--write-locks` explicitly selects those candidates for the checkout.
Use the exact source-job root and guest repository from its verified capture;
the guest default refers to the historical seed and cannot replay the new lock.
Qualify a fresh detached assembly against the selected locks with
`builder/zero-lock.py qualify-root`; its `--vendor-source` is the pristine
pinned host pi-gen checkout. Failed activation receipts are preserved; corrected
inputs require a fresh disposable assembly.

```sh
python3 builder/zero-lock.py qualify-root \
  --guest-root /var/tmp/tyrell-zero-lock-CAPTURE_JOB/rootfs \
  --vendor-source /absolute/path/to/pristine/pi-gen-COMMIT
# A verifier-only correction can inspect the unchanged activated root again:
python3 builder/zero-lock.py verify-root \
  --guest-root /var/tmp/tyrell-zero-lock-CAPTURE_JOB/rootfs \
  --metadata artifacts/tyrell-zero-root-qualification/QUALIFICATION_JOB/image-metadata.json
```

Use the exact root and metadata paths printed by the preceding command. A
read-only recheck preserves metadata, locks and activation receipts; changed
inputs require a fresh assembly.

An existing lock can be replayed using local verified DEBs, without network
resolution or an unconstrained union of stage repositories:

```sh
python3 image/zero-lock.py materialize \
  --lock locks/tyrell-zero-apt.json \
  --output /Volumes/garage/tyrell-build/zero/REPLAY_REPOSITORY \
  --assets-dir /Volumes/garage/tyrell-build/apt-repository/pool \
  --assets-dir build/zero-inputs
python3 image/zero-lock.py verify /Volumes/garage/tyrell-build/zero/REPLAY_REPOSITORY
```

Custom DEBs and their receipts must also be available at their recorded paths.
Rebuilding different bytes requires recapture and requalification, even when a
package's version string is unchanged.

## Inspect and prepare

```sh
make zero-check
python3 scripts/builder image \
  --profile image/profiles/tyrell-zero.json \
  --apt-repository /Volumes/garage/tyrell-build/zero/apt-repository \
  --prepare-only
```

`zero-check` evaluates actual package/receipt bytes, lock relationships and
qualified implementation hashes. A `complete` or enabled flag alone cannot
make a profile build-ready. A missing package, changed helper or stale receipt
keeps the build gate closed. Hardware and battery qualification remain separate.
The implementation proof must bind the selected profile, vendor/camera locks,
hardware/power contracts and seed lock, as well as the critical preparation,
APT replay, activation, runner and verifier helpers. Qualifying only the main
runner cannot authorize changed dependency or vendor preparation code.

Preparation validates the inputs and captures the complete file allowlist,
including the pristine vendor source, recipes, contracts, locks, package bytes
and repository. It does not start the VM. This operation is also available
while final pipeline qualification is pending, so captured preparation itself
can be tested without enabling an image build. The printed job can be inspected
with `image status --job JOB_ID`; it remains `prepared` until an attempt starts.

Fresh-image validation requires the official disk-ID and first-boot chain to
remain unexecuted. It rejects a completed or partial disk-ID journal, a prior
Wizard expansion-attempt marker, and a pending `resize2fs_once` script/link.
The private Wizard requires the disk-ID service and runs after it, so their
partition-table writes cannot overlap. A condition-skipped disk-ID service on
an already initialized card keeps its ordinary systemd semantics.

The initial rpi-resize enablement link remains captured and required before
boot. Because the pinned service disables itself, only that exact link is
omitted from the runtime Store invariant snapshot. Its unit and drop-in bytes,
the disk-ID service/link, and the fstrim mask remain protected. These host and
file-image checks do not replace physical first-boot acceptance.

## Build and observe

After input and implementation qualification passes:

Check guest capacity before launch. The November baseline refresh backed up
the stopped 160 GiB builder before expanding it to 224 GiB. A new
image job requires 30 GB plus twice the captured bundle size. The runner checks
current capacity independently of the input readiness receipt; retained jobs
consume additional space.

```sh
python3 scripts/builder image \
  --profile image/profiles/tyrell-zero.json \
  --apt-repository /Volumes/garage/tyrell-build/zero/apt-repository \
  --policy baseline --no-wait
python3 scripts/builder image status --job JOB_ID
python3 scripts/builder image logs --job JOB_ID --follow
python3 scripts/builder image wait --job JOB_ID
python3 scripts/builder image export --job JOB_ID
```

The guest first copies and verifies the archive into a root-owned job namespace.
Every build has its own work directory; every attempt has a separate log,
durable result and output directory. The systemd environment, result and export
bind the job, attempt number and exact archive hash. Status/wait never infer
failure from an SSH timeout; they reattach to the matching live service or use
its durable result.

The final stage installs the frozen runtime and custom packages, detaches build
runtime mounts, restores authenticated runtime APT sources and activates the
profile offline. Export does not upgrade or autoremove the qualified package
closure. The independent verifier checks the final raw image read-only before
compressed output and manifests can be published.

Outputs use the declared names `tyrell-zero-trixie-arm64-lite.img` and
`tyrell-zero-trixie-arm64-lite.img.xz`. Successful exports live in attempt-specific
directories under image storage. The reviewed November recipe publishes
`image-artifacts-zero-nov19`; captured September jobs retain
`image-artifacts-zero`. The earlier Zero baseline/control and stock/source
`image-artifacts` pointers remain independent. Captured inputs and the original
guest manifest accompany the final host manifest and checksums. Publishing a
candidate does not promote it to a physical-acceptance baseline.

## Resume a failed stage2 attempt

```sh
python3 scripts/builder image resume --job JOB_ID --from stage2 --no-wait
python3 scripts/builder image status --job JOB_ID --attempt 2
python3 scripts/builder image logs --job JOB_ID --attempt 2
```

A retry reuses the original root-owned captured inputs. It requires an
authoritatively terminal failed attempt, completed stage1, matching job/profile/
configuration identities, no leftover mounts, shared locks and fresh capacity
checks. Failed stage2/export trees are quarantined rather than deleted. A
successful earlier attempt cannot certify a later retry. New profile, discovery,
stock or dependency selections and stage4 recovery are rejected; stage0/1
failures require a new job.

## Activation and acceptance boundaries

Package installation remains inactive. Only the offline integrator selects the
Zero target, masks incompatible legacy sessions and unqualified vendor
launcher/app updaters, prepares recovery
permissions, captures the final Store baseline, binds session receipts and
publishes the final activation receipt. It refuses a live, provisioned or
incompatibly activated root. Normal authenticated security maintenance remains
available; base-image updates use the fixed Updates guidance and reviewed image
workflow. Extra power tuning remains disabled.

Host adversarial tests, native package tests, disposable-root qualification and
captured vendor preparation establish this pipeline's implementation boundaries.
The first Zero baseline and matched frozen full-source control have completed
file-image validation, including an actual stage2 interruption/resume. See the
[validation record](tyrell-zero-image-validation.md) for captured identities,
checksums, comparisons and local artifact pointers. Spare-card hardware and
battery acceptance retain their own gates in the
[implementation plan](tyrell-zero-implementation-plan.md).
