#!/usr/bin/env python3
"""Assemble a separate, inactive ARM64 software qualification kit.

The private native peer is built/tested by Video CMake before this step. No
installer hook, launcher, service, capability or device-access rule is emitted.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[1]
sys.path.insert(0, str(PACKAGE))
from handoff_candidate import PROFILE, MANIFEST, PEER, digest, inspect, regular, validate_profile, read_json, REVISION

NAME = "tyrell-video-handoff-candidate"
VERSION = "0.1.0-1"

def copy(source, target, mode=0o644):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(mode)

def source_bindings():
    inputs = {"LICENSE": digest(regular(ROOT, "LICENSE"))}
    for tree in (PACKAGE, ROOT / "packages/tyrell-video", ROOT / "packages/tyrell-app-common"):
        for path in sorted(tree.rglob("*")):
            if "__pycache__" in path.parts or path.suffix == ".pyc": continue
            if path.is_symlink(): raise ValueError("Symlink in candidate inputs")
            if path.is_file(): inputs[path.relative_to(ROOT).as_posix()] = digest(path)
    return inputs

def assemble(stage, peer, *, revision, epoch, source, locks):
    validate_profile(read_json(PACKAGE / "candidate-profile.json"))
    for module in sorted(PACKAGE.glob("handoff_*.py")):
        copy(regular(PACKAGE, module.name), stage / "usr/lib/tyrell-video-handoff" / module.name)
    copy(PACKAGE / "tyrell-video-handoff-candidate", stage / "usr/bin/tyrell-video-handoff-candidate", 0o755)
    copy(PACKAGE / "candidate-profile.json", stage / PROFILE)
    copy(PACKAGE / "README.md", stage / "usr/share/doc" / NAME / "README.md")
    copy(ROOT / "LICENSE", stage / "usr/share/doc" / NAME / "copyright")
    copy(peer, stage / PEER, 0o755)
    payload = {path.relative_to(stage).as_posix(): {"sha256": digest(path), "bytes": path.stat().st_size,
        "mode": path.stat().st_mode & 0o777} for path in sorted(stage.rglob("*")) if path.is_file()}
    manifest = {"schema": 1, "kind": "video-handoff-candidate-manifest", "source_revision": revision,
        "source_date_epoch": epoch, "architecture": "arm64", "payload": payload,
        "source_files_sha256": source, "dependency_locks_sha256": locks,
        "live_admission": False, "physical_testing": "pending"}
    (stage / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    return inspect(stage)

def main():
    os.umask(0o022)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--native-peer", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() not in ("aarch64", "arm64"):
        parser.error("Build the candidate in the disposable Debian ARM64 worker")
    if not REVISION.fullmatch(args.source_revision): parser.error("Exact source commit required")
    actual = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    if actual != args.source_revision: parser.error("Source revision differs from checkout")
    if subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"], text=True).strip():
        parser.error("Candidate packages require a clean source checkout")
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "")
    if not epoch.isdigit(): parser.error("Set captured SOURCE_DATE_EPOCH")
    if args.native_peer.is_symlink() or not args.native_peer.is_file(): parser.error("Regular native peer required")
    with args.native_peer.open("rb") as stream: header = stream.read(20)
    if len(header) != 20 or header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(header[18:20], "little") != 183:
        parser.error("Native qualification peer must be a Linux ARM64 ELF")
    peer_hash = digest(args.native_peer)
    source = source_bindings()
    locks = {name: digest(regular(ROOT, name)) for name in ("locks/build-inputs.json", "locks/video-build-inputs.json")}
    output = args.output_dir.absolute()
    if output.is_symlink() or output.exists(): parser.error("Use a fresh candidate artifact directory")
    output.mkdir(parents=True)
    deb = output / (NAME + "_" + VERSION + "_arm64.deb")
    with tempfile.TemporaryDirectory(prefix="tyrell-video-handoff-package-") as directory:
        stage = Path(directory) / "root"; stage.mkdir()
        inspection = assemble(stage, args.native_peer, revision=args.source_revision, epoch=int(epoch), source=source, locks=locks)
        if inspection["native_peer_sha256"] != peer_hash:
            raise ValueError("Staged peer differs from captured native input")
        control = stage / "DEBIAN"; control.mkdir()
        (control / "control").write_text(
            f"Package: {NAME}\nVersion: {VERSION}\nArchitecture: arm64\n"
            "Maintainer: Tyrell contributors <tyrell@localhost>\nSection: devel\nPriority: optional\n"
            "Depends: python3 (>= 3.11), libc6, libstdc++6, libglib2.0-0t64, libdrm2, libpulse0, "
            "libgstreamer1.0-0 (>= 1.26), libgstreamer-plugins-base1.0-0 (>= 1.26), "
            "gstreamer1.0-plugins-base, gstreamer1.0-plugins-good, gstreamer1.0-plugins-bad, gstreamer1.0-libav, fonts-dejavu-core\n"
            "Description: Inactive asynchronous Video software qualification kit\n"
            " Private authenticated shell/actor peer with memory LCD and fake audio.\n"
            " No launcher replacement, service activation or live device admission.\n")
        for path in sorted(stage.rglob("*")):
            os.utime(path, (int(epoch), int(epoch)), follow_symlinks=False)
        os.utime(stage, (int(epoch), int(epoch)))
        if source_bindings() != source or any(digest(ROOT / name) != value for name, value in locks.items()) or digest(args.native_peer) != peer_hash:
            raise ValueError("Candidate inputs changed during assembly")
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(stage), str(deb)], check=True)
        # Validate bytes extracted from the actual archive, not only the stage.
        extracted = Path(directory) / "extracted"
        subprocess.run(["dpkg-deb", "--extract", str(deb), str(extracted)], check=True)
        if inspect(extracted) != inspection: raise ValueError("Extracted candidate differs from stage")
        scripts = subprocess.check_output(["dpkg-deb", "--ctrl-tarfile", str(deb)])
        import io, tarfile
        with tarfile.open(fileobj=io.BytesIO(scripts)) as archive:
            if {m.name.removeprefix("./") for m in archive if m.isfile()} != {"control"}:
                raise ValueError("Unexpected candidate maintainer script")
        content = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", str(deb)])
        with tarfile.open(fileobj=io.BytesIO(content)) as archive:
            members = archive.getmembers()
            if any(not (m.isdir() or m.isfile()) for m in members):
                raise ValueError("Nonregular candidate archive entry")
            names = {m.name.removeprefix("./") for m in members if m.isfile()}
            expected = set(read_json(extracted / MANIFEST)["payload"]) | {MANIFEST}
            if names != expected: raise ValueError("Candidate archive inventory changed")
    report = {"schema": 1, "kind": "a2-d5-candidate-package", "status": "passed", "source_revision": args.source_revision,
        "package": deb.name, "package_sha256": digest(deb), "package_bytes": deb.stat().st_size,
        "inspection": inspection, "native_peer_sha256": peer_hash, "maintainer_scripts": [], "automatic_units": [],
        "capabilities": [], "installed_on_host": False, "hardware_qualified": False, "live_admission": False,
        "physical_testing": "pending"}
    (output / "a2-d5-candidate-package.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))

if __name__ == "__main__": main()
