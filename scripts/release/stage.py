#!/usr/bin/env python3
"""Stage reviewed candidate data from exact retained source-check artifacts."""
from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import shutil
import sys

import github

ROOT = Path(__file__).resolve().parents[2]


def relative(name):
    if (not isinstance(name, str) or not name or any(c in name for c in "\\\0\r\n\t")
            or PurePosixPath(name).is_absolute() or str(PurePosixPath(name)) != name
            or any(part in {"", ".", ".."} for part in name.split("/"))):
        raise ValueError("Unsafe reviewed selection path")
    return name


def destination(root, name):
    path = Path(root)
    for part in relative(name).split("/"):
        path = path / part
        if path.is_symlink():
            raise ValueError("Redirected staged input: " + name)
    return path


def preserve_copy(source, destination_path, expected):
    source, destination_path = Path(source), Path(destination_path)
    if github.sha(source) != expected:
        raise ValueError("Reviewed input checksum mismatch: " + str(source))
    if os.path.lexists(destination_path):
        if destination_path.is_symlink() or not destination_path.is_file() or github.sha(destination_path) != expected:
            raise ValueError("Preserving a different existing staged input: " + str(destination_path))
        return False
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive publication avoids replacing retained input bytes on retry.
    with source.open("rb") as stream, destination_path.open("xb") as output:
        shutil.copyfileobj(stream, output, 1024**2)
    if github.sha(destination_path) != expected or github.sha(source) != expected:
        raise ValueError("Reviewed input changed during staging")
    return True


def stage(repository, run_id, source_sha, selection_path, selection_sha, inputs_root, *, repository_root=ROOT):
    github.checked(source_sha, r"[0-9a-f]{40}", "source revision")
    github.checked(selection_sha, r"[0-9a-f]{64}", "selection checksum")
    selection_path = relative(selection_path)
    reviewed = github.local(repository_root, selection_path)
    if github.sha(reviewed) != selection_sha:
        raise ValueError("Reviewed selection checksum mismatch")
    selection = github.read(reviewed)
    if selection.get("schema") != 1 or selection.get("source_commit") != source_sha:
        raise ValueError("Reviewed selection does not bind the selected source")
    checks = selection.get("checks")
    if not isinstance(checks, list) or len(checks) != len(github.SUITES):
        raise ValueError("Reviewed selection must name all required check artifacts")
    selected = {}; parents = []
    for item in checks:
        if not isinstance(item, dict):
            raise ValueError("Invalid reviewed check selection")
        name = relative(item.get("path"))
        github.checked(item.get("sha256"), r"[0-9a-f]{64}", "reviewed report checksum")
        path = PurePosixPath(name); suite = path.name.removesuffix(".json")
        if suite not in github.SUITES or path.name != suite + ".json" or suite in selected or str(path.parent) == ".":
            raise ValueError("Each suite needs one exact SUITE.json report in its own directory")
        parent = str(path.parent).casefold()
        if any(parent == old or parent.startswith(old + "/") or old.startswith(parent + "/") for old in parents):
            raise ValueError("Check artifact directories must be distinct and nonoverlapping")
        parents.append(parent); selected[suite] = item
    if set(selected) != set(github.SUITES):
        raise ValueError("Reviewed selection must name all required source suites exactly once")
    inputs_root = Path(inputs_root).absolute()
    if inputs_root.is_symlink() or inputs_root.resolve() != inputs_root:
        raise ValueError("Canonical unredirected staging root required")
    if inputs_root.exists() and not inputs_root.is_dir():
        raise ValueError("Staging root must be a directory")
    # Image restoration remains a separate explicit operation; no rebuilding or
    # implicit fallback to a mutable publication pointer happens here.
    image = selection.get("image")
    if image is not None:
        if not isinstance(image, dict):
            raise ValueError("Invalid reviewed image selection")
        image_directory = destination(inputs_root, image.get("directory"))
        if not image_directory.is_dir():
            raise ValueError("Restore the exact selected image export before staging")
    documents = selection.get("documents")
    if not isinstance(documents, list):
        raise ValueError("Reviewed candidate documents required")
    for item in documents:
        if not isinstance(item, dict):
            raise ValueError("Invalid reviewed document selection")
        relative(item.get("path"));github.checked(item.get("sha256"),r"[0-9a-f]{64}","reviewed document checksum")
    source_run = github.run(repository, run_id, ".github/workflows/checks.yml", source_sha)
    inputs_root.mkdir(parents=True, exist_ok=True)
    downloaded = []; retained = []
    for suite in github.SUITES:
        item = selected[suite]; report_path = destination(inputs_root, item["path"])
        artifact_name = f"checks-{suite}-{source_sha}"
        github.artifact(repository, run_id, artifact_name, source_sha)
        if not os.path.lexists(report_path):
            if os.path.lexists(report_path.parent):
                raise ValueError("Preserving an existing artifact directory without its selected report")
            github.gh("run", "download", str(run_id), "--repo", repository, "--name", artifact_name,
                      "--dir", report_path.parent)
            downloaded.append(suite)
        else:
            retained.append(suite)
        report = github.local(inputs_root, item["path"])
        if github.sha(report) != item["sha256"]:
            raise ValueError("Staged report differs from the reviewed checksum: " + suite)
        data = github.read(report)
        if (data.get("suite") != suite or data.get("status") != "passed"
                or data.get("source", {}).get("commit") != source_sha or data["source"].get("dirty") is not False):
            raise ValueError("Staged report does not describe the selected clean source: " + suite)
    copied_documents = []
    for item in documents:
        target = destination(inputs_root, item["path"])
        if os.path.lexists(target):
            if target.is_symlink() or not target.is_file() or github.sha(target) != item["sha256"]:
                raise ValueError("Preserving a different existing staged document")
        else:
            source = github.local(repository_root, item["path"])
            if preserve_copy(source, target, item["sha256"]):
                copied_documents.append(item["path"])
    preserve_copy(reviewed, destination(inputs_root, "release-selection.json"), selection_sha)
    return {"schema":1,"status":"passed","scope":"reviewed-data-staging-only","source_commit":source_sha,
            "source_run":source_run,"selection_path":selection_path,"selection_sha256":selection_sha,
            "inputs_root":str(inputs_root),"downloaded_suites":downloaded,"retained_suites":retained,
            "copied_documents":copied_documents,"image_built":False,"hardware_qualified":False,"promotable":False,
            "next_step":"Authenticate every selected report against canonical hosted artifacts, then assemble with trusted candidate tooling."}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    for name in ("repository","run-id","source-sha","selection","selection-sha256"):
        parser.add_argument("--"+name,required=True)
    parser.add_argument("--inputs-root",type=Path,required=True);parser.add_argument("--report",type=Path,required=True)
    args=parser.parse_args(argv)
    if args.report.is_symlink() or args.report.parent.resolve() != args.report.parent.absolute():
        parser.error("Unredirected report path required")
    if args.report.absolute().resolve().is_relative_to(args.inputs_root.absolute().resolve()):
        parser.error("Staging report must be outside retained candidate inputs")
    result={"schema":1,"status":"failed","scope":"reviewed-data-staging-only","image_built":False,"hardware_qualified":False,"promotable":False}
    try:
        result=stage(args.repository,args.run_id,args.source_sha,args.selection,args.selection_sha256,args.inputs_root)
    except Exception as error:
        result["error"]=str(error);print("stage: "+str(error),file=sys.stderr)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(__import__("json").dumps(result,sort_keys=True,indent=2)+"\n")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":raise SystemExit(main())
