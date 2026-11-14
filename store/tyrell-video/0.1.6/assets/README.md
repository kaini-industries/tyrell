# Store visual assets

These assets accompany the unchanged Tyrell Video 0.1.6 package, source
`src-11a4e420adee` from commit `ad4140aeee2de98cec66d85a83eb9d7dbd0ca8e1`.

| File | Size | Purpose |
| --- | --- | --- |
| `icon-256.png` | 256 × 256 | Existing app artwork resized from the package's 80 × 80 icon. |
| `01-browser-preview.png` | 320 × 170 | File browser UI preview. |
| `02-lcd-player-preview.png` | 320 × 170 | LCD player UI preview with a labeled software example scene. |
| `03-tracks-preview.png` | 320 × 170 | Audio/subtitle menu UI preview. |
| `04-hdmi-controller-preview.png` | 320 × 170 | Handheld HDMI controller UI preview. |

The four screenshots are byte-identical copies of the final 0.1.6 builder's
deterministic software-rendered app previews. They contain fixture filenames and
states, with no movie frames or user media. They illustrate the interface; they
are not photographs, TV captures, playback measurements or evidence of physical
HDMI qualification. Identify them as rendered UI previews in the Store listing.
The first-app real-device demonstration video remains a separate review asset.

The icon preserves the existing geometric artwork and aspect ratio. It was
mechanically resized with macOS `sips --resampleHeightWidth 256 256`; the app's
installed icon and package bytes were not changed. The enlargement retains the
source icon's pixel detail.

All images were inspected visually. `manifest.json` records dimensions, source
paths and SHA-256 hashes. The PNG dimensions match the current
[Store schema](https://github.com/CardputerZero/AppBuilder/blob/main/docs/APP_BUILDER_JSON.md):
1–6 screenshots at 320 × 170 and a square icon between 128 and 512 pixels
(256 recommended).
