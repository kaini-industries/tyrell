# Panel experiment preparation

H3 preparation, November 24, 2026. The [pinned source audit](panel-source-audit.md),
inactive experiment definition and recorded-counter analysis prepare a separate
panel investigation. They do not build or load a driver, enable TE, issue an
ioctl or change an application. Physical baseline acceptance remains pending.

The audit found different kernel/BSP pins, a GPL kernel driver, a mutating
PRESENT probe and no optical-completion or overall-deadline guarantee. Its
findings are constraints for a future port. The current M5 and Tyrell
fbdev/fsync paths retain their existing ownership and failure behavior.

## Inspect an inactive experiment

The committed [experiment template](../research/panel-experiment-template.json)
is separate from active image profiles. Generate a private copy and inspect it:

```sh
python3 scripts/panel/run.py template --output /private/tmp/h3-definition.json
python3 scripts/panel/run.py check --definition /private/tmp/h3-definition.json \
  --output /private/tmp/h3-inspection.json
```

`check` returns 2 with an inspectable `inactive-experiment` report. It validates
metadata consistency and exact audit/baseline bindings; it cannot enable a build
or deployment. Invalid inputs and output errors return 1. Choose a new output
file for every attempt; earlier results are preserved.

A proposed candidate must describe kernel, headers, modules and device-tree
artifacts with the same aggregate `build_source_commit` and declared kernel
release, plus source-export and license-notice descriptors. The aggregate
commit identifies the experiment recipe/capture, not identical upstream Git
commits for Linux and the BSP. Mixed identities and duplicate/traversal paths
are rejected. No candidate pathname is opened by this metadata check.

Descriptor consistency does not verify artifact bytes, ELF/module vermagic,
header contents, firmware closure, reproducibility or licenses. Those require
a separately qualified build/export workflow. Even complete candidate metadata
keeps all app, panel and fifteen-category hardware gates `not-tested`.

## Analyse recorded SPI activity

The [F2 collector](baseline-observations.md) already records bounded SPI
statistics. H3 adds parent capture envelopes around each snapshot call so
counter analysis can include child startup and return overhead. It does not
add a new live collector. This command only reads an existing bundle:

```sh
python3 scripts/panel/run.py counters --bundle /path/to/observation-bundle \
  --spi spi0.0 --output /private/tmp/h3-spi-analysis.json
```

Select the alias from the actual recorded inventory. `spi0.0` here is an example,
not automatic LCD identification. Stable canonical paths identify the selected
recorded counter source; they cannot attest its physical connection to the LCD.

The analyzer checks bundle hashes, collector/record-validator identity, complete
samples, unsigned counters, canonical-source continuity and parent capture
envelopes. It reports missing/denied/malformed sources, resets, overlapping
windows, transfer errors and timeouts explicitly. Legacy bundles without
measured parent envelopes remain useful historical observations but cannot gain
those bounds by inventing timestamps. An incomplete analysis returns 2; only a
complete descriptive analysis returns 0.

Rate intervals use the earliest and latest possible counter-read times inside
the recorded capture windows. They describe uncertainty from those windows,
not kernel deadlines or independent electrical measurements. Preserve the
source bundle, exact tool revision, raw values and the output's source hashes.

Dividing SPI byte deltas by 108,800 gives **bus-byte full-frame equivalents** for
320 × 170 RGB565. SPI bytes can include commands and other traffic. This is not
a count of completed pixel frames, PRESENT successes, TE events, visible refreshes
or optical scanout. Interrupt occurrences also are not process wakeups; none of
these counters establish tearing reduction or battery benefit.

The definition/input limit is 256 KiB, experiment metadata is limited to 64 KiB,
and output reports are limited to 2 MiB. Bundle/sample limits remain F2's bounds.
Reports bind exact input and implementation hashes and explicitly deny hardware
qualification. No result authorizes live probing or release promotion.

## Qualification before a driver change

Keep the retained Z3 comparison pair and original card unchanged. First accept
the baseline on an identified spare target. Prepare a separate, coherent
kernel/header/module/device-tree/firmware profile, original source/build
materials and applicable GPL notices. Verify candidate bytes, source closure,
module ABI and recovery before boot or installation.

Use the existing [display benchmark](tanks-display-benchmark.md) to separate
render, readback, conversion, copy and commit costs. Ordinary-RAM tests do not
reproduce mapped GEM behavior or SPI/optical timing. Declare workloads, budgets,
instrument burden and matched conditions before physical trials.

Require original M5 and all six current Tyrell apps to launch, draw and return;
test dirty/full redraws, RGB565 color/orientation, foreground ownership, TE
enabled/disabled/missing/timeout states, error visibility, fallback, idle IRQ/CPU,
brightness/dark/wake, HDMI/audio/VT handoff, abnormal exit and recovery. Observe
tearing optically and qualify the complete hardware matrix on the exact final
BSP. Battery claims need the separate [battery protocol](tyrell-zero-battery-validation.md).

The current source increment completes preparation and parsing checks. It
does not complete H3's accepted-baseline, candidate-build or physical gates.
