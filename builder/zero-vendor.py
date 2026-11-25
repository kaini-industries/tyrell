#!/usr/bin/env python3
"""Fetch explicit pinned inputs, then replay the native Wizard source offline.

No upstream working tree is changed. ``stage`` accepts only verified regular
files/directories from these archives, into a fresh destination. It does not
execute source, invoke Git, or access the network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = ROOT / "locks/tyrell-zero-vendor-inputs.json"
DEFAULT_CACHE = ROOT / "build/zero-vendor/downloads"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative_path(value: str, *, allow_dot: bool = False) -> PurePosixPath:
    path = PurePosixPath(value)
    if (not value or "\\" in value or path.is_absolute()
            or any(p in {"", ".", ".."} for p in value.split("/"))):
        if allow_dot and value == ".":
            return path
        raise ValueError(f"unsafe relative path: {value!r}")
    return path


def load_lock(path: Path = DEFAULT_LOCK) -> dict:
    lock = json.loads(path.read_text())
    if lock.get("schema_version") != 1 or not lock.get("archives"):
        raise ValueError("unsupported or empty vendor input lock")
    names = set()
    for entry in lock["archives"]:
        name = relative_path(entry["filename"])
        if len(name.parts) != 1 or entry["filename"] in names:
            raise ValueError("duplicate/non-flat input filename")
        names.add(entry["filename"])
        relative_path(entry["archive_root"])
        relative_path(entry["destination"], allow_dot=True)
        if (len(entry["sha256"]) != 64 or
                any(c not in "0123456789abcdef" for c in entry["sha256"]) or
                not all(isinstance(entry[k], int) and entry[k] > 0
                        for k in ("bytes", "members", "unpacked_bytes"))):
            raise ValueError("invalid input integrity fields")
        if not entry["url"].startswith("https://codeload.github.com/"):
            raise ValueError("vendor input URL must use official HTTPS codeload")
    return lock


def verify_archive(path: Path, entry: dict) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing/non-regular archive: {path}")
    if path.stat().st_size != entry["bytes"] or digest(path) != entry["sha256"]:
        raise ValueError(f"archive size/SHA256 mismatch: {path.name}")


def verify_inputs(lock: dict, cache: Path) -> None:
    for entry in lock["archives"]:
        verify_archive(cache / entry["filename"], entry)


def fetch(lock: dict, cache: Path) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    for entry in lock["archives"]:
        target = cache / entry["filename"]
        if target.exists() or target.is_symlink():
            verify_archive(target, entry)
            continue
        handle, temporary = tempfile.mkstemp(prefix=".vendor-", dir=cache)
        os.close(handle)
        temporary = Path(temporary)
        try:
            subprocess.run(["curl", "--fail", "--location", "--silent", "--show-error",
                            "--proto", "=https", "--proto-redir", "=https",
                            "--output", str(temporary), entry["url"]], check=True)
            verify_archive(temporary, entry)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def safe_extract(archive: Path, destination: Path, entry: dict) -> None:
    """Preflight the whole tar before writing; never call extract/extractall."""
    verify_archive(archive, entry)
    if any(p.is_symlink() for p in [destination, *destination.parents]):
        raise ValueError("extraction destination/ancestors cannot be symlinks")
    if destination.is_symlink() or (destination.exists() and any(destination.iterdir())):
        raise ValueError(f"extraction destination must be empty: {destination}")
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if (len(members) != entry["members"] or
                sum(m.size for m in members) != entry["unpacked_bytes"]):
            raise ValueError("archive member/expanded-size mismatch")
        paths = {}
        root = entry["archive_root"]
        for member in members:
            name = member.name.rstrip("/")
            path = relative_path(name)
            if path.parts[0] != root or not (member.isfile() or member.isdir()):
                raise ValueError(f"unsupported/escaping archive member: {member.name}")
            relative = PurePosixPath(*path.parts[1:])
            if str(relative) == ".":
                if not member.isdir():
                    raise ValueError("archive root is not a directory")
                continue
            if relative in paths:
                raise ValueError(f"duplicate archive member: {member.name}")
            paths[relative] = member
        for relative in paths:
            for parent in relative.parents:
                if parent in paths and not paths[parent].isdir():
                    raise ValueError(f"archive parent is a file: {relative}")
        destination.mkdir(parents=True, exist_ok=True)
        for relative, member in paths.items():
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as src, target.open("xb") as out:
                    shutil.copyfileobj(src, out)
                # Preserve executable bits without archive owner/set-ID metadata.
                target.chmod(0o755 if member.mode & 0o111 else 0o644)


def offline_guard(lock: dict) -> str:
    allowed = {}
    for entry in lock["archives"]:
        if entry["destination"].startswith("SDK/github_source/"):
            allowed[entry["destination"][len("SDK/github_source/"):]] = {
                "commit": entry["commit"], "url": entry["repository"]}
    # The SDK executes this helper anew in each component's globals. Appending
    # overrides also catches its second, branch-following wget_github definition.
    return '''

# BEGIN TYRELL LOCKED OFFLINE INPUTS (generated by builder/zero-vendor.py)
def _tyrell_no_download(*args, **kwargs):
    raise RuntimeError("Tyrell native build forbids downloads/Conan; restage locked source inputs")

sample_wget = wget_tar_xz = wget_tar_gz = wget_zip = _tyrell_no_download
wget_github = wget_github_commit = check_wget_down = _tyrell_no_download
Export_conanenv = ensure_static_lib_version = _tyrell_no_download

def check_component(component_name):
    import os
    from pathlib import Path
    allowed = ''' + repr(allowed) + '''
    entry = env['GIT_REPO_LISTS'][component_name]
    root = Path(os.environ['GIT_REPO_PATH']).resolve(strict=True)
    path = Path(entry['path'])
    if path.is_symlink():
        raise RuntimeError("Tyrell source dependency cannot be a symlink")
    resolved = path.resolve(strict=True)
    relative = resolved.relative_to(root).as_posix()
    identity = {'commit': entry['commit'], 'url': entry['url']}
    if allowed.get(relative) != identity or not resolved.is_dir():
        raise RuntimeError("Tyrell source dependency outside locked native closure: " + component_name)
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise RuntimeError("Tyrell source dependency has a symlink ancestor")
    return str(resolved)
# END TYRELL LOCKED OFFLINE INPUTS
'''


def offline_helper(upstream: str, lock: dict) -> str:
    # Components execute this whole helper in their own SCons globals. The
    # November SDK evaluates a Path annotation before our appended overrides,
    # so its import must precede the unchanged upstream definitions.
    return "from pathlib import Path\n" + upstream + offline_guard(lock)


def stage(lock: dict, cache: Path, destination: Path) -> dict:
    verify_inputs(lock, cache)
    destination = destination.absolute()
    if any(p.is_symlink() for p in [destination, *destination.parents]):
        raise ValueError("stage destination/ancestors cannot be symlinks")
    if destination.exists():
        raise ValueError(f"refusing to replace existing stage: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Build beside the final directory and rename only after complete replay.
    temporary = Path(tempfile.mkdtemp(prefix=".tyrell-vendor-", dir=destination.parent))
    try:
        for entry in lock["archives"]:
            target = temporary if entry["destination"] == "." else temporary / entry["destination"]
            safe_extract(cache / entry["filename"], target, entry)
        patch = lock["sdk_offline_patch"]
        helper = temporary / patch["path"]
        if digest(helper) != patch["upstream_sha256"]:
            raise ValueError("SDK offline patch upstream hash mismatch")
        helper.write_text(offline_helper(helper.read_text(), lock))
        manifest = {
            "schema_version": 1,
            "kind": "verified-offline-native-wizard-source-stage",
            "archives": [{k: e[k] for k in ("id", "commit", "sha256", "destination")}
                         for e in lock["archives"]],
            "sdk_offline_helper_sha256": digest(helper),
            "native_build": lock["native_build"],
        }
        (temporary / ".tyrell-vendor-stage.json").write_text(json.dumps(manifest, indent=2) + "\n")
        temporary.rename(destination)
        return manifest
    except BaseException:
        shutil.rmtree(temporary)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fetch", "check", "stage"))
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--destination", type=Path, default=ROOT / "build/zero-vendor/source")
    args = parser.parse_args()
    lock = load_lock(args.lock)
    if args.action == "fetch":
        fetch(lock, args.cache)
    elif args.action == "check":
        verify_inputs(lock, args.cache)
    else:
        stage(lock, args.cache, args.destination)
    print(f"vendor {args.action}: {len(lock['archives'])} locked archives verified")


if __name__ == "__main__":
    main()
