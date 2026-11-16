# Tyrell Zero native session

This package supplies root-owned boot/readiness state,
a direct normal-user launcher supervisor in greetd's PAM session, a fixed native
recovery display, native Wizard completion, and inactive systemd/profile configuration.
The integrated build also includes the patched native Wizard GUI; the guarded
Store backend belongs to `tyrell-zero-store`. Its installed integration
manifest says `complete: false`; it therefore routes to recovery and cannot
mark an image session-ready. Never install/activate this base package on the
persistent build VM or current CardputerZero.

Build in the existing Debian ARM64 builder:

```sh
SOURCE_DATE_EPOCH=1794528000 python3 packages/tyrell-zero-session/build-package.py --output-dir /var/tmp/session-artifacts --wizard-binary /verified/LaunchWizard
```

This compiles, tests and assembles a DEB without installation or service
activation. Host-only state tests need Python 3.11+:

```sh
python3 -m unittest discover -s packages/tyrell-zero-session/tests -v
```

Omitting `--wizard-binary` builds an inactive foundation package without the GUI.
Use `make zero-native-wizard` and `make zero-integration WIZARD_ARTIFACTS=...`
from the project root for the source-verified native integration workflow.

## Integration boundary

The package has no maintainer scripts and no enabled units. Its activation
recipe under `/usr/share/tyrell-zero/profile/activation.json` is data for the
image integrator, not an installer. Real image activation must first verify
complete source/resource/runtime and package-guard acceptance, install greetd's
PAM dependencies and qualify `_greetd` display access. The private Wizard runs
only in the root-latched setup mode. Native/recovery use the custom
`tyrell-zero-session.service`, never greetd's packaged shell configuration.

Before a real image may set `complete: true` in
`/usr/share/tyrell-zero/session-integration.json`, record schema `1`, profile
`tyrell-zero`, workflow `native-setup-v1`, APPLaunch version `0.6.105`, a SHA-256
of the actual pre-image qualification record, and an `assets` object containing
`path`/`sha256` for all five exact components:

- `launcher`: `/usr/share/APPLaunch/bin/M5CardputerZero-APPLaunch`, whose vendor
  SHA-256 is fixed in code.
- `wizard`: `/usr/libexec/tyrell-zero/LaunchWizard`.
- `store_dispatcher`: `/usr/share/APPLaunch/bin/M5CardputerZero-AppStore`.
- `store_original`: `/usr/share/APPLaunch/bin/M5CardputerZero-AppStore.vendor`.
- `store_backend`: `/usr/libexec/tyrell-zero/appstore-package-helper`.

All manifests, parent directories and components must be root-owned and not
group/world writable; assets must be executable, nonsymlink, non-set-ID regular
files. The original main and Store binaries are pinned in code. A complete
manifest additionally needs `runtime_files` hashes for the session, recovery,
completion helpers and Store module/policy/catalog. The actual fixed receipt at
`/usr/share/tyrell-zero/pre-image-qualification.json` must match the manifest's
receipt hash, assets and runtime map, with passed session/Wizard/Store suites.
This pre-image receipt explicitly leaves physical hardware unqualified.

## Wizard completion API

The private Wizard's normal and Imager completion paths both use
`/usr/libexec/tyrell-zero-wizard-complete` to write
root-owned `/etc/tyrell-zero/native-account.v1.json` with schema `1`, profile
`tyrell-zero`, `completed: true`, and an `account` object containing `name`,
`uid: 1000`, normal `gid`, and `/home/NAME` as `home`. The helper validates this
against the actual passwd entry; no password or Wi-Fi credentials enter these
records. Wait for keyboard-guide exit and account-migration cleanup first.

The completion helper calls the state API as root. It durably writes
`/var/lib/tyrell-zero/readiness.v1.json`, bound to integration and account hashes.
The state API does not consume factory/rearm markers or reboot. The completion
helper consumes markers and clears its checkpoint last, then requests controlled reboot; interruption
leaves a safe setup/resume route. A native outer checkpoint at
`/var/lib/tyrell-zero/setup-apply.v1.json` uses schema/profile/workflow, a named
step (`network`, `account`, `locale`, `ssh`, `native-config`, `ready`, `reboot`),
and `configuration_sha256` of the replay target for early steps, then the native
account record for final steps. Only matching final
`ready`/`reboot` checkpoints permit the readiness commit. Vendor numeric outer
checkpoints require explicit recovery; the independent eight-line vendor
account-migration journal remains preserved.

## Runtime guarantees and remaining proof

The root helper creates one boot-latched route and literal greetd configuration
under `/run/tyrell-zero`. Setup cannot become autologin during the same boot.
Helper/greetd restarts retain the initial-session authorization runfile. Missing,
corrupt or incompatible state uses recovery; a changed latched account or asset
blocks restart. No account mutation, service activation or reset is performed by
the helper.

The supervisor requires UID1000, a real active local `seat0`/VT7 user session,
normal user bus/audio sockets, and a single-session lock. It executes the pinned
launcher directly, retains the PAM/POSIX session, owns a separate child process
group and gives it the foreground VT. Linux child-subreaper adoption and pidfd
signals also clean up native apps that the vendor starts in another process
group, including after a launcher crash. Cleanup is bounded and scoped to its
own descendants; unsupported tracking fails before launcher startup. It restores KD/termios/foreground state,
limits crash restarts, and uses fixed recovery after the crash budget. It does
not create a private D-Bus session or shell fallback. Recovery renders once to
the framebuffer and waits for signals, with a fixed text fallback if access is
unavailable.

Unit and renderer tests are not hardware acceptance. Real first boot, active
seat/framebuffer/input, `_greetd` recovery permissions, HDMI VT leasing, audio,
USB authorization still require the complete
integration and spare-card gates in revision 3. This package does not claim
Z0/Z1 completion or battery benefit by itself.
