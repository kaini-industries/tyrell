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
| `python` | Linux CI; Python, OpenSSL 3, CMake/CTest, FFmpeg/ffprobe and dpkg tools; existing builder/image/script/Zero/app-tool tests plus CI, release and Catalog signature/cache regressions |
| `game` | C/C++ compiler, CMake/CTest; pinned raylib/ENet; headless framework, native I/O fixtures, Beacon network and Sandline tests; X11/OpenGL development libraries, Xvfb/xauth for desktop lifecycle smoke checks |
| `chess` | C/C++ compiler, CMake/CTest; pinned raylib; headless chess rules/controller/render/input/LAN tests plus actual desktop app build and Xvfb frame smoke (same desktop prerequisites as `game`) |
| `chess-native` | Disposable Debian 13 ARM64; pinned raylib; native default Chess CTest and app-only DEB inspection, plus a separate enabled offline shell build and production-loop checks |
| `status` | Disposable Debian 13 ARM64; pinned LVGL; CTest and app-only DEB construction, no live install |
| `video` | Disposable Debian 13 ARM64; pinned LVGL, GStreamer >=1.26 development/runtime plugins, libdrm/libpulse and FFmpeg encoders; CTest and app-only DEB construction |
| `zero-native` | Disposable Debian 13 ARM64 with CMake/Ninja/dpkg tools; foundation builds and install/reinstall/remove/purge in empty temporary roots, plus the separate Catalog native app, locked LVGL, OpenSSL development headers, CTest and app-only DEB inspection |

The workflow declares system prerequisites. Native checks reject a substitute
distribution or architecture. Downloaded library archives use existing locks,
verified SHA-256 and fresh extraction per run. Installed toolchain packages are
recorded rather than frozen; this is not a byte-identical build claim.

Required missing tools, test groups, failures and skips fail a selected suite.
Some tests intentionally skip on macOS or when socket binding is restricted;
that local result is incomplete evidence, not a reason for CI to waive them.
Video preflights FFmpeg encoders and GStreamer plugins and checks registered
media tests so missing prerequisites cannot silently remove playback coverage.
Video also compiles a separate SDL-enabled UI target and runs only its command
and shared-model checks with dummy SDL drivers. The native package remains
SDL-disabled. Chess/Video require their new U1 command/layout groups; generated
preview images and semantic bounds JSON are retained in the evidence artifact.

Status packages use shell support disabled. A separate enabled build requires
the [L1 transport, input and production-loop fake-shell checks](shell-link-status.md)
in addition to the model, CLI and headless renderer tests. No device node or
actual Gh0st process is used; passing does not establish physical compatibility
or a 100 ms pause guarantee.

Video requires [checkpoint and blocking-teardown checks](video-checkpoint-lifecycle.md).
A separate native guard build verifies refusal of linked launches before any
resources are acquired; it does not enable live shell playback. A2 timing JSON
is retained with the suite evidence. The normal package disables this guard,
and the existing SDL command checks remain separate.

The [A1 native event-wait and idle GUI checks](status-idle-loop.md) are required
in both Status configurations. They exercise real pipes/socket polling and the
production loop with a memory display. Matched PR #8 comparisons remain a
separate recorded experiment; hosted CI does not fetch an old baseline.

The [A3 offline Chess checks](game-chess-lifecycle.md) cover clock expiry,
cooperative AI cancellation, stale results, save failures and native framebuffer
ownership. The `chess-native` suite builds the actual experimental native app
and exercises its loop with socket peers and wrapped evdev/framebuffer calls.
It also inspects a separate default package with shell support disabled. Game
checks preserve real elapsed-time gaps and run desktop lifecycle checks under
Xvfb. Nested A3 measurement JSON is retained with the evidence. These fixtures
do not establish physical input/display compatibility or a hard pause deadline.

Zero foundation lifecycle roots use the existing `--force-depends` inspector and
empty-root harness. They verify inactive package contents and scoped dpkg
behavior; they do not prove the installed runtime closure, patched native Wizard,
offline activation, PAM/VT, boot or hardware. No suite starts Lima, tunes power,
flashes a card or contacts the handheld.

C1's [separate Catalog checks](qualified-catalog.md) run in the existing Python
and `zero-native` suites, keeping the eight-suite release contract. Catalog
changes select both signature/cache regressions and native UI/package checks,
including the unchanged original Store guard tests. Native previews and semantic
bounds under `c1-previews/` are retained. No Catalog package is installed in the
worker or added to the approved Store catalog/default image.

F2/H1's [observation tooling](baseline-observations.md) has required Python
fixture tests for read limits, sysfs aliases, cancellation, incomplete evidence
and comparison boundaries. Scoped observation changes select documentation and
Python checks. CI infrastructure changes still select all eight suites. Fixtures
use private mock roots; CI never runs collection against the worker's hardware
or turns synthetic observations into Z4/Z5/H1 acceptance.

## Workflow selection and evidence

`.github/workflows/checks.yml` runs an always-present planner and one
**compatibility gate**. Documentation-only changes select documentation checks.
Pull requests and main-branch pushes run checks; feature-branch pushes do not
duplicate an open PR's jobs. Manual dispatch selects every suite.
Package READMEs/assets remain package inputs. Shared game code selects games and
both Chess configurations; shared locks, builder/image code, the ledger and CI infrastructure receive
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

The F1 read-only preflight remains a local command. Restore the exact captured
archive and select its clean source checkout; missing retained bytes fail rather
than triggering a replacement download or rebuild. The selected checkout must
contain these preflight tools. Earlier Z3 captures remain historical evidence;
do not relabel them as a later source revision.

On a clean selected checkout:

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
[frozen pipeline](tyrell-zero-pipeline.md).

R1 upgrades `.github/workflows/image-candidate.yml` to manually restored,
hash-selected [immutable candidate assembly](immutable-candidates.md). It
authenticates all eight source suites and compares selected reports with their
retained hosted artifacts. A separate default-branch manual promotion workflow
rechecks that exact candidate and reviewed qualification request before a
serialized immutable prerelease publication. Release tooling regression tests
are required in the Python suite. Neither workflow builds an image or writes a
device; missing physical gates remain explicit and block hardware-alpha claims.
