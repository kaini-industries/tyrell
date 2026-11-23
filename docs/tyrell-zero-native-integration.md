# Native Wizard and AppStore integration

Revision 3 now has a real native Wizard derivative and a guarded vendor Store
boundary. These are inactive ARM64 packages and pre-image tests. They do not
activate a session, build a Zero image, or access the Cardputer Zero or its SD card.
Session 0.3.0 adds fixed native Updates guidance. The separate
[frozen pipeline](tyrell-zero-pipeline.md) qualifies the complete dependency root
and performs image-only activation; installing these packages remains inactive.

## Rebuild in the existing ARM64 builder

```sh
# Explicit fetch verifies seven exact source archives; compilation stays offline.
python3 builder/zero-vendor.py fetch
make zero-native-deps
make zero-native-wizard
# Use the artifact directory printed by the preceding command:
make zero-integration WIZARD_ARTIFACTS=artifacts/tyrell-zero-native/WIZARD_JOB
```

The existing exact vendor `.deb` cache is reused. A different cache location can
be supplied explicitly:

```sh
python3 scripts/builder zero build-integration \
  --wizard-artifacts artifacts/tyrell-zero-native/WIZARD_JOB \
  --vendor-deb /absolute/path/applaunch_0.6.105-m5stack1_arm64.deb
```

The baseline must match SHA-256
`a38cffde9cb4d60f4ff2d6c95323c64f66cc705fdf315b0550e123ca7b5a9544`.
Its original main/Store executables are extracted as regular, hash-checked files;
no vendor maintainer script runs during this host preparation. Status 0.1.0 and
Video 0.1.6/0.1.7 come from the retained `dist/` snapshots. Device credentials,
unrelated packages and application work are excluded from build transfers.

`zero-native-deps` installs only build dependencies in the existing VM. The
session/Store packages are installed only in disposable roots. Source and guest
builder locks serialize builds; sudo's descriptor handling cannot release the
guest lock early. Every job has an immutable input snapshot, retained logs and
checksummed exports under `artifacts/tyrell-zero-native/JOB/`.

## Native Wizard

The actual M5 GUI is built from launcher commit
`e7f60859fcc8bd867f3b8c4b3acd45dfa82ca39f` and SDK commit
`730634b0fec605c2087836fd35b37d3cf21b1a4c`, with five pinned source dependencies.
Only the two exact `.105` Wizard files accept the source patch. The Cardputer
runtime defaults retain framebuffer FULL rendering, evdev, pthread, NEON and
FreeType; only the cross compiler prefix is removed. Generic native defaults
lack the framebuffer backend. Compilation runs inside a fresh network namespace
and the SDK's downloader/Conan paths fail closed.

`tyrell-zero-session 0.3.0-1` installs the derivative at
`/usr/libexec/tyrell-zero/LaunchWizard`. The original packaged Wizard and M5 main
remain untouched. The normal setup path and Imager path use one fixed root
completion helper. The independent keyboard guide/account migration journal
remain vendor implementations. Partial provisioning cannot skip a password
change merely because the username already matches.

Native account, checkpoint and readiness records are durable and versioned.
Marker cleanup occurs only after readiness; the checkpoint is removed last.
Controlled reboot is required before native autologin. A failed reboot can be
retried, and setup never starts the launcher in the current boot. See the
[Wizard transaction details](../packages/tyrell-zero-session/wizard/README.md).

## Guarded AppStore

`tyrell-zero-store 0.1.0-1` owns the public Store dispatcher. The offline image
integrator registers a package-specific diversion before installation, preserving
the exact vendor GUI as `.vendor`. Normal UI execution retains the public
`argv[0]`; privileged package modes use the private helper instead of the
unguarded vendor backend. Package removal followed by explicit offline diversion
restoration recovers the exact original executable.

The first qualified catalog contains only Status 0.1.0-1 and Video 0.1.6-1/0.1.7-1.
Package bytes, payload, control relationships and maintainer scripts are checked.
Unknown packages or missing unqualified dependencies require base maintenance.
There is no general root command, repository override or global `apt -f` repair.

User-owned pending files retain the pinned native protocol. Qualified package
and previous-version rollback bytes are staged under root ownership before
mutation. Durable root journals prevent completed replay and block newer
operations on a package with unfinished work. Repair is scoped to that app.
Base package versions/files and named cache-trigger metadata are checked around
operations. See [Store policy and lifecycle](../packages/tyrell-zero-store/README.md).

## Qualification boundaries

The combined job runs native CTests and real dispatcher/dpkg operations in a
fresh mount/network namespace. Its disposable root uses an overlay over `/usr`,
a copied package database, synthetic locked accounts and empty runtime endpoints.
Package children lose mount, chroot, device creation, module and reboot
capabilities. No host process, service bus, framebuffer or SD device is mounted.
The live builder package database is hashed before and after.

The lifecycle root models the exact vendor executables over the builder's
installed runtime. Integration packages use `--force-depends` for files-only
lifecycle tests; this does not prove runtime dependency closure. A modeled half-installed app state tests real
root-staged rollback after a forced dpkg failure and deletion of user cache.
This is useful transaction proof, not a frozen Zero runtime or physical power cut.

The separate payload qualification binds all five native executables, supporting
Python modules, Store policy/catalog and original vendor font identities to a
real pre-image receipt. Changing a helper behind an unchanged wrapper fails
validation. Exported `qualified-session-integration.json` and
`pre-image-qualification.json` are evidence for these exact payloads; they are
not an image activation command. The DEB itself still ships `complete: false`.

The final Zero image must independently assemble its APT closure and native
compatibility lock, generate its own baseline snapshot/receipt, apply the planned
session/updater activation policy, and pass the offline verifier. Boot, actual
account provisioning, seat/VT, original native apps, all hardware and battery
performance remain spare-card acceptance gates. No battery improvement is
claimed by this package milestone.
