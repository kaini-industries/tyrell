# Synthetic playback fixture for the Store demonstration

`TYRELL-DEMO.mp4` is 45 seconds of generated geometric motion with quiet beeps.
Keep its matching `TYRELL-DEMO.srt` beside it to demonstrate external captions.
The short filename fits the handheld browser. No film, downloaded content or user
media was used. The optional poster is a decoded frame from the same clip.

This fixture is input to the player while filming the real Cardputer Zero. It
is **not** the phone recording required for a first Store submission and is not
evidence of physical HDMI playback. It does not alter the app or its `.deb`.

The video is H.264 Constrained Baseline, 640 × 360, square pixels, 24 fps; audio
is mono AAC-LC at 48 kHz. Each second begins with two white frames and an 80 ms
880 Hz beep whose source peak is 0.10 (−20 dBFS). Start with moderate player/TV
volume. The moving pattern and the player's elapsed-time display make pause and
seeking easy to see. Eight external SRT cues cover the clip; subtitles start disabled in the
app and can be selected with T. The clip can be restarted with Space after EOF.

Use the checked-in media directly. To regenerate into a new directory on a
computer with FFmpeg, ffprobe and the `libx264`/AAC encoders:

```sh
python3 store/tyrell-video/0.1.6/demo/generate.py --output /path/to/new-directory
```

The generator downloads nothing and refuses existing output filenames. It
encodes in a private temporary directory, publishes without replacing files,
checks the codec/dimensions/duration and 4 MiB budget, and decodes the full clip
to verify it. `manifest.json` records the exact command arguments, tool version,
probe results, generator hash and output checksums. Commands run in a private
working directory and use portable filenames. Encoded bytes may differ with
other FFmpeg/library versions; the manifest pins the supplied files.

The newly generated fixture, poster, caption text and generator are supplied
under the project's [MIT license](../LICENSE.txt).
FFmpeg is a generation tool and is not bundled here. The pattern comes from its
`testsrc2` filter; audio is a mathematical sine wave gated by `aevalsrc`.
