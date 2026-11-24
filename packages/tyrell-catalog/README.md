# Tyrell Catalog

An optional normal-user catalog for the Cardputer Zero's 320 × 170 LCD.
It opens a signed bundled/cached catalog without networking. Refresh, package
download and artwork download are explicit actions. It has no install, remove,
repair, helper, shell-command or root-authorization operation. The existing M5
Store executable, dispatcher, approvals and transaction recovery are unchanged.

The initial seed lists only the exact root-policy-approved Status `0.1.0-1` and
Video `0.1.6-1`/`0.1.7-1` archives. Their newer source versions are not included.
"Qualified" identifies the existing package/base boundary; it does not assert
physical acceptance for a newer binary, another target, or this frontend.

The seed's public key is pinned in this package. Its temporary bootstrap private
key was destroyed after signing; it is an offline snapshot, not a deployed
publisher. `source.json` deliberately has no live metadata endpoint. Production
refresh needs a separately managed signing key, reviewed public-key/source update
and hosted signed metadata. Package/artwork URLs identify exact Git objects;
their reachability and any private-repository access are not qualified by signing.

## Build and check

Use verified LVGL sources from `locks/build-inputs.json`. Configuration does not
download dependencies. Native DEBs require Debian 13 ARM64; builds also need
OpenSSL development headers, Python 3 and the OpenSSL 3 CLI.

```sh
cmake -S packages/tyrell-catalog -B build/catalog \
  -DLVGL_SOURCE_DIR=/path/to/verified/lvgl -DBUILD_TESTING=ON
cmake --build build/catalog --parallel 4
ctest --test-dir build/catalog --output-on-failure
python3 -m unittest discover -s packages/tyrell-catalog/tests -p 'test*.py'
cpack --config build/catalog/CPackConfig.cmake -G DEB -B build/catalog/packages
python3 packages/tyrell-catalog/tools/check_package.py build/catalog/packages/tyrell-catalog_0.1.0-1_arm64.deb
```

The package adds its own APPLaunch entry. It does not enter the default image or
approve itself for installation through the root Store helper. New base-image,
Store approval and physical launch/input/exit qualification remain separate.
Native Gh0st shell-link launching is gated until its lifecycle is qualified.

## Trust and cache

Signed metadata binds package version, architecture, size, SHA-256, base
compatibility, artwork identity, sequence and validity interval. Verification
uses Ed25519 with a fixed OpenSSL provider and bounded inputs. The public-key
identity is the SHA-256 of its Ed25519 DER representation. Discovery signatures
cannot change the installed Store policy, allowed dependencies or root verbs.

The base identity hashes the reviewed Store source/vendor ABI and exact root
policy/catalog bytes. The backend checks the installed root-owned files and
preserved vendor binary. Missing or different base context allows browsing but
disables downloads. Expired, future-dated or clock-unknown metadata is likewise
browse-only. The GUI retires a fresh label at its signed time boundary without
network polling; failed local revalidation disables its actions. Each download revalidates freshness and compatibility before
publication and checks exact size/hash; artwork is additionally PNG-bounded.

The protected minimum sequence is shipped in root-owned `trust.json`. The
normal-user high-water mark prevents accidental rollback within an intact cache;
it is not tamper-resistant. Deleting or replacing user state can replay an older
still-valid signed catalog above the packaged floor. Advancing the protected
floor or revoking/rotating keys requires a reviewed package/base update. A cache
or network response cannot supply trusted keys or revoke them. New key material
and a compatible signed seed should ship together; revoked cached signatures
stop being trusted even for browsing.

Metadata is limited to 256 KiB and 32 entries; packages to 16 MiB; artwork to
64 KiB and 128 × 128 pixels, restricted to noninterlaced eight-bit RGB/RGBA PNG
with IHDR/IDAT/IEND chunks. User cache quotas are 64 MiB and 96 files. Cache I/O
uses anchored descriptors, excludes links and unexpected owners, serializes
mutations, and atomically preserves the previous verified catalog on failure.
HTTPS origins are pinned, redirects are rejected and transfers are supervised
with cancellation/deadlines. GUI shutdown cancels/reaps its worker process group.
These are software limits, not a guarantee against a kernel or scheduler stall.

The frontend uses a fixed isolated Python backend and a bounded data protocol.
It does not execute metadata or downloaded binaries. Artwork is hashed and
dimension-checked from bounded memory before rendering; an editable cache path
is never handed directly to the image decoder.

## Offline signing

`tools/generate.py` reads the existing qualified `dist/` and `store/` provenance,
checks exact approved DEB and artwork bytes, and pins URLs to a selected Git
revision. It requires a caller-owned private key and never creates a production
key or publishes anything:

```sh
python3 packages/tyrell-catalog/tools/generate.py \
  --source-revision EXACT_40_HEX_COMMIT --sequence NEXT_SEQUENCE \
  --issued-at UTC_EPOCH --expires-at UTC_EPOCH \
  --private-key /secure/offline/publisher.pem \
  --output /new/output/catalog.json --report /new/output/generation.json
```

Keep the publisher key outside the repository and cache. A new key, endpoint,
protected floor or base identity requires explicit review and qualification.
Do not replace accepted bytes under an existing package version. See
[`docs/qualified-catalog.md`](../../docs/qualified-catalog.md) for implementation
and validation boundaries.
