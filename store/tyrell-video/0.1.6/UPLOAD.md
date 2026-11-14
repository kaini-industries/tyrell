# Tyrell Video 0.1.6 submission handoff

This is a prepared draft. Nothing in this archive has been submitted to the
CardputerZero Store. The real-device demonstration recording is still required;
physical TV playback testing of the 0.1.6 nonroot helper is pending.

Extract the archive. On Linux/macOS with `sha256sum`, verify it with
`sha256sum -c SHA256SUMS` from this directory. On macOS, `shasum -a 256 -c
SHA256SUMS` is equivalent.

The file to select in [the developer portal](https://dev.cardputer.cc/) is
`tyrell-video_0.1.6-1_arm64.deb`, SHA-256
`3e6b8f50e0f076e783943e77f61e2b57b856a7168f44886dd7d7646013fb239f`.
The ZIP itself is not a Store upload format.

Use `portal-fields.json` as a form reference. It is not an import file. Enter
the title, summary, description, Media category, author, MIT license and proposed
share code `TYVD`. Enable background service and external display; leave the
other five declarations off. Upload `assets/icon-256.png`, followed by the four
numbered screenshot PNGs. They are rendered UI previews and the description
discloses that. Keep email privacy enabled and leave the optional source URL
blank. The source repository's visibility is not changed.

Choose the GitHub account intended to own the Store package. The first uploader
owns it for subsequent updates; the author display name does not choose the
owner. Recheck title/share-code availability immediately before submission.
`TYVD` was unused when this draft was prepared, but has not been reserved.

Read `recording-guide.md` and use the separate synthetic files under `demo/` to
record the actual app on a physical CardputerZero. Neither those MP4 contents
nor the UI previews are the required demonstration recording. Add the authentic
recording and factual `reviewer-notes.md` material to the generated PR when
submitting. A first app needs maintainer approval after the video review.

Current `czdev publish --deb ./tyrell-video_0.1.6-1_arm64.deb` from this extracted
directory can alternatively use `app-builder.json`. That command uploads a
public fork release and creates a PR; it is an actual submission action.
The web portal is the simpler route with an omitted source URL and hidden email.

`validation.json` records package and metadata checks. `package.json` pins the
binary and source revision. Files under `package-checks/` are sanitized evidence,
not source code or a new TV qualification. The package is unchanged from the
tested 0.1.6 build.
