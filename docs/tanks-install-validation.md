# Sandline Tanks native installation

Installed and verified on the CardputerZero on November 14, 2026.

| Item | Result |
| --- | --- |
| Package | `tyrell-tanks 0.1.0-1`, Debian ARM64 |
| Package SHA-256 | `961beb67b647536a894534bffd3c968e425a0a52af4cfdc5b9a319e95ce59ae2` |
| APPLaunch name | **Sandline Tanks** |
| Runtime | raylib 6.2 software rendering, ENet 1.3.18, 320 × 170 RGB565 |
| Added packages | Exactly one: `tyrell-tanks` |
| Existing package versions | Unchanged |
| Protected firmware/boot/launcher hashes | All 12 unchanged |
| Kernel | Unchanged |
| Original launcher | Same PID/start time, zero restarts |

## Native port and package checks

The game now uses a native raylib memory/software build instead of a desktop
window. It renders directly at LCD resolution, converts complete frames to
RGB565, and commits once per frame with 30 Hz pacing. The evdev adapter supports
held controls, press/release edges, hostname/IP text, modifiers, repeat text,
and recovery after dropped input events. Display/keyboard discovery uses the
confirmed native devices; geometry checks refuse an incompatible framebuffer.
Framebuffer contents and file descriptors are restored/closed on ordinary exit.
SIGINT, SIGTERM, and SIGHUP request a clean exit.

All five ARM64 CTests passed: simulation, real UDP networking, CLI help, native
input, and native rendering. Tests include the real software-rendered game with
fonts, PNG export, and an explicit JSON state report. Separate process checks
confirmed clean SIGTERM and SIGHUP exits. All five desktop regression tests also
passed after sharing the platform-independent game loop.

The package passed exact-payload, AArch64 ELF, launcher entry, icon, dependency,
and license checks. It contains no maintainer scripts, services, configuration
files, or GPU/desktop runtime dependencies. Its native dependency list is
`libc6`, `libgcc-s1`, and `libstdc++6`; those were already installed on the Zero.
An isolated Debian ARM64 builder install/run/remove check passed before the
same checksum-verified package was installed on the physical device.

## Actual device checks

The installed **Sandline Tanks** entry appeared in the stock app carousel and
opened through APPLaunch. The running game was verified as a descendant of the
original launcher, using `/dev/fb0` through `/dev/fb_lcd` and the existing
TCA8418 keyboard. The real framebuffer was captured and visually inspected.

Synthetic press/release events exercised menu selection, driving and hull
turning, independent turret turning, firing, and the scoreboard. Escape returned
to the game menu; a second Escape exited. The same launcher resumed with its
original start time and zero restarts. The test did not press physical keyboard
switches or qualify hardware rollover. Observed RSS was about 4.8–5.2 MiB.

With the installed game left open at its menu, a five-second SPI counter window
measured **30.002 full-frame equivalents/s**, with zero errors and timeouts.
This is a menu/overview transfer observation, not a sustained full-match,
panel-vsync, tearing, thermal, or battery test.

## LAN check

The Zero hosted a match from its game menu. A separate desktop client joined,
received authoritative snapshots, moved 6.26 world units, and fired. The peer
report recorded two players, two active participant shells at peak, and valid
world state. The Zero's keyboard/display test and return-to-launcher check also
passed in host mode. The client detected the host leaving when the device test
exited the match normally.

The opposite direction—Zero joining a host on this Mac—did not complete in
this environment. The real LCD showed the correctly typed address, but the
desktop host recorded no joined peer. Its cause was not diagnosed, and no
firewall or network configuration was changed. LAN hosting on the Zero is the
verified path for this test build.

## Testing and retained evidence

The game was left open at its main menu. Press Enter for **Driving Range**.
W/S drives, A/D turns the hull, Q/E turns the turret, and Space fires. Escape
returns to the menu; Escape there exits to APPLaunch. The app remains available
as **Sandline Tanks** after exit. The launcher may need to be awakened and
unlocked before selecting it.

The package, checksums, and source-hash manifest remain under ignored
`artifacts/tanks-native/`. Raw audits, actual LCD captures, input event records,
and the LAN peer report remain under ignored `device-results/tanks-install/`.
The native code and packaging recipes are in `packages/tyrell-tanks/`.

No firmware image, kernel, SPI setting, controller firmware, or launcher
replacement was installed. The ARM64 build VM is returned to its prior stopped
state after verification. Sandline is a test prototype: audio, AI, client-side
prediction, discovery, and internet matchmaking remain future work.
