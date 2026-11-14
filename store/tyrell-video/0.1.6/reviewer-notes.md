# First publication: Tyrell Video 0.1.6

Prepared text for the eventual publication PR; not posted anywhere.

Package: `tyrell-video`, version `0.1.6-1`, architecture `arm64`, size 685526 bytes.
SHA-256: `3e6b8f50e0f076e783943e77f61e2b57b856a7168f44886dd7d7646013fb239f`.
Build source revision: `src-11a4e420adee`.

Tyrell Video is a C++17/LVGL/GStreamer local-file player for CardputerZero.
It has native-screen playback, a keyboard file browser, audio/text-subtitle
selection and dedicated HDMI playback with the controller on the handheld.
The listing includes four explicitly identified rendered UI previews, using
fixture filenames rather than personal media.

## Hardware and package evidence

- 34 ARM64 CTests passed, with 31 Python lifecycle/security cases inside the
  helper CTest.
  Package install, diagnostics/render and removal passed in the isolated Debian
  builder. The unchanged default Status build and installation checks passed.
- The final 0.1.6 package is installed on the actual Zero and passes `dpkg -V`.
  Console acquisition/return, duplicate-client rejection, access denial outside
  `video`, runtime ownership migration, client crash, helper SIGKILL/recovery
  and reacquisition passed on the device with HDMI disconnected.
- Earlier playback-engine tests established smooth native playback and tested
  selected H.264 HDMI profiles, TV audio, captions and hotplug recovery. They
  are not a physical TV test of the new 0.1.6 helper. That TV retest remains
  pending and is disclosed in the public description.
- No firmware, boot or APPLaunch changes. The launcher kept its PID and zero
  restarts during the new helper checks; 12 protected files stayed unchanged.

## Nonroot HDMI console service

The player is unprivileged. The socket-activated system helper uses the locked
nonlogin `tyrell-video-hdmi` account. Both the main helper and ExecStopPost run
with only `CAP_SYS_TTY_CONFIG`; no SYS_ADMIN, SYS_PTRACE, DAC-bypass capability,
setuid bit, file capability or root command prefix is used.

Systemd supplies a read-only `/dev/tty0` descriptor because the stock device is
root-only. The helper validates its device identity, and its closed device
policy permits that console and DRM devices. The root-owned 0660 socket and
kernel-captured peer credentials authorize the local `video` group for one fixed
connection-lifetime lease. Clients cannot supply commands, paths or VT numbers.
An exclusive lock and private write-ahead marker support console restoration,
including after the helper is killed; later explicit console changes are
respected. The desktop stays running while its original VT is temporarily
inactive. Dedicated HDMI cannot share the screen with an active desktop.

Both helpers install under `/usr/lib/tyrell-video/`. APPLaunch entries are
preserved. The current official path and nonroot-service checks pass, as do
the maintainer-script and special-file checks. The package includes original
MIT code and LVGL's license; GStreamer and codec/font libraries are system
dependencies with their own installed notices.

## Items to complete before first-app review

An authentic real-device demonstration video must be attached to the PR. It is
not included yet. Do not present the synthetic fixture, UI previews or this
prepared text as the recording. Keep the HDMI retest limitation unless it is
actually completed. Omit the source URL; this submission does not change source
repository visibility. The Store maintainers decide whether to approve the app.
