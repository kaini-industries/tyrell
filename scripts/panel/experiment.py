"""Inactive H3 experiment metadata; no builder, artifact reader or hardware API."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re

ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = ROOT / "research/panel-source-audit.json"
UPSTREAM = "a52c5d78dbeab6dc7fc8e21604b7bbe05d7a35e5"
HEX256 = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
ROLES = {"kernel", "headers", "modules", "device-tree"}
APPS = ("m5-applaunch", "status", "video", "chess", "sandline", "beacon", "catalog")
SCENARIOS = (
    "baseline-acceptance", "bsp-source-and-license", "abi-compatibility", "fbdev-fsync-fallback",
    "te-wait", "te-timeout", "missing-te-fallback", "present-error-visibility", "foreground-ownership",
    "rgb565-color-orientation", "dirty-full-redraw", "brightness-dark-wake", "hdmi-audio-vt-return",
    "abnormal-exit-recovery", "idle-irq-cpu", "optical-observation", "battery-measurement",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_keys(items):
    result = {}
    for key, value in items:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def read_reference(path):
    require(not path.is_symlink() and path.is_file(), "missing regular reference: " + path.name)
    with path.open("rb") as stream:
        raw = stream.read(512 * 1024 + 1)
    require(len(raw) <= 512 * 1024, "oversized source reference")
    value = json.loads(raw, object_pairs_hook=unique_keys,
                       parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite reference")))
    return raw, value


def shape(value, keys, label):
    require(isinstance(value, dict) and set(value) == set(keys), "unexpected or missing " + label + " fields")


def text(value, label, maximum=256):
    require(isinstance(value, str) and 0 < len(value) <= maximum and
            not any(ord(char) < 32 or ord(char) == 127 for char in value), "invalid " + label)


def relative_path(value):
    text(value, "artifact reference path")
    require("\\" not in value and ":" not in value and not value.startswith("/") and
            all(part not in ("", ".", "..") for part in value.split("/")) and
            PurePosixPath(value).as_posix() == value, "noncanonical relative artifact path")


def descriptor(value):
    shape(value, ("path", "sha256", "bytes"), "artifact descriptor")
    relative_path(value["path"])
    require(isinstance(value["sha256"], str) and HEX256.fullmatch(value["sha256"]), "invalid artifact SHA-256")
    require(type(value["bytes"]) is int and 1 <= value["bytes"] <= 2**40, "invalid artifact byte count")


def baseline_reference():
    raw, evidence = read_reference(ROOT / "research/tyrell-zero-image-validation.json")
    _, hardware = read_reference(ROOT / "image/contracts/tyrell-zero-hardware.json")
    image = evidence["zero_image"]
    return {"evidence_sha256": hashlib.sha256(raw).hexdigest(),
            "raw_image_sha256": image["raw_image"]["sha256_after"],
            "export_manifest_sha256": image["export"]["host_manifest"]["sha256"],
            "source_commit": image["source_commit"], "kernel_release": hardware["kernel_release"],
            "bsp_commit": hardware["bsp_commit"], "hardware_acceptance": "not-tested"}


def audit_reference():
    raw, audit = read_reference(AUDIT_PATH)
    require(isinstance(audit, dict) and type(audit.get("schema")) is int and audit.get("schema") == 1 and
            audit.get("kind") == "panel-source-audit" and isinstance(audit.get("upstream"), dict) and
            audit["upstream"].get("commit") == UPSTREAM and
            audit.get("scope") == "source-inspected-only" and
            audit.get("grants_release_approval") is False and audit.get("hardware_qualified") is False,
            "unsupported panel source audit")
    return {"path": "research/panel-source-audit.json", "sha256": hashlib.sha256(raw).hexdigest(),
            "upstream_commit": UPSTREAM}


def hardware_ids():
    _, value = read_reference(ROOT / "image/contracts/tyrell-zero-hardware.json")
    return tuple(row["id"] for row in value["capabilities"])


def template():
    """Record the proposed experiment separately from active image profiles."""
    return {"schema": 1, "kind": "tyrell-panel-experiment", "id": "h3-panel-present",
            "enabled": False, "grants_release_approval": False,
            "source_audit": audit_reference(), "baseline": baseline_reference(),
            "candidate": {"build_source_commit": None, "kernel_release": None, "artifacts": [],
                          "license_notice": None, "source_export": None},
            "gates": {"hardware": {key: "not-tested" for key in hardware_ids()},
                      "apps": {key: "not-tested" for key in APPS},
                      "scenarios": {key: "not-tested" for key in SCENARIOS}},
            "notes": "Planning metadata only. Candidate bytes, ABI, BSP build and physical outcomes remain unverified."}


def inspect(value):
    """Check metadata coherence only; never open a candidate artifact pathname."""
    shape(value, ("schema", "kind", "id", "enabled", "grants_release_approval", "source_audit",
                  "baseline", "candidate", "gates", "notes"), "experiment")
    require(type(value["schema"]) is int and value["schema"] == 1, "unsupported experiment schema")
    require(value["kind"] == "tyrell-panel-experiment" and value["id"] == "h3-panel-present",
            "unsupported experiment identity")
    require(value["enabled"] is False and value["grants_release_approval"] is False,
            "experiment must remain inactive and deny release approval")
    require(value["source_audit"] == audit_reference(), "panel source audit binding changed")
    require(value["baseline"] == baseline_reference(), "retained baseline binding changed")
    text(value["notes"], "notes", 4096)
    gates = value["gates"]
    shape(gates, ("hardware", "apps", "scenarios"), "gate")
    for name, expected in (("hardware", hardware_ids()), ("apps", APPS), ("scenarios", SCENARIOS)):
        shape(gates[name], expected, name + " gate")
        require(all(result == "not-tested" for result in gates[name].values()),
                "experiment metadata cannot grant acceptance")
    candidate = value["candidate"]
    shape(candidate, ("build_source_commit", "kernel_release", "artifacts", "license_notice", "source_export"), "candidate")
    require(isinstance(candidate["artifacts"], list) and len(candidate["artifacts"]) <= 16,
            "candidate artifact inventory exceeds limit")
    empty = candidate == {"build_source_commit": None, "kernel_release": None, "artifacts": [],
                          "license_notice": None, "source_export": None}
    if not empty:
        require(isinstance(candidate["build_source_commit"], str) and COMMIT.fullmatch(candidate["build_source_commit"]),
                "candidate requires one aggregate build source commit")
        text(candidate["kernel_release"], "candidate kernel release")
        require(len(candidate["artifacts"]) == len(ROLES), "candidate requires all four artifact roles")
        roles, paths = set(), set()
        for artifact in candidate["artifacts"]:
            shape(artifact, ("role", "path", "sha256", "bytes", "build_source_commit", "kernel_release"), "candidate artifact")
            require(isinstance(artifact["role"], str) and artifact["role"] in ROLES and
                    artifact["role"] not in roles, "unknown or duplicate artifact role")
            roles.add(artifact["role"])
            descriptor({key: artifact[key] for key in ("path", "sha256", "bytes")})
            require(artifact["path"] not in paths, "duplicate artifact path")
            paths.add(artifact["path"])
            require(artifact["build_source_commit"] == candidate["build_source_commit"] and
                    artifact["kernel_release"] == candidate["kernel_release"],
                    "mixed kernel/header/module/device-tree build identities")
        for key in ("license_notice", "source_export"):
            descriptor(candidate[key])
            require(candidate[key]["path"] not in paths, "duplicate source/license artifact path")
            paths.add(candidate[key]["path"])
    require(len(json.dumps(value, sort_keys=True, allow_nan=False).encode()) <= 65536,
            "experiment metadata exceeds limit")
    blockers = ["Accepted baseline and an identified spare test target remain pending.",
                "Candidate bytes, ELF/module/header ABI, complete BSP closure and reproducibility are not verified.",
                "Source/license distribution, live present ABI and fbdev/fsync fallback require independent review.",
                "All app, panel and fifteen-category hardware gates remain not tested.",
                "Visible tearing/scanout and electrical/battery benefit require physical evidence."]
    if empty:
        blockers.insert(1, "Candidate build/artifact/source-distribution identities are unrecorded.")
    return {"schema": 1, "kind": "tyrell-panel-experiment-inspection", "status": "inactive-experiment",
            "scope": "metadata-only", "metadata_consistent": True,
            "candidate_metadata_complete": not empty, "deployment_allowed": False,
            "grants_release_approval": False, "hardware_qualified": False, "hardware_writes": 0,
            "blocking_gates": blockers,
            "experiment_sha256": hashlib.sha256((json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest(),
            "source_audit_sha256": value["source_audit"]["sha256"]}
