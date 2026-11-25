# Tyrell Zero guarded native Store

This package preserves the exact vendor AppStore UI from Store commit
`1ceeaed715766d336b1a885f58d03d5e4c33d6fa`, as shipped in locked
`applaunch 0.6.111`. A compiled dispatcher owns the public Store path and gives
the original UI its fixed public `argv[0]`. The UI runs as the normal user; its
existing `sudo --package-helper` protocol enters a separate Python package guard.
Legacy root UI/backend installation modes fail closed.

Version 0.1.1 selects the November 19 vendor UI and keeps the same exact Status
and Video package approvals. The vendor Settings bulk-app updater is outside
this helper protocol: it runs global dpkg configuration and installs unpinned
latest vendor packages/dependencies. Zero's offline activation must mask that
service as well as the base launcher updater. The vendor bytes remain intact;
that Settings update path is unsupported until separately qualified. Its
metadata-only APT index refresh is separate from package mutation.

The catalog qualifies exact Status `0.1.0-1` and Video `0.1.6-1`/`0.1.7-1`
archives, payloads, control metadata and maintainer scripts. It does not approve
future versions. Both Video versions have the same reviewed scripts: they create
or validate a locked dedicated HDMI account and operate only their own HDMI
socket/service. This is a guard for base-system integration, not a sandbox for
arbitrary package scripts.

The helper copies input into immutable root-owned SHA256 staging before inspecting
or installing it. It checks root-owned policy/catalog and base file/package
identities, protects normalized base paths, checks actual installed dependency
versions/alternatives/virtual Provides, and validates any APT simulation against
frozen approved dependency bytes. The initial dependency catalog is empty, so
missing dependencies require a qualified image/base update. No `dpkg-dev` or
compiler toolchain is needed at runtime. Qualified cache triggers use named exact
script identities; there is no global configure or pending-trigger recovery.

Root transaction journals bind the requested prior/target identities. Upgrades
freeze the prior qualified DEB before mutation. Failed operations support
app-scoped configure, root-frozen rollback or removal; completed/stale transaction
replays cannot remove a later installed version. Repair can reconcile a completed
root journal after a UI crash without executing a second package operation.

Build in the existing ARM64 builder:

```sh
python3 packages/tyrell-zero-store/build-package.py --output-dir /build/output
```

The package has no maintainer scripts or service activation. **Do not install it
on the handheld or the live build toolchain.** Assemble an isolated image root:

```sh
python3 packages/tyrell-zero-store/activate-image.py prepare --root /build/image-root
dpkg --root=/build/image-root --install /build/output/tyrell-zero-store_0.1.1-1_arm64.deb
python3 packages/tyrell-zero-store/activate-image.py capture --root /build/image-root \
  --protect-file /etc/systemd/system/tyrell-zero-session.service
```

`prepare` registers a real package-specific `dpkg-divert` before installation,
preserving the original at `M5CardputerZero-AppStore.vendor` with SHA256
`cb1a9db6224c90e3b4efdfd6da2936aa4d6e535c40fede64517ba7b09aa661aa`.
The Store package owns the public dispatcher, so its upgrades replace that file;
future `applaunch` updates use the diverted vendor path. `capture` freezes all
installed base package versions, shipped Tyrell runtime modules/policies and
integrator-specified masks/configuration. Run it only after the complete image
layout and package database are finalized. The session integration receipt must
also bind every runtime file and this snapshot.

For explicit offline removal, remove/purge the Store package first, then run
`activate-image.py restore --root /build/image-root`; it verifies the pinned vendor
bytes and restores them while removing the diversion. A live uninstall requires
base image maintenance; it is not a Store operation.

The private helper accepts only the pinned native CLI and configured sudo UID
1000. Its pending file is fixed by the system account home, with anchored
no-follow filesystem access. It has no environment/CLI override for policy,
root, catalog, helper executable or package executor. The internal Python API
allows executor/root injection for isolated tests only. Tests use real DEB
inspection and Debian version comparisons, adversarial path/script/transaction
checks, and qualified install/upgrade/downgrade/remove/repair/rollback sequences.
