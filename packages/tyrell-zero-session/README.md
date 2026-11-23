# Tyrell Zero native session

Version **0.3.0** supplies root-owned boot/readiness state,
a direct normal-user launcher supervisor in greetd's PAM session, a fixed native
recovery display, native Wizard completion, and inactive systemd/profile configuration.
The integrated build also includes the patched native Wizard GUI; the guarded
Store backend belongs to `tyrell-zero-store`. Its installed integration
manifest says `complete: false`. Package installation therefore cannot mark an
image session-ready or enable the native profile. The separate offline image
activation tool must verify the assembled runtime and publish its bound receipts.
Install/remove tests belong in disposable roots; do not activate this base
package on the persistent build VM or current CardputerZero.

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
image integrator. The repository's `image/zero_activation.py` owns image-only
activation after the frozen runtime and all custom packages have been installed.
It requires a canonical, root-owned offline root with no nested mounts and
checks the source qualification receipt, exact installed package closure,
native executable/resource/library/configuration identities and registered
Store diversion before mutation.

The explicit `--prepare-runtime` action removes detached debootstrap runtime
contents, preserving the `proc`, `sys`, `dev` and `run` parent directories. It
rejects runtime sockets, unsafe ownership, mounts and existing provisioning or
activation records. Normal activation then requires those runtime trees empty.
Neither action mounts an image or starts a service.

Activation selects `multi-user.target` and the Zero target, masks competing
desktop/legacy sessions, installs the logind and updater policies, and grants
the existing unprivileged `_greetd` account only the required `video`/`input`
group membership. The guarded Store baseline is captured after this fixed
configuration; final session qualification and integration receipts bind that
baseline. `/usr/share/tyrell-zero/image-activation.json` is published last.
An interrupted operation leaves session integration incomplete; changed
activated roots require a fresh assembly rather than silently accepting new
identities. The private Wizard runs
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
The original source receipt remains separately preserved at
`/usr/share/tyrell-zero/source-pre-image-qualification.json`; assembled-root
receipts retain its identity instead of inventing new suite results.

## Updates help

Version 0.3.0 adds a fixed, read-only **Tyrell Zero Updates** screen to the
recovery executable. Offline activation creates its APPLaunch entry. It explains
that base/session updates use reviewed images, the known reference card should
be retained, and the guarded Store manages qualified applications. Enter,
Escape or Q on the native keyboard returns to the launcher; confirmations do
not repeat. This screen runs as the normal launcher user and invokes no package
manager or update command.

The incompatible vendor launcher updater is masked and its supported status is
seeded as `failed:incompatible`. Ordinary APT metadata/security maintenance is
preserved; the help screen does not claim a replacement automatic base updater.
Display-free guidance and a software-rendered preview are available for tests:

```sh
tyrell-zero-session-recovery --updates --text
tyrell-zero-session-recovery --updates --render-ppm /tmp/zero-updates.ppm
```

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

ARM64 builder, unit and renderer tests are not ARM64 device qualification.
The Updates screen has software-rendered layout/CLI checks; its physical LCD
and keyboard exit still require spare-card acceptance. Real first boot, active
seat/framebuffer/input, `_greetd` recovery permissions, HDMI VT leasing, audio,
USB authorization still require the complete
integration and spare-card gates in revision 3. This package alone does not
qualify a bootable image, complete the hardware milestones or establish battery
benefit. Earlier accepted 0.1.0/0.2.0 artifacts retain their original evidence.
