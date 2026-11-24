#!/usr/bin/env python3
"""Offline signed discovery from the three exact reviewed Store package bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalog_metadata as metadata

ROOT = Path(__file__).resolve().parents[3]
QUALIFIED = {
    ("tyrell-status", "0.1.0-1", "arm64"): "15471a8f605e826d6f81daf3260602042b70b9c19330cb03b0bf839039533b2b",
    ("tyrell-video", "0.1.6-1", "arm64"): "3e6b8f50e0f076e783943e77f61e2b57b856a7168f44886dd7d7646013fb239f",
    ("tyrell-video", "0.1.7-1", "arm64"): "7ddf4ead5f9d27e44d92ab510b8cca503847af36762d60b315fea62de4450d79",
}
HOST = "https://raw.githubusercontent.com"


def checked_file(root, relative):
    path = Path(root)
    for part in relative.split("/"):
        metadata.require(part not in ("", ".", ".."), "Unsafe qualified source path")
        path = path / part
        metadata.require(not path.is_symlink(), "Redirected qualified source file")
    metadata.require(path.is_file(), "Missing qualified source file: " + relative)
    return path


def file_bytes(root, relative, *, maximum=metadata.MAX_PACKAGE_BYTES):
    path = checked_file(root, relative)
    metadata.require(path.stat().st_size <= maximum, "Qualified source file exceeds its bound")
    return path.read_bytes()


def json_file(root, relative):
    return metadata.load_json(file_bytes(root, relative, maximum=metadata.MAX_ENVELOPE_BYTES))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pinned_bytes(root, revision, relative, expected):
    data = file_bytes(root, relative)
    metadata.require(digest(data) == expected, "Qualified file checksum differs: " + relative)
    try:
        captured = subprocess.run(["/usr/bin/git", "-C", str(root), "show", revision + ":" + relative],
                                  check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise metadata.MetadataError("Selected immutable source revision lacks " + relative) from error
    metadata.require(captured == data, "Published URL revision contains different bytes: " + relative)
    return data


def generate_payload(repository_root, source_revision, sequence, issued_at, expires_at):
    root = Path(repository_root)
    metadata.require(isinstance(source_revision, str) and bool(re.fullmatch(r"[0-9a-f]{40}", source_revision)),
                     "An exact source revision is required for immutable asset URLs")
    catalog = json_file(root, "packages/tyrell-zero-store/config/store-catalog.json")
    metadata.require(catalog.get("schema") == 1 and isinstance(catalog.get("packages"), list), "Reviewed root catalog is invalid")
    reviewed = {}
    for row in catalog["packages"]:
        metadata.require(isinstance(row, dict), "Invalid reviewed root package")
        identity = tuple(row.get(k) for k in ("package", "version", "architecture"))
        metadata.require(all(isinstance(v, str) for v in identity)
                         and identity in QUALIFIED and identity not in reviewed
                         and row.get("sha256") == QUALIFIED[identity], "Unknown or changed root-qualified package identity")
        reviewed[identity] = row
    metadata.require(set(reviewed) == set(QUALIFIED), "The exact reviewed package set is required")
    entries = []
    for identity, row in sorted(reviewed.items()):
        package, version, architecture = identity
        release = version.removesuffix("-1")
        folder = "dist/" + package + "/" + release + "/"
        name = package + "_" + version + "_" + architecture + ".deb"
        path = folder + name
        data = pinned_bytes(root, source_revision, path, row["sha256"])
        metadata.require(type(row.get("bytes")) is int and len(data) == row["bytes"], "Reviewed package size differs")
        provenance = json_file(root, folder + "provenance.json")
        manifest = json_file(root, folder + "manifest.json")
        metadata.require(all(provenance.get(k) == v for k, v in {
            "package": package, "debian_version": version, "architecture": architecture,
            "package_file": name, "package_sha256": row["sha256"]}.items()), "Distribution provenance differs from approved bytes")
        metadata.require(all(manifest.get(k) == v for k, v in {
            "package": package, "version": version, "architecture": architecture}.items())
            and manifest.get("sha256", {}).get(name) == row["sha256"], "Distribution manifest differs from approved bytes")
        metadata.require(manifest.get("verification", {}).get("automated_tests", {}).get("result") == "passed",
                         "Distribution lacks passing automated qualification")
        sums = file_bytes(root, folder + "SHA256SUMS", maximum=metadata.MAX_ENVELOPE_BYTES).decode("ascii")
        selected = [line for line in sums.splitlines() if line == row["sha256"] + "  " + name]
        metadata.require(len(selected) == 1, "Distribution checksum inventory does not bind selected package")
        icon_path = "packages/" + package + "/assets/" + package + ".png"
        icon_key = "usr/share/APPLaunch/share/images/" + package + ".png"
        icon_hash = row.get("files", {}).get(icon_key)
        metadata.require(isinstance(icon_hash, str) and bool(metadata.HEX.fullmatch(icon_hash)), "Approved package lacks exact icon identity")
        icon = pinned_bytes(root, source_revision, icon_path, icon_hash)
        metadata.require(24 <= len(icon) <= metadata.MAX_ARTWORK_BYTES and icon[:8] == b"\x89PNG\r\n\x1a\n"
                         and icon[12:16] == b"IHDR", "Qualified artwork is not a bounded PNG")
        width, height = struct.unpack_from(">II", icon, 16)
        if package == "tyrell-video":
            store = "store/tyrell-video/" + release + "/"
            qualification = json_file(root, store + "validation.json")
            if version == "0.1.6-1":
                metadata.require(qualification.get("package_sha256") == row["sha256"]
                                 and qualification.get("package_file") == name,
                                 "Legacy Store qualification differs from distribution")
            else:
                metadata.require(qualification.get("package", {}).get("sha256") == row["sha256"]
                                 and qualification["package"].get("version") == version
                                 and qualification["package"].get("bytes") == len(data), "Store qualification differs from distribution")
            artwork_manifest = json_file(root, store + "assets/manifest.json")
            matching = [item for item in artwork_manifest.get("files", [])
                        if item.get("file") == "icon-256.png" and item.get("source_path") == icon_path
                        and item.get("source_sha256") == icon_hash]
            metadata.require(len(matching) == 1, "Store artwork provenance does not bind the package icon")
            uploaded_icon = file_bytes(root, store + "assets/icon-256.png", maximum=metadata.MAX_ARTWORK_BYTES)
            metadata.require(digest(uploaded_icon) == matching[0]["sha256"], "Store artwork digest differs from its manifest")
        entries.append({"package": package, "version": version, "architecture": architecture,
                        "sha256": row["sha256"], "bytes": len(data), "url": HOST + "/kaini-industries/tyrell/" + source_revision + "/" + path,
                        "title": "Tyrell Status" if package == "tyrell-status" else "Tyrell Video",
                        "summary": "Read-only system status for the handheld" if package == "tyrell-status" else "Local video with subtitles and handheld or HDMI playback",
                        "artwork": {"sha256": icon_hash, "bytes": len(icon), "url": HOST + "/kaini-industries/tyrell/" + source_revision + "/" + icon_path,
                                    "width": width, "height": height, "format": "png"}})
    payload = {"schema": 1, "sequence": sequence, "issued_at": issued_at, "expires_at": expires_at,
               "base_id": metadata.base_identity(root), "packages": entries}
    metadata.validate_payload(payload, [HOST])
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--sequence", type=int, required=True)
    parser.add_argument("--issued-at", type=int, required=True)
    parser.add_argument("--expires-at", type=int, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = generate_payload(args.repository_root, args.source_revision, args.sequence, args.issued_at, args.expires_at)
        signed = metadata.sign_payload(payload, args.private_key, allowed_origins=[HOST])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as stream:
            stream.write(signed)
        report = {"schema": 1, "status": "passed", "scope": "offline-discovery-metadata-only",
                  "source_revision": args.source_revision, "envelope_sha256": digest(signed),
                  "key_id": metadata.load_json(signed)["key_id"], "base_id": payload["base_id"],
                  "sequence": payload["sequence"], "entries": len(payload["packages"]),
                  "install_authority_granted": False, "publication_performed": False}
        with args.report.open("x") as stream:
            json.dump(report, stream, sort_keys=True, indent=2)
            stream.write("\n")
    except (ValueError, OSError) as error:
        print("catalog metadata: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
