# Tyrell Video HDMI television validation

November 13, 2026, on the existing Debian 13 ARM64 CardputerZero and an LG TV
SSCR2. This is the first attached-display qualification; earlier reports marked
HDMI hardware testing pending.

Version 0.1.2 adds an automatic desktop console handoff; see the
[follow-up validation](video-hdmi-handoff-validation.md). The setup below records
the earlier 0.1.1 qualification conditions.

## Findings and correction

The TV advertised progressive 720p60 and 1080p modes. The normal user could open
`/dev/dri/card0`; the internal SPI display remained on the separate `card1`.
The active labwc desktop owned HDMI. The player correctly refused that owner
and retained LCD playback. LightDM was temporarily stopped for dedicated HDMI
tests, and a runtime factory-precheck mask prevented interference during hotplug.
No firmware or boot configuration was changed.

The first actual TV run exposed a performance failure that disconnected-output
checks could not detect. The user confirmed correct proportions but slow video
and beeps that did not match the white flashes. The pipeline converted and
scaled every frame to the display's 1280 x 720 size in software. Merely changing
the output format from BGRx to NV12 or I420 did not solve the bottleneck.

The corrected path owns a black framebuffer for the selected display mode and
uses an unused NV12 overlay plane for native-size video. VC4 scales and fits the
picture in hardware. It explicitly configures BT.709 limited-range conversion,
restores prior plane properties and CRTCs, and releases its background only after
pipeline shutdown. Square-pixel negotiation handles anamorphic inputs without
forcing normal videos to the display's full resolution.

