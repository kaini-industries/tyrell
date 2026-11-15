# Prebuilt ARM64 packages

These Debian package snapshots retain exact builds and their validation records.
Video 0.1.7 passed automated build/package checks and the recorded HDMI recovery,
software-playback and normal-launch qualification for the tested H.264 profile.
The earlier Video 0.1.5 is also
published on
[GitHub Releases](https://github.com/kaini-industries/tyrell/releases/tag/tyrell-video-v0.1.5).

| App | Package | Validation | Checksums and provenance |
| --- | --- | --- | --- |
| Tyrell Video 0.1.7 | [ARM64 `.deb`](tyrell-video/0.1.7/tyrell-video_0.1.7-1_arm64.deb) | 35 ARM64 tests; install/remove; [controlled recovery, ten-minute software HDMI run and normal launch](../docs/video-hdmi-017-validation.md) | [SHA256SUMS](tyrell-video/0.1.7/SHA256SUMS), [provenance](tyrell-video/0.1.7/provenance.json) |
| Tyrell Video 0.1.6 | [ARM64 `.deb`](tyrell-video/0.1.6/tyrell-video_0.1.6-1_arm64.deb) | 34 ARM64 tests; nonroot console checks; [TV results and observed startup failure](../docs/video-hdmi-016-validation.md) | [SHA256SUMS](tyrell-video/0.1.6/SHA256SUMS), [provenance](tyrell-video/0.1.6/provenance.json) |
| Tyrell Video 0.1.5 | [ARM64 `.deb`](tyrell-video/0.1.5/tyrell-video_0.1.5-1_arm64.deb) | 34 ARM64 tests; install/remove checks; native LCD and HDMI device checks | [SHA256SUMS](tyrell-video/0.1.5/SHA256SUMS), [provenance](tyrell-video/0.1.5/provenance.json) |
| Tyrell Status 0.1.0 | [ARM64 `.deb`](tyrell-status/0.1.0/tyrell-status_0.1.0-1_arm64.deb) | 4 ARM64 tests; install/remove checks | [SHA256SUMS](tyrell-status/0.1.0/SHA256SUMS), [provenance](tyrell-status/0.1.0/provenance.json) |

On the CardputerZero, from the repository root:

```sh
cd dist/tyrell-video/0.1.7
sha256sum -c SHA256SUMS
sudo apt install ./tyrell-video_0.1.7-1_arm64.deb
```

For Status, use `dist/tyrell-status/0.1.0` and
`tyrell-status_0.1.0-1_arm64.deb` instead. Start either app through APPLaunch.
APT resolves the package's declared dependencies.

Version 0.1.7 adds a one-shot software-video retry for a specific HDMI KMS
buffer-pool activation failure after hardware output. It retains the dedicated
nonroot helper introduced in 0.1.6. The
[Store preparation](../store/tyrell-video/0.1.7/README.md) includes the completed
qualification and reviewed real-device recording. The results cover the stated
hardware, H.264 profile and injected failure; other formats can have different
performance. The exact 0.1.7 package was
[submitted as Store PR #324](https://github.com/CardputerZero/packages/pull/324),
with its [real-device recording posted for review](https://github.com/CardputerZero/packages/pull/324#issuecomment-6890010512).
Automated submission checks passed; the first-app release awaits manual
maintainer review and merge. Earlier package bytes and their original build
provenance remain unchanged.

Each version folder retains the original build/source manifests and test logs.
The original manifest describes the complete builder export; `SHA256SUMS`
covers the files committed in this folder. `provenance.json` identifies the
exact committed build inputs. Later README or distribution updates do not
change the recorded package bytes or their build provenance.

Generated exports under `artifacts/`, VM state, raw device evidence and OS images
remain ignored. Add future verified packages in new version folders, retain
their provenance, update the links and add a file-specific `.gitignore`
exception. Preserve already published package bytes; rebuild changes as a new
package version. Historical Video versions remain available on GitHub Releases.
