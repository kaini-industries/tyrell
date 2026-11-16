# Tyrell Zero native-session decision

Status: selected architecture for implementation, November 15, 2026. No native
session package or new image has been built. This decision supports the
[implementation plan](tyrell-zero-implementation-plan.md) and preserves M5
APPLaunch as the primary interface.

## Preserve M5, replace desktop-dependent integration

Keep the original `applaunch 0.6.105` archive and original M5 executable bytes.
The source revision is `e7f60859fcc8bd867f3b8c4b3acd45dfa82ca39f`. The native
package does not optimize or replace the launcher's UI/render/audio behavior.
It supplies only base boot, session, provisioning, recovery and package-policy
integration.

The exact vendor Wizard requires `raspi-config ... B4`, writes LightDM
configuration and enables a legacy user service. Its preprovisioned
`finish_configured_system()` shortcut also hands off to that user service.
Both routes must be patched in a **private Wizard derivative**. Do not modify
only the normal setup callback or suppress its error.

Build the derivative from the exact release source, pinning recursive
dependencies, assets, patch files and build inputs. Install it under
`/usr/libexec/tyrell-zero/LaunchWizard`; retain vendor resource paths and
`WorkingDirectory=/usr/share/APPLaunch`. Inspect `DT_NEEDED`, dynamic backends,
CLI providers, resource/font/audio lookup and RPATH behavior. Prove these paths
in Z0 before making an image. Vendor-owned Wizard files remain intact.

## One real local PAM session

