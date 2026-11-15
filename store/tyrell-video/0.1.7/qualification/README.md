# Candidate device qualification

These are sanitized records for the exact 0.1.7 package identified by its
SHA-256 in each result. They are separate from the rendered listing previews,
synthetic demo media and authentic phone recording required for first-app
review. No personal movie frames, filenames, credentials or private source
tree are included.

`startup-recovery.json` separates five ordinary hardware-decoder starts under
temporary buffer pressure from controlled KMS fault injection. The latter
deliberately disables kmssink's DMA-import recognition and fails one or two
distinct KMS pool activations. The installed app is unchanged; the real sink
produces the error. This is not a reproduction or occurrence-rate measurement
of natural CMA exhaustion. Device logs capture the sink warning and app retry
reason; domain/code eligibility is tested separately by the app's policy test.

The before/after PNGs are actual handheld framebuffer captures, both paused at
0:20 across subtitle activation. The third capture confirms the selected SRT
track. They cannot prove TV scanout timing, audio sync or caption readability;
those observations are recorded separately in `user-observations.json`.

`endurance.json` records the final software-recovery run and its measurement
limits. `hdmi-validation.json` identifies output geometry and test scope.
`lifecycle.json` records connected crash recovery and restoration checks.
`fixture-profile.json` describes the generated, continuous twelve-minute HD
source; host output paths have been reduced to its basename.

## Temporary fault helper

`kms-activation-fault.c` is MIT-licensed qualification source, never installed
by the package. It was compiled in an isolated Debian ARM64 environment:

```sh
cc -std=c11 -shared -fPIC -O2 -Wall -Wextra -Wpedantic -Werror \
  $(pkg-config --cflags gstreamer-allocators-1.0) kms-activation-fault.c \
  -Wl,--no-as-needed $(pkg-config --libs gstreamer-allocators-1.0) \
  -Wl,--as-needed -ldl -pthread -o libtyrell-kms-fault.so
```

For a controlled test only, preload that library into a normal-user player
process with `TYRELL_KMS_FAIL_ACTIVATIONS=1` (or `2`) and
`TYRELL_KMS_REJECT_DMABUF=1`. A mutex-protected qdata marker limits injection to
distinct pool lifetimes; caller/library and pool-type checks restrict it to
kmssink. No bus messages are synthesized. The recorded library checksum pins
the tested binary. The helper is removed after qualification and does not
change firmware, decoder ranks, the system audio default or Store permissions.

The test results and authentic recording remain subject to Store maintainer
review. Preparing this folder performs no upload or publication.
