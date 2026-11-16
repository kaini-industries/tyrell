# Tyrell Zero implementation progress

Revision 3 implementation started November 15, 2026. The native Wizard/AppStore
increment is now built and tested on the existing ARM64 builder. This is not a
bootable Zero image or completed Z0–Z2 milestone.
This implementation increment performs no access to the Cardputer Zero,
its firmware or its SD card.

## Native integration increment

The current accepted build is `20261116T030615312119Z-9c96e960`, exported under
`artifacts/tyrell-zero-native/20261116T030615312119Z-9c96e960/`. It produces
`tyrell-zero-session 0.2.0-1` with the actual patched native Wizard GUI and
`tyrell-zero-store 0.1.0-1` with the compiled dispatcher and protected helper.
The original M5 main and Store executable identities remain pinned.

The Wizard compiled offline from seven exact source archives, using pinned
Cardputer framebuffer/input defaults with only the cross compiler prefix
removed. Its ARM64 ELF and linked libraries were checked. Both normal and Imager
completion paths use the durable native readiness transaction; setup remains
latched until reboot. The original guide/account migration is preserved.

The Store preserves the vendor GUI/public invocation and uses an explicitly
registered offline diversion. Exact Status/Video catalog bytes, dependencies,
scripts, immutable root staging, base invariants, named triggers, transaction
journals and app-scoped repair/rollback guard package operations. An unfinished
transaction blocks newer operations on the same app until authenticated repair.

All six native CTest targets passed: 36 session tests, 17 Wizard/config tests,
28 Store tests, C++ routing and recovery checks. The disposable root passed nine
real dispatcher/dpkg lifecycle checks, including install/reinstall, Video
upgrade, tampered-input/replay rejection, repair, root-staged rollback after an
induced dpkg failure and modeled interruption, removal/purge and restoration of
the original vendor Store. The live builder's dpkg database hash was unchanged.
Host builder and profile suites passed 55 and 29 tests.

The generated pre-image receipt binds executables, runtime helpers, policy,
catalog and vendor fonts. A modified helper behind an unchanged wrapper was
rejected. The packaged default manifest remains `complete: false`; qualification
artifacts do not activate the VM or device. Tests model the vendor executable
baseline over the builder runtime and do not certify the final Zero APT closure,
actual GUI/account provisioning, seat/hardware or battery performance.

Use the [native integration workflow](tyrell-zero-native-integration.md) to
reproduce these builds. Fresh adversarial reviews corrected stale transaction
replay, FIFO/hardlink handling, rollback durability, source-receipt validation,
guest lock lifetime and the native framebuffer configuration.

## Available now

- `make zero-check` performs read-only profile inspection. It validates the
  stage0–2 profile, pinned vendor identities, fifteen-category hardware contract
  and baseline power contract. `structural_valid: true` is distinct from
  `build_ready: false`; missing implementation/qualification gates are listed.
- `make zero-packages` builds both foundation packages in the existing native
  ARM64 Lima builder. `make zero-session-package` and
  `make zero-power-policy-package` select one. Source transfers use an allowlist
  without device credentials, and each job retains its captured inputs/logs.
- The guest runs the package tests, inspects package contents, rejects maintainer
  activation scripts/service-enable links, and exercises install, reinstall,
  remove and purge in disposable dpkg roots. Their separate dpkg logs remain
  inside those roots. No package is installed into the live builder.
- Exports under `artifacts/tyrell-zero/JOB_ID/` contain the `.deb` files, test
  logs, source/toolchain inventories, manifests and checksums.
  `artifacts/tyrell-zero/LATEST.json` identifies the latest successful build.
  These generated artifacts remain local development outputs.

The original foundation build was `20261116T023454094402Z-0531c7d1`, producing version
`0.1.0-1` ARM64 packages for both components. It passed 25 Linux session unit
tests without skips, 15 power-policy unit tests, three session CTest targets
(state suite, recovery version and render), and both disposable dpkg lifecycles.
Host builder/profile suites passed 36 and 29 tests respectively. Independent
acceptance checked current-source hashes, package checksums/root ownership and
the AArch64 recovery executable. Earlier development snapshots are retained,
but are not the current accepted native integration build.

## Package behavior and boundaries

`tyrell-zero-session` supplies root-owned state/readiness validation, exact
migration-journal parsing, boot-latched native/recovery configuration, a direct
normal-user PAM-session supervisor and a compiled fixed recovery display.
The supervisor validates local logind/VT7/audio readiness, bounds crash restarts,
cleans up owned descendants and restores terminal state. Package installation
does not enable its units, mask the vendor session or configure greetd.

The installed integration manifest is deliberately incomplete. The integrated
packages now supply the real private Wizard and protected Store boundary, but
readiness still requires an actual receipt for all matching payloads in the
assembled image. No placeholder reports that final image complete.
PAM/VT/seat/framebuffer/HDMI behavior still needs the planned boot/spare-card
acceptance; headless tests do not establish it.

`tyrell-zero-power-policy` provides strict baseline/four-preset validation,
read-only inspection and generation-bound invalidation bookkeeping. Baseline
application is an explicit no-op; optimized application exits 78. There are no
active tuning units, rules, timers or sysfs writes in this increment. Production
eligibility/application/restore integration remains future work.

The isolated package roots use `--force-depends` because they are not complete
Debian systems. Their successful lifecycle tests prove files-only packaging,
not dependency closure, image startup, native hardware or battery performance.

## Input freezing started

The [source audit](../locks/tyrell-zero-session-source.json) pins the original
launcher/SDK trees, top-level gitlinks and inspected build/fetch definitions.
Seven recursive native source archives, the patch/config and actual Wizard/Store
builds are now recorded. The audit remains `complete: false` pending final
runtime/resource/APT and assembled-image acceptance.

The exact `greetd 0.10.3-4` ARM64 package was downloaded from Debian and checked
against the existing builder's authenticated APT metadata: SHA-256
`8bf65899d76ca397ae8c64bf336bab65f00ec5e3385944a643e3aa70221935d0`.
It is cached locally, not installed. The
[package record](../locks/tyrell-zero-greetd.json) explicitly leaves the complete
Zero APT closure unqualified.

## Next implementation gates

1. Freeze the final runtime/resource and native compatibility identities; revalidate
   the Wizard and protected Store against that complete filesystem.
2. Add the profile-owned session/updater activation policy and final Store baseline
   snapshot without activating the persistent builder or handheld.
3. Freeze the coherent Zero APT/custom-package/compatibility locks, then add the
   profile-aware stage2 runner, attempt/resume handling and independent verifier.
4. Build the baseline image only after those gates pass. Hardware, selected
   battery-policy and exported-image acceptance stay on a spare card.

Existing desktop-source and stock-derived image modes retain their interfaces.
No Zero image export or power-saving claim is produced by these packages.
The [revision 3 plan](tyrell-zero-implementation-plan.md) remains the full target;
the [implementation record](../research/tyrell-zero-implementation.json) captures
actual build evidence and the still-incomplete milestone status.
