# Compatibility and qualification ledger

F1, November 24, 2026. The machine-readable inventory is
[`research/compatibility-ledger.json`](../research/compatibility-ledger.json).
It records exact source/package identities and finite evidence, not a general
compatibility certificate or release approval.

The six current app source boundaries are snapshotted separately from retained
packages and historical device builds. Existing evidence must not migrate to a
new binary merely because the app's version string or target architecture is
unchanged. CI run reports describe the checkout they actually test; they do not
upgrade the device results in this ledger.

## Current source versus qualified artifacts

| Component | Current source | Exact retained or reported artifact | Original M5 evidence | Zero / Gh0st boundary |
| --- | --- | --- | --- | --- |
| Status | 0.1.2, event-driven native idle; experimental optional shell link | Retained `0.1.0-1`, SHA `15471a8f…`; historical device `0.1.0-1`, SHA `9f8ad2f3…` | Retained package has automated ARM64 build/install/remove evidence. A **different** same-version package has real LCD, synthetic input and launcher-return evidence. | Zero file image contains retained `15471a8f…`; new Status sources and Gh0st acceptance pending. |
| Video | 0.1.9, checkpoint gate, media actor and separate D5 software candidate | Retained `0.1.7-1`, SHA `7ddf4ead…` | Specific H.264 HDMI/software-recovery, audio/caption, ten-minute run and launcher-return evidence applies to retained 0.1.7. | Controlled D1–D5 software checks and inactive candidate package need exact device acceptance; live Gh0st media remains unsupported while qualified driver/shell deployment is pending. |
| Chess | 0.1.4, optional offline lifecycle | Retained `0.1.1-1`, SHA `c14cb5fe…` | Retained 0.1.1 UI/input, Wi-Fi LAN and launcher-return evidence. | New native/default-package and fake-shell checks cover source behavior; device acceptance remains untested. Shell support defaults off and linked LAN is gated; not in the default Zero payload. |
| Sandline Tanks | 0.1.1, default-off native offline lifecycle with GameRuntime 0.1.2 | Earlier native test package SHA `961beb67…`, available only in ignored local artifacts | Historical pre-extraction package has synthetic input/display and Zero-host LAN evidence; opposite LAN direction did not complete. | Controlled offline lifecycle and default package checks remain separate from physical M5/Gh0st acceptance. Linked LAN stays gated; no default Zero inclusion or inherited device qualification. |
| Beacon Arena | Framework/example 0.1.2, default-off native offline lifecycle | CI retains the exact default ARM64 executable; no qualified device package | Framework/headless/network and controlled native ownership checks; physical M5 acceptance pending. | Linked LAN stays gated. No package, launcher integration, default Zero inclusion or Gh0st qualification. |
| Catalog | 0.1.0, optional signed offline discovery and cache | Development CI packages only; no retained qualified device artifact | Native/headless software checks; physical launch/input/exit acceptance pending | Exact legacy Store approvals remain separate. No install verbs, default image inclusion, live feed or Gh0st lifecycle qualification. |

The ledger has a current-source row for every app on original M5, Zero and
Gh0st. These runtime scenarios remain **not tested for the current snapshot**;
historical exact-artifact rows carry the earlier results. This is deliberate,
including when an app's current implementation already exists. The next scoped
CI run can establish automated results for that checkout without asserting a
device result.

D5's current Video source boundary includes the handoff modules and root license
used by its [separate qualification package](video-qualified-integration.md). This updates
the moving source digest; it does not change retained-package identities or
mark any current-source device scenario passed.

Beacon's [offline pilot](beacon-offline-lifecycle.md) adds the shared
commands/shell-link boundary to its source identity. The framework and game
build-helper changes also update the declared Chess and Sandline snapshots;
their app policies and historical results are unchanged. Current-source runtime
rows still require new exact-artifact physical acceptance.

The [LAN policy decision](game-lan-lifecycle-design.md) selects local termination
on observed shell suspension. It adds no runtime implementation or qualified
LAN scenario; all linked-LAN admission gates and current device results remain
unchanged. Its inspected-source hashes and future, unexecuted acceptance cases
are recorded separately from this ledger's historical evidence.

References: [Status device report](device-validation.md),
[retained Status provenance](../dist/tyrell-status/0.1.0/provenance.json),
[Video report](video-hdmi-017-validation.md),
[Chess 0.1.1](chess-ui-input-validation.md),
[Chess 0.1.2 boundary](chess-clock-validation.md),
[Sandline installed build](tanks-install-validation.md) and
[framework extraction](game-framework-validation.md).

