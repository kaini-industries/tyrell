# CardputerZero 3D display benchmark

This benchmark compares two complete 320 × 170 rendering paths before choosing
the Tanks game's device renderer. It does not change the launcher, firmware,
kernel, SPI rate, services, or boot configuration. No physical-device results
are implied by desktop or virtual-machine smoke tests.

The software executable uses raylib 6.2's `PLATFORM_MEMORY` and `rlsw` backend.
The GLES executable uses an original minimal GLES2 shader and an EGL
surfaceless/pbuffer context. Both generate the same 854 untextured triangles,
camera, colors, moving tanks, depth test, native resolution, and RGB565
presentation. GLES shaders and raylib's fixed-function software implementation
are different renderers; their rasterization/color rounding need not be pixel
identical. This is a display-pipeline comparison using a representative small
scene, not a benchmark of the complete multiplayer game, textures, audio, or
networking.

## Build

The Tanks CMake project adds `packages/tyrell-tanks/benchmark` and supplies
`RAYLIB_SOURCE_DIR` pointing to the extracted raylib 6.2 source. The benchmark
builds its own minimal static `tanks_bench_raylib`; it does not reuse the
desktop game's raylib backend. The important definitions are
`PLATFORM_MEMORY`, `GRAPHICS_API_OPENGL_SOFTWARE`, and
`SUPPORT_CUSTOM_FRAME_CONTROL=1`. The last flag prevents `EndDrawing()` from
performing an implicit copy, input poll, or frame pacing. The benchmark flushes
geometry through `EndDrawing()`, then explicitly times `rlCopyFramebuffer()`
into a preallocated RGBA8888 buffer. raylib 6.2's memory backend still allocates
its small unused internal output buffer; no dependency sources are patched.

Targets:

- `tanks-display-bench-rlsw`: available on macOS and Linux; only C/C++, raylib
  source, math, and threads are needed.
- `tanks-display-bench-gles`: generated on Linux when EGL and GLES2 development
  headers/libraries are available. It links `EGL`, `GLESv2`, and threads, with
  no raylib dependency. The typical Debian build dependencies are
  `libegl1-mesa-dev` and `libgles2-mesa-dev`.

Build either target with the enclosing project's configured build directory:

```sh
cmake --build build/tanks-desktop --target tanks-display-bench-rlsw
# On Linux, after configuring the benchmark-only build below:
cmake --build build/tanks-linux --target tanks-display-bench-gles
```

To configure a native Linux benchmark/test build without windowing dependencies,
run `python3 scripts/tanks deps`, then:

```sh
cmake -S packages/tyrell-tanks -B build/tanks-linux \
  -DCMAKE_BUILD_TYPE=Release -DTANKS_BUILD_DESKTOP=OFF \
  -DRAYLIB_SOURCE_DIR="$PWD/build/game-deps/raylib-6.2" \
  -DENET_SOURCE_DIR="$PWD/build/game-deps/enet-1.3.18"
cmake --build build/tanks-linux --parallel 4
ctest --test-dir build/tanks-linux --output-on-failure
```

Build on ARM64 Linux for the actual device. The executables are under
`build/tanks-linux/benchmark/`. A macOS executable cannot run on the Zero.

## Desktop smoke test

Use `--fb none` to avoid any framebuffer access. Save a PPM frame separately
from the JSON report:

```sh
tanks-display-bench-rlsw --fb none --warmup 0.1 --seconds 2 --frames 30 \
  --fps 30 --output /tmp/tanks-rlsw.json --frame-output /tmp/tanks-rlsw.ppm
```

The PPM is the final RGB565 frame expanded to RGB888; it checks orientation,
geometry and color channel conversion. `rlsw` exports top-first rows, whereas
GLES readback is bottom-first; conversion handles these separately. The
benchmark uses its own `steady_clock` for measurements and pacing, including
on macOS where the upstream memory backend's timer is limited.

