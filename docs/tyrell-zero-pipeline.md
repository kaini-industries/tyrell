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

The existing five-stage seed lock and repository remain intact. Zero has its
own three-stage repository. Each stage selects one version per package and
architecture; transient candidates remain distinct from the final installed
inventory. Custom packages are captured separately from the OS APT pool.

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
  --power-deb artifacts/tyrell-zero/POWER_JOB/tyrell-zero-power-policy/tyrell-zero-power-policy_0.1.0-1_arm64.deb \
  --seed-repository /Volumes/garage/tyrell-build/apt-repository \
  --retained-root /var/tmp/tyrell-image-work/pi-gen/work/tyrell-trixie-arm64/stage2/rootfs \
  --repository-output /Volumes/garage/tyrell-build/zero/NEW_REPOSITORY
```

The printed artifacts include candidate locks and the disposable guest-root
path. `--write-locks` explicitly selects those candidates for the checkout.
Qualify a fresh detached assembly against the selected locks with
`builder/zero-lock.py qualify-root`; its `--vendor-source` is the pristine
pinned host pi-gen checkout. Failed activation receipts are preserved; corrected
inputs require a fresh disposable assembly.

```sh
python3 builder/zero-lock.py qualify-root \
  --guest-root /var/tmp/tyrell-zero-lock-CAPTURE_JOB/rootfs \
  --vendor-source /Volumes/garage/tyrell-build/sources/pi-gen
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

Preparation validates the inputs and captures the complete file allowlist,
including the pristine vendor source, recipes, contracts, locks, package bytes
and repository. It does not start the VM. This operation is also available
while final pipeline qualification is pending, so captured preparation itself
can be tested without enabling an image build. The printed job can be inspected
with `image status --job JOB_ID`; it remains `prepared` until an attempt starts.

## Build and observe

After input and implementation qualification passes:

Check guest capacity before launch. The qualified November 23 builder has only
3.76 GiB free; a fresh image build requires 30 GiB plus twice the captured bundle
size. Reclaim or expand guest storage first. The runner enforces this capacity
check independently of the input readiness receipt.

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
directories under image storage and publish `image-artifacts-zero`; the existing
stock/source `image-artifacts` link remains independent. Captured inputs and
the original guest manifest accompany the final host manifest and checksums.

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
Zero target, masks incompatible legacy sessions/updater, prepares recovery
permissions, captures the final Store baseline, binds session receipts and
publishes the final activation receipt. It refuses a live, provisioned or
incompatibly activated root. Normal authenticated security maintenance remains
available; base-image updates use the fixed Updates guidance and reviewed image
workflow. Extra power tuning remains disabled.

Host adversarial tests, native package tests, disposable-root qualification and
captured vendor preparation establish this pipeline's implementation boundaries.
The first complete Zero image build, exported-image qualification and spare-card
hardware/battery acceptance retain their own milestones in the
[implementation plan](tyrell-zero-implementation-plan.md).
