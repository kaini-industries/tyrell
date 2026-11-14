# Prebuilt ARM64 packages

These verified Debian packages are committed to Git so a repository checkout
contains the current installable apps. The previous Video 0.1.5 is also published on
[GitHub Releases](https://github.com/kaini-industries/tyrell/releases/tag/tyrell-video-v0.1.5).

| App | Package | Validation | Checksums and provenance |
| --- | --- | --- | --- |
| Tyrell Video 0.1.6 | [ARM64 `.deb`](tyrell-video/0.1.6/tyrell-video_0.1.6-1_arm64.deb) | 34 ARM64 tests; install/remove and nonroot console recovery checks; TV test pending | [SHA256SUMS](tyrell-video/0.1.6/SHA256SUMS), [provenance](tyrell-video/0.1.6/provenance.json) |
| Tyrell Video 0.1.5 | [ARM64 `.deb`](tyrell-video/0.1.5/tyrell-video_0.1.5-1_arm64.deb) | 34 ARM64 tests; install/remove checks; native LCD and HDMI device checks | [SHA256SUMS](tyrell-video/0.1.5/SHA256SUMS), [provenance](tyrell-video/0.1.5/provenance.json) |
| Tyrell Status 0.1.0 | [ARM64 `.deb`](tyrell-status/0.1.0/tyrell-status_0.1.0-1_arm64.deb) | 4 ARM64 tests; install/remove checks | [SHA256SUMS](tyrell-status/0.1.0/SHA256SUMS), [provenance](tyrell-status/0.1.0/provenance.json) |

On the CardputerZero, from the repository root:

```sh
cd dist/tyrell-video/0.1.6
sha256sum -c SHA256SUMS
sudo apt install ./tyrell-video_0.1.6-1_arm64.deb
```

For Status, use `dist/tyrell-status/0.1.0` and
`tyrell-status_0.1.0-1_arm64.deb` instead. Start either app through APPLaunch.
APT resolves the package's declared dependencies.

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
