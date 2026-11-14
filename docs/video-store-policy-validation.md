# Tyrell Video Store policy and HDMI helper

November 14, 2026. Version 0.1.6 replaces the root HDMI console helper with a
dedicated nonroot service. Store publication remains a separate step: this
change does not submit an app or make the private source repository public.

## Security boundary

The player remains a normal-user process without capabilities. The helper uses
the locked, nonlogin `tyrell-video-hdmi` system account, created at package
configuration and retained after removal to avoid reusing its identity. Its
effective, bounding and ambient capability sets contain only
`CAP_SYS_TTY_CONFIG`, which Linux requires for the console switches. It has no
`CAP_SYS_ADMIN`, `CAP_SYS_PTRACE`, DAC bypass, setuid bit or file capabilities.

The stock image makes `/dev/tty0` root-only. `OpenFile=` lets systemd supply a
read-only descriptor for that single device without changing device permissions.
The helper validates the activation environment and character-device identity
and duplicates the descriptor with close-on-exec. Both the main process and
`ExecStopPost` recovery run under the same nonroot identity and capability limit.
The package requires systemd 253 or newer; the Zero under test runs 257.

The root-owned Unix socket remains mode 0660, group `video`. The helper verifies
`SO_PEERCRED` and `SO_PEERGROUPS` captured by the kernel at connection time.
Root and any local member of `video` may acquire the fixed console lease. This
explicitly replaces the former executable-path check: a separate nonroot UID
cannot inspect another user's `/proc/PID/exe` without excessive privileges.
No commands, filenames, console numbers or environment are accepted from clients.
Unexpected data terminates the lease. Unsupported group queries or oversized
group lists fail closed. This is a trusted local display-access group, not an
executable allowlist.

The service retains one exclusive lock and a mode-0700 runtime directory. A
write-ahead marker records the original and temporary consoles before switching.
The desktop stays running, and normal disconnect or abnormal process termination
restores its console. An explicit later console switch is respected. DRM polling
uses the existing video-group device access and implicit master acquisition;
it needs no system-administration capability. Existing systemd sandboxing and
a closed device policy limit the service to DRM devices and its inherited console.

## Packaging and validation

Both helpers now install in `/usr/lib/tyrell-video/`. The generic desktop and
hicolor icon duplicates are removed; APPLaunch's desktop and icon remain. The
package no longer needs disallowed shared install directories. Upgrading stops
the old lease before replacing its files; systemd owns migration of its preserved
runtime directory to the new service account. No firmware, boot or launcher
configuration is changed.

The builder checks exact nonroot identity, capability limits, fixed helper
commands, package paths, file types, account creation and package removal.
Helper tests exercise credentials, denied peers, inherited descriptors, private
runtime ownership, simultaneous leases, and normal and failed console recovery.

The systemd-257 device probe caught a naming discrepancy: with
`StandardInput=socket`, the accepted connection is removed from `LISTEN_FDS`
but its `connection` name remains in `LISTEN_FDNAMES`. The helper accepts that
specific observed layout as well as the ordinary matching layout, then validates
the actual console descriptor. The probe established that both execution and
recovery receive it with the single expected capability.

TV playback testing of 0.1.6 is pending: HDMI is disconnected and the user asked
to finish without a TV test. Earlier playback, audio and hotplug qualifications
apply to the unchanged playback engine, not to physical validation of this new
helper. Passing package checks does not itself constitute Store approval.

## Policy sources

Checked the official Store rules, including both upload workflows, on November
14, 2026. The rules reject root system services and restrict shared install paths.

- [Install path policy](https://github.com/CardputerZero/packages/blob/main/.github/scripts/install_path_policy.py), blob `bab1318f4c6c41d4bfee4fbf1de24e004e5ca6ac`.
- [Portal validation](https://github.com/CardputerZero/packages/blob/main/.github/workflows/process-web-submission.yml), blob `2e30925e98235209706274bfaa4684f937993128`.
- [CLI validation](https://github.com/CardputerZero/packages/blob/main/.github/workflows/validate-pr.yml), blob `1a13112e39f6ad8553391535c31c6b8c74f96fcd`.
- [systemd OpenFile](https://www.freedesktop.org/software/systemd/man/257/systemd.service.html#OpenFile=) and [257 descriptor handling](https://github.com/systemd/systemd/blob/v257/src/core/service.c#L1584-L1598).
- [Linux VT permissions](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/drivers/tty/vt/vt_ioctl.c) and [DRM master permissions](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/drivers/gpu/drm/drm_auth.c).
