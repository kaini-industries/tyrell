#!/usr/bin/env python3
"""Exercise the real headless renderer and its public report/CLI contract."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import tempfile


def run(binary, arguments):
    return subprocess.run([binary, *arguments], capture_output=True, text=True, timeout=10)


def smoke(binary):
    with tempfile.TemporaryDirectory(prefix="tanks-display-benchmark-") as directory:
        frame = Path(directory) / "arena.ppm"
        result = run(binary, ["--fb", "none", "--warmup", "0", "--seconds", "2",
                              "--frames", "5", "--fps", "0", "--frame-output", str(frame)])
        assert result.returncode == 0, result.stderr
        # This also verifies renderer startup logs do not corrupt JSON stdout.
        report = json.loads(result.stdout)
        assert report["schema_version"] == 1
        assert (report["width"], report["height"], report["frames"]) == (320, 170, 5)
        assert report["backend"] == "rlsw"
        assert report["scene"] == "shared-untextured-tank-arena-v1"
        assert report["triangles"] == 854
        assert report["hardware_accelerated"] is False
        assert report["physical_display_test"] is False
        assert report["fsync_enabled"] is False
        assert report["fsync_calls"] == 0
        assert report["fb"] == "none"
        assert report["restore_error"] == ""
        assert report["interrupted"] is False
        assert report["elapsed_seconds"] > 0
        assert report["completed_frame_rate"] > 0
        assert abs(report["completed_frame_rate"] * report["elapsed_seconds"] - 5) < 0.025
        metrics = report["metrics"]
        expected = {"scene_build", "render", "readback", "rgb565_conversion",
                    "present_including_commit", "fsync", "work_total", "frame_start_interval"}
        assert set(metrics) == expected
        for name, values in metrics.items():
            assert set(values) == {"mean_ms", "p50_ms", "p95_ms", "max_ms"}, name
            assert all(math.isfinite(value) and value >= 0 for value in values.values()), name
            assert values["p50_ms"] <= values["p95_ms"] <= values["max_ms"], name
            assert values["mean_ms"] <= values["max_ms"] + 1e-6, name
        assert metrics["render"]["mean_ms"] > 0
        assert metrics["readback"]["mean_ms"] > 0
        assert metrics["rgb565_conversion"]["mean_ms"] > 0
        assert metrics["frame_start_interval"]["mean_ms"] > 0
        assert all(value == 0 for value in metrics["fsync"].values())
        parts = frame.read_bytes().split(b"\n", 3)
        assert parts[:3] == [b"P6", b"320 170", b"255"]
        pixels = parts[3]
        assert len(pixels) == 320 * 170 * 3
        colors = {pixels[index:index + 3] for index in range(0, len(pixels), 3)}
        assert len(colors) > 15, "Scene is empty, constant, or missing colored geometry"
        # The common scene is deliberately asymmetric vertically: sky above,
        # floor below. Catch a readback orientation regression as well as an
        # accidentally cleared/blank render.
        at = lambda x, y: pixels[(y * 320 + x) * 3:(y * 320 + x + 1) * 3]
        assert at(160, 20) != at(160, 120), "Expected sky/floor orientation differs"
        assert int(report["frame_checksum_fnv1a64"]) > 0
        assert "not panel-vsync" in report["measurement_scope"]


def invalid(binary):
    for arguments in (["--frames", "1.5"], ["--fps", "nan"], ["--fps", "241"],
                      ["--seconds", "601"], ["--seconds", "0"], ["--warmup", "-1"],
                      ["--output"], ["--unknown-option"]):
        result = run(binary, arguments)
        assert result.returncode != 0, arguments
        assert "Display benchmark:" in result.stderr, (arguments, result.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--invalid", action="store_true")
    args = parser.parse_args()
    if args.invalid:
        invalid(args.binary)
    else:
        smoke(args.binary)