A virtual machine may provide llvmpipe instead of a GPU. The GLES executable
prints `GL_RENDERER`, `GL_VENDOR`, and `GL_VERSION` into its JSON. It refuses
software or unrecognized renderers by default. `--allow-software` permits a
functional smoke test and leaves `hardware_accelerated=false` in that report;
such results are not measurements of the CardputerZero GPU. Recognized virtual
renderers such as virgl also do not establish CardputerZero performance.

## Device measurements

Run only one display owner at a time under the normal launcher handoff. The
default `--fb auto` checks `/dev/fb_lcd`, then `/proc/fb` for an ST7789 name,
then the framebuffer's sysfs driver link. It never guesses that `/dev/fb0` is
the LCD. An explicit `--fb /dev/fbN` is available after identifying the native
LCD. Geometry and channel checks refuse anything other than 320 × 170 RGB565,
including the normal HDMI framebuffer. No display mode ioctl is issued.

Use the same conditions for both paths and retain the JSON plus captured
frames. For example:

```sh
tanks-display-bench-rlsw --fb auto --warmup 2 --seconds 15 --fps 30 \
  --output /tmp/tanks-rlsw-30.json --frame-output /tmp/tanks-rlsw.ppm
tanks-display-bench-gles --fb auto --warmup 2 --seconds 15 --fps 30 \
  --output /tmp/tanks-gles-30.json --frame-output /tmp/tanks-gles.ppm
```

Repeat at `--fps 0` for throughput, then at the desired game rate to assess
pacing. `--frames N` is an additional limit; the run stops at the first of the
duration limit, frame limit, SIGINT or SIGTERM. Normal output is JSON to stdout;
`--output path.json` saves it to a file. Startup failures return a nonzero exit
with a clear diagnostic. Initialization, warmup, file export, and screen
restoration are outside the measured window.

By default, each completed frame copies its RGB565 rows to the mapped LCD,
then calls `fsync()` once. This follows the repository's
[native LCD refresh validation](video-lcd-refresh-validation.md): plain mapped
writes were subject to a 50 ms deferred-I/O delay, whereas one explicit commit
per finished frame removed that scheduling limit without a driver change.
`--no-commit` is a controlled comparison with the deferred behavior. Do not use
its faster copy time alone to claim a faster physical display.

The output object saves the visible framebuffer before drawing and restores
it on normal exit, interruption, and ordinary C++ failure. It unmaps memory and
closes its descriptor. No cleanup is possible after SIGKILL or sudden power
loss. `restore_error` records a failed restoration commit and makes the run
return exit code 2. Framebuffers remain exclusively owned by the calling
process for the run; the executable does not manage launcher arbitration.

The companion `packages/tyrell-tanks/benchmark/run-device.py` automates bounded
runs on an idle stock Zero. Stage it beside both ARM64 executables in a new
directory under `/tmp`, then run `python3 run-device.py` on the device as the
launcher user. It refuses an already-suspended launcher, launcher children, or
running Tyrell Video. Check that no other application owns the display before
running. It temporarily suspends the existing launcher, waits for each
benchmark's post-warmup marker, and samples the central five seconds of SPI
transfers. It then restores the LCD and resumes the same launcher, recording
PID/restart/state checks in `device-summary.json`. SIGINT, SIGTERM, and SIGHUP
trigger cleanup; SIGKILL or power loss cannot do so. No package is installed.
`--case rlsw-30` selects one test; `--help` lists all cases. Output stays beside
the script, so do not stage it into a system application directory.

## Reading the report

Each stage reports mean, median, 95th percentile, and maximum CPU wall time:

- `scene_build`: common CPU geometry generation.
- `render`: drawing and completion. GLES explicitly uses `glFinish()` so GPU
  completion is counted here rather than hidden inside readback.
