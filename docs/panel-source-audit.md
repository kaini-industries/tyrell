# Pinned panel-source audit

H3 source inspection, November 24, 2026. The
[machine-readable inventory](../research/panel-source-audit.json) binds 17
retrieved UTF-8 files to their immutable commits, Git blob identities, byte
counts and SHA-256 hashes. This is source evidence. It does not establish a
Tyrell driver build, accepted hardware, scanout timing or a performance gain.
No upstream installer, kernel module or device test was run; no driver source
or UAPI header is vendored into Tyrell.

## Inputs and the porting boundary

The reviewed Gh0st commit is
`a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5`. Its
[lock](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/os/lock.json)
selects kernel `6.18.60+rpt-rpi-v8`, package `1:6.18.60-1+rpt1`, and M5 BSP
`a3fd3e83c0247e16e194e7e40f7fc1f5be933dd4`. Its
[staging recipe](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/os/packages/gh0st-panel-modules/stage.sh)
builds against selected headers, checks module vermagic and substitutes the
custom panel module in an explicit module list.

Tyrell's retained baseline instead selects kernel `6.18.39+rpt-rpi-v8` and BSP
`75518549a2640486f5d13ea72ca4935b198d7c20`. In that pinned
[panel source](https://github.com/m5stack/m5stack-linux-dtoverlays/blob/75518549a2640486f5d13ea72ca4935b198d7c20/modules/st7789v-1.0/panel-mipi-dbi-m.c),
TE state, waiting and IRQ setup are compiled out. The
[v5 overlay](https://github.com/m5stack/m5stack-linux-dtoverlays/blob/75518549a2640486f5d13ea72ca4935b198d7c20/modules/CardputerZero/cardputerzero-v5-overlay.dts#L448)
requests 60 MHz SPI and leaves the GPIO5 TE property commented out. These
source identities do not establish live pin wiring or permit copying the
newer module into the old image.

A future port needs a separate profile binding coherent kernel packages,
headers, modules, overlays, firmware and configuration, with fresh image and
application qualification. Preserve the Z3 comparison pair and its original
inputs. The upstream
[standalone installer](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/os/kernel/panel-mipi-dbi-m/install.sh)
writes live module/boot paths; it is not Tyrell's isolated staging or recovery
procedure. Package overlay copies also need explicit ownership and rollback;
running `depmod` on removal does not prove restoration of boot files.

## The observed interface

The driver defines `GH0ST_FBIO_PRESENT` as `_IO('F', 0xC0)`, encoded as
`0x46c0` by the Linux asm-generic ioctl convention used by ARM64. There is no
payload, version negotiation or separate capability query. Under
`CONFIG_DRM_FBDEV_EMULATION`, the
[handler](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/os/kernel/panel-mipi-dbi-m/panel-mipi-dbi-m.c#L801)
drains pending deferred framebuffer work and DRM helper damage work, then
returns zero. Other requests delegate to the original handler, or return
`ENOTTY` when none exists.

This is a work-completion interface. Transfer failures are logged by the
driver's void damage callback; that SPI result is not returned by PRESENT.
Zero therefore cannot certify a successful transfer or visible optical
completion. The handler adds no explicit overall deadline or foreground-owner
protocol. A userspace timer cannot guarantee cancellation of a kernel operation
already blocked on work or I/O.

The pinned Rust
[framebuffer caller](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/crates/gh0st-ui/src/fb.rs)
executes PRESENT while opening the framebuffer to detect support. That probe
can flush pending work; it is not read-only discovery. Later failure produces
a false return to the display caller, while its fsync helper discards errors.
The upstream [guide](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/docs/panel-driver.md)
describes a 20 ms timer fallback. Preserve Tyrell's existing app-owned
fbdev/fsync behavior and diagnostics instead of adopting either the live probe
or error suppression incidentally. Tyrell's
[Video commit helper](../packages/tyrell-video/src/lcd_commit.h) already records
commit failures and preserves the original mapped path; its
[native GameRuntime](../packages/tyrell-game/src/native.cpp) retains explicit
framebuffer ownership and one commit per completed frame.

## TE and copy behavior worth testing

The proposed [TE overlay](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/os/kernel/panel-mipi-dbi-m/gh0st-panel-te.dts)
adds GPIO5 to the panel node created by the v5 overlay. The patched driver
timestamps both IRQ edges, filters period/blanking estimates, and chooses
whether to lead, trail or wait for the scan. IRQs begin disabled and use the
unlazy flag. The idle test uses an increment followed by `> te_idle_edges`;
with the default 20, disabling occurs on the 21st counted idle edge.

Missing TE or a disabled parameter bypasses timing waits. A default individual
wait is 50 ms; timeout warns and may proceed without reliable phase. Up to
four scan-selection attempts, mutable parameters, workqueue scheduling, locks
and SPI work prevent treating that value as a total PRESENT deadline. Record
blind/fresh/slept states, timeouts and idle IRQ behavior separately. The
published upstream frame-rate and tearing observations remain upstream claims,
not Tyrell acceptance.

The [fast-copy path](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/os/kernel/panel-mipi-dbi-m/panel-mipi-dbi-m.c#L602)
uses row scratch copies followed by RGB565 byte swapping/transposition. It
applies to native RGB565, ordinary mapped GEM storage, excluding I/O-memory and
imported GEM buffers; other buffers use generic conversion and transposition.
An ordinary-RAM benchmark can check bounds, pitch, orientation, damaged-region
contents and equivalent output. Its timings do not reproduce write-combined
GEM, kernel-copy costs, transfer cadence or optical tearing.

## License and next gates

The repository [root license](https://github.com/therezor/CardPuterZeroOS/blob/a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5/LICENSE)
is MIT. The kernel driver separately declares `GPL-2.0` and original Noralf
Trønnes authorship; that declaration also exists in Tyrell's pinned M5 source.
The Rust/root license does not relicense the driver. A later driver distribution
needs applicable notices and a reviewed corresponding-source/build-material
closure. The overlay's complete distribution obligations remain a separate
review; no kernel code or header is copied by this audit.

Host-only preparation can establish source identities, inert experiment inputs,
copy correctness and measurement parsing. Activation remains behind baseline
acceptance, coherent experimental artifacts and an identified spare target.
Require original M5 and Tyrell app launch/return, dirty/full redraws, optical
tearing observations, idle IRQs, dim/dark/wake, HDMI/audio restoration, crashes
and recovery on each exact candidate. Keep SPI-byte equivalents, completed
software frames, work completions and optical scanout as separate outcomes.
