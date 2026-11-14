# Tyrell Video 0.1.6 Store submission

Prepared November 14, 2026. This folder is a reviewable submission draft; it has
not been uploaded to the Store. Use the existing immutable ARM64 package:

[`tyrell-video_0.1.6-1_arm64.deb`](../../../dist/tyrell-video/0.1.6/tyrell-video_0.1.6-1_arm64.deb)

SHA-256: `3e6b8f50e0f076e783943e77f61e2b57b856a7168f44886dd7d7646013fb239f`.
Application version is 0.1.6; the Debian package version is **0.1.6-1**.
Preparation does not rebuild or modify the package.

## Prepared listing

| Field | Value |
| --- | --- |
| Package | `tyrell-video` |
| Display name | Tyrell Video |
| Summary | Local video with subtitles, audio tracks and handheld or HDMI playback |
| Category | Media |
| Author display name | Tyrell contributors |
| License | MIT |
| Proposed share code | `TYVD` |
| Permissions enabled | Background service; external display |
| Source repository | Omitted; the existing source repository remains private |
| Public email | Hidden in the portal |
| Images | One 256×256 icon; four 320×170 rendered UI previews |

[`app-builder.json`](app-builder.json) is the canonical listing for current
`czdev publish --deb` support. It lives here so it does not replace Tyrell's
existing builder. Its version matches the `.deb` version, including `-1`.
[`portal-fields.json`](portal-fields.json) contains equivalent web-form values;
it is a reference, not an import format. The portal accepts entered fields and
PNG files, not a JSON upload. [`permissions.md`](permissions.md) explains the
seven declarations. The detailed public description explicitly labels the
screenshots as previews and states the remaining TV test.

At 2026-11-14 15:21:35 UTC, `TYVD`, the normalized title and package name had
no conflicts in published Store data. This is not a reservation; recheck at
submission time. The authenticated GitHub account that first submits the
package becomes its Store owner. The author display name does not choose that
account. The eventual publisher supplies their own login; no login is guessed
or embedded in this draft.

## Remaining review evidence

The first publication requires an authentic short video of the app running on
a real device, attached to the generated PR for maintainer review. It has not
been recorded. Follow the [recording guide](recording-guide.md), using the
included synthetic demo instead of a personal movie. UI previews and demo
fixtures do not fulfill this video requirement.

The installed 0.1.6 helper passed on-device console access, ownership migration
and crash recovery. Physical TV playback testing of this nonroot helper is
pending at the user's request. Earlier playback-engine HDMI results do not
establish a TV test of 0.1.6. Keep this limitation in the listing and
[reviewer notes](reviewer-notes.md) until a real test supplies new evidence.

## Build the local handoff archive

From the repository root:

```sh
python3 scripts/prepare-video-store.py
```

This creates `build/store/tyrell-video-0.1.6-submission.zip` with the exact `.deb`,
listing, images, demo, review notes and checksums. The script has no network or
publishing action. It verifies the checked-in files and the immutable package
before assembling an explicit file list. Extract it before using its contents;
the Store does not accept the ZIP itself.

## Eventual web submission

After reviewing the prepared material and arranging the real-device video:

1. Open [the developer portal](https://dev.cardputer.cc/) and log in to the
   GitHub account intended to own this package.
2. Select the `.deb` named above. Selecting it performs a local precheck.
3. Leave the optional source URL blank. Copy the listing values from
   `portal-fields.json`, select Media and the two declared requirements, upload
   `assets/icon-256.png`, and add the four screenshot PNGs in numerical order.
   Keep email privacy enabled.
4. Recheck the code/name availability and the declarations. The **Submit to
   AppStore** action uploads the binary and opens the publication workflow.
   This preparation has not performed that action.
5. In the generated PR, add the actual device recording and the factual
   reviewer notes. Wait for the first-app maintainer review; passing automatic
   checks alone does not publish a new app.

Current `czdev publish --deb FILE` is an alternative, run from this folder or
the extracted archive so it finds this `app-builder.json`. That command is an
actual publishing action: it creates a public fork release/PR and should only
be run when ready to submit. The portal is the simpler route for keeping the
source URL omitted and email hidden.

## Validation and sources

[`validation.json`](validation.json) records the current upstream metadata
check, its advisory warnings, published-name lookup and package evidence.
[`SHA256SUMS`](SHA256SUMS) covers the prepared files. Package/build checks are in
the [0.1.6 snapshot](../../../dist/tyrell-video/0.1.6/) and
[helper report](../../../docs/video-store-policy-validation.md).

- [Official listing schema](https://github.com/CardputerZero/AppBuilder/blob/main/docs/APP_BUILDER_JSON.md).
- [Official metadata validator](https://github.com/CardputerZero/packages/blob/main/.github/scripts/store_meta_policy.py).
- [Portal form and upload fields](https://github.com/CardputerZero/dev-portal/blob/main/site/app.js).
- [First-app publication review](https://github.com/CardputerZero/packages/blob/main/.github/workflows/process-web-submission.yml).
