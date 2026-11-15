# Real-device demonstration recording

An authentic recording of the current Tyrell Video 0.1.7 run has been supplied.
The approximately 17.1-second original and full derivative were reviewed. They
show the real TV, handheld controller and readable 0.1.7 test captions, using
only the generic fixture. Recorded sound contains 880 Hz pulses about one
second apart; this is not a calibrated audio/video-delay measurement.

The [prepared sharing copy](recording/Tyrell-Video-0.1.7.mp4) is SDR BT.709
H.264/AAC with source metadata removed. See the [manifest](recording/manifest.json)
for provenance and preparation details. It is ready to attach to the eventual
first-app PR; no public upload has occurred. The older unreceived 0.1.6 footage
is no longer needed to supply the demonstration.

Hardware qualification of the new software retry passed for the tested profile. The
recording does not by itself complete those tests or demonstrate every recovery
case. Keep its version and conditions distinct from the diagnostic results.

The first-app review needs a short authentic video of the app running on a real
CardputerZero. A phone recording that shows the screen and keys is suitable.
If demonstrating HDMI, include the television and handheld together. A rendered
preview, controller framebuffer capture or this synthetic clip alone does not
establish what the physical TV displayed or sounded like. Store policy does not
specify a 45-second minimum or require every control test to appear in the
demonstration. The supplied short clip can be used for review; additional
footage below is optional, not a new prerequisite.

## Optional additional footage

Copy `demo/TYRELL-DEMO.mp4` and matching `demo/TYRELL-DEMO.srt` together into
`~/Videos` on the Zero. This generic, versionless 45-second geometric test
pattern and quiet tone fixture is unchanged; it is content to play while filming,
not the demonstration recording itself. Do not use personal movie footage,
private filenames, account information or network settings.

For a longer optional walkthrough, about 45–60 seconds is convenient:

1. Wake/unlock APPLaunch, open Tyrell Video, select `TYRELL-DEMO.mp4` and press
   Enter. Show motion and sound for several seconds. Space replays after EOF.
2. While the elapsed time is before the end, press Space to pause briefly,
   then Space to resume. Keep the elapsed time visible to show continuity.
3. Press T and move Down/S to a row starting **Subs:**, commonly
   `Subtitles 1 (SRT)`, then press Enter. The **Subtitles: Off** row disables
   captions. Show readable captions with time continuing from the same point.
4. Use Right/D and Left/A to seek ten seconds, then Up/W or Down/S for volume.
5. Press Esc to return to the browser, then Q to return to APPLaunch.

For a separate HDMI segment, show correct fitting, readable captions, TV sound
matching the white flashes and no desktop cursor. Unplug HDMI for at least
15 seconds: the LCD should receive a paused picture at the same position.
Reconnect, select O and resume with Space; show TV picture and sound returning.
Do not claim the 0.1.7 retry was exercised merely because normal playback worked.
Its occurrence must be supported by the actual diagnostic/qualification record.

Review the recording before attaching it to the eventual Store PR. Keep the
original and describe its actual app version and test conditions. Maintainer
review, not the existence of a synthetic clip or a passing build, decides
first-app approval.
