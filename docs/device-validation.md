# CardputerZero installation and live validation

Completed November 13, 2026 over SSH on the existing Debian installation.
`tyrell-status` remains installed. The original firmware and APPLaunch were
preserved; the device finished at its original launcher with Tyrell Status
available in the app list.

## Installed package

| Item | Verified value |
| --- | --- |
| Package | `tyrell-status 0.1.0-1`, ARM64 |
| Source revision | `src-aafb85a69389` |
| Package SHA-256 | `9f8ad2f3562c251ee9cb2c3c1b1292bb2d3fce069e257a55fb60cf656c1396bb` |
| Running kernel | `6.18.39+rpt-rpi-v8` |
| Original launcher | `applaunch 0.6.105` |

The uploaded package checksum matched before installation. Its dependencies were
already available; no existing package versions changed. Standard desktop,
icon-theme and mailcap cache triggers ran during installation. Package integrity
verification and `dpkg --audit` were clean afterward.

## Live test results

| Check | Result |
| --- | --- |
| CLI version and JSON | Passed on the CardputerZero, reporting the expected build revision |
| Device telemetry | Uptime and memory available; `wlan0` up with carrier; `eth0` down; BQ27220 battery reported 100%, Charging; no telemetry warnings |
| Native launch | Selected the installed `.desktop` entry in the original APPLaunch and activated it with Enter |
| Real display | Rendered correctly through `/dev/fb_lcd`; 320 × 170 RGB565, 16 bits per pixel, 640-byte stride |
| Input and navigation | Synthetic EV_KEY events through the existing evdev keyboard device switched System → Network with `2`, returned with `1`, and exited with `Q` |
| Launcher return | Original launcher resumed, still active/running with PID `1064` and `NRestarts=0` |
| Native app memory | One running-process snapshot reported RSS 4,424 KiB, approximately 4.3 MiB |

The app opened the real framebuffer and keyboard devices. Screens were captured
from the actual framebuffer and visually inspected; these are device results,
distinct from the earlier VM headless previews:

- System display: local `device-results/display-system.png`.
- Network display: local `device-results/display-network.png`.
- Original launcher restored: local `device-results/launcher-restored.png`.

Raw captures and device-specific evidence are ignored by Git and are not
published with this source repository.

Synthetic keyboard events validate the existing Linux input path, page changes
and exit behavior. **Physical keyboard switches were not pressed or tested.**
Battery data validates reading the current driver state; charging/discharging
transitions and gauge accuracy were not exercised. The RSS value is a snapshot,
not a long-running memory or performance test.

## Preservation checks

Before/after package inventories show exactly one addition: `tyrell-status`.
No existing package was removed or upgraded. The running kernel was unchanged.
All 12 protected-file hashes matched, covering selected boot configuration and
firmware files, device overlays, the launcher binary and user-service unit,
image/driver provenance, and the kernel package pin. The launcher process,
start time and restart count were unchanged.

These checks establish preservation of the selected files and package versions;
they are not a byte-for-byte comparison of the entire running filesystem.
No firmware image, controller firmware or launcher replacement was performed.

## Using the app

Select **Tyrell Status** in APPLaunch and press Enter. Controls are:

- `1`, Left or `Z`: System page.
- `2`, Right or `C`: Network page.
- `R`: refresh telemetry.
- `ESC` or `Q`: exit back to APPLaunch. `Q` was verified in this test.

For display-free status over SSH, run `tyrell-status --json` or
`tyrell-status --version`. Network values indicate local link state, not
Internet reachability. See [app documentation](../packages/tyrell-status/README.md).

To remove only this package later:

```sh
sudo dpkg --remove tyrell-status
```

Removal is documented for rollback and **was not performed on the device**.
The earlier [builder validation](build-validation.md) separately tested package
installation and removal inside the dedicated VM.

## Evidence

These files are retained locally under the ignored `device-results/` directory:

- `before.json`: pre-installation inventory and protected hashes.
- `after.json`: post-installation inventory and protected hashes.
- `preservation-check.json`: before/after comparison.
- `gui-test.json`: live GUI test and framebuffer metadata.
- `install-and-cli.txt`: installation and CLI output.
- `live-status.json`: device telemetry.
