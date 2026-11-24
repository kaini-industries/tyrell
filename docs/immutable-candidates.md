# Immutable candidates and explicit promotion

R1 packages selected, verified bytes into a development candidate and provides a
separate manual promotion path. It does not build a replacement image, start the
builder, install a package, write a card or update a device. Existing images and
retained app packages keep their original source and qualification identities.

The first image adapter supports frozen Tyrell Zero exports and requires the
`tyrell-zero` target. Historical stock-derived and full-source control images
are not silently imported through that adapter. App-only candidates can select
their explicitly qualified runtime target independently.

There are three separate decisions: whether the files form a consistent
candidate, whether its exact source and candidate checks passed, and whether its
advertised device scenarios have enough reviewed evidence for publication.
Passing one decision does not substitute for the others.

## Source, tooling and retained inputs

The candidate source commit identifies the code that produced its binaries and
captured image inputs. The trusted tooling revision identifies the reviewed
release scripts that inspect and package them. These may differ. A later tooling
commit must not relabel an older capture as newly built code, and a later
promotion request must not change the candidate's source identity.

Restore the exact retained inputs and completed export selected by the review:

1. Identify their retaining run/artifact or storage location and record expected
   SHA-256 values before assembly.
2. Restore those bytes to ordinary directories. Select explicit paths rather
   than a mutable `latest` pointer or the convenience image-artifact symlinks.
3. Verify the selected source checkout, input archive, export inventory and
   manifests with the trusted tooling. Captured recipes are data; assembly never
   executes them or silently downloads replacement dependencies.
4. Assemble a new candidate only after these checks pass. A missing or changed
   capture is an error, not permission to reconstruct an equivalent archive.

The existing [captured-input preflight](continuous-integration.md) remains useful
before construction. It establishes archive and source consistency, not complete
vendor closure, build readiness or device support. A fresh checkout cannot
recreate unavailable retained inputs. If a rebuild is required, use the
[frozen image pipeline](tyrell-zero-pipeline.md), create a new capture and retain
its own identity. Stage2 resume stays bound to its original capture.

Run the trusted tooling with a separate clean source checkout when necessary:

```sh
python3 scripts/release/candidate.py assemble \
  --selection /path/to/selection.json \
  --inputs-root /path/to/restored-inputs \
  --repository-root /path/to/selected-source-checkout \
  --output-root build/release-candidates \
  --report build/ci/candidate-assembly.json
```

The selection uses schema 1 and contains `release_version`, `source_commit`,
`target`, `optimized_power`, `image`, `packages`, `checks` and `documents`.
Paths are relative to `inputs-root`, and selected files carry expected `sha256`
values. An image selection pins its export directory, host-manifest SHA-256,
input-archive SHA-256 and original guest-manifest SHA-256. Each DEB selects its
package/version/architecture and build report; the report must bind that exact
package hash. `checks` selects exact passing source reports. A document has a
path, SHA-256 and role: `qualification`, `known-issues`, `recovery`, `license`,
`inventory`, `input-manifest` or optional `evidence`. Qualification, known issues
and recovery are single documents; the other required roles may have several.
The selection is a reviewed input, not a shell script.

The [read-only example selection](../research/r1-example/selection.json) uses
exact PR #11 artifacts from source `6be169bcfccf9b6760533c52cc210ff37da7556e`.
Its retained workflow artifacts can expire, its device acceptance remains
untested, and no promotion request was created for it.

For an image, `optimized_power` must agree with the captured power preset.
App-only candidates cannot advertise an optimized image power claim.

## Candidate contents and identity

The selection names the intended image and/or app DEBs, their exact identities,
and the accompanying evidence. Candidate assembly records all selected bytes in
a deterministic release manifest and `SHA256SUMS`. The manifest defines the
candidate identity; its deterministic directory/archive representation permits
independent verification without mutating the candidate.

The ID is the SHA-256 of canonical `release-manifest.json` bytes: sorted keys,
two-space indentation, ASCII JSON and a final newline. It excludes itself from
its file inventory. Assembly produces `candidate-ID/` and `candidate-ID.tar`;
the tar has deterministic member ordering and normalized metadata. Reassembly
with the same complete selection produces the same candidate and archive bytes.
Assembly refuses occupied output paths rather than replacing them.

The bundle carries, as applicable:

- The compressed image, app DEBs and an optional selected bmap.
- Source and input manifests, image input capture, original guest manifest,
  host export augmentation, verification report and package inventories.
- A qualification matrix with advertised targets and scenarios, including
  missing or failed acceptance; known issues and recovery guidance.
- License notices and the selected provenance/evidence files needed to review
  the package and image identities.
- A complete file inventory with names, sizes and SHA-256 values, plus checksums
  for the manifest and other bundle files.

For images, assembly reuses the existing capture/export verifiers and streams
the XZ payload to check its raw SHA-256. That digest must agree with the image
verification's unchanged before/after identity and manifest payload identity.
The export must agree with the original verified guest manifest, selected
attempt, captured-input archive and exact source. Rehashing a modified export
does not turn it into the selected export.