Use Trixie `greetd 0.10.3-4` on VT7. Its worker sets PAM TTY, seat0/VT and user
class, opens the PAM session, then executes the session command as the account.
Run a native supervisor and M5 APPLaunch directly beneath this child, so normal
applications inherit the real session association. Do not launch APPLaunch
through a separate user-manager unit and assume an imported environment variable
creates a local session.
[Worker source](https://sources.debian.org/src/greetd/0.10.3-4/greetd/src/session/worker.rs/),
[Debian PAM stack](https://sources.debian.org/src/greetd/0.10.3-4/debian/greetd.greetd.pam/).

The proposed root-owned configuration is:

```toml
[terminal]
vt = 7
switch = true

[general]
source_profile = false
runfile = "/run/tyrell-zero/greetd.initial-session"

[default_session]
command = "/usr/libexec/tyrell-zero-session-recovery"
user = "_greetd"

[initial_session]
command = "/usr/libexec/tyrell-zero-session"
user = "CONFIGURED_UID_1000_ACCOUNT"
```

The username is resolved from the validated UID1000 account and encoded by a
configuration writer; commands are fixed literals. Do not interpolate user
input into command strings. Version 0.10.3-4 already prepends its own shell
`exec`; the configuration must not add another one. greetd's initial autologin is a local handheld
policy after successful provisioning; it neither enables SSH nor establishes
a default password. Preserve the existing screensaver/lock behavior.

Use `multi-user.target` with a proposed `tyrell-zero.target` requiring the
boot-state helper and conditionally selecting the Wizard, greetd or fixed
recovery mode. In native-ready mode, the helper provides the validated native
configuration. In recovery mode, it provides a package-owned recovery-only
configuration with **no initial session**. greetd uses one fixed runtime config
path chosen by the root helper, never its packaged interactive-shell default.
Override Wizard dependencies to remove the desktop target
request. Order both UI paths after the boot-state helper, and gate them on
separate root-created, boot-latched mode files. Never run the Wizard and native
launcher as simultaneous framebuffer/input owners.

Only this profile masks the systemd **user** `APPLaunch.service`. Keep user
PipeWire/Pulse/WirePlumber and the existing `/run/user/UID/bus`. The supervisor
waits for this normal bus/audio readiness; it must not create a private
`dbus-run-session`. Preserve relevant groups, keymaps, udev rules and RTKit
configuration.

greetd owns VT7 and conflicts with `getty@tty7.service`. In the profile, disable
automatic VT gettys (`NAutoVTs=0`, `ReserveVT=0`) and prevent the normal tty1 login
shell. greetd initially uses text mode: the supervisor must save, configure and
restore appropriate tty/KD/termios state for the framebuffer application, with
no key echo or competing shell. Check VT switches, child crashes and restoration;
do not assume a new process inherits a usable graphics mode automatically.

## Transactional first boot

The boot-state helper runs before either UI. It creates a root-owned mode under
`/run/tyrell-zero` once per boot and does not reevaluate into autologin merely
because the Wizard changes files later in the same boot.

| Persistent state | Boot route |
| --- | --- |
| Factory setup/rearm markers or valid incomplete migration/configuration journal | Native Wizard; no greetd initial session |
| Valid readiness record, matching account/configuration and no pending setup | greetd/native session |
| Corrupt/inconsistent state without a valid setup-recovery path | Fixed recovery; no silent account overwrite or autologin |

Normal and Imager/preprovisioned paths converge on the same idempotent native
completion:

1. Preserve keyboard-guide ordering and its early UID1000 audio startup. Wait
   for guide exit and existing account-migration cleanup before native startup.
2. Preserve the vendor migration journal. Resolve the configured account by UID,
   validate its normal-user identity, and perform network/SSH/account setup
   through the existing explicit provisioning choices.
   Qualify effective Wi-Fi power saving for normal and Imager-created profiles,
   preserving explicit user choices; an unset/default profile field alone is
   insufficient proof of the required effective state.
3. Replace B4/LightDM configuration and legacy user-unit handoff with atomic,
   root-owned native configuration and expected-state hashes.
4. Write a versioned readiness record after configuration validation. Then
   consume setup markers and disable the first-boot Wizard. If interrupted
   between these actions, remaining markers/journal select the safe completion
   route on the next boot.
5. Request controlled reboot for both normal and preprovisioned completion.
   The next boot validates the account/readiness/configuration and starts greetd.

Neither an existing username nor the old “already configured” shortcut may
bypass incomplete native setup. Explicit rearm/factory-reset requests must be
honored on the next boot through the same dispatcher. Preserve user data unless
the requested reset contract explicitly requires otherwise.

Namespace the private Wizard's **outer apply checkpoint** separately, proposed
as `/var/lib/tyrell-zero/setup-apply.v1.json`. Bind it to the profile/version,
native workflow/step identities and configuration fingerprint. Do not reuse
vendor numeric step positions after replacing desktop/handoff operations.
Preserve the independent vendor account-migration journal at
`/var/lib/LaunchWizard/account-migration.state`; it remains necessary for safely
resuming a partially renamed account. A legacy or incompatible outer checkpoint
must take an explicit validated migration/recovery path, never skip native
configuration or the readiness commit. Credentials must not enter exported
manifests or logs.

## Supervisor and recovery

The supervisor is a normal-user process inside the active PAM session. It
executes the unchanged M5 binary, waits for it, and provides bounded restart
after unexpected exit. A crash limit transitions to a fixed native recovery
display inside the existing session; it does not busy-loop or open a shell.
Any deliberate session-exit operation terminates the supervisor and lets greetd
perform normal PAM cleanup.

A full session failure goes to greetd's unprivileged fixed recovery display.
That display exposes no login shell, arbitrary commands or automatic-auth IPC.
Recover through authenticated SSH when enabled, orderly reboot, or off-device
repair/reference media. Necessary display/input permissions for `_greetd` are
explicit and narrow; the recovery screen is not a root application.

greetd initial-session authorization is once per boot. Its runfile must survive
greetd restart and must not be deleted to manufacture a second autologin. Put it
outside a runtime directory that is removed whenever greetd restarts; the
boot-state helper owns its `/run/tyrell-zero` parent. Only reboot clears it.
[Versioned configuration semantics](https://manpages.debian.org/trixie/greetd/greetd.5.en.html).

## M5 package-management integration

The exact launcher updater uses `APPLaunch.service` restart/health/`MainPID`
operations and directly installs or rolls back launcher packages. Until a
qualified base-update backend exists, mask **`applaunch-updater.service`** in
Zero. Atomically seed its root-owned status with the vendor-supported
`failed:incompatible` result, and provide a profile-owned **Tyrell Zero Updates**
help entry explaining the qualified base-image update path. The unchanged vendor
UI can show “No compatible update”; arbitrary new status strings cannot supply
custom instructions. Do not mask ordinary APT metadata/security maintenance or
pretend a masked unit is healthy.

Ordinary AppStore actions have a separate backend and must remain functional
for qualified compatible packages. The exact Store source gitlink is
`b02db122396a7d33c41c66199ab443437975cfee`. Its GUI derives its privileged backend
executable from `argv[0]`; its package helper has direct-dpkg, APT, removal,
repair and rollback paths. APT pinning alone cannot protect the base.

The selected proposed integration is a registered **dpkg diversion** of only
`/usr/share/APPLaunch/bin/M5CardputerZero-AppStore`:

- Preserve the original executable's exact bytes at the registered diversion
  target `/usr/share/APPLaunch/bin/M5CardputerZero-AppStore.vendor`. Keeping its
  directory also preserves any origin-relative resource lookup.
- Install a profile-owned dispatcher at the original public path. For ordinary
  nonroot UI use, execute the vendor binary with `argv[0]` fixed to that public
  path and the original resource/working-directory behavior. The M5 interface
  remains the vendor UI; the main APPLaunch executable is untouched.
- Implement that exec/argument behavior in a compiled dispatcher; do not depend
  on a nonportable shell `exec -a`. Root UI delegation is not a supported mode.
- Privileged helper invocations return through the dispatcher to a narrowly
  adapted derivative of the exact Store backend. Preserve its request/result
  protocol and existing authorization semantics; reject unsupported root modes.
  Install this private backend at
  `/usr/libexec/tyrell-zero/appstore-package-helper`.
  Root legacy `--install`, `--reinstall`, `--upgrade` and `--uninstall` entry
  points must also be rejected or guarded, not delegated around `--package-helper`.
- Copy candidate packages into root-owned staging before inspection and use
  those same immutable bytes throughout execution. A precheck followed by an
  unchanged helper reading a user-writable path is not sufficient.
- Apply the protected-package/file and dependency-transaction policy to install,
  removal, reconfigure, repair and rollback, including Breaks/Conflicts/Replaces/
  Provides effects. Review exact maintainer-script hashes for qualified package
  versions; unsupported base-affecting/script-unknown operations fail before
  execution with a meaningful compatibility/base-update error.
  Normalize payload paths and symlink aliases, protecting the dispatcher,
  diverted original and policy manifests themselves. Broad `dpkg --configure -a`
  repair is allowed only for an audited app-scoped interrupted transaction;
  unrelated or protected pending packages require base maintenance instead.
- Extend the same protected-base policy to the separate
  `tyrell-zero-power-policy` package, its helpers, selected preset/contract,
  generated tmpfiles/udev overrides and the relevant `raspberrypi-sys-mods`
  configuration. Ordinary application transactions must not replace or remove
  them. Supported base/security maintenance revalidates the effective policy;
  this does not authorize disabling ordinary security updates.
- Preserve diversion/mask ownership across package reinstall, upgrade and failed
  rollback. Protect session-policy removal through the normal application path;
  removal is a base-maintenance operation with a validated replacement/recovery
  route. Do not automatically reenable a desktop-dependent legacy unit on a
  desktop-free image. Capture diversion records and private-original hashes.

This is not arbitrary-package sandboxing. The first release qualifies the
ordinary official/verified native-package lifecycle and rejects unsupported
transactions rather than asserting that every future Store package is safe.
Normal supported application installation must refresh/discover its launcher
entry and permit launch/remove/repair without modifying protected base identity.
The compatible package versions and script review are recorded in a proposed
`locks/tyrell-zero-native-compat.json`.
[Exact Store UI/backend selection](https://github.com/CardputerZero/Store/blob/b02db122396a7d33c41c66199ab443437975cfee/main/ui/appstore.cpp),
[package backend](https://github.com/CardputerZero/Store/blob/b02db122396a7d33c41c66199ab443437975cfee/main/backend/appstore_native.cpp),
[launcher updater interface](https://github.com/CardputerZero/launcher/blob/e7f60859fcc8bd867f3b8c4b3acd45dfa82ca39f/ext_components/cp0_lvgl/src/cp0_launcher_updater.hpp).

## Proof gates

Before the first image is called session-ready, exercise:

- Fresh unprovisioned, renamed-account and Imager/preprovisioned setup.
- Keyboard-guide audio, interrupted migration/config writes, repeated completion,
  rearm/factory reset and missing/corrupt readiness records.
- First native reboot with no LightDM installed; correct UID, seat0, VT7, class
  and active session for APPLaunch and a child application.
- Normal-user runtime bus/audio and UDisks mount/eject without a desktop auth
  agent or broad authorization exemption.
- Launcher crash limits, full-session and greetd restart, retained runfile,
  fixed recovery behavior, no duplicate launcher, no shell and no root app.
- VT/KD/termios state and the existing HDMI unused-VT lease, including getty
  conflicts, hotplug, crashes and restoration to the native session.
- Ordinary AppStore install/remove/reinstall/repair/rollback and protected-base
  rejection on every direct-dpkg/APT route, with immutable staged-byte checks.
- Source/payload identities, declared diversions, vendor-main-byte preservation,
  package reinstall and base-update policy behavior.

Headless unit/chroot tests support this work but do not prove a local seat,
framebuffer, keyboard, media routing or hardware recovery. Use a booted ARM64
test environment and then the spare CardputerZero card as specified in the main
plan; preserve the current card and firmware.
