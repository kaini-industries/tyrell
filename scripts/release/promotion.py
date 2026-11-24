#!/usr/bin/env python3
"""Fail-closed promotion policy for an already verified immutable candidate.

This module does not authenticate caller-supplied JSON. The workflow adapter must
fetch run/job/artifact metadata from GitHub, restore and verify the exact archive,
and read the request/qualification bytes from its trusted reviewed checkout.
Only then may it call ``evaluate``. Nothing here creates tags or releases.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import re


HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,95}\Z")
TARGETS = {"original-m5", "tyrell-zero", "gh0st", "stock-derived", "full-source"}
REQUIRED_SOURCE_JOBS = {
    "plan", "compatibility gate", "portable (docs)", "portable (python)",
    "portable (game)", "portable (chess)", "native (chess-native)",
    "native (status)", "native (video)", "native (zero-native)",
}
REQUEST_KEYS = {
    "schema", "kind", "candidate_sha256", "archive_sha256", "source_commit",
    "release_version", "target", "level", "optimized_power",
    "acknowledge_known_issues", "known_issues_sha256", "recovery_sha256",
    "manual_base_selection", "base_image_sha256", "qualification_path",
    "qualification_sha256",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _hash(value: object, label: str, *, commit: bool = False) -> str:
    pattern = HEX40 if commit else HEX64
    _require(isinstance(value, str) and bool(pattern.fullmatch(value)),
             label + " must be an exact lowercase digest")
    return value


def _positive_id(value: object, label: str) -> int:
    _require(type(value) is int and value > 0, label + " must be a positive integer")
    return value


def _path(value: object, label: str) -> str:
    _require(isinstance(value, str) and bool(value), label + " must be a relative path")
    _require(not PurePosixPath(value).is_absolute() and "\\" not in value
             and all(part not in {"", ".", ".."} for part in value.split("/")),
             label + " must be a safe repository-relative path")
    return value


def manifest_sha256(manifest: dict) -> str:
    """Identity of the canonical release-manifest.json bytes used by R1."""
    return hashlib.sha256((json.dumps(manifest, sort_keys=True, indent=2,
                                      allow_nan=False) + "\n").encode()).hexdigest()


def tag_name(release_version: str) -> str:
    _require(isinstance(release_version, str) and bool(VERSION.fullmatch(release_version)),
             "release_version must be a finite version identifier")
    _require(release_version.lower() not in {"latest", "stable", "main", "master", "dev"},
             "moving aliases cannot identify an immutable candidate")
    return "candidate-" + release_version


def _one_role(files: dict, role: str) -> tuple[str, dict]:
    matches = [(path, item) for path, item in files.items()
               if isinstance(item, dict) and item.get("role") == role]
    _require(len(matches) == 1, "candidate requires exactly one " + role + " document")
    path, item = matches[0]
    _path(path, "candidate document")
    _hash(item.get("sha256"), role + " document")
    return path, item


def _run(run: dict, *, path: str, repository: str, head_sha: str | None = None) -> dict:
    _require(isinstance(run, dict), "missing authenticated workflow run metadata")
    _positive_id(run.get("id"), "workflow run id")
    _require(run.get("repository") == repository, "workflow run belongs to another repository")
    _require(run.get("path") == path, "unexpected workflow path")
    _hash(run.get("head_sha"), "workflow head", commit=True)
    if head_sha is not None:
        _require(run["head_sha"] == head_sha, "workflow run tested another source commit")
    _require(run.get("status") == "completed" and run.get("conclusion") == "success",
             "required workflow run has not completed successfully")
    return run


def _checks(checks: dict, source: str, identity: str, archive: str, repository: str) -> dict:
    _require(isinstance(checks, dict), "missing authenticated checks")
    source_run = _run(checks.get("source_run"), path=".github/workflows/checks.yml",
                      repository=repository, head_sha=source)
    jobs = source_run.get("jobs")
    _require(isinstance(jobs, list), "source checks require authenticated job metadata")
    names = [job.get("name") for job in jobs if isinstance(job, dict)]
    _require(len(names) == len(jobs) == len(set(names)), "ambiguous source check job inventory")
    _require(REQUIRED_SOURCE_JOBS <= set(names), "all eight source suites and the compatibility gate are required")
    for job in jobs:
        _require(job.get("status") == "completed" and job.get("conclusion") == "success",
                 "source check job failed, skipped or is incomplete")
    candidate_run = _run(checks.get("candidate_run"), path=".github/workflows/image-candidate.yml",
                         repository=repository)
    _require(candidate_run.get("event") == "workflow_dispatch", "candidate run must be explicitly dispatched")
    report = checks.get("candidate_report")
    _require(isinstance(report, dict) and type(report.get("schema")) is int and report["schema"] == 1
             and report.get("kind") == "tyrell-candidate-verification"
             and report.get("status") == "passed", "missing successful exact-candidate verification")
    _require(report.get("candidate_sha256") == identity and report.get("archive_sha256") == archive
             and report.get("source_commit") == source, "verification report identifies another candidate")
    _require(report.get("tooling_commit") == candidate_run["head_sha"],
             "candidate verification does not bind the trusted tooling commit")
    _positive_id(report.get("run_id"), "verification report run id")
    _require(report.get("run_id") == candidate_run["id"], "verification report belongs to another workflow run")
    artifact = checks.get("candidate_artifact")
    _require(isinstance(artifact, dict), "missing authenticated candidate artifact")
    _positive_id(artifact.get("id"), "candidate artifact id")
    _require(artifact.get("expired") is False, "candidate artifact is expired or availability is unknown")
    _require(isinstance(report.get("artifact_name"), str) and bool(report["artifact_name"])
             and artifact.get("name") == report["artifact_name"], "candidate artifact name differs from verification")
    workflow_run = artifact.get("workflow_run")
    _require(isinstance(workflow_run, dict), "candidate artifact has no workflow run binding")
    _positive_id(workflow_run.get("id"), "candidate artifact workflow run id")
    _require(isinstance(workflow_run, dict) and workflow_run.get("id") == candidate_run["id"]
             and workflow_run.get("head_sha") == candidate_run["head_sha"],
             "candidate artifact belongs to another run or tooling commit")
    return {"source_run_id": source_run["id"], "candidate_run_id": candidate_run["id"],
            "candidate_artifact_id": artifact["id"], "tooling_commit": candidate_run["head_sha"]}


def _qualification(manifest: dict) -> dict[tuple[str, str, str], dict]:
    qualification = manifest.get("qualification")
    _require(isinstance(qualification, dict) and type(qualification.get("schema")) is int
             and qualification["schema"] == 1 and isinstance(qualification.get("records"), list),
             "candidate requires an explicit qualification matrix")
    result = {}
    files = manifest["files"]
    for row in qualification["records"]:
        _require(isinstance(row, dict), "invalid qualification row")
        sha = _hash(row.get("artifact_sha256"), "qualification artifact")
        _require(row.get("target") in TARGETS and row.get("gate") in {"z4", "app-acceptance", "z6"},
                 "unknown qualification target or gate")
        key = (sha, row["target"], row["gate"])
        _require(key not in result, "duplicate qualification row")
        _require(row.get("result") in {"passed", "failed", "not-tested"}, "unknown qualification result")
        _require(row.get("evidence_scope") in {"device-tested", "automated-tested", "source-inspected", "not-tested"},
                 "unknown qualification evidence scope")
        evidence = row.get("evidence")
        _require(isinstance(evidence, list), "qualification evidence must be explicit")
        limits = row.get("limitations")
        _require(isinstance(limits, list) and limits and all(isinstance(v, str) and v.strip() for v in limits),
                 "qualification limitations must remain visible")
        for reference in evidence:
            _require(isinstance(reference, dict), "invalid qualification evidence reference")
            path = _path(reference.get("path"), "qualification evidence")
            _require(path in files and reference.get("sha256") == files[path].get("sha256"),
                     "qualification evidence is not bound to candidate bytes")
        if row["result"] != "not-tested":
            _require(bool(evidence) and row["evidence_scope"] in {"device-tested", "automated-tested"},
                     "a qualification outcome requires actual bound test evidence")
        else:
            _require(row["evidence_scope"] in {"not-tested", "source-inspected"},
                     "untested qualification cannot claim executed tests")
        result[key] = row
    return result


def evaluate(manifest: dict, candidate_sha256: str, request: dict, checks: dict, *,
             repository: str, approved_qualification_sha256: str | None = None,
             existing: dict | None = None) -> dict:
    """Validate explicit promotion inputs; return a finite publish authorization.

    ``manifest`` must already pass candidate.verify_directory/verify_archive.
    ``checks`` and ``existing`` are normalized, live GitHub API observations, not
    documents accepted from a candidate archive. The adapter independently
    authenticates trusted tooling ancestry and the reviewed request/qualification
    files; the approved qualification digest is passed only after that check.
    """
    identity = _hash(candidate_sha256, "candidate")
    _require(isinstance(manifest, dict) and type(manifest.get("schema")) is int
             and manifest["schema"] == 1 and manifest.get("kind") == "tyrell-release-candidate",
             "unsupported candidate manifest")
    _require(manifest_sha256(manifest) == identity, "candidate digest differs from the verified manifest")
    source = _hash(manifest.get("source_commit"), "candidate source", commit=True)
    tag = tag_name(manifest.get("release_version"))
    _require(manifest.get("target") in TARGETS, "unknown candidate target")
    _require(type(manifest.get("optimized_power")) is bool, "candidate power claim must be explicit")
    _require(isinstance(request, dict) and set(request) == REQUEST_KEYS,
             "promotion request has missing or unknown fields")
    _require(type(request.get("schema")) is int and request["schema"] == 1
             and request.get("kind") == "tyrell-promotion-request", "unsupported promotion request")
    for key, expected in (("candidate_sha256", identity), ("source_commit", source),
                          ("release_version", manifest["release_version"]), ("target", manifest["target"]),
                          ("optimized_power", manifest["optimized_power"])):
        _require(request.get(key) == expected and type(request.get(key)) is type(expected),
                 "promotion request differs from candidate: " + key)
    _require(request.get("level") in {"development", "hardware-alpha"}, "unknown promotion level")
    _require(request.get("acknowledge_known_issues") is True, "known issues require explicit acknowledgment")
    archive = _hash(request.get("archive_sha256"), "candidate archive")
    files = manifest.get("files")
    _require(isinstance(files, dict) and bool(files), "missing bound candidate files")
    for role, field in (("known-issues", "known_issues_sha256"), ("recovery", "recovery_sha256"),
                        ("qualification", "qualification_sha256")):
        _, item = _one_role(files, role)
        _require(request.get(field) == item["sha256"], "request does not acknowledge the exact " + role + " document")
    _path(request.get("qualification_path"), "reviewed qualification")
    _require(approved_qualification_sha256 == request["qualification_sha256"],
             "qualification bytes were not approved from the reviewed checkout")
    assets = manifest.get("assets")
    _require(isinstance(assets, list) and bool(assets), "candidate has no installable assets")
    images = [a for a in assets if isinstance(a, dict) and a.get("kind") == "image"]
    _require(len(images) <= 1, "a promotion must select one base image at most")
    if images:
        _require(request.get("manual_base_selection") is True
                 and request.get("base_image_sha256") == images[0].get("sha256"),
                 "base image requires explicit manual selection of its exact bytes")
    else:
        _require(request.get("manual_base_selection") is False and request.get("base_image_sha256") is None,
                 "app-only candidate cannot imply a selected base image")
    run_binding = _checks(checks, source, identity, archive, repository)
    _require(isinstance(existing, dict) and existing.get("immutable_releases_enabled") is True,
             "repository immutable releases must be enabled before publication")
    _require(existing.get("release_exists") is False and existing.get("tag_exists") is False,
             "release or tag already exists; never overwrite or resume publication")
    registered = existing.get("package_versions")
    _require(isinstance(registered, list), "existing package/version registry must be checked")
    previous = {}
    for package in registered:
        _require(isinstance(package, dict), "invalid existing package registry row")
        key = tuple(package.get(k) for k in ("package", "version", "architecture"))
        _require(all(isinstance(v, str) and v for v in key), "invalid existing package identity")
        digest = _hash(package.get("sha256"), "registered package")
        _require(key not in previous or previous[key] == digest, "existing registry has conflicting package bytes")
        previous[key] = digest
    requirements = []
    package_keys = set()
    target = manifest["target"]
    for asset in assets:
        _require(isinstance(asset, dict) and asset.get("kind") in {"image", "app-deb"}, "unknown installable asset")
        digest = _hash(asset.get("sha256"), "asset")
        _require(asset.get("path") in files and files[asset["path"]].get("sha256") == digest,
                 "installable asset is not bound to candidate files")
        if asset["kind"] == "image":
            requirements.append((digest, target, "z4"))
            if manifest["optimized_power"]:
                requirements.append((digest, target, "z6"))
        else:
            key = tuple(asset.get(k) for k in ("package", "version", "architecture"))
            _require(all(isinstance(v, str) and v for v in key) and key not in package_keys,
                     "ambiguous candidate package identity")
            package_keys.add(key)
            _require(key not in previous or previous[key] == digest,
                     "package/version/architecture was already accepted with different bytes")
            requirements.append((digest, target, "app-acceptance"))
            if target == "gh0st":
                requirements.append((digest, "original-m5", "app-acceptance"))
    _require(not manifest["optimized_power"] or (bool(images) and request["level"] == "hardware-alpha"),
             "optimized power claims require a hardware-qualified image alpha")
    rows = _qualification(manifest)
    missing = []
    for key in requirements:
        row = rows.get(key)
        _require(row is not None, "candidate manifest hides a required qualification row: "
                 + key[1] + "/" + key[2])
        if not row or row["result"] != "passed" or row["evidence_scope"] != "device-tested":
            missing.append({"artifact_sha256": key[0], "target": key[1], "gate": key[2]})
    if request["level"] == "hardware-alpha":
        _require(not missing, "hardware alpha lacks exact-artifact device acceptance: "
                 + ", ".join(item["target"] + "/" + item["gate"] for item in missing))
    return {
        "schema": 1, "kind": "tyrell-promotion-authorization", "status": "approved",
        "candidate_sha256": identity, "archive_sha256": archive, "source_commit": source,
        "release_version": manifest["release_version"], "tag_name": tag,
        "target": target, "level": request["level"], "repository": repository,
        "hardware_supported": request["level"] == "hardware-alpha",
        "optimized_power_qualified": manifest["optimized_power"] and not missing,
        "missing_hardware_gates": missing, "qualification_sha256": request["qualification_sha256"],
        "known_issues_sha256": request["known_issues_sha256"], "recovery_sha256": request["recovery_sha256"],
        "prerelease": True, "make_latest": False, "immutable_release_required": True,
        "automatic_channel_movement": False, "in_place_migration": False, "device_write": False,
        **run_binding,
    }
