# User-provided real-device recording

`Tyrell-Video-0.1.7.mp4` is the entire 17.1-second phone recording supplied by
the user during the final 0.1.7 HDMI test. It shows the real handheld controller,
the television's moving test pattern and readable captions. The TV test label
identifies 0.1.7. The fixture is generated content being played by the app;
the surrounding device/TV footage is an authentic phone recording.

The original HEVC/AAC MOV remains unchanged. This derivative is H.264/AAC at
1280×720, with actual HLG/BT.2020 HDR to SDR/BT.709 tone mapping for browser
review compatibility. Metadata, chapters and timed-data tracks
were removed. The whole picture, duration and microphone audio are retained;
there are no cuts, screen replacements or added sound. `manifest.json` records
both hashes, the conversion and review limits. `poster.jpg` is an actual frame.

The user separately confirmed smooth TV motion, matching beep/flash sound,
readable captions and the handheld controller in this run. The recording is
not a calibrated latency/frame-rate measurement or a demonstration of every
control and recovery case. Those checks are described in `qualification/`.

Attach this actual clip to the eventual first-app publication PR with the
qualification notes. Its inclusion in a local handoff is not an upload or
maintainer approval.