## Image modes

| Mode | Historical exact capture | What passed | What remains untested |
| --- | --- | --- | --- |
| Stock-derived | Reviewed November 14 job, source `e4933fa…`; raw SHA `a1c25eac…` | Offline verification, protected-path/package preservation and export binding | Physical cold boot and native peripherals on that image |
| Full-source control | November 24 matched control, source `5adaecb…`; raw SHA `e4e0d73d…` | Frozen construction, read-only checks and compressed-payload/export binding | Physical acceptance and settled-memory/battery comparison |
| Tyrell Zero | November 24 Z3 baseline, same source; raw SHA `207fcf45…` | Frozen construction, actual stage2 recovery, read-only checks and export binding | Z4 provisioning/session/Store/hardware, Z5 memory/pressure and Z6 battery policies |

These raw/compressed images are retained outside Git. Ledger validation verifies
the committed evidence files and their stated artifact digests; it does not
access ignored image pointers or revalidate external images. A passing ledger
therefore works in a fresh checkout without making a fresh-checkout image-build
readiness claim. See [stock image evidence](stock-image-validation.md) and
[Zero/control evidence](tyrell-zero-image-validation.md).

## States and bindings

Every record contains a subject, target, scenario, implementation state,
result, evidence scope, toolchain description, evidence references and explicit
limitations. The record's binding is either:

- `current-source`: the precise app/dependency source boundary in this checkout.
  Its aggregate digest includes sorted relative names and file hashes, so a new
  header or shared runtime change invalidates it as well as a modified file.
- `historical-exact-artifact`: a retained or documented package/image identity.
  Its result applies only to those bytes, target and scenario. It does not
  qualify current source or automatically transfer between M5, Zero and Gh0st.

`implementation` is `implemented` or `planned`. `result` is `passed`, `failed`
or `not-tested`. `evidence_scope` is `source-inspected`, `automated-tested`,
`device-tested` or `not-tested`. A planned or not-tested scenario cannot claim a
test outcome. A current-source snapshot cannot claim physical acceptance: that
requires exact package/image bytes. A skipped CI prerequisite is not device
acceptance and must not become a passed ledger entry.

Retained package SHA-256 is checked against both the file and a bound manifest.
Documented packages and file images are explicitly `external-not-revalidated`;
their digest must appear in bound evidence, but external bytes are not read.
All evidence paths are repository-relative, regular files, without traversal or
symlink components. Evidence files themselves are hash-bound so silent report
changes require review.

## Validate and maintain

Run from the repository root:

```sh
python3 scripts/ci/ledger.py validate
python3 scripts/ci/ledger.py validate --json
python3 -m unittest discover -s scripts/ci/tests -p test_ledger.py
```

The validator is standard-library-only and read-only. Its reusable API is
`validate_ledger(root, ledger=None) -> list[str]`; an empty error list means
consistent ledger data, never release/hardware approval.

When app/dependency source changes, review its declared boundary, record a new
source digest using `source_digest(root, roots)` from the validator, and keep new
runtime/device scenarios `not-tested` until their evidence exists. Never copy
historical results onto the refreshed source identity. Current CI reports are
separate generated artifacts; this ledger is not a mutable cache of the latest
green check.

U1's [shared command/UI implementation](shared-commands-ui.md) adds the common
source boundary to Video and Chess. Its new source versions and automated UI
checks do not transfer the retained packages' physical acceptance or extend the
approved Store catalog. Current runtime scenarios remain separately untested.

L1's [experimental Status pilot](shell-link-status.md) adds the common source
boundary to Status too. Its default build disables shell IPC; an enabled build
has separate fake-shell tests. The retained 0.1.0 package and image payload stay
bound to their original bytes and receipts.

C1's [cached Catalog frontend](qualified-catalog.md) has its own source boundary,
including shared commands, locked LVGL and the reviewed Store configuration.
Its signed seed is discovery data. It cannot authorize another version, transfer
historical device evidence, change root callers or upgrade a runtime result.

For a new qualified package, retain a new version's exact bytes/manifests,
register their SHA-256, source identity and evidence, then add scoped records.
For an evidence correction, review the changed claim before rebinding its hash.
For a new image or base/package change, requalify through the existing locks,
activation and independent verifier; editing this ledger cannot repair stale
receipts or authorize a protected-base change.

The ledger deliberately sets `grants_release_approval: false`. Candidate
promotion must separately bind required passing run reports to its exact source
commit, input hashes and artifact bytes, with required device gates still
visible. F1 does not implement automatic release, device writing or a broader
Store trust policy.
