#!/usr/bin/env python3
"""Materialize the pinned Cardputer GUI backend configuration for native ARM64.

The generic native defaults omit the framebuffer, evdev and threading choices.
Reuse the vendor's Cardputer defaults, removing only the cross compiler prefix.
The filename deliberately contains no ``cross``: the vendor SConstruct treats
that substring as permission to enter its release-SDK download path.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

DEFAULTS = "config_defaults.mk"
CARDPUTER = "linux_x86_cross_cp0_config_defaults.mk"
OUTPUT = "tyrell_native_config.mk"
INPUT_SHA256 = {
    DEFAULTS: "b04847f37c5e88c1bbc170f058462e5bef17ceeca52afe0d10d2ef79ad30bd32",
    CARDPUTER: "b1670fdcac827fc47504d36ced37979a0f3728ad09ab5e25c8742c4c412e5863",
}
PREFIX = b'CONFIG_TOOLCHAIN_PREFIX="aarch64-linux-gnu-"\n'
REQUIRED = (
    b"CONFIG_V9_5_LV_USE_LINUX_FBDEV=y\n",
    b"CONFIG_V9_5_LV_LINUX_FBDEV_RENDER_MODE_FULL=y\n",
    b"CONFIG_V9_5_LV_DRAW_SW_ASM_NEON=y\n",
    b"CONFIG_V9_5_LV_USE_DRAW_SW_ASM=1\n",
    b"CONFIG_V9_5_LV_USE_EVDEV=y\n",
    b"CONFIG_V9_5_LV_OS_PTHREAD=y\n",
    b"CONFIG_V9_5_LV_USE_FREETYPE=y\n",
    b"CONFIG_CP0_LVGL_INIT_WIFI=y\n",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def trusted_regular(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("native config input/output has a symlink ancestor")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
        raise ValueError("native config requires nonwritable regular inputs")
    return path.read_bytes()


def generate(source_root):
    source_root = Path(source_root).absolute()
    project = source_root / "projects/LaunchWizard"
    values = {name: trusted_regular(project / name) for name in INPUT_SHA256}
    if any(sha(data) != INPUT_SHA256[name] for name, data in values.items()):
        raise ValueError("native config vendor input SHA-256 mismatch")
    original = values[CARDPUTER]
    if original.count(PREFIX) != 1 or any(line not in original for line in REQUIRED):
        raise ValueError("native Cardputer configuration contract differs")
    content = original.replace(PREFIX, b"", 1)
    if b"CONFIG_TOOLCHAIN_" in content or b"CONFIG_V9_5_LV_USE_SDL=y" in content:
        raise ValueError("native config cannot select a cross toolchain or SDL")
    target = project / OUTPUT
    if target.exists() or target.is_symlink():
        if trusted_regular(target) != content:
            raise ValueError("refusing to replace a different native config")
    else:
        fd, name = tempfile.mkstemp(prefix=".tyrell-native-", dir=project)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o644)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            # Both paths are inside a private, allowlisted build job. Never
            # follow an existing destination introduced between checks.
            os.link(name, target)
        finally:
            os.unlink(name)
    return {"schema": 1, "kind": "native-cardputer-wizard-config",
            "inputs": INPUT_SHA256, "config": OUTPUT, "sha256": sha(content),
            "derivation": "Pinned Cardputer runtime defaults with only cross compiler prefix removed",
            "cross_toolchain": False, "framebuffer_render_mode": "full",
            "evdev": True, "pthread": True, "neon": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(generate(args.source_root), indent=2, sort_keys=True))
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
