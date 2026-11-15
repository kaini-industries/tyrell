# Tyrell Video 0.1.7 Store submission

Submitted as [CardputerZero/packages PR #324](https://github.com/CardputerZero/packages/pull/324)
on November 14, 2026. Both automated submission checks passed. The
[recording and qualification notes](https://github.com/CardputerZero/packages/pull/324#issuecomment-6890010512)
are posted; first-app maintainer review and merge remain pending. The app is
not yet published in the Store catalog. See the [submission receipt](../submissions/0.1.7.json).

The package below is unchanged. This folder retains the November 14 prepared
handoff, including its pre-submission `validation.json`, `UPLOAD.md` and reviewer
notes. The receipt records current publication status. Do not rerun the publisher
for this version: use the existing PR.

Package: `tyrell-video_0.1.7-1_arm64.deb`, architecture `arm64`, 686346 bytes.
SHA-256: `7ddf4ead5f9d27e44d92ab510b8cca503847af36762d60b315fea62de4450d79`.
App version is 0.1.7; Debian version is **0.1.7-1**. Build source is
`src-03a5a269b185`, commit `8cba407dcce3675844acc4b203fa78a664044dc3`.
These are the exact bytes installed and tested on the CardputerZero.

## Listing

| Field | Value |
| --- | --- |
| Package / title | `tyrell-video` / Tyrell Video |
| Summary | Local video with subtitles, audio tracks and handheld or HDMI playback |
| Category / license | Media / MIT |
| Author display name | Tyrell contributors |
| Proposed share code | `TYVD` |
| Enabled requirements | Background service; external display |
| Source repository / public email | Source URL omitted; email hidden |
| Listing images | 256×256 icon; four 320×170 rendered UI previews |

[`app-builder.json`](app-builder.json) is the canonical listing for publishing
this prebuilt package. [`portal-fields.json`](portal-fields.json) mirrors the
web form values; the portal does not import that JSON. The authenticated GitHub
account that first submits the package becomes its Store owner. No login or
source URL is guessed. The submission omits the source URL and does not change
the project repository's visibility. Its current public visibility is recorded
separately in the submission receipt.

Current official path, service, dangerous-hook and metadata checks pass. The
metadata has zero errors, with advisory suggestions for translations and a
source URL. Title/package/share-code uniqueness was checked against all 49
published records at the pinned snapshot; recheck at submission. `TYVD` is
proposed, not reserved. See [`validation.json`](validation.json).

## Qualification

All 35 ARM64 CTests and builder package install/render/diagnostics/remove checks
passed. Attached-TV testing of 0.1.6 established geometry, TV audio, captions,
paused hotplug recovery and desktop handoff, but also exposed an intermittent
KMS pool-allocation startup failure. That older draft is superseded.

Version 0.1.7 retries that specific error once with software decoding. Actual
controlled tests verified recovery at the intended paused 0:20 position, caption
time preservation and safe paused LCD fallback after a second injected failure.
The software-recovered HDMI run advanced **615.097 seconds**, with 14,755 sink
render calls and three sink drops. The user confirmed smooth picture, matching
TV sound, readable captions and the handheld controller. Five additional starts
used hardware decoding without triggering recovery. Connected player/helper
crash restoration, normal APPLaunch launch/exit and final device audit passed.

The HD fixture is 1920×816 at 24 fps, H.264 Constrained Baseline/AAC; the TV mode
was 1280×720. These results do not promise smooth playback for every codec,
resolution or bitrate. Controlled fault injection does not reproduce or measure
the incidence of natural CMA exhaustion. The temporary injector was removed;
firmware, CMA configuration and boot settings were unchanged. Detailed scope,
counters and sanitized evidence are in [`qualification/`](qualification/README.md)
and the [validation report](../../../docs/video-hdmi-017-validation.md).

## Authentic recording

[`recording/Tyrell-Video-0.1.7.mp4`](recording/Tyrell-Video-0.1.7.mp4) is the
user-supplied 17.1-second phone clip showing the real TV, readable 0.1.7 captions
and handheld controller. It is ready to attach to the eventual first-app PR.
The entire clip was converted to SDR BT.709 H.264/AAC with phone metadata and
timed-data tracks removed; the original is unchanged. Its [manifest](recording/manifest.json)
pins both hashes. Full decoding and independent review passed. It is not a
calibrated latency or scanout measurement. No 45-second minimum is imposed.

The four listing images are explicitly rendered UI previews. The generic
synthetic `demo/` clip is optional playback content, not the phone recording.
See [`recording-guide.md`](recording-guide.md) for an optional longer walkthrough.

## Archived local handoff

From the repository root:

```sh
python3 scripts/prepare-video-store.py --version 0.1.7
```

Output: `build/store/tyrell-video-0.1.7-submission.zip`. It contains the exact
package, listing, assets, recording, qualification, demo and checksums. Extract
it and verify `SHA256SUMS`. The Store accepts the `.deb` and listing assets,
not the ZIP itself. [`UPLOAD.md`](UPLOAD.md) is the portable submission guide.

Log in at [the developer portal](https://dev.cardputer.cc/) using the intended
owner account, recheck name/code availability, then select the exact package and
listing assets. Leave the source URL blank and email hidden. Submission is a
public upload; attach the supplied recording and [`reviewer-notes.md`](reviewer-notes.md)
to the generated first-app PR. Alternatively, `czdev publish --deb
./tyrell-video_0.1.7-1_arm64.deb` uses this prebuilt listing and performs a public
fork-release/PR upload. The original archive was assembled before publication; the subsequent publisher
run is recorded in the receipt.

## Submission record

The publisher account is `beatsforthemind`. The official publisher uploaded the
exact `.deb` to that account's Store-package fork and created the seven-file
metadata/image PR. The recording and poster are hosted alongside the package;
release-server SHA-256 digests match all three prepared files. No app source
was added to the Store PR. This submission did not change the main project
repository's visibility. The historical handoff instructions above describe
the preparation and are retained for reproducibility.

## Requirements sources

- [Listing schema](https://github.com/CardputerZero/AppBuilder/blob/main/docs/APP_BUILDER_JSON.md).
- [Metadata validator](https://github.com/CardputerZero/packages/blob/main/.github/scripts/store_meta_policy.py).
- [Portal fields](https://github.com/CardputerZero/dev-portal/blob/main/site/app.js).
- [First-app review workflow](https://github.com/CardputerZero/packages/blob/main/.github/workflows/process-web-submission.yml).
