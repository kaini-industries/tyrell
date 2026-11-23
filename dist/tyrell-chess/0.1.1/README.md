# Tyrell Chess 0.1.1

Exact ARM64 update installed and tested on Cardputer Zero on November 15, 2026.
[Controls](../../../packages/tyrell-chess/README.md) ·
[UI/input validation](../../../docs/chess-ui-input-validation.md)

Adds spatial/held keyboard navigation, safer draw/leave confirmations,
contextual Help and hints, clearer clock/result states, reliable puzzle focus
and checked progress/preferences saving. Original firmware, launcher and Chess
state were preserved.

- `tyrell-chess_0.1.1-1_arm64.deb`: exact installed app package.
- `manifest.json`: SHA-256 of captured source inputs and the package.
- `tests.txt`: all 17 ARM64 CTest groups passed.
- `package-tools-tests.txt`: four package-tool regression tests passed.
- `package-check.txt`: architecture, dependencies, payload and license checks.
- `install-check.txt`: VM install/run/integrity/remove checks.
- `SHA256SUMS`: checksums of these retained files.

Native tests used injected events through the real keyboard driver and inspected
LCD captures. Physical switches and an external keypad were not exercised.
