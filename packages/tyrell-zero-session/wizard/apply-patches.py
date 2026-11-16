#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Apply the reviewed private Wizard patch only to the exact .105 inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

REVISION = "e7f60859fcc8bd867f3b8c4b3acd45dfa82ca39f"
PATCH_SHA256 = "3d05a08131a374f35e35e81092dddb669bc85b9b7ee2353dee51cd02680435b8"
EXPECTED = {
    "projects/LaunchWizard/main/ui/wizard_service.cpp": "324021773ef5aac20591180ff08e4dddca6fa88e92b0816e657bb6d0045438b7",
    "projects/LaunchWizard/main/src/main.cpp": "a6a8be0768e0f5d52f351c3f880d2fa820836111340c34b35c78b778c81f9175",
}
HERE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular(root, relative):
    target = root / relative
    ancestry = [target, *target.parents]
    if any(p.is_symlink() for p in ancestry[:ancestry.index(root) + 1]):
        raise ValueError("source symlink refused")
    if not target.is_file():
        raise ValueError("source input missing")
    return target


def apply(source):
    source = Path(source).absolute()
    patch = HERE / "patches/0001-native-wizard-completion.patch"
    identity = json.loads((HERE / "patches/identity.json").read_text())
    if (identity.get("schema") != 1 or identity.get("revision") != REVISION or
            identity.get("patch_sha256") != PATCH_SHA256 or digest(patch) != PATCH_SHA256 or
            set(identity.get("files", {})) != set(EXPECTED) or
            any(identity["files"][p].get("before") != h for p, h in EXPECTED.items())):
        raise ValueError("private Wizard patch identity mismatch")
    found = {p: digest(regular(source, p)) for p in EXPECTED}
    after = {p: identity["files"][p]["after"] for p in EXPECTED}
    if found == after:
        return identity  # Safe idempotence; partial applications are rejected.
    if found != EXPECTED:
        raise ValueError("private Wizard requires exact .105 source; restage unknown or partial inputs")
    with tempfile.TemporaryDirectory(prefix="tyrell-wizard-") as temporary:
        staging = Path(temporary)
        for relative in EXPECTED:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)
        subprocess.run(["patch", "--batch", "--forward", "-p1", "-i", str(patch)],
                       cwd=staging, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if {p: digest(staging / p) for p in EXPECTED} != after:
            raise ValueError("patched Wizard output identity mismatch")
        for relative in EXPECTED:
            target = source / relative
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as out:
                temporary_file = Path(out.name)
                out.write((staging / relative).read_bytes())
                out.flush()
                os.fsync(out.fileno())
                os.fchmod(out.fileno(), target.stat().st_mode & 0o777)
            os.replace(temporary_file, target)
    return identity


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    args = parser.parse_args()
    print(json.dumps(apply(args.source_root), sort_keys=True))
