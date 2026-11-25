# Qualified catalog and cached frontend

C1 adds an optional `tyrell-catalog` application, currently 0.1.1. It browses signed
discovery metadata and downloads verified files as the normal user. It does not
install packages, remove or repair apps, invoke the Store helper, or replace
the original M5 Store. Existing Store policy, caller restrictions, dispatch and
transaction recovery stay unchanged.

The first catalog contains the exact Status `0.1.0-1` and Video
`0.1.6-1`/`0.1.7-1` archives already approved by the Zero Store. It excludes newer
source versions, Chess and the Catalog package itself. A valid metadata signature
does not expand those root approvals or qualify another package, dependency,
maintainer script, base image or hardware target.

## Metadata and signing

[`tools/generate.py`](../packages/tyrell-catalog/tools/generate.py) is an offline
generator. It checks the existing `dist/` package bytes, sizes, manifests,
provenance and checksum inventories against the root Store catalog. Video's
Store qualification and artwork manifests must agree too. URLs identify exact
Git revisions and exact approved artwork; generation never builds or uploads a
package. It requires a caller-owned private key outside the repository.

The canonical envelope contains schema, signing-key identity, payload and an
Ed25519 signature. A domain-separated signature covers the complete unsigned
envelope. The payload binds sequence, issue/expiry epochs, base identity and
every package's name, version, architecture, byte count, SHA-256, URL, title,
summary and artwork descriptor. Duplicate or unknown fields, invalid types,
unsafe URLs, oversized metadata and ambiguous identities fail verification.