- `readback`: `rlCopyFramebuffer()` or GLES `glReadPixels()` into RGBA8888.
- `rgb565_conversion`: channel conversion plus the required row orientation.
- `present_including_commit`: mapped row copies plus the optional fsync.
- `fsync`: just the explicit commit request; it is already included in present.
- `work_total`: all the above stages, excluding intentional pacing sleep.
- `frame_start_interval`: intervals between consecutive measured frame starts;
  the first frame has no interval and is excluded from this metric.

`completed_frame_rate` includes pacing and end-of-frame sleep. A missed pacing
deadline increments `missed_pacing_deadlines`; pacing debt is dropped so the
tool does not burst stale frames to catch up. The frame checksum hashes the
final RGB565 bytes in little-endian order. `physical_display_test=false` means
`--fb none`, where present/commit stages have no real display work.

These are software stage timings and completed application frames. **An fsync
request is not a panel-vsync timestamp or proof that a physical refresh
completed.** The report states this explicitly. A physical refresh-rate claim
needs a separate SPI transfer-counter or equivalent hardware observation,
as in the prior LCD validation. Compare rendering margins, readback cost and
pacing with that external observation before choosing the production backend.

## Measured hardware results

On November 14, 2026, both executables ran on the connected stock CardputerZero
at 320 × 170. The GPU identified itself as **VC4 V3D 2.1**, confirming hardware
acceleration rather than llvmpipe. Each case used one second of warmup and an
eight-second measured run, with a separate central five-second SPI counter
window. These are single short runs, not sustained thermal/battery tests.

| Path | App frames/s | SPI full-frame equivalents/s | Mean work/frame |
| --- | ---: | ---: | ---: |
| raylib software, unpaced, commits | 233.06 | 38.54 | 4.28 ms |
| GLES2, unpaced, commits | 170.34 | 38.40 | 5.86 ms |
| raylib software, paced at 30, commits | 30.00 | 30.07 | 5.11 ms |
| GLES2, paced at 30, commits | 30.00 | 30.02 | 6.97 ms |
| raylib software, paced at 30, deferred writes | 30.00 | 14.98 | 4.63 ms |

No paced run missed its application pacing deadline, and all SPI windows had
zero errors/timeouts. The explicit commit is a request, so application frame
counts can exceed transferred frames. The unpaced app rates are **not visible
screen frame rates**. SPI byte counts are transfer equivalents, not panel-vsync
timestamps or a check for tearing.

For this scene, software rendering averaged 3.32 ms versus GLES2's 0.74 ms, but
GLES2 readback averaged 4.40 ms versus software's 0.24 ms. The simplest initial
native game port should therefore use raylib's software path with 30 Hz pacing
and one commit per completed frame. Retain the GPU path for comparison as scene
complexity grows; the complete game has not been benchmarked on the device.

The visible framebuffer was restored byte-for-byte, and the original launcher
resumed with the same PID and zero restarts. No package, driver, firmware, boot
or launcher configuration changed. Raw reports, logs, and final PPMs remain
under ignored `device-results/tanks/`; curated timings, identities, and binary
checksums are in [tanks-benchmark-results.json](tanks-benchmark-results.json).
See [prototype validation](tanks-validation.md) for the desktop checks.

## Automated validation

The standalone software target was built in Release mode with Apple Clang 21
on an Apple Silicon Mac. An eight-frame, 320 × 170 headless run produced a
parseable JSON report and a visually inspected tank-arena PPM. These are
functional checks, separate from the hardware measurements above. Both Linux
ARM64 backends also compiled successfully and their real-device PPMs were
visually inspected for orientation, geometry, and color.

When the parent project enables `BUILD_TESTING` and Python 3 is available,
`tanks_display_benchmark_smoke` exercises five real software-rendered frames,
parses clean stdout JSON, checks dimensions and measured frame count, validates
finite timing percentiles, and verifies that the PPM contains a colored scene
with the expected sky/floor orientation. A second CTest,
`tanks_display_benchmark_invalid_cli`, checks rejection of fractional frame
counts, nonfinite/out-of-range limits, missing values and unknown options.
Both tests passed on the same Mac build. These tests access no real framebuffer.
