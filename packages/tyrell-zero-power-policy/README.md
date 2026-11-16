# Tyrell Zero power-policy foundation

This is the revision-3 package foundation, version `0.1.0-1`. It defaults to
`baseline`, installs configuration and inactive resources only, and contains
**no automatic power activation**. Installation must not tune the builder or
handheld. No systemd unit, active tmpfiles configuration, USB udev rule, timer
or maintainer script is installed.

The four initial selections are `baseline`, `ethernet`, `cpu` and `combined`.
Every initial selection captures a 2,000 ms leaf delay; the respective Ethernet
controls are on/auto/on/auto and CPU factors are 50/50/1/1. The strict selection
format is:

```json
{"schema": 1, "preset": "baseline"}
```

The package embeds the captured `image/contracts/tyrell-zero-power.json` and
checks that its values and safety gates match this foundation. The source and
contract hashes are included in package/build provenance.

## Commands

The installed helper is `/usr/libexec/tyrell-zero-power-policy`:

```sh
/usr/libexec/tyrell-zero-power-policy validate
/usr/libexec/tyrell-zero-power-policy inspect
/usr/libexec/tyrell-zero-power-policy apply
```

`validate` checks selection/contract syntax and values. `inspect` reads identity
inputs and always reports hardware, battery and optimized activation as
unqualified/unimplemented. Neither writes files. `apply` is an explicitly
reported no-op for baseline; non-baseline apply exits 78 without writing
hardware, generating an override or creating runtime state. Success from
baseline does not certify that the current hardware settings match baseline.

An internal locked state engine supports generation-bound invalidation of an
existing **owned** generated runtime override. It invalidates authorization
before deletion and refuses changed, unregistered or symlink files. The CLI
`invalidate --generation ID` is limited to this cleanup; it never writes sysfs.
It reports `recovery-required` unless the defined CPU baseline can be observed,
and even CPU readback alone never certifies whole-policy recovery. A controlled
baseline boot and full effective-policy verification remain required.

The supported-flow lock cannot serialize arbitrary administrator commands or an
external tmpfiles reader. Production activation must integrate ordering,
generation checks and effective-state verification. This foundation does not
start such a reader or expose an automatically consumed candidate file.

## Build and test

Run logic tests without accessing live hardware:

```sh
python3 -m unittest discover -s packages/tyrell-zero-power-policy/tests -v
```

Assemble the package inside the existing native Debian ARM64 builder:

```sh
SOURCE_DATE_EPOCH=1794528000 python3 packages/tyrell-zero-power-policy/build-package.py \
  --output-dir /var/tmp/tyrell-zero-power-artifacts
```

`--contract PATH` selects the captured power contract when inputs are bundled
outside the normal checkout. `--source-revision VALUE` records the captured
revision. Assembly validates architecture, runs the suite, installs files into
a staging directory and uses `dpkg-deb`; it never installs the resulting package.
Output includes a `.deb`, test log and provenance manifest. Real installation
tests belong in an isolated root with services blocked and no host sysfs/bus
bindings; do not use the generic application install/remove verifier.

## Remaining release gates

The eligibility observer is not complete board/CPU attestation. Optimized
activation, boot ordering/late policy handling, integrated Ethernet topology
matching, supported update/apply serialization and actual live restoration
remain unimplemented. No spare-card hardware acceptance or battery-energy
qualification is claimed. Conditional factor/delay searches require new captured
identities; the initial schema deliberately does not accept arbitrary values.