The verification key is pinned in root-owned
`/usr/share/tyrell-catalog/trust.json`; its ID hashes the Ed25519 DER public key.
Neither the user cache nor network metadata can supply trusted keys. Runtime
verification uses fixed OpenSSL 3 paths, a sanitized environment and a timeout.
Ed25519's raw-input invocation follows the
[OpenSSL documentation](https://docs.openssl.org/3.5/man1/openssl-pkeyutl/).

The packaged offline seed was signed once using a temporary bootstrap key whose
private half was destroyed. The public pin and signed bytes are committed.
There is **no configured live metadata endpoint or deployed continuing signer**.
Refresh reports that configuration state only when explicitly requested. Signed
raw-Git URLs do not establish server availability or private-repository access.

The 0.1.1 package rotates that bootstrap key for the November 19 Store base:
source `1ceeaed715766d336b1a885f58d03d5e4c33d6fa`, vendor executable SHA-256
`cb1a9db6224c90e3b4efdfd6da2936aa4d6e535c40fede64517ba7b09aa661aa`,
and compatible-base ID
`c9bc9175deb1a14c017fe65c8c573183d822cb0779150c168674b690de385ed6`.
Its new public key ID is
`03f9c1538b49b6f078370096956a161b81342667b4c8713bb7516947ad09f0da`.
The new offline seed and protected floor use sequence 2; the prior key is revoked.
The old private key was not recovered or reused. A new temporary key outside the
workspace signed the seed and was deleted after signature verification. All three
approved package/artwork entries, exact URLs and validity timestamps are preserved.
The former seed and public pin remain in Git history. This binding update adds no
default-image payload, Store approval, continuing publisher or device acceptance.

Deploying a live feed requires an independently managed signing key, reviewed
public-key/source configuration and hosted metadata. Rotation is a reviewed
package/base update: add the next public key and signed seed, select a minimum
sequence, then revoke the prior key when its metadata must stop being trusted.
Revocation and source-policy changes invalidate an in-flight backend operation
before publication as well as later reads. A revoked signature cannot remain a
trusted offline catalog.

## Compatibility, clocks and replay

The compatible-base ID hashes the reviewed Store source revision, preserved
vendor ABI digest and exact Store policy/catalog bytes. This is a bounded
discovery compatibility identity, **not the full raw-image or protected-base
qualification**. The existing installer independently verifies its full base,
dependency and package transaction contracts.

The backend reads the fixed root-owned Store policy/catalog and hashes the
preserved vendor executable. Missing, changed or unsafe installed context makes
the catalog browse-only. Signed metadata for another pinned base is rejected.
Downloads recheck the root trust/source snapshot and installed base before and
immediately before publication, including after transfer and file sync.

The GUI receives signed issue/expiry timestamps and updates a fresh view's
eligibility using the local clock while idle and before actions, without network
polling. A failed local revalidation invalidates the previous fresh label and
download/artwork actions; retained rows become an unavailable, browse-only view.

Expired, future-dated and clock-unknown metadata may be browsed with explicit
labels. It cannot permit new package/artwork downloads. The verifier does not
invent a reliable clock or treat an offline cache as proof of release freshness.

The minimum sequence is protected by the installed root-owned trust package.
The normal-user cache also keeps an atomic high-water hint to reject accidental
rollback while that state is intact. It cannot provide tamper-resistant
advancement: deleting user state can replay an older still-valid signed catalog
above the packaged floor. A security-sensitive floor must advance through a
reviewed protected package/base update; C1 adds no privileged floor-update verb.

## Offline startup and bounded operations

Native launch starts the separate Catalog entry and reads local signed state
only. It does not refresh or fetch artwork automatically. The bundled signed
artwork permits an offline first view. The original M5 Store retains its own
startup and mutation path; C1 makes no vendor Store startup improvement claim.

| Operation | Behavior |
| --- | --- |
| Browse | Verify local metadata, read bounded local artwork, display compatibility/freshness and exact versions; no network |
| Refresh | Explicit HTTPS request to the reviewed endpoint; reject invalid signature, base, sequence, time or response and preserve the previous verified catalog |
| Download | Explicit selected package identity; verify metadata again, fetch exact bytes and publish to the user cache after size/hash/base/time checks |
| Artwork | Explicit missing-artwork fetch; verify signed hash/size and the supported bounded PNG subset |
| Cancel/exit | Stop the worker process group and reap the backend; preserve completed verified state and discard partial work |

The backend permits only pinned HTTPS origins and rejects redirects, credentials,
unsafe path encodings and unbounded responses. A supervised network child bounds
DNS/TLS/read work; it closes inherited descriptors, has its own termination
timer, and cannot retain the parent's cache lock after a supervisor crash. The
GUI separately caps view/operation time and cleans up remaining group members
before reaping the leader, preventing PID reuse during cleanup.

Cache paths use anchored no-follow descriptors, owner/mode checks and regular
single-link files. Mutations use one exclusive lock and atomic synced state
publication. Cancellation, expiry or a root-policy/base change at the final
commit fence leaves the previous verified state intact. Quotas are 64 MiB and
96 files; full-cache errors preserve valid files. Metadata is at most 256 KiB,
32 entries and 90 days per validity interval; packages are at most 16 MiB and
artwork at most 64 KiB and 128 × 128 pixels.

Artwork supports noninterlaced RGB/RGBA PNG at eight bits per channel and only
IHDR/IDAT/IEND chunks. CRCs, inflation size and filters are checked. Compressed
text/profile chunks are rejected, and the native decoder omits ancillary
inflation paths. The GUI hashes bounded bytes into owned memory before decoding,
so changing an editable cache pathname cannot swap the rendered bytes.

## Frontend and packaging

The 320 × 170 LVGL frontend provides focused rows, details, verified artwork,
contextual help and visible state/error labels. Commands use shared Tyrell
metadata. It starts a fixed isolated Python backend with bounded TSV output;
it never passes metadata to a shell or executes a downloaded file. Headless
fixtures and scripts are explicit preview-only paths and cannot transfer files.
Native Gh0st shell-link launches are refused before backend/device acquisition
until a separate lifecycle policy is qualified.

The app-only ARM64 DEB adds its own executable, backend, public trust/seed,
artwork, APPLaunch entries and license notices. It has no maintainer scripts,
conffiles, root helper, service, sudo rule or vendor diversion. Package inspection
checks exact payload paths, ownership/modes, ELF architecture, runtime modules,
signature/base identity and absence of privileged/desktop dependencies. The
package is neither in the approved root Store catalog nor the default image.

Build and generator commands are in the
[package README](../packages/tyrell-catalog/README.md). Catalog source and native
checks run within the existing `python` and `zero-native` suites, preserving
R1's eight-suite promotion contract. The latter still runs all existing Store
guard/dispatcher and isolated foundation lifecycle checks before Catalog checks.

## Validation boundary

Tests use real Ed25519 signatures, adversarial metadata and cache objects,
supervised socket/process fixtures, actual PNG bytes and deterministic native
LVGL previews. They cover signature/base/type/size errors, replay/expiry and
unknown clocks, redirects, quotas, cancellation, concurrent refresh, interrupted
sync/replace, policy revocation, worker death and cache links. A cross-module
test verifies that even a valid discovery signature for a new app/version is
still rejected by the unchanged root Store catalog.
The committed offline seed is also verified against the current reviewed Store
base, so a source/policy/vendor change cannot silently leave stale package pins.

Native/headless checks do not prove physical keyboard/display acceptance, power
savings, live endpoint deployment or network availability. No original card,
firmware, image, root approval or retained qualification receipt is changed.
Current Catalog M5/Zero runtime acceptance and Gh0st lifecycle remain separately
untested in the compatibility ledger.
