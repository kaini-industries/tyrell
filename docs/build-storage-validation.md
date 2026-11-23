# Build storage recovery — November 23, 2026

Reconnecting the existing `garage` volume restored all **28 project symlinks**:
the builder backing disk, dependency/download caches, historical test outputs
and retained stock-image export. The configured image-storage root remains
`/Volumes/garage/tyrell-build`. No links were retargeted, VM replaced, caches
recreated or dependency downloads performed.

## Capacity and builder

The mounted external volume had approximately **530 GiB free**. Both host and
image storage preflight checks passed with the existing reserves, including
the combined 50 GiB image-output and VM-growth budget on the external volume.
The internal filesystem had approximately 12 GiB free.

The original 64 GiB backing disk was available. The existing `tyrell-build`
instance started successfully, reported `aarch64`, and returned no findings
from `dpkg --audit`. It was returned to its previous **Stopped** state. This
boot check did not build packages or certify every byte of the VM disk.

## Dependency caches and normal builds

The cached LVGL, raylib and ENet archives match their locked SHA-256 values.
`python3 scripts/game deps --backend headless` also verified the exact raylib
and ENet extracted source trees using the restored cache, without downloads.

The ordinary commands succeeded:

| Command | Result |
| --- | --- |
| `make chess` | Headless source 0.1.2 built successfully |
| `make chess-test` | 17/17 CTest groups passed |
| `make game GAME_BACKEND=headless` | Framework built successfully |
| `make game-test GAME_BACKEND=headless` | 6/6 CTest groups passed |

Both CMake configurations use the original `build/game-deps` path rather than
the temporary sources recovered during the earlier assessment. Network tests
passed with local loopback access.

The reviewed relocation inventories cover 23 project-cache directories,
1,974 original entries and 1,492 regular files. Every original entry retains
its recorded content and metadata. Twenty-two directories match exactly;
`device-tools` has 19 later additions containing Video tools and Python caches.
Those additions were preserved. Unrelated application caches were outside this
check.

## Frozen APT repository and retained image

The external APT lock is byte-identical to `locks/image-apt.json`.
`python3 image/apt-lock.py verify /Volumes/garage/tyrell-build/apt-repository`
passed for **1,679 package identities**, covering 1,757 retained files.

All **15 stock-export checksum entries** passed. Streaming the compressed
image also reproduced its exact recorded raw payload without writing a new
raw image:

| File | Bytes | SHA-256 |
| --- | --- | --- |
| `tyrell-trixie-arm64.img.xz` | 1,942,911,472 | `8acab9a62b18e40ca198406d30d174f2de1442eecb539147a4c7d37b6e71a108` |
| Decompressed payload | 7,415,529,472 | `a1c25eac2a7c3989773666bdd3f883a06d9f455c47ad5bc2312f73650afedff5` |

These values match the [reviewed November 14 build](stock-image-validation.md).
Its retained report records 582 passing offline checks, 7,279 protected paths
and 1,685 original packages. This recovery check verified retained bytes; it
did not rerun the ARM64 image verifier or build a new image. Physical spare-card
boot and peripheral acceptance remain pending.

See [storage recovery instructions](building.md#restore-relocated-build-storage)
for subsequent reconnects. No handheld or SD card was accessed.
