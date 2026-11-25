# Bringing stock CardputerZero updates into Tyrell

Use the stock-image build mode to carry a newer official CardputerZero OS into
a Tyrell image. It starts from the complete, checksum-locked official image and
installs the verified Tyrell Status package into a new copy. The stock kernel,
drivers, boot assets, launcher, applications, and camera/audio packages stay
together. No online APT operation runs inside the derived image.

The original `make image` source/frozen-APT workflow remains available for
customizing the OS recipe. Its September baseline and package locks are not
automatically changed by stock-image refreshes.

The [reviewed validation report](stock-image-validation.md) records 114 passing
host tests and a fresh ARM64 stock-derived image with **582 passing offline
checks**. It includes strict ownership and firmware-routing preservation. The
[adversarial review](stock-image-adversarial-review.md) documents the guards and
regressions; the original 580-check pre-review artifact remains historical.

## Tyrell Zero base updates

Official M5 updates remain inputs to future Tyrell Zero releases. Zero uses the
vendor pi-gen recipe and pinned BSP, launcher and native packages, with its own
session/Store integration. Its source profile is not assembled by overlaying
Zero onto the latest complete stock image. `make stock-check` discovers stock
releases; refreshing `image/stock-image.json` does **not** update Zero's profile
or its separate source, dependency and compatibility locks.

Adopting an official release into Zero is a reviewed baseline refresh:

1. Identify the exact vendor release/recipe and compare its launcher, BSP,
   kernel/firmware, native applications and dependency changes with the retained
   Zero baseline. Preserve the old inputs and image for comparison/recovery.
2. Refresh the coherent profile and lock set, including native Wizard source
   adaptations, offline source archives, frozen APT closure, native compatibility
   identities and Store protected-base policy. Review integration assumptions;
   changing a hash alone does not qualify the new component.
3. Build a new immutable candidate through the frozen Zero pipeline. Run source,
   package, image and activation checks against those exact inputs. Do not reuse
   old receipts or silently change retained baseline/control artifacts.
4. Qualify the new exact image on a spare target before hardware-supported
   promotion. Repeat affected peripheral, launcher/session, app/Store and
   recovery checks; redo comparisons when claiming memory or power benefits.

The pinned base intentionally prevents the vendor launcher updater or an app
transaction from independently replacing these integrated components. Ordinary
authenticated security maintenance retains its supported path. A future
in-place base updater would require a separate design and qualification.

The [current pipeline](tyrell-zero-pipeline.md) supports construction and
validation of captured Zero inputs; automatic upstream-to-Zero lock refresh and
an on-device base-update service are not implemented. Tracking vendor releases
remains compatible with Tyrell's apps and M5 support, with a new reviewed
candidate for each adopted baseline. Physical testing remains a separate gate.
The [November 19 refresh](tyrell-zero-nov19-refresh.md) has completed this path
through file-image validation; its physical acceptance remains pending.

## Check and select an upstream release

```sh
make stock-check
```

This queries the official `CardputerZero/pi-gen` releases, verifies the small
release build-info asset, and reports the selected release against
`image/stock-image.json`. It does not download the large OS image, change the
active lock, start the VM, or access the handheld. Stable releases are the
default; drafts are never selected. At implementation time, the latest stable
release is September 29, 2026; the November 13 image is a prerelease.
If the newest stock release changes to an unsupported OS, architecture, or image
format, the check fails explicitly instead of silently selecting an older image.

Write an updated lock to a new path:

```sh
python3 image/stock-image.py refresh --output build/stock-candidate.json
```

The output must not already exist. The lock retains the release, versioned
asset URL, byte size, SHA-256, verified build-info, full recipe and driver
commits, kernel version, and launcher version. Prereleases require explicit
`--include-prereleases`; `--release TAG` selects a specific release subject to
the same channel policy. A normal build never looks up “latest.”

Review and commit a selected lock when adopting a new baseline. Keep old locks
to rebuild prior releases. A GitHub-provided asset digest verifies the selected
release bytes; it is not a separate vendor signature.

## Build a stock-derived image

The host needs `xz` (`brew install xz` on macOS). The existing native ARM64
builder must have its image tools provisioned. Build Tyrell Status if a verified
package is not already available:

```sh
python3 scripts/builder image-deps
make package
make image-stock
```

To use a staged candidate:

```sh
make image-stock STOCK_LOCK=build/stock-candidate.json
```

The equivalent direct command accepts existing lifecycle options:

```sh
python3 scripts/builder image --stock-lock build/stock-candidate.json --no-wait
python3 scripts/builder image status
python3 scripts/builder image logs
python3 scripts/builder image wait
python3 scripts/builder image export
```

Downloads use the configured image-storage volume, with a content-addressed
stock-image cache. The selected lock, official compressed image, Tyrell package,
and image recipe are verified and captured in the retained per-job input
archive. Each stock job has its own guest output directory and does not reuse
or overwrite retained pi-gen work. Source-build discovery, APT-repository, and
stage-resume options cannot be combined with stock mode.

Preflight budgets the known image download and captured archive on the host,
then checks free space inside the VM before transfer/extraction. The guest
budget covers both input copies, the exact XZ raw size, a full raw-equivalent
compressed output, and a 1 GiB reserve. Decompression rechecks output capacity
and refuses to write beyond the measured raw size. Physical free space on the
volume holding the VM disk is checked separately.

## What is verified

Construction happens only on regular image files inside the builder. It checks
the supported partition layout and vendor component versions, records the
protected stock files before changing the copy, and rejects modifications to
their contents, modes, owners, groups, or symlink targets. Firmware routing
through `/etc/alternatives` is included. Protected subtree roots must be real
directories so a future symlinked layout cannot silently weaken the snapshot.
Tyrell is installed offline; missing dependencies fail the build
rather than pulling a different package set from APT. A compact stock filesystem
with insufficient free space fails explicitly and needs a separately reviewed
expansion step.

The derived image applies Tyrell first-boot provisioning: locked bootstrap
credentials, SSH disabled, fresh machine identity, no host SSH keys or saved
network profiles, and the vendor LaunchWizard setup path. These are intentional
changes to stock provisioning. Protected boot and hardware content remains
unchanged.

Offline validation checks stock preservation, module/kernel ABI, expected
launcher and package versions, exact Tyrell package payload, launcher entry,
first-boot settings, filesystem consistency, and cleanup. The final manifest
records both the upstream image identity and the derived output identity.

Offline success is not a physical boot test. For each newly adopted stock
release, boot the resulting image on a spare microSD and check display,
keyboard/modifiers, audio, networking, camera/sensors where fitted, power-off,
and Tyrell launch/exit before treating it as hardware-validated. The workflow
does not flash a card or update the connected CardputerZero.