Only declared regular files enter the bundle. Duplicate names, redirected paths,
symlinks, traversal, changed digests and incomplete inventories fail. Reusing an
occupied candidate destination never overwrites accepted bytes. Changing a
binary, evidence file, qualification claim or known issue requires a new
candidate identity; promotion metadata is recorded separately.

Large archives use ordered parts of at most 1 GiB and an index that binds the
whole archive's SHA-256/size and every part's name, position, size and SHA-256.
Verify all parts and the reconstructed archive before reading its manifest.
This keeps individual assets below GitHub's per-file limit of 2 GiB. GitHub
currently permits up to 1,000 release assets; automatic source ZIP/tarball links
are repository snapshots, not substitutes for these verified binary assets.
See [GitHub's release storage rules](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

Independent local verification requires both the expected manifest ID and
archive SHA-256, selected outside the archive:

```sh
python3 scripts/release/candidate.py verify-archive \
  --archive /path/to/candidate-ID.tar \
  --sha256 EXPECTED_ARCHIVE_SHA256 --candidate-id EXPECTED_MANIFEST_SHA256 \
  --output-root build/candidate-inspection \
  --report build/ci/candidate-verification.json
```

Use `verify --directory PATH --candidate-id ID --report PATH` to check an
already restored directory. The archive verifier extracts only checked regular
members into a new destination; an occupied extraction destination is preserved.

## Candidate checks and trusted execution

Candidate assembly and promotion are manual workflows. Push and tag events do
not publish anything. A retained, file-validated development candidate can be
useful even while device rows remain untested; its labels must keep those limits
visible.

The source checks must come from a successful authenticated `checks` workflow
run in this repository for the candidate's exact source SHA. R1 requires the
full eight suites and compatibility gate, rather than treating a partial scoped
run or an arbitrary green report as release evidence. The promotion path also
requires the successful assembly/verification run for the exact candidate
identity. The source run, assembly run, candidate bytes and later reviewed
request must agree.

Downloaded JSON is not its own authority. The workflow verifies GitHub run
metadata and artifact provenance before interpreting results. A source check
cannot certify a different binary, and a passed candidate check cannot qualify
hardware. Missing, expired or changed retained artifacts fail closed.

The upgraded `.github/workflows/image-candidate.yml` authenticates the complete
source run and compares each selected report with its freshly downloaded hosted
copy. Packages must be the exported artifacts named and hashed by those reports.
It then assembles and independently verifies the archive, retaining only the
candidate archive and its run-bound `candidate-verification.json`. The older
`scripts/ci/candidate.py` remains a read-only capture-preflight CLI.

Assembly dispatch always takes `source_sha`, `checks_run_id` and
`selection_sha256`. It supports two input modes:

- **Reviewed selection file:** set `selection_path` to an exact file in the
  trusted tooling checkout. Staging restores the eight canonical hosted source
  check artifact trees directly and copies declared documentation from reviewed
  repository files after checking every expected hash. An app-only candidate
  therefore needs no separately uploaded input artifact.
- **Retained input tree:** set `artifact_run_id` and `artifact_name` together.
  That restored artifact contains `release-selection.json` at its root and every
  selected relative file. This supports already captured image exports without
  rebuilding them. A reviewed `selection_path` can also be combined with an
  already retained image-input tree; its explicit hashes still govern selection.

At least one mode is required, and half of an artifact run/name pair is invalid.
The workflow does not upload private local inputs automatically. Image exports
and captures must already be retained; missing bytes must be restored before
dispatch without substituting a new capture or hidden rebuild.

Staging preserves the reviewed selection bytes as `release-selection.json`; it
does not rewrite the recipe to fit downloaded artifacts. Each selected check
report keeps the basename `SUITE.json` under a distinct, non-overlapping parent,
for example `checks/docs/docs.json` and
`checks/chess-native/chess-native.json`. Evidence paths stay relative to that
report's parent. Document paths retain their repository-relative names, so a
reviewed `research/releases/known-issues.md` is staged under that same relative
path. A hash mismatch fails rather than replacing an already selected file.

Write permission belongs only to the final publication job. It runs the trusted
release tooling, not executable content from a candidate archive or a script
chosen solely by the candidate source revision. Read-only verification completes
before publication is available. No downloaded recipe, maintainer script or
candidate-provided executable runs during release inspection.

## Reviewed promotion and qualification

Promotion starts with a separately reviewed request in a later trusted commit.
It pins the candidate identity and archive digest, source, intended
release/version, advertised target, release level and exact qualification evidence. That
request is inspectable before dispatch and does not edit the candidate manifest.
Changing a requested candidate or advertised claim requires another review.

Adding new device receipts or changing the sealed qualification matrix requires
a newly assembled candidate, even when the same binaries are reused; a promotion
request alone cannot upgrade `not-tested` rows.

Manual dispatch selects the assembly run, whose retained report identifies the
source-check run. Both are authenticated against the exact identities approved
by the request; the request does not embed a mutable latest-run selector.

The request uses schema 1 and kind `tyrell-promotion-request`. It pins
`candidate_sha256`, `archive_sha256`, `source_commit`, `release_version`,
`target`, `level` and `optimized_power`; explicitly acknowledges the exact known
issues and recovery document hashes; and selects `qualification_path` plus its
SHA-256 from the reviewed checkout. That qualification file must be byte-for-byte
the candidate's bound matrix. Image promotion also requires
`manual_base_selection: true` and the exact `base_image_sha256`; an app-only
request uses `false` and `null` for those fields.

Qualification rows name `artifact_sha256`, `target`, `gate` (`z4`,
`app-acceptance` or `z6`), `result`, `evidence_scope`, bound evidence paths/hashes
and explicit limitations. Required missing gates stay present as `not-tested`
rows. A supported Gh0st app also needs its original-M5 app acceptance. Receipt
review is the human authority for device claims; a candidate-supplied success
flag alone cannot grant that authority.

| Requested level or claim | Required evidence |
| --- | --- |
| Development artifact | Exact files/input/source binding and required successful source/candidate checks; missing device acceptance remains explicit. |
| Hardware-supported alpha | The same checks plus reviewed acceptance for every advertised exact image/package, target and scenario; Zero requires its Z4 gates and included apps require their applicable app acceptance. |
| Optimized power or battery claim | The relevant hardware acceptance plus Z6 evidence for the exact image and selected power policy. A smaller image or passing file check does not establish battery improvement. |

Qualifications bind exact artifact SHA-256 values, not only version strings,
source revisions or architecture. The [compatibility ledger](compatibility-matrix.md)
denies release approval and distinguishes historical artifacts from current
sources. For example, the retained Status package's automated checks do not
inherit physical results from a differently hashed same-version package.
Current Zero/control evidence remains file validation; pending Z4/Z6 rows cannot
be promoted by changing their labels.

Initial base updates stay manually selected and reviewed. Recovery instructions
must identify the known-good image and retained inputs, explain how to preserve
the original card/firmware, and state the acceptance limits. This work adds no
automatic channel movement, in-place base migration or card flashing.

## Immutable GitHub publication

GitHub release immutability is a prerequisite for publication. The read-only
repository preflight during R1 implementation observed it disabled; no repository
setting was changed. Maintainers can enable it under repository **Settings →
Releases → Enable release immutability**. Enforcement applies to future releases,
so enabling it does not retroactively protect older assets. See
[GitHub's immutability setup](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes).

The workflow checks the setting again before publication and refuses a missing,
disabled or unreadable result. The GitHub API documents repository
**Administration: read** for this endpoint; the workflow token may need a
separate maintainer-provided read credential to inspect it. Keep that permission
separate from release-writing authority, and do not paste credentials into
selection files or requests. See the
[immutable-release setting endpoint](https://docs.github.com/en/rest/repos/repos#check-if-immutable-releases-are-enabled-for-a-repository).

The optional repository `RELEASE_READ_TOKEN` secret supplies only that setting
read, exposed as `GH_RELEASE_READ_TOKEN`; the ordinary workflow credential
remains separate. `.github/workflows/promote-candidate.yml` runs only by manual
dispatch on the default branch. Its read-only review job prepares the live
policy result, and its publication job repeats the live checks before any write.
`github.py publish-reviewed` is the trusted write entry point.
`publish.py` used directly only prepares local parts and metadata; it has no
publish switch and cannot convert a supplied approval JSON into a release.

Promotion dispatch takes `candidate_run_id`, `candidate_id`, `archive_sha256`,
`request_path` and `request_sha256`. The selected request and approved
qualification document must already be present in the default-branch checkout;
the dispatch does not submit a new approval body or bypass its review.

Publication is serialized. It refuses an existing release or tag and rejects
changed bytes for an already accepted package/version identity. It does not use
asset clobbering, replace an old manifest or move a tag. The final job creates a
new tag atomically at the exact source, creates a draft, uploads all selected
assets, verifies their digests against the candidate
and multipart index, then publishes a prerelease with `latest` disabled. An
interruption can leave a reserved tag and/or draft for review. The job does not
automatically delete, overwrite or resume that state, and partial upload is not
a completed promotion. A failed attempt does not authorize replacement of
earlier accepted assets.

The retained publication receipt records observed tag reservation, draft
creation and verified-asset stages. If a later step fails, it keeps the last
recorded state and any observed draft ID for inspection. An API failure can still
leave an uncertain remote outcome; inspect the selected tag/release before
choosing any manual recovery action.

Workflow completion must identify the created release and observed immutable
state before claiming publication. Local fixture tests and dry checks do not
create a release or prove the live write path. R1 implementation itself creates
no release, enables no setting and promotes no package/image; those actions need
their concrete reviewed request and manual workflow dispatch.
