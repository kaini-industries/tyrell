# Tyrell Zero implementation progress

November 24 baseline and matched-control construction is in progress. Fresh
pre-image requalification passed 24,461 checks and 415 host tests (two platform
skips); the builder is backed up and expanded to 160 GiB. See the
[image validation record](tyrell-zero-image-validation.md) for current outcomes.

The November 23, 2026 increment implements the frozen dependency locks, offline
activation, profile runner and independent verifier. It qualifies a detached
runtime assembly and the pipeline's preparation and failure boundaries. A
complete Zero image has not yet been built or booted. No handheld, firmware or
SD card was accessed.

## Current pipeline increment

The current native inputs are build `20261123T161402256037Z-73a9f781`, with
`tyrell-zero-session 0.3.0-1` and `tyrell-zero-store 0.1.0-1`, plus the retained
Status and baseline power-policy packages. The session adds fixed Updates
guidance and retains the actual native Wizard, boot-latched readiness and
authenticated normal-user PAM supervisor. Package installation remains inactive;
only the explicit offline image integrator selects the Zero session policy.

Zero's independent stage0–2 APT lock contains the exact final 955 OS packages.
Its four separately captured custom packages bring the qualified installed
inventory to 959. The signed Debian Release/index chain authenticates the exact
greetd bytes. The original five-stage seed repository remains unchanged.

The compatibility lock binds native executables, complete library identities,
reachable ELF dependencies, runtime resources/configuration and protected
hardware trees. Mandatory runtime roots must resolve all dependencies. An
unused optional libcaca GL backend has an explicit hash-bound, noneligible
record; it cannot exempt a mandatory runtime dependency. Two clean BSP builds
in private disposable overlay roots reproduced all 16 modules and 391 overlays
exactly, while retained BSP/package/header bytes and the live builder's dpkg
database remained unchanged.

Offline activation validates the frozen installed closure, detaches temporary
runtime content, masks incompatible legacy sessions and the base updater,
configures greetd/PAM policy, captures the final guarded Store baseline and
publishes the activation receipt last. It refuses live, provisioned, redirected
or incompatible roots. The independent verifier checks that resulting root
without relying on activation's acceptance decision. Its raw-image adapter
uses read-only mounts and verifies that the image bytes remain unchanged.
The activated ARM64 root passed all 24,458 independent verification checks.

The runner captures the exact vendor source, recipes, contracts, locks,
packages, repository and evidence. Root-owned guest input copies, isolated work
directories and separate attempt logs/results bind each execution to its
original archive. Terminal stage2 retry requires the completed stage1,
authoritative failure, matching configuration and no leftover mounts. Export
checks the durable guest manifest and original archive before publishing the
independent `image-artifacts-zero` pointer.

The actual ARM64 wrapper passed 30 assertions covering deliberate child failure,
replay/result preservation, changed recipe/archive bytes, environment mismatch
and durable interruption. These are failure-only fixtures; they do not stand
in for a successful full image build. The machine-readable
[pipeline qualification](../research/tyrell-zero-pipeline.json) records final
test counts, source hashes and exact evidence references. The
[pipeline workflow](tyrell-zero-pipeline.md) documents capture, qualification,
preparation, build, observation, retry and export.

## Available now

- `make zero-check` inspects the profile without starting the VM. Build readiness
  requires matching artifact bytes, coherent locks, qualified source/runtime
  receipts and implementation/test hashes. Enabled or complete flags alone do
  not satisfy the gate. Missing local generated artifacts keep a fresh checkout
  blocked until those exact inputs are restored or requalified.
- `make zero-prepare` captures a frozen job without starting the VM. Explicit
  prepared-job status remains available before an attempt starts.
- `make image-zero` selects the baseline Zero profile. The profile-aware CLI
  supports attempt-specific status/logs/wait/export and terminal stage2 retry.
- `make zero-native-wizard` and `make zero-integration WIZARD_ARTIFACTS=...`
  build the native Wizard/session and Store packages on the existing ARM64
  builder. `make zero-packages` builds foundation
  session/power packages; generated outputs and captured sources remain local
  development artifacts.

The [source audit](../locks/tyrell-zero-session-source.json) covers the seven
selected recursive Wizard archives, inspected build/fetch definitions, own
Store guard sources and exact pinned binary/resource imports. It does not claim
that unused launcher monorepo gitlinks were recursively checked out or built.
The audit's runtime binding is distinct from assembled-image and hardware
acceptance. The [greetd record](../locks/tyrell-zero-greetd.json) likewise records
authenticated package/runtime qualification without claiming a device boot.

## Power and hardware boundaries

The power-policy package validates four presets and generation-bound
bookkeeping. Baseline application is an explicit no-op; optimized application
still exits 78. No active tuning units, sysfs writes or power-saving claims are
introduced. Normal authenticated security maintenance remains available.

Headless package, activation and verifier tests do not establish actual account
provisioning, PAM/VT/seat behavior, framebuffer/HDMI rendering, Wi-Fi/audio,
Store interaction or battery performance on the handheld. Those remain
spare-card acceptance work.

## Retained historical evidence

The November 16 native integration build
`20261116T030615312119Z-9c96e960` produced session `0.2.0-1` and Store `0.1.0-1`.
Its six native CTest targets and nine disposable dispatcher/dpkg lifecycle
checks passed; the live builder database remained unchanged. Its original
scope did not qualify the final Zero APT closure or offline image activation.
The [native integration workflow](tyrell-zero-native-integration.md) and
[implementation record](../research/tyrell-zero-implementation.json) preserve
that evidence as recorded.

The original foundation build `20261116T023454094402Z-0531c7d1` produced session
and power-policy `0.1.0-1`, with native state/recovery and disposable packaging
tests. Its power-policy DEB remains a selected baseline input. Files-only
foundation lifecycle tests used isolated dpkg roots and did not prove dependency
closure; the current runtime qualification uses a complete offline APT assembly.

## Next acceptance gates

The November 23 builder was returned to `Stopped` with 3.76 GiB free. On
November 24 it was backed up and expanded to 160 GiB for image construction.
Fresh jobs still require their measured capacity check. Retained roots and
qualification artifacts are preserved.

1. Build the baseline stage0–2 image from the qualified capture, then pass raw
   exported-image verification and checksum/provenance inspection.
2. Exercise successful image execution and stage2 recovery with actual pi-gen
   output, in addition to the implemented host and native failure fixtures.
3. Boot a spare card and complete native setup/session/Store and the protected
   hardware contract. Measure footprint and battery behavior separately before
   enabling any optimized policy.

The [revision 3 plan](tyrell-zero-implementation-plan.md) remains the full target.
Existing desktop-source and stock-derived image interfaces remain available.
