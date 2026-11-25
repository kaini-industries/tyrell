"""Inspect an exact software qualification kit; never authorize live resources.

Hash verification detects package drift. It is not a trusted deployment
attestation and cannot turn a qualification fixture into a device grant.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

PROFILE = "usr/share/tyrell-video-handoff/candidate-profile.json"
MANIFEST = "usr/share/tyrell-video-handoff/source-manifest.json"
PEER = "usr/lib/tyrell-video-handoff/qualification/native-peer"
MODULES = ("handoff_audio", "handoff_authority", "handoff_candidate", "handoff_channel", "handoff_display_state",
    "handoff_ledger", "handoff_media_state", "handoff_participant_state", "handoff_participant_worker",
    "handoff_participants", "handoff_protocol", "handoff_session", "handoff_shell", "handoff_vt")
SHA = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")
DEVICE_CASES = (
    "identified-spare-target-and-image", "exact-player-shell-package-and-dependency-identities",
    "legacy-writers-and-pending-restoration-excluded", "qualified-kms-lcd-audio-and-vt-finality",
    "playing-paused-preroll-seek-track-caption-and-output-transitions",
    "launch-return-input-held-keys-and-responsiveness", "missing-hdmi-rollback-and-external-owner-conflict",
    "privacy-cutoff-and-crash-sigterm-recovery",
)

class CandidateError(ValueError):
    pass

def require(value, message):
    if not value: raise CandidateError(message)

def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def regular(root, name):
    require(type(name) is str and name and "\\" not in name, "invalid package path")
    path = PurePosixPath(name)
    require(not path.is_absolute() and all(p not in ("", ".", "..") for p in name.split("/")), "unsafe package path")
    current = Path(root)
    require(not current.is_symlink() and current.is_dir(), "invalid package root")
    for part in path.parts:
        current = current / part
        require(not current.is_symlink(), "symlink in package payload: " + name)
    require(current.is_file() and stat.S_ISREG(current.stat().st_mode), "missing regular payload: " + name)
    return current

def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate manifest field")
        result[key] = value
    return result

def read_json(path):
    require(path.stat().st_size <= 1024 * 1024, "candidate metadata exceeds bound")
    return json.loads(path.read_text(), object_pairs_hook=unique)

def validate_profile(profile):
    expected = {"schema": 1, "kind": "video-handoff-software-candidate", "protocol": "TVH2",
        "capabilities": "async-drain,epoch-barrier,helper-receipts", "profile": "disposable-v2",
        "native_output": "memory-lvgl-lcd-fake-audio", "live_admission": False,
        "physical_testing": "pending", "default_m5_policy": "direct-player", "automatic_activation": False,
        "required_device_evidence": list(DEVICE_CASES)}
    require(type(profile) is dict and profile == expected and type(profile["schema"]) is int and
        profile["live_admission"] is False and profile["automatic_activation"] is False,
        "only the fixed software candidate is supported; live/device admission remains closed")
    return profile

def inspect(root):
    root = Path(root)
    manifest = read_json(regular(root, MANIFEST))
    require(type(manifest) is dict and set(manifest) == {"schema", "kind", "source_revision", "source_date_epoch",
        "architecture", "payload", "source_files_sha256", "dependency_locks_sha256", "live_admission", "physical_testing"},
        "invalid candidate manifest")
    require(type(manifest["schema"]) is int and manifest["schema"] == 1 and manifest["kind"] == "video-handoff-candidate-manifest" and
        type(manifest["source_revision"]) is str and REVISION.fullmatch(manifest["source_revision"]) and
        type(manifest["source_date_epoch"]) is int and manifest["source_date_epoch"] >= 0 and manifest["architecture"] == "arm64" and
        manifest["live_admission"] is False and manifest["physical_testing"] == "pending", "invalid candidate identity or admission")
    payload = manifest["payload"]
    require(type(payload) is dict and 5 <= len(payload) <= 128 and PROFILE in payload and PEER in payload,
        "missing native/profile closure")
    fixed = {PROFILE, PEER, "usr/bin/tyrell-video-handoff-candidate",
        "usr/share/doc/tyrell-video-handoff-candidate/README.md", "usr/share/doc/tyrell-video-handoff-candidate/copyright"}
    fixed.update("usr/lib/tyrell-video-handoff/" + name + ".py" for name in MODULES)
    require(set(payload) == fixed, "candidate module/entry-point closure differs from the fixed kit")
    for name, binding in payload.items():
        require(name != MANIFEST and (name.startswith(("usr/lib/tyrell-video-handoff/", "usr/share/tyrell-video-handoff/",
            "usr/share/doc/tyrell-video-handoff-candidate/")) or name == "usr/bin/tyrell-video-handoff-candidate"),
            "payload outside the private candidate layout")
        require(type(binding) is dict and set(binding) == {"sha256", "bytes", "mode"} and
            type(binding["sha256"]) is str and SHA.fullmatch(binding["sha256"]) and type(binding["bytes"]) is int and
            binding["bytes"] >= 0 and type(binding["mode"]) is int and binding["mode"] in (0o644, 0o755), "invalid payload binding")
        path = regular(root, name)
        require(path.stat().st_size == binding["bytes"] and stat.S_IMODE(path.stat().st_mode) == binding["mode"] and
            digest(path) == binding["sha256"], "candidate payload changed: " + name)
    for key in ("source_files_sha256", "dependency_locks_sha256"):
        require(type(manifest[key]) is dict and bool(manifest[key]) and len(manifest[key]) <= 512 and
            all(type(name) is str and type(value) is str and SHA.fullmatch(value) for name, value in manifest[key].items()),
            "invalid source/dependency binding")
    require(set(manifest["dependency_locks_sha256"]) == {"locks/build-inputs.json", "locks/video-build-inputs.json"},
        "incomplete candidate dependency boundary")
    copied = {PROFILE: "packages/tyrell-video-handoff/candidate-profile.json",
        "usr/bin/tyrell-video-handoff-candidate": "packages/tyrell-video-handoff/tyrell-video-handoff-candidate",
        "usr/share/doc/tyrell-video-handoff-candidate/README.md": "packages/tyrell-video-handoff/README.md",
        "usr/share/doc/tyrell-video-handoff-candidate/copyright": "LICENSE"}
    for name in payload:
        if name.startswith("usr/lib/tyrell-video-handoff/") and name.endswith(".py"):
            copied[name] = "packages/tyrell-video-handoff/" + name.rsplit("/", 1)[1]
    require(all(manifest["source_files_sha256"].get(source) == payload[name]["sha256"] for name, source in copied.items()),
        "payload differs from captured source")
    for prefix in ("usr/lib/tyrell-video-handoff", "usr/share/tyrell-video-handoff", "usr/share/doc/tyrell-video-handoff-candidate"):
        tree = root / prefix
        actual = set()
        for path in tree.rglob("*"):
            require(not path.is_symlink(), "unexpected symlink in private candidate layout")
            require(path.is_dir() or stat.S_ISREG(path.stat().st_mode), "special file in private candidate layout")
            if path.is_file(): actual.add(path.relative_to(root).as_posix())
        expected = {name for name in payload if name.startswith(prefix + "/")}
        if MANIFEST.startswith(prefix + "/"): expected.add(MANIFEST)
        require(actual == expected, "private candidate inventory changed")
    validate_profile(read_json(regular(root, PROFILE)))
    # A rewritten manifest cannot switch this module into a live profile.
    return {"schema": 1, "kind": "video-handoff-candidate-inspection", "status": "verified-software-payload",
        "source_revision": manifest["source_revision"], "native_peer_sha256": payload[PEER]["sha256"],
        "payload_files": len(payload), "manifest_sha256": digest(regular(root, MANIFEST)),
        "live_admission": False, "physical_testing": "pending", "hardware_qualified": False,
        "pending_device_cases": list(DEVICE_CASES)}

def require_live_admission(*_args, **_kwargs):
    raise CandidateError("Live admission is unavailable: qualified asynchronous shell, legacy-writer exclusion, driver finality and exact device acceptance are pending")

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/"), help="Inspect a staged or installed candidate payload")
    parser.add_argument("--check-live", action="store_true", help="Report the closed live-admission gate")
    args = parser.parse_args(argv)
    try:
        facts = inspect(args.root)
        if args.check_live: require_live_admission(facts)
        print(json.dumps(facts, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "refused", "live_admission": False, "error": str(error)}))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
