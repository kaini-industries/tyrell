# First package validation

Completed November 13, 2026. The CardputerZero was not contacted during this implementation.

This report records the builder milestone. A subsequent authorized SSH install
and device test is recorded in [device validation](device-validation.md).

- Package: `tyrell-status_0.1.0-1_arm64.deb` (546,040 bytes).
- SHA-256: `9f8ad2f3562c251ee9cb2c3c1b1292bb2d3fce069e257a55fb60cf656c1396bb`.
- Source revision: `src-aafb85a69389`; all exported source-file hashes match the workspace.
- Build: native Debian 13 ARM64 VM; c++ (Debian 14.2.0-19) 14.2.0; cmake version 3.31.6.
- Target: ARMv8-A with Cortex-A53 tuning; actual output is an AArch64 Linux ELF.
- Four CTests passed: telemetry fixtures, CLI version, CLI help, and LVGL headless rendering.
- ARM64 package metadata, payload and runtime dependencies inspected. No maintainer scripts, services, boot files, kernel modules or configuration replacements.
- Installed only in the dedicated builder, JSON and render smoke tests passed, then removed with binary and launcher-entry cleanup verified.
- Actual 320×170 System and Network renders were visually checked. Values shown are VM telemetry; the battery is unavailable there.
- Exported artifact checksums and source provenance verified on the host.
- Builder isolation verified: zero host shared filesystem mounts. `.env` is excluded from source transfer and Git.

See [build instructions](building.md) for commands and artifact details. Local evidence is retained in `artifacts/manifest.json`, `artifacts/tests.txt`, `artifacts/install-test.txt`, `artifacts/package-contents.txt`, and `artifacts/elf-report.txt`. These generated files are ignored by Git and are recreated by a build; they are not published with the source repository.

At this builder milestone, no package had been installed on the CardputerZero. Physical display/input, battery, launcher handoff, offline backup/recovery and a full custom OS image had not been tested. See the subsequent device report for the later live app checks. APT package inputs are recorded per build rather than snapshot-frozen; byte-identical rebuilds are not claimed.
