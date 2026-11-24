#!/usr/bin/env python3
"""Read-only captured Zero input preflight, not an image build or promotion."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def verify(bundle, expected_sha256, expected_revision, root=ROOT):
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("An exact expected input archive SHA-256 is required")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_revision):
        raise ValueError("An exact expected source commit is required")
    if bundle.is_symlink() or not bundle.is_file():
        raise ValueError("Missing regular captured input bundle; restore the exact retained bytes")
    with bundle.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected_sha256:
            raise ValueError("Captured input archive checksum mismatch")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if head != expected_revision:
        raise ValueError("Checkout differs from selected source commit")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=root):
        raise ValueError("Candidate preflight requires a clean selected checkout")
    api = runpy.run_path(str(root / "image/zero_pipeline.py"))
    metadata = api["verify_bundle"](bundle)
    if metadata.get("source_commit") != expected_revision or metadata.get("source_dirty") is not False:
        raise ValueError("Capture must come from the exact clean selected source commit")
    # Compare captured recipes to the trusted checkout, not code extracted from
    # the supplied archive. Never run or extract captured scripts in this scope.
    checked = 0
    for name, identity in metadata["files"].items():
        if name.startswith(("image/", "locks/", "builder/", "packages/")) or name == "scripts/builder":
            path = root / name
            if path.is_symlink() or not path.is_file() or api["digest"](path) != identity["sha256"]:
                raise ValueError("Captured recipe/lock differs from checkout: " + name)
            checked += 1
    if not checked:
        raise ValueError("Capture has no checked recipe inputs")
    return {"schema": 1, "status": "passed", "scope": "captured-input-preflight-only",
            "source_commit": expected_revision, "bundle_sha256": expected_sha256,
            "capture_sha256": metadata["capture_sha256"], "checked_recipe_files": checked,
            "image_built": False, "hardware_qualified": False, "promotable": False,
            "limitations": ["No native build, detached activation, image verification or device test was run.",
                            "Archive integrity does not establish complete dependency/vendor closure or image-build readiness.",
                            "Use the existing frozen image pipeline for construction; R1 release promotion remains separate."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    report = {"schema": 1, "status": "failed", "scope": "captured-input-preflight-only",
              "promotable": False, "image_built": False, "hardware_qualified": False}
    try:
        report = verify(args.bundle, args.sha256, args.expected_revision)
    except Exception as error:
        report["error"] = str(error)
        print("candidate: " + str(error), file=sys.stderr)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
