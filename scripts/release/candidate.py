#!/usr/bin/env python3
"""Assemble and verify immutable development candidates; never build or deploy."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SHA = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
TARGETS = {"original-m5", "tyrell-zero", "gh0st", "stock-derived", "full-source"}
ROLES = {"qualification", "known-issues", "license", "recovery", "inventory", "input-manifest", "evidence"}
RESERVED = {"release-manifest.json", "SHA256SUMS", "metadata/source-manifest.json"}
MAX_METADATA = 32 * 1024**2
MAX_ARCHIVE = 32 * 1024**3
MAX_FILES = 100000


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode()


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    path = Path(path)
    if path.stat().st_size > MAX_METADATA:
        raise ValueError("Oversized JSON metadata: " + str(path))
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("Duplicate JSON field: " + key)
            result[key] = value
        return result
    return json.loads(path.read_bytes(), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Invalid JSON number")))


def relative(name):
    if not isinstance(name, str) or not name or any(c in name for c in "\\\0\r\n\t"):
        raise ValueError("Unsafe candidate path")
    path = PurePosixPath(name)
    if path.is_absolute() or str(path) != name or any(part in {"", ".", ".."} for part in name.split("/")):
        raise ValueError("Unsafe candidate path: " + name)
    return name


def safe_path(root, name, *, directory=False):
    name = relative(name)
    root = Path(root)
    if root.is_symlink() or root.resolve() != root.absolute() or not root.is_dir():
        raise ValueError("Unredirected input directory required")
    path = root
    for part in name.split("/"):
        path = path / part
        if path.is_symlink():
            raise ValueError("Symlinked candidate input: " + name)
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError("Missing candidate input: " + name)
    return path


def tree_files(directory):
    directory = Path(directory)
    if directory.is_symlink() or directory.resolve() != directory.absolute() or not directory.is_dir():
        raise ValueError("Unredirected candidate directory required")
    result = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Unsafe candidate entry: " + str(path))
        if path.is_file():
            relative(path.relative_to(directory).as_posix())
            result.append(path)
    if len(result) > MAX_FILES:
        raise ValueError("Candidate contains too many files")
    names = [path.relative_to(directory).as_posix().casefold() for path in result]
    if len(names) != len(set(names)):
        raise ValueError("Case-colliding candidate entries")
    return result


def descriptor(root, value):
    if not isinstance(value, dict) or not SHA.fullmatch(value.get("sha256", "")):
        raise ValueError("Each selected file needs its exact SHA-256")
    path = safe_path(root, value.get("path"))
    if digest(path) != value["sha256"]:
        raise ValueError("Selected file checksum mismatch: " + value["path"])
    return path


def source_snapshot(repository_root, expected_revision):
    root = Path(repository_root)
    if not COMMIT.fullmatch(expected_revision):
        raise ValueError("Exact source commit required")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args])
    if git("rev-parse", "HEAD").decode().strip() != expected_revision or git("status", "--porcelain"):
        raise ValueError("A clean checkout at the exact selected source commit is required")
    files = {}
    for name in sorted(filter(None, git("ls-files", "-z").decode().split("\0"))):
        path = safe_path(root, name)
        files[name] = {"sha256": digest(path), "bytes": path.stat().st_size,
                       "executable": bool(path.stat().st_mode & 0o111)}
    if not files:
        raise ValueError("Empty source inventory")
    return {"schema": 1, "source_commit": expected_revision, "source_dirty": False, "files": files}


def package_identity(path):
    """Read Debian control metadata without executing package code or dpkg hooks."""
    with Path(path).open("rb") as stream:
        if stream.read(8) != b"!<arch>\n":
            raise ValueError("Selected application is not a Debian package")
        control = None
        seen = set()
        while header := stream.read(60):
            if len(header) != 60 or header[58:] != b"`\n":
                raise ValueError("Malformed Debian ar header")
            name = header[:16].decode("ascii").strip().removesuffix("/")
            if name in seen:
                raise ValueError("Duplicate Debian ar member")
            seen.add(name)
            try:
                size = int(header[48:58])
            except ValueError:
                raise ValueError("Malformed Debian member size") from None
            if size < 0:
                raise ValueError("Malformed Debian member size")
            if stream.tell() + size + size % 2 > Path(path).stat().st_size:
                raise ValueError("Truncated Debian package member")
            if name.startswith("control.tar"):
                if control is not None or size > MAX_METADATA:
                    raise ValueError("Invalid Debian control archive")
                payload = stream.read(size)
                if len(payload) != size:
                    raise ValueError("Truncated Debian control archive")
                control = payload
            elif name == "debian-binary":
                if stream.read(size) != b"2.0\n":
                    raise ValueError("Unsupported Debian package version")
            else:
                stream.seek(size, 1)
            if size % 2:
                stream.seek(1, 1)
        if control is None or not {"debian-binary"} <= seen or not any(n.startswith("data.tar") for n in seen):
            raise ValueError("Incomplete Debian package")
    with tarfile.open(fileobj=io.BytesIO(control), mode="r:*") as archive:
        members = [m for m in archive.getmembers() if m.name.removeprefix("./") == "control"]
        if len(members) != 1 or not members[0].isfile() or members[0].size > 1024**2:
            raise ValueError("Invalid Debian control record")
        text = archive.extractfile(members[0]).read().decode()
    fields = {}
    for line in text.splitlines():
        if not line or line[0].isspace():
            continue
        key, separator, value = line.partition(": ")
        if not separator or key in fields:
            raise ValueError("Malformed Debian control fields")
        fields[key] = value
    result = {name: fields.get(key) for name, key in
              (("package", "Package"), ("version", "Version"), ("architecture", "Architecture"))}
    if (not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", result["package"] or "")
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+.~:-]*", result["version"] or "")
            or result["architecture"] != "arm64"):
        raise ValueError("Expected an identified ARM64 application package")
    return result


def verify_image(directory, expected, source_commit, repository_root=None):
    """Recheck the trusted frozen export contract and compressed/raw binding."""
    directory = Path(directory)
    tree_files(directory)
    zero = runpy.run_path(str(ROOT / "image/zero_pipeline.py"))
    common = runpy.run_path(str(ROOT / "image/build-image.py"))
    recorded = common["verify_checksums"](directory)
    manifest_path = safe_path(directory, "image-manifest.json")
    bundle_path = safe_path(directory, "image-input.tar")
    if digest(manifest_path) != expected.get("manifest_sha256") or digest(bundle_path) != expected.get("input_archive_sha256"):
        raise ValueError("Image export differs from selected manifest/input archive")
    metadata = zero["verify_bundle"](bundle_path)
    if metadata.get("source_commit") != source_commit or metadata.get("source_dirty") is not False:
        raise ValueError("Image capture must bind the exact clean selected source commit")
    if metadata.get("profile_id") != "tyrell-zero":
        raise ValueError("Only source-bound frozen Zero exports are supported")
    preset = metadata.get("selected_power_preset")
    if not isinstance(preset, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", preset):
        raise ValueError("Captured image power preset must remain explicit")
    manifest = read_json(manifest_path)
    guest_sha = expected.get("guest_manifest_sha256")
    if not SHA.fullmatch(guest_sha or ""):
        raise ValueError("Selected Zero guest manifest SHA-256 required")
    identity = read_json(safe_path(directory, "attempt.json"))
    if (identity.get("bundle_sha256") != expected["input_archive_sha256"]
            or not isinstance(identity.get("job_id"), str) or type(identity.get("attempt_id")) is not int
            or identity["attempt_id"] < 1):
        raise ValueError("Invalid frozen image attempt identity")
    zero["validate_host_export"](directory, identity, identity, guest_sha, common, recorded=recorded)
    guest = read_json(safe_path(directory, "guest-image-manifest.json"))
    if set(recorded) != set(guest["sha256"]) | {"image-input.tar", "guest-image-manifest.json", "image-manifest.json"}:
        raise ValueError("Image export contains files outside the selected verified manifest")
    if manifest.get("inputs") != metadata or read_json(safe_path(directory, "build-inputs.json")) != metadata:
        raise ValueError("Image input manifests differ from captured archive")
    verification = read_json(safe_path(directory, "image-verification.json"))
    if verification != manifest.get("verification") or verification.get("status") != "passed":
        raise ValueError("Image export lacks a matching passing file verifier")
    image = verification.get("image", {})
    raw_sha = image.get("sha256_after")
    if not SHA.fullmatch(raw_sha or "") or image.get("sha256_before") != raw_sha:
        raise ValueError("Raw image changed during file verification")
    raw_name = relative(image.get("name"))
    raw_size = image.get("bytes")
    if type(raw_size) is not int or not 0 < raw_size <= MAX_ARCHIVE:
        raise ValueError("Invalid verified raw image size")
    compressed = [path for path in tree_files(directory) if path.name.endswith(".img.xz")]
    if len(compressed) != 1 or compressed[0].name != raw_name + ".xz":
        raise ValueError("Expected the verified image's sole compressed artifact")
    payload = hashlib.sha256(); size = 0
    with lzma.open(compressed[0], "rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            size += len(chunk)
            if size > raw_size:
                raise ValueError("Compressed payload exceeds verified raw size")
            payload.update(chunk)
    if payload.hexdigest() != raw_sha or size != raw_size or manifest.get("compressed_payload_sha256") != raw_sha:
        raise ValueError("Compressed image payload differs from verified raw image")
    raw_path = directory / raw_name
    if raw_path.exists() and (digest(safe_path(directory, raw_name)) != raw_sha or raw_path.stat().st_size != raw_size):
        raise ValueError("Retained raw image differs from verified bytes")
    if repository_root is not None:
        for name, item in metadata["files"].items():
            if name.startswith(("image/", "locks/", "builder/", "packages/")) or name == "scripts/builder":
                if digest(safe_path(repository_root, name)) != item["sha256"]:
                    raise ValueError("Captured recipe differs from selected source checkout: " + name)
    return {"raw_sha256": raw_sha, "raw_bytes": raw_size,
            "selected_power_preset": preset,
            "compressed_payload_sha256": raw_sha, "input_archive_sha256": digest(bundle_path),
            "capture_sha256": metadata["capture_sha256"], "guest_manifest_sha256": guest_sha,
            "export_manifest_sha256": digest(manifest_path), "compressed_name": compressed[0].relative_to(directory).as_posix()}


def verify_qualification(manifest, directory):
    qualification = manifest.get("qualification")
    if not isinstance(qualification, dict) or qualification.get("schema") != 1 or not isinstance(qualification.get("records"), list):
        raise ValueError("An explicit qualification matrix is required")
    identities = set()
    for row in qualification["records"]:
        if not isinstance(row, dict) or row.get("result") not in {"passed", "failed", "not-tested"}:
            raise ValueError("Invalid qualification result")
        if row.get("evidence_scope") not in {"device-tested", "automated-tested", "not-tested"}:
            raise ValueError("Invalid qualification scope")
        if not SHA.fullmatch(row.get("artifact_sha256", "")) or row.get("target") not in TARGETS or row.get("gate") not in {"z4", "app-acceptance", "z6"}:
            raise ValueError("Invalid qualification identity")
        identity = (row["artifact_sha256"], row["target"], row["gate"])
        if identity in identities:
            raise ValueError("Duplicate qualification gate")
        identities.add(identity)
        if row["result"] == "not-tested" and row["evidence_scope"] != "not-tested":
            raise ValueError("Untested qualification cannot claim tested scope")
        if (not isinstance(row.get("limitations"), list) or not row["limitations"]
                or not all(isinstance(s, str) and s.strip() for s in row["limitations"])):
            raise ValueError("Qualification limitations must remain explicit")
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or (row["result"] != "not-tested" and not evidence):
            raise ValueError("Qualification outcome needs bound evidence")
        for reference in evidence:
            path = descriptor(directory, reference)
            if manifest["files"].get(reference["path"], {}).get("sha256") != digest(path):
                raise ValueError("Qualification evidence is not in the candidate inventory")
    for asset in manifest["assets"]:
        gate = "z4" if asset["kind"] == "image" else "app-acceptance"
        required = {(asset["sha256"], manifest["target"], gate)}
        if asset["kind"] == "app-deb" and manifest["target"] == "gh0st":
            required.add((asset["sha256"], "original-m5", "app-acceptance"))
        if asset["kind"] == "image" and manifest["optimized_power"]:
            required.add((asset["sha256"], manifest["target"], "z6"))
        if not required <= identities:
            raise ValueError("Missing qualification gates must remain explicit as not-tested rows")


def assemble(selection, inputs_root, output_root, *, repository_root=ROOT):
    inputs_root, output_root = Path(inputs_root), Path(output_root)
    if selection.get("schema") != 1 or selection.get("target") not in TARGETS:
        raise ValueError("Unsupported candidate selection schema/target")
    version = selection.get("release_version")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", version):
        raise ValueError("An explicit release version is required")
    optimized = selection.get("optimized_power", False)
    if type(optimized) is not bool:
        raise ValueError("optimized_power must be a boolean")
    source = source_snapshot(repository_root, selection.get("source_commit", ""))
    planned = {}; case_names = set()
    def add(name, path, role, expected=None):
        name = relative(name)
        planned_value = (Path(path), role, expected or digest(path))
        # Shared check evidence can be referenced repeatedly with the same role.
        if name in planned:
            if planned[name] != planned_value:
                raise ValueError("Conflicting selected candidate input: " + name)
            return
        if name in RESERVED or name.casefold() in case_names:
            raise ValueError("Reserved or colliding candidate path: " + name)
        case_names.add(name.casefold()); planned[name] = planned_value
    checks = selection.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("Selected source checks are required")
    reports = {}
    for item in checks:
        path = descriptor(inputs_root, item); report = read_json(path)
        if (report.get("status") != "passed" or report.get("source", {}).get("commit") != source["source_commit"]
                or report["source"].get("dirty") is not False or report.get("scope") != "source-checks-only"):
            raise ValueError("Checks must pass for the exact clean candidate source")
        if report.get("suite") in {r["suite"] for r in reports.values()}:
            raise ValueError("Duplicate selected check suite")
        add(item["path"], path, "check-report", item["sha256"]); reports[item["path"]] = report
        for entry in report.get("exported_evidence", []):
            name = (PurePosixPath(item["path"]).parent / relative(entry["artifact_path"])).as_posix()
            evidence = descriptor(inputs_root, {"path": name, "sha256": entry["sha256"]})
            add(name, evidence, "check-evidence", entry["sha256"])
    documents = selection.get("documents")
    if not isinstance(documents, list):
        raise ValueError("Explicit candidate documentation required")
    by_role = {}
    for item in documents:
        role = item.get("role")
        if role not in ROLES:
            raise ValueError("Unknown candidate documentation role")
        path = descriptor(inputs_root, item)
        if path.stat().st_size == 0:
            raise ValueError("Empty candidate documentation")
        by_role.setdefault(role, []).append(item["path"]); add(item["path"], path, role, item["sha256"])
    for role in ROLES - {"evidence"}:
        if not by_role.get(role) or (role in {"qualification", "known-issues", "recovery"} and len(by_role[role]) != 1):
            raise ValueError("Required candidate documentation missing/duplicated: " + role)
    assets = []
    packages = selection.get("packages", [])
    if not isinstance(packages, list):
        raise ValueError("Invalid package selection")
    package_keys = set()
    for item in packages:
        path = descriptor(inputs_root, item); identity = package_identity(path)
        if any(item.get(key) != value for key, value in identity.items()):
            raise ValueError("Selected Debian identity differs from package control")
        key = tuple(identity.values())
        if key in package_keys:
            raise ValueError("Duplicate selected package/version/architecture")
        package_keys.add(key)
        report_name = item.get("build_report"); report = reports.get(report_name)
        if report is None:
            raise ValueError("Package requires a selected build check report")
        results = [r for r in report.get("results", []) if r.get("kind") == "package-build" and r.get("sha256") == item["sha256"] and r.get("status") == "passed"]
        if len(results) != 1:
            raise ValueError("Package bytes lack an exact passing build result")
        mappings = [e for e in report.get("exported_evidence", []) if e.get("original_path") == results[0].get("path") and e.get("sha256") == item["sha256"]]
        if len(mappings) != 1 or (PurePosixPath(report_name).parent / relative(mappings[0]["artifact_path"])).as_posix() != item["path"]:
            raise ValueError("Package is not the bound exported build artifact")
        # Already copied as check evidence; retain its specific payload role.
        planned[item["path"]] = (path, "app-deb", item["sha256"])
        assets.append(dict(kind="app-deb", path=item["path"], sha256=item["sha256"], bytes=path.stat().st_size,
                           build_report=report_name, **identity))
    image = selection.get("image")
    if image is not None:
        if selection["target"] != "tyrell-zero":
            raise ValueError("Frozen Zero images require the tyrell-zero target")
        directory = safe_path(inputs_root, image.get("directory"), directory=True)
        binding = verify_image(directory, image, source["source_commit"], repository_root)
        if optimized != (binding["selected_power_preset"] != "baseline"):
            raise ValueError("optimized_power must reflect the captured image power preset")
        for path in tree_files(directory):
            add(path.relative_to(inputs_root).as_posix(), path, "image-export")
        primary = directory / binding.pop("compressed_name")
        assets.append(dict(kind="image", path=primary.relative_to(inputs_root).as_posix(), sha256=digest(primary),
                           bytes=primary.stat().st_size, export_directory=image["directory"], **binding))
    if not assets:
        raise ValueError("Candidate requires at least one image or application package")
    if optimized and image is None:
        raise ValueError("Optimized power claims require an identified image")
    if len({a["sha256"] for a in assets}) != len(assets):
        raise ValueError("Duplicate candidate payload")
    if output_root.is_symlink() or output_root.resolve() != output_root.absolute():
        raise ValueError("Canonical unredirected output root required")
    output_root.mkdir(parents=True, exist_ok=True)
    lock = output_root / ".candidate-assembly.lock"
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    staging = Path(tempfile.mkdtemp(prefix=".candidate-", dir=output_root))
    archive_partial = None
    partial_created = False
    try:
        os.close(fd)
        files = {}
        for name, (path, role, expected) in sorted(planned.items()):
            destination = staging / name; destination.parent.mkdir(parents=True, exist_ok=True)
            if digest(path) != expected:
                raise ValueError("Selected candidate input changed before copying: " + name)
            shutil.copyfile(path, destination)
            if digest(destination) != expected or digest(path) != expected:
                raise ValueError("Candidate input changed during assembly: " + name)
            files[name] = {"sha256": expected, "bytes": destination.stat().st_size, "role": role}
        source_path = staging / "metadata/source-manifest.json"; source_path.parent.mkdir(exist_ok=True)
        source_path.write_bytes(canonical(source))
        files["metadata/source-manifest.json"] = {"sha256": digest(source_path), "bytes": source_path.stat().st_size, "role": "source-manifest"}
        manifest = {"schema": 1, "kind": "tyrell-release-candidate", "classification": "development",
                    "source_commit": source["source_commit"], "release_version": version, "target": selection["target"],
                    "optimized_power": optimized, "assets": sorted(assets, key=lambda a:a["path"]),
                    "checks": sorted(reports), "qualification": read_json(staging / by_role["qualification"][0]),
                    "files": dict(sorted(files.items())), "hardware_qualified": False, "grants_release_approval": False}
        (staging / "release-manifest.json").write_bytes(canonical(manifest))
        candidate_id = digest(staging / "release-manifest.json")
        all_files = {name: item["sha256"] for name, item in files.items()}
        all_files["release-manifest.json"] = candidate_id
        (staging / "SHA256SUMS").write_text("".join(f"{sha}  {name}\n" for name, sha in sorted(all_files.items())))
        verify_directory(staging, candidate_id)
        if source_snapshot(repository_root, source["source_commit"]) != source:
            raise ValueError("Selected source changed during candidate assembly")
        destination = output_root / ("candidate-" + candidate_id)
        archive_path = output_root / ("candidate-" + candidate_id + ".tar")
        if os.path.lexists(destination) or os.path.lexists(archive_path):
            raise ValueError("Preserving existing immutable candidate output")
        archive_partial = output_root / (".candidate-" + candidate_id + ".partial")
        with tarfile.open(archive_partial, "x", format=tarfile.PAX_FORMAT) as archive:
            partial_created = True
            for path in tree_files(staging):
                info = tarfile.TarInfo(path.relative_to(staging).as_posix()); info.size = path.stat().st_size
                info.mode = 0o444; info.uid = info.gid = info.mtime = 0
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
        # Hardlink publication fails atomically if another output already exists.
        os.link(archive_partial, archive_path)
        staging.rename(destination)
        return {"schema": 1, "status": "passed", "candidate_id": candidate_id, "manifest_sha256": candidate_id,
                "directory": str(destination), "archive": str(archive_path), "archive_sha256": digest(archive_path),
                "classification": "development", "hardware_qualified": False, "promotable": False}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if archive_partial is not None and partial_created:
            archive_partial.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def verify_directory(directory, candidate_id=None):
    directory = Path(directory); paths = tree_files(directory)
    manifest_path = safe_path(directory, "release-manifest.json")
    if candidate_id is not None and (not SHA.fullmatch(candidate_id) or digest(manifest_path) != candidate_id):
        raise ValueError("Candidate manifest identity mismatch")
    manifest = read_json(manifest_path)
    if manifest_path.read_bytes() != canonical(manifest):
        raise ValueError("Candidate manifest must use canonical encoding")
    if (manifest.get("schema") != 1 or manifest.get("kind") != "tyrell-release-candidate"
            or manifest.get("classification") != "development" or manifest.get("hardware_qualified") is not False
            or manifest.get("grants_release_approval") is not False or type(manifest.get("optimized_power")) is not bool
            or manifest.get("target") not in TARGETS or not COMMIT.fullmatch(manifest.get("source_commit", ""))):
        raise ValueError("Invalid development candidate manifest")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Candidate inventory required")
    if {p.relative_to(directory).as_posix() for p in paths} != set(files) | {"release-manifest.json", "SHA256SUMS"}:
        raise ValueError("Candidate inventory does not cover exact directory contents")
    for name, item in files.items():
        if (not isinstance(item, dict) or set(item) != {"sha256", "bytes", "role"}
                or type(item["bytes"]) is not int or item["bytes"] < 0
                or item["role"] not in ROLES | {"check-report", "check-evidence", "image-export", "app-deb", "source-manifest"}):
            raise ValueError("Invalid candidate inventory entry")
        path = descriptor(directory, {**item, "path": name})
        if path.stat().st_size != item.get("bytes"):
            raise ValueError("Candidate file size changed")
    expected = {name:item["sha256"] for name,item in files.items()}; expected["release-manifest.json"] = digest(manifest_path)
    sums = "".join(f"{sha}  {name}\n" for name,sha in sorted(expected.items()))
    if safe_path(directory, "SHA256SUMS").read_text() != sums:
        raise ValueError("Candidate checksums differ from exact inventory")
    source = read_json(safe_path(directory, "metadata/source-manifest.json"))
    if source.get("source_commit") != manifest["source_commit"] or source.get("source_dirty") is not False:
        raise ValueError("Candidate source manifest mismatch")
    for role in ("qualification", "known-issues", "recovery", "license", "inventory", "input-manifest"):
        selected = [n for n,item in files.items() if item.get("role") == role]
        if not selected or (role in {"qualification", "known-issues", "recovery"} and len(selected) != 1):
            raise ValueError("Missing/duplicated candidate role: " + role)
        if any(files[name]["bytes"] == 0 for name in selected):
            raise ValueError("Candidate documentation must not be empty")
        if role == "qualification" and read_json(directory / selected[0]) != manifest.get("qualification"):
            raise ValueError("Embedded qualification differs from retained matrix")
    verify_qualification(manifest, directory)
    checks = manifest.get("checks")
    if not isinstance(checks, list) or not checks or len(checks) != len(set(checks)):
        raise ValueError("Exact selected candidate check reports required")
    reports = {}; suites = set()
    for name in checks:
        report = read_json(safe_path(directory, name))
        if (files.get(name, {}).get("role") != "check-report" or report.get("status") != "passed"
                or report.get("scope") != "source-checks-only"
                or report.get("source", {}).get("commit") != manifest["source_commit"]
                or report["source"].get("dirty") is not False or report.get("suite") in suites):
            raise ValueError("Candidate checks do not match its clean source")
        suites.add(report.get("suite")); reports[name] = report
        for entry in report.get("exported_evidence", []):
            evidence_name = (PurePosixPath(name).parent / relative(entry["artifact_path"])).as_posix()
            descriptor(directory, {"path": evidence_name, "sha256": entry["sha256"]})
            if files.get(evidence_name, {}).get("sha256") != entry["sha256"]:
                raise ValueError("Candidate check evidence is not inventoried")
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("Candidate payloads required")
    for asset in assets:
        path = descriptor(directory, asset)
        if files.get(asset["path"], {}).get("sha256") != asset["sha256"] or path.stat().st_size != asset.get("bytes"):
            raise ValueError("Candidate payload differs from inventory")
        if asset.get("kind") == "app-deb":
            if any(asset.get(key) != value for key,value in package_identity(path).items()):
                raise ValueError("Candidate package identity mismatch")
            report = reports.get(asset.get("build_report"), {})
            results = [r for r in report.get("results", []) if r.get("kind") == "package-build" and r.get("sha256") == asset["sha256"] and r.get("status") == "passed"]
            mappings = [e for e in report.get("exported_evidence", []) if len(results) == 1 and e.get("original_path") == results[0].get("path") and e.get("sha256") == asset["sha256"]]
            if (len(results) != 1 or len(mappings) != 1
                    or (PurePosixPath(asset["build_report"]).parent / relative(mappings[0]["artifact_path"])).as_posix() != asset["path"]):
                raise ValueError("Candidate package differs from exact passing build export")
        elif asset.get("kind") == "image":
            if manifest["target"] != "tyrell-zero":
                raise ValueError("Frozen Zero images require the tyrell-zero target")
            export = safe_path(directory, asset.get("export_directory"), directory=True)
            binding = verify_image(export, {"manifest_sha256":asset.get("export_manifest_sha256"),
                                           "input_archive_sha256":asset.get("input_archive_sha256"),
                                           "guest_manifest_sha256":asset.get("guest_manifest_sha256")}, manifest["source_commit"])
            if manifest["optimized_power"] != (binding["selected_power_preset"] != "baseline"):
                raise ValueError("Candidate power claim differs from captured preset")
            primary = (PurePosixPath(asset["export_directory"]) / binding.pop("compressed_name")).as_posix()
            if primary != asset["path"] or any(asset.get(key) != value for key,value in binding.items()):
                raise ValueError("Candidate image binding mismatch")
        else:
            raise ValueError("Unknown candidate payload kind")
    if manifest["optimized_power"] and not any(a["kind"] == "image" for a in assets):
        raise ValueError("Optimized power claims require an identified image")
    return manifest


def verify_archive(archive, expected_sha256, candidate_id, output_root):
    archive, output_root = Path(archive), Path(output_root)
    if not SHA.fullmatch(expected_sha256) or not SHA.fullmatch(candidate_id):
        raise ValueError("Exact archive and candidate digests required")
    if archive.is_symlink() or not archive.is_file() or digest(archive) != expected_sha256:
        raise ValueError("Candidate archive checksum mismatch")
    if output_root.is_symlink() or output_root.resolve() != output_root.absolute():
        raise ValueError("Canonical unredirected extraction root required")
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / ("candidate-" + candidate_id)
    if os.path.lexists(destination):
        raise ValueError("Preserving existing candidate extraction")
    staging = Path(tempfile.mkdtemp(prefix=".verify-", dir=output_root))
    try:
        with tarfile.open(archive, "r:") as source:
            members = source.getmembers()
            names = [relative(m.name) for m in members]
            if (len(members) > MAX_FILES or len(names) != len(set(n.casefold() for n in names))
                    or sum(m.size for m in members) > MAX_ARCHIVE
                    or any(not m.isfile() or m.islnk() or m.size < 0 for m in members)):
                raise ValueError("Unsafe, duplicate or oversized candidate archive")
            for member in members:
                target = staging / member.name; target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as payload, target.open("xb") as output:
                    shutil.copyfileobj(payload, output, 1024**2)
        manifest = verify_directory(staging, candidate_id)
        if digest(archive) != expected_sha256:
            raise ValueError("Candidate archive changed during verification")
        staging.rename(destination)
        return {"schema":1,"status":"passed","candidate_id":candidate_id,"archive_sha256":expected_sha256,
                "directory":str(destination),"source_commit":manifest["source_commit"],"classification":"development",
                "hardware_qualified":False,"promotable":False}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("assemble"); build.add_argument("--selection", type=Path, required=True)
    build.add_argument("--inputs-root", type=Path, required=True); build.add_argument("--output-root", type=Path, required=True)
    build.add_argument("--repository-root", type=Path, default=ROOT); build.add_argument("--report", type=Path, required=True)
    check = commands.add_parser("verify"); check.add_argument("--directory", type=Path, required=True)
    check.add_argument("--candidate-id", required=True); check.add_argument("--report", type=Path, required=True)
    unpack = commands.add_parser("verify-archive"); unpack.add_argument("--archive", type=Path, required=True)
    unpack.add_argument("--sha256", required=True); unpack.add_argument("--candidate-id", required=True)
    unpack.add_argument("--output-root", type=Path, required=True); unpack.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    report_path = args.report.absolute()
    if args.command == "verify" and report_path.resolve().is_relative_to(args.directory.resolve()):
        parser.error("Verification report must be outside the immutable candidate directory")
    if args.command in {"assemble", "verify-archive"}:
        output = args.output_root.resolve()
        if report_path.resolve().is_relative_to(output):
            parts = report_path.resolve().relative_to(output).parts
            if parts and parts[0].startswith("candidate-"):
                parser.error("Report cannot overwrite an addressed candidate output")
    if args.report.is_symlink() or args.report.parent.resolve() != args.report.parent.absolute():
        parser.error("Unredirected report path required")
    report = {"schema":1,"status":"failed","hardware_qualified":False,"promotable":False}
    try:
        if args.command == "assemble":
            report = assemble(read_json(args.selection), args.inputs_root, args.output_root, repository_root=args.repository_root)
        elif args.command == "verify-archive":
            report = verify_archive(args.archive, args.sha256, args.candidate_id, args.output_root)
        else:
            manifest = verify_directory(args.directory, args.candidate_id)
            report = {"schema":1,"status":"passed","candidate_id":args.candidate_id,"source_commit":manifest["source_commit"],
                      "classification":"development","hardware_qualified":False,"promotable":False}
    except Exception as error:
        report["error"] = str(error)
        print("candidate: " + str(error), file=sys.stderr)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_bytes(canonical(report))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
