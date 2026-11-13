# CardputerZero hardware and recovery research

Researched **2026-11-13**. This document describes vendor hardware support,
source availability, and a proposed recovery workflow. No flashing, controller
upgrade, package installation, or device configuration change was performed.

## Device identity

CardputerZero is M5Stack's **Raspberry Pi Compute Module Zero (CM0) Linux
computer**. The original Cardputer and Cardputer-Adv use different hardware;
their ESP32 firmware, Arduino examples, and PlatformIO configurations do not
provide a CardputerZero operating-system build workflow.

The following are **vendor specifications**, rather than assertions that every
peripheral was exercised on the acquired unit.

| Component | Vendor specification |
| --- | --- |
| Processor | RP3A0/BCM2837, four Cortex-A53 cores, 1 GHz, ARMv8-A |
| Memory/storage | 512 MB LPDDR2; microSD |
| Display | 1.9-inch ST7789v3, 320 × 170, SPI |
| Keyboard | 46 keys; TCA8418 controller on I2C |
| Networking | 2.4 GHz Wi-Fi, Bluetooth, 10/100 Ethernet through USB |
| Audio | ES8389 codec, microphone, speaker, TRRS jack |
| Other hardware | RX8130CE RTC, BQ27220 gauge, infrared TX/RX |
| Expansion | Switchable I2C/UART Grove port; 14-pin SPI/UART/I2C/GPIO/USB bus |
| Full model | IMX219 camera, BMI270/BMM150, included 32 GB microSD |
| Lite model | No built-in camera or IMU/magnetometer; no included microSD |

