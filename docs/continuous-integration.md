# Scoped checks and candidate input preflight

F1 adds a compatibility ledger and source checks. Passing these checks does not
qualify a new image, device, package deployment or release. The
[compatibility matrix](compatibility-matrix.md) describes the exact historical
artifact evidence separately from current sources.

## Local entry points

Run from the repository root with Python 3.12 or newer:

```sh
python3 scripts/ci/ledger.py validate
python3 scripts/ci/scopes.py --base BASE_COMMIT --head HEAD_COMMIT
python3 scripts/ci/scopes.py --all
python3 scripts/ci/run.py --suite docs --report build/ci/docs.json
python3 scripts/ci/run.py --suite python --report build/ci/python.json
python3 scripts/ci/run.py --suite game --report build/ci/game.json
python3 scripts/ci/run.py --suite chess --report build/ci/chess.json
```

`BASE_COMMIT` and `HEAD_COMMIT` are full commit SHAs. The selector compares
committed revisions, including deleted and both renamed paths. Use `--all` when
previewing coverage for uncommitted work. The runner permits a dirty local
checkout, records it and rejects source changes during execution. CI passes
`--expected-revision SHA`, requiring that exact clean checkout.

| Suite | Required environment and scope |
| --- | --- |
| `docs` | Standard-library Python; validates ledger bindings and local documentation links |
| `python` | Linux CI; Python, CMake/CTest, FFmpeg/ffprobe and dpkg tools; existing builder/image/script/Zero/app-tool tests plus CI regression tests |
| `game` | C/C++ compiler, CMake/CTest; pinned raylib/ENet; headless framework, Beacon network and Sandline tests |
| `chess` | C/C++ compiler, CMake/CTest; pinned raylib; headless chess rules/controller/render/input/LAN tests |
| `status` | Disposable Debian 13 ARM64; pinned LVGL; CTest and app-only DEB construction, no live install |
| `video` | Disposable Debian 13 ARM64; pinned LVGL, GStreamer >=1.26 development/runtime plugins, libdrm/libpulse and FFmpeg encoders; CTest and app-only DEB construction |
| `zero-native` | Disposable Debian 13 ARM64 with CMake/Ninja/dpkg tools; foundation builds and install/reinstall/remove/purge in empty temporary roots |

The workflow declares system prerequisites. Native checks reject a substitute
distribution or architecture. Downloaded library archives use existing locks,
verified SHA-256 and fresh extraction per run. Installed toolchain packages are
recorded rather than frozen; this is not a byte-identical build claim.

Required missing tools, test groups, failures and skips fail a selected suite.
Some tests intentionally skip on macOS or when socket binding is restricted;
that local result is incomplete evidence, not a reason for CI to waive them.
Video preflights FFmpeg encoders and GStreamer plugins and checks registered
media tests so missing prerequisites cannot silently remove playback coverage.

Zero foundation lifecycle roots use the existing `--force-depends` inspector and
empty-root harness. They verify inactive package contents and scoped dpkg
behavior; they do not prove the installed runtime closure, patched native Wizard,
offline activation, PAM/VT, boot or hardware. No suite starts Lima, tunes power,
flashes a card or contacts the handheld.

## Workflow selection and evidence

`.github/workflows/checks.yml` runs an always-present planner and one
**compatibility gate**. Documentation-only changes select documentation checks.
Pull requests and main-branch pushes run checks; feature-branch pushes do not
duplicate an open PR's jobs. Manual dispatch selects every suite.
Package READMEs/assets remain package inputs. Shared game code selects games and
Chess; shared locks, builder/image code, the ledger and CI infrastructure receive
broader checks. Unknown inputs and unavailable comparison history select all
suites. There is no workflow-level path filter that leaves a required gate pending.

Portable jobs use Ubuntu workers; native jobs use Debian 13 containers on ARM64
workers. Base-integration packages are never installed into their live root.
The native container explicitly trusts only its mounted checkout in its own Git
configuration; checkout's temporary configuration is not assumed to persist.
The gate requires all selected matrix jobs to succeed for the planned source
SHA. An unselected native matrix can be skipped; a selected job cannot.
Configure this gate as a required repository check when adopting branch
protection; this change does not modify repository rules or publish a release.

Every generated `build/ci/SUITE.json` binds source commit, dirty state and an
aggregate source-input digest, environment, scope, outcome and evidence paths.
Per-group Python reports preserve skipped-test names/reasons, and CTest emits
JUnit plus its inventory. Reviewable logs, reports and package outputs are copied
under `build/ci/evidence/`. Workflow artifacts exclude dependency source caches
and full build trees. Each report's `exported_evidence` maps original work paths
to artifact-relative paths and hashes. Native app/foundation package inventories
and compiler/tool versions identify the actual worker. Reports deny image construction and hardware
qualification. Historical ledger results are never automatically upgraded by CI.

## Manual candidate input preflight

`.github/workflows/image-candidate.yml` is a separate manual dispatch. It needs
an exact source commit, retained artifact run/name in this repository and the
expected SHA-256 of an existing `image-input.tar`. Restore that exact archive;
missing retained inputs fail rather than triggering a replacement download or
rebuild. The workflow does not upload private local archives automatically.
The selected source commit must contain these F1 tools. Earlier Z3 captures
remain historical evidence; dispatch cannot retroactively test their old commits
with files that did not exist there. Prepare a new capture at a clean F1-or-later
commit for this workflow, without overwriting the accepted Z3 pair.

Local equivalent, on a clean selected checkout:

```sh
python3 scripts/ci/candidate.py \
  --bundle /path/to/retained/image-input.tar \
  --sha256 EXPECTED_ARCHIVE_SHA256 --expected-revision SOURCE_COMMIT \
  --report build/ci/candidate.json
```

The trusted checkout's existing bundle verifier checks archive inventory/bytes;
captured recipes/locks must match that checkout and metadata must identify the
same clean source commit. No captured recipe is executed or extracted.
Archive self-consistency is not a proof of a complete vendor/dependency closure
or image-build readiness; those remain the frozen pipeline's independent gates.
This preflight is deliberately **not an image build or promotable result**.
Construction/activation/image verification still use the
[frozen pipeline](tyrell-zero-pipeline.md). R1's immutable release packaging and
promotion remain future work; required hardware evidence remains explicit.