GStreamer clock synchronization and QoS remain enabled. `skip-vsync=true` avoids
an additional wait after VC4's atomic plane commit. GStreamer documents this
option for atomic drivers to avoid double vsync. [GStreamer 1.26 kmssink source](https://github.com/GStreamer/gstreamer/blob/1.26.2/subprojects/gst-plugins-bad/sys/kms/gstkmssink.c#L2555)

| Experimental path | Wall time | Media position | Sink buffer arrivals | QoS drop statistic | CPU time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Forced 720p BGRx / software scaling | 85.00 s | 82.77 s | 353 | 919 | 88.88 s |
| Forced 720p NV12 / software scaling | 12.01 s | 9.84 s | 48 | 102 | 12.66 s |
| Forced 720p I420 / software scaling | 12.00 s | 9.75 s | 53 | 92 | 12.87 s |
| Native NV12 / HVS scaling | 12.01 s | 9.98 s | 205 | 37 | 8.85 s |
| HVS, two conversion workers, relaxed lateness | 60.01 s | 58.22 s | 1,264 | 136 | 48.00 s |
| HVS, no additional vsync wait | 32.00 s | 30.07 s | 729 | 0 | 25.52 s |

The source was a 640 x 360, 24 fps H.264/AAC flash-and-beep clip. Each second
starts with a 125 ms white flash and matching 880 Hz beep. These numbers count
buffers reaching the final sink, including preroll; they are not exact scanout
counts. QoS is the maximum reported per-element statistic, not an additive count
of every lost frame. The final comparison delivered approximately 24 buffers
per second with no reported QoS drops. The worker/lateness changes were not
retained.

## HDMI audio setup

The HDMI card initially had profile `off`; only the onboard sink was exposed.
Enabling `pro-audio` failed to create an HDMI sink: raw `hw:0,0` offered only
`IEC958_SUBFRAME_LE`, which that path could not negotiate. The ALSA HDMI PCM
supplies the required subframe conversion.

A temporary user-session PulseAudio-compatible sink enabled TV sound:

```sh
pactl load-module module-alsa-sink \
  device=hdmi:CARD=vc4hdmi,DEV=0 \
  sink_name=tyrell_hdmi_acceptance \
  channels=2 channel_map=front-left,front-right \
  rate=48000 format=s32le mmap=false
```

Record the returned module ID and use `pactl unload-module ID` after playback.
The failed pro-audio profile was returned to `off` before loading this sink.
The system default remained the onboard sink throughout; only the test player's
stream used the HDMI sink. A fresh player load selected the exposed HDMI sink
automatically. The stock image still needs a working exposed HDMI audio sink
for this preference; otherwise the player uses its documented onboard fallback.
[PipeWire module options](https://docs.pipewire.org/page_pulse_module_alsa_sink.html),
[ALSA vc4 HDMI conversion](https://github.com/alsa-project/alsa-lib/blob/master/src/conf/cards/vc4-hdmi.conf).

## Final package and physical checks

The tested package is source `src-8432c1ea6942`, SHA-256
`c44b07e53299e56e3f394bd61430b2f8da8ae1117b05bdecc8dbd468c71903f0`.
All 19 CTests and builder package install/remove checks passed. The user confirmed smooth video with matching beeps, a working handheld
controller, readable SRT captions, and manual return to HDMI with O. A longer
clip and at least 15 seconds unplugged confirmed frozen/paused LCD fallback.
A five-second unplug was inconclusive because the BCM2837 connector status
relies on kernel polling; the earlier short-clip capture was also at EOS.

The user shortened the sustained test to ten minutes. The native APPLaunch run
completed 607.925 seconds of media playback in 612.951 seconds wall time, then
exited cleanly through the normal signal handler. The longer source file was
stopped before EOF; this result covers a ten-minute playback window.

- 14,591 buffers reached the HDMI sink, approximately 24 per media second.
- No reported QoS drops, playback error, or output fallback occurred.
- Average CPU: 30.5846% of one core; peak app-reported RSS: 65,360 KiB (63.8 MiB).
- Twenty-one resource samples covered 602.5 seconds. After the first minute,
  sampled RSS stayed between 64,744 and 65,624 KiB; descriptors stayed at 41 and
  threads at 14. Median sampled CPU was 30.03% of one core.
- HDMI remained connected in every sample. Available system memory stayed
  above 111,416 KiB. The live HDMI PCM stream was RUNNING; a mid-run temperature
  reading was 51.5 C.

Normal exit restored the pre-test 1920 x 1080 at 60 Hz CRTC mode, and the
owned video overlay was no longer active. The original Video launcher entry,
LightDM activity and HDMI audio profile were restored; the temporary audio
module and runtime precheck mask were removed. All 12 protected hashes and the
kernel match the before inventory. APPLaunch retained PID 1064 with zero
restarts, package verification was clean, and the system audio default, volume
and mute state match their starting values.

The computer-only HDMI conversion profile was subsequently reduced to 640 x
360, matching the smooth test. Its aspect-preserving conversion test now covers
both profiles; all six host-tool tests and 20 builder tests passed. The tested
package retains its exact build snapshot; its native playback sources are
unchanged by this later computer-tool adjustment.

## Scaling

During native HDMI playback of the 320 x 170 square-pixel baseline, DRM reported
an NV12 source rectangle of 320 x 170 and destination 1280 x 680 at (0,20) in a
1280 x 720 mode. Both dimensions scale by exactly four, with 20-pixel bars above
and below; there is no source crop or aspect stretch. A software negotiation
check also converted 720 x 576 with SAR 16:15 to 768 x 576 with SAR 1:1, retaining
the 4:3 display aspect.

The 640 x 480 calibration card used a 960 x 720 destination at (160,0), with
160-pixel side bars. The user confirmed the cyan circle and pink square were
correctly shaped and every edge of the white border was visible. This checks
both application aspect fitting and overscan on this particular TV.

The tested display is a 16:9 LG TV. Other displays and unusual EDID physical
aspect ratios need separate qualification.

## HD-source playback limits

The HDMI signal is HD: testing used 1280 x 720 at 60 Hz, and the restored desktop
uses 1920 x 1080 at 60 Hz. This is separate from decoding and presenting every
frame of an HD source file. M5Stack specifies 1080p30 video output and
H.264/MPEG-4 decoding for this board. [Vendor specifications](https://docs.m5stack.com/en/CardputerZero).

Short 24 fps H.264/AAC fixtures each contained 96 frames. With the current app
and `v4l2h264dec`, only 32 buffers reached the sink for the 720p fixture and 13
for the 1080p fixture; both reported substantial QoS drops. Limited-range BT.709
VUI-tagged variants produced the same 32 and 13 arrivals. Retagging changed
metadata only and was a negotiation experiment, not a pixel-colour validation.
Both HD-source profiles are **degraded in this tested pipeline**. This does not
establish an intrinsic hardware decoding or HDMI resolution limit; further
format/conversion profiling is needed before promising smooth HD-source playback.

The 1080p source still had correct geometry: DRM reported a 1920 x 1080 source
and a 1280 x 720 destination, with no crop. A correctly scaled image alone is
therefore insufficient to establish smooth playback.

Raw evidence is kept locally in ignored
`device-results/video-hdmi-television-evidence/`, including the native report,
resource samples, profile comparisons, DRM snapshots and restoration checks.
Temporary test files were removed from the device after collection.

## Release 0.1.1

The rebuilt ARM64 package is `tyrell-video_0.1.1-1_arm64.deb`, source
`src-c388ca9ecd83`, SHA-256
`06ff072275a893755ec26c3c73f9b1709c6d20c41f7a29779019ad92d393b623`.
Its ten native source files match the HDMI-tested `src-8432c1ea6942` build; the
release adds version/documentation metadata and the conservative computer-side
conversion preset. The fresh build passed all 19 CTests and package
install/remove checks; all 26 host checks passed.

Version 0.1.1 was installed on the Zero and verified using its version output,
package integrity check, diagnostics and a deterministic UI preview. All 12
protected hashes, the kernel and APPLaunch process remained unchanged. The
normal launcher entry and starting desktop/audio setup remain in place.

The [GitHub release](https://github.com/kaini-industries/tyrell/releases/tag/tyrell-video-v0.1.1)
provides the Debian package, checksums, build/source manifests, validation
summary and computer-side conversion tool.