Source: [M5Stack product specification and pin map](https://docs.m5stack.com/en/CardputerZero).
The [CM0 datasheet](https://pip-assets.raspberrypi.com/categories/1286-raspberry-pi-compute-module-zero/documents/RP-009251-DS-3-cm0-datasheet.pdf)
describes the compute module. M5Stack also publishes a ten-page
[CardputerZero schematic, revision 0.6.1 dated 2026-07-02](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/1243/C154-CardputerZERO_SCH_V0.6.1_20260702.pdf).

## Three separate software layers

1. **Linux image:** Raspberry Pi boot files, kernel, root filesystem,
   services, launcher, and applications. This is the first customization target.
2. **Board support:** kernel modules and device-tree overlays connecting Linux
   to the screen, keyboard, audio, battery, controller, and sensors.
3. **M5IOE1/PY32 controller firmware:** code running on a separate control MCU.
   Building a Linux image does not automatically rebuild this firmware.

The M5IOE1 is particularly important: display reset/backlight, power signals,
keyboard/USB reset, expansion switching, and LEDs depend on it. Preserve its
working firmware and Linux support during initial OS development.
The [Linux M5IOE1 driver](https://github.com/m5stack/m5stack-linux-dtoverlays/blob/main/modules/m5ioe1-1.0/m5ioe1.c)
includes GPIO, PWM, ADC, pin control, and a read-only hardware-ID proc interface.

[FactoryTest](https://github.com/CardputerZero/FactoryTest) includes a PY32
upgrade service and a packaged firmware archive under `assets/py32_firmware`.
That establishes an upgrade mechanism, not a complete public controller-firmware
source toolchain. [M5IOE1](https://github.com/m5stack/M5IOE1) is an Arduino/ESP-IDF
host driver library; it should not be mistaken for the controller's own source.

## Board-support sources and installed evidence

The public board-support repository is
[m5stack/m5stack-linux-dtoverlays](https://github.com/m5stack/m5stack-linux-dtoverlays).
Its [CardputerZero Makefile](https://github.com/m5stack/m5stack-linux-dtoverlays/blob/main/modules/CardputerZero/Makefile)
builds the component modules and overlays. Revision selection is explicit;
current vendor image scripts use `CONFIG_CARDPUTERO_V0_5=y`.

The primary SSH investigation independently observed these installed facts:

- September 29 image, pi-gen commit
  `5f4c44f3431bf1dfb968c10f7228ad6fa0c66cf4`, built through stage4.
- Driver revision `75518549a2640486f5d13ea72ca4935b198d7c20`.
- Modules include `m5ioe1`, `tca8418_keypad_m5stack`, `es8389_m5stack`,
  `cardputerzero_audio`, and `panel_mipi_dbi_m`.
- Boot configuration includes the CardputerZero v5, `bmi270_bmm150`, and
  `bq27220_v5` overlays.
- `/proc/cardputerzero_hw_id` returns raw `0x06`; its marketed revision mapping
  is not established. Do not change v5 overlays based on that value alone.
- Framebuffer `panel-mipi-dbid` reports 320 × 170, 16 bpp;
  `/dev/fb_lcd` resolves to `fb0`, and
  `/dev/dri/cardputer-zero-internal` resolves to `card1`.
- The APPLaunch user service is active.

Those observations identify the starting baseline. Public `main` has changed
since that baseline, so retaining the installed revisions is preferable for the
first reproduction. Do not infer that a module loading proves every function
has passed a physical test.

The [vendor pi-gen driver stage](https://github.com/CardputerZero/pi-gen/blob/cardputerzero_v1.0/stage2/05-cardputerzero/01-run.sh)
currently clones driver `main`, records its SHA, and builds against target
`linux-headers-rpi-v8`. Pin that clone for repeatable inputs. Keep the kernel,
matching headers, compiled modules, and overlays together as one tested set.

## Important interfaces to retain

- Preserve the vendor display/keyboard paths before replacing the launcher.
  A generic Pi image is not evidence of complete handheld support.
- Retain shutdown handling: the [vendor board Makefile](https://github.com/m5stack/m5stack-linux-dtoverlays/blob/main/modules/CardputerZero/Makefile)
  builds a power-fail module as well as audio and GPIO support.
- The [v5 overlay](https://github.com/m5stack/m5stack-linux-dtoverlays/blob/main/modules/CardputerZero/cardputerzero-v5-overlay.dts)
  declares the controller, display, keyboard, audio, RTC, backlight, and GPIO
  relationships. Use it as the wiring source for a future alternative distro.
- Keep networking available while testing UI packages; do not switch USB modes
  during an Ethernet-dependent session without an independent connection.

## Preserve and restore using microSD

Recommended project procedure, to be executed separately from this research:

1. Shut down cleanly, cut power, and remove the supplied card.
2. Use a reader to make an **offline, whole-card image**, including partition
   table, boot partition, and root filesystem. An SSH copy of live files is not
   an equivalent recovery image.
3. Record image size and SHA-256; retain the original card unchanged. Keep this
   private backup separate from distributable images because it contains the
   provisioned device's credentials and identity.
4. Write a copy to a spare card large enough for the original image and verify
   it boots before depending on the backup. Record manual hardware checks.
5. Test custom images on spare cards. Recover by powering off and returning to
   the original card, or restoring the verified full image to a spare card.

The [official flashing guide](https://docs.m5stack.com/en/compute/cardputerzero/image)
recommends a microSD reader and [M5 Imager](https://github.com/CardputerZero/m5stack-imager).
It supports account, Wi-Fi, and SSH provisioning. Select the target disk
carefully: flashing replaces its contents. Preserve a local copy of a versioned
factory download as well as the personalized backup.

## USB recovery fallback

The same guide documents USB flashing as slower and potentially host-sensitive:
install Raspberry Pi [usbboot/rpiboot](https://github.com/raspberrypi/usbboot),
using documented commit `61c482663c9b2a61ee86ba491f131ba4ca90e4ea` if compatibility
requires it; insert the card, select the right-side USB-C connection with the
physical selector, connect that port to the host, and run `rpiboot`. Once the
host is waiting, hold BOOT, switch device power on, keep holding BOOT for more
than five seconds, then release. When the card appears as storage, use the
imager. The guide also documents
`rpiboot -d mass-storage-gadget64` for improved transfer speed.

Use the current guide's illustrations for switch/port orientation. USB recovery
has not been physically tested in this investigation; the spare-card reader
workflow is the simpler first recovery path.

## Images, provenance, and licenses

The [pi-gen releases](https://github.com/CardputerZero/pi-gen/releases) expose
versioned image assets, digests, and build-info metadata. The visible November 13
release is a **prerelease**; the September 29 release is marked stable. Prefer
versioned URLs and verified SHA-256 over a mutable `latest` alias.

[pi-gen](https://github.com/CardputerZero/pi-gen) uses BSD-3-Clause;
[launcher](https://github.com/CardputerZero/launcher), Template, and FactoryTest
declare MIT. Individual kernel sources carry their own licenses; for example,
the M5IOE1 Linux driver declares GPL-2.0-only. Preserve notices and the sources
corresponding to distributed modified modules. The build-system license does
not relicense every package, font, boot binary, or firmware inside an image.
Review component notices and Raspberry Pi firmware redistribution terms before
publishing an image. Controller firmware source availability remains unverified.
