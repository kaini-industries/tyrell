# Tyrell Video 0.1.7 handoff

The exact package, device qualification and authentic recording are prepared.
Nothing has been uploaded to the Store; maintainer approval is still required.
Extract this handoff and run `sha256sum -c SHA256SUMS` (or
`shasum -a 256 -c SHA256SUMS` on macOS). The Store does not accept the ZIP itself.

Select `tyrell-video_0.1.7-1_arm64.deb` in
[the developer portal](https://dev.cardputer.cc/), SHA-256
`7ddf4ead5f9d27e44d92ab510b8cca503847af36762d60b315fea62de4450d79`.
The app version is 0.1.7; the Debian version includes `-1`.

1. Log in with the GitHub account intended to own the app. Recheck package,
   title and proposed `TYVD` code availability; the first uploader owns updates.
2. Use `portal-fields.json` as a form reference: Tyrell Video, Media, Tyrell
   contributors, MIT. Leave the source URL blank and keep email hidden.
3. Enable background service and external display; leave the other five flags
   off. Upload `assets/icon-256.png` and the four numbered UI previews. These
   are rendered examples, distinct from real-device evidence.
4. Review the measured limits in `reviewer-notes.md` and `qualification/`.
   Submit is a public upload. Attach `recording/Tyrell-Video-0.1.7.mp4` and the
   reviewer notes to the generated first-app PR for maintainer review.

The supplied 17.1-second phone recording shows the real TV, readable 0.1.7
captions and the handheld. Its entire duration is preserved in the SDR
H.264/AAC copy, with original phone metadata removed. See `recording/manifest.json`
for hashes and review limits. It is ready to attach; neither a 45-second minimum
nor a new recording is required by this preparation. The synthetic `demo/`
clip and listing previews are separate materials.

Current `czdev publish --deb ./tyrell-video_0.1.7-1_arm64.deb` from this extracted
directory is an alternative using `app-builder.json`; it performs an actual
public fork-release and PR upload. Neither path has been run here.

The actual-device checks passed for the measured H.264/AAC profile: one bounded
software-decoder recovery, more than ten minutes of recovered TV playback with
captions, connected crash restoration, normal APPLaunch launch/exit and a clean
final audit. Other codecs/profiles can require conversion. Original package
bytes, source identity and checksums remain fixed. Preparing or sharing this
local handoff does not change source-repository visibility.
