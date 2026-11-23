# Tyrell Chess 0.1.0

Native Cardputer Zero ARM64 app package, installed and smoke-tested November 15,
2026. Choose **Tyrell Chess** in APPLaunch. [Controls and build guide](../../../packages/tyrell-chess/README.md).

- `tyrell-chess_0.1.0-1_arm64.deb`: exact installed package.
- `manifest.json`: SHA-256 hashes of captured source inputs and package.
- `tests.txt`: all 11 ARM64 CTest groups passed.
- `package-tools-tests.txt`: four packaging-tool regressions passed.
- `package-check.txt`: payload, architecture, dependencies and licenses checked.
- `install-check.txt`: exact package installed, ran, verified and removed in VM.
- `SHA256SUMS`: checksums of retained files.

See the [validation record](../../../docs/chess-validation.md) for native UI,
keyboard and Wi-Fi tests and their limits. Firmware and stock launcher were
preserved. LAN peers must run the Zero port; Advance ESP-NOW is incompatible.
