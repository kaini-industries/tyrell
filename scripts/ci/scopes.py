#!/usr/bin/env python3
"""Select CI suites from both sides of a Git change, without path-filtered gates.

Unknown paths and unavailable comparison commits select every suite. This is
deliberately conservative: adding a new build input must not make CI go quiet.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUITES = ("docs", "python", "game", "chess", "chess-native", "status", "video", "zero-native")
PORTABLE = ("docs", "python", "game", "chess")
NATIVE = ("chess-native", "status", "video", "zero-native")
SHA = re.compile(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?\Z")


def select(paths):
    """Return ordered suites; all paths are repository-relative POSIX names."""
    selected = {"docs"}
    for path in paths:
        if path.startswith("./"):
            path = path[2:]
        if (not path or path.startswith("/") or ".." in path.split("/")
                or "\\" in path):
            raise ValueError("Expected a repository-relative POSIX path")
        # These files are consumed by every check, not just documentation.
        if (path.startswith(("scripts/ci/", ".github/"))
                or path in ("Makefile", ".gitignore", "research/compatibility-ledger.json")):
            selected.update(SUITES)
        elif path.startswith("packages/tyrell-app-common/"):
            selected.update(SUITES)
        # Package READMEs, icons, licenses and CMake files enter the package,
        # so they are intentionally not included in the docs-only rule below.
        elif path.startswith("packages/tyrell-game/"):
            selected.update(("python", "game", "chess", "chess-native"))
        elif path.startswith("packages/tyrell-tanks/"):
            selected.update(("python", "game"))
        elif path.startswith("packages/tyrell-chess/"):
            selected.update(("python", "chess", "chess-native"))
        elif path.startswith("packages/tyrell-status/"):
            selected.update(("python", "status"))
        elif path.startswith("packages/tyrell-video/"):
            selected.update(("python", "video"))
        elif path.startswith("packages/tyrell-zero-"):
            selected.update(("python", "zero-native"))
        elif path in ("scripts/game", "scripts/tanks", "locks/game-build-inputs.json"):
            selected.update(("python", "game", "chess", "chess-native"))
        elif path == "scripts/chess":
            selected.update(("python", "chess", "chess-native"))
        elif path.startswith("scripts/video-") or path == "scripts/prepare-video-store.py":
            selected.update(("python", "video"))
        elif path == "locks/build-inputs.json":
            selected.update(("python", "status", "video", "zero-native"))
        elif path.startswith(("builder/", "image/", "locks/")) or path == "scripts/builder":
            # Shared captures, package manifests and profile recipes cover all
            # consumers; this also retains stock/full-source regression tests.
            selected.update(SUITES)
        elif path.startswith(("dist/", "store/")):
            selected.update(("python", "zero-native"))
        elif path.startswith("scripts/tests/"):
            selected.add("python")
        elif path.startswith(("docs/", "research/")) or path in ("README.md", "LICENSE"):
            pass
        else:
            selected.update(SUITES)
    return [suite for suite in SUITES if suite in selected]


def parse_name_status(data):
    """Parse git diff --name-status -z, including deleted/renamed source names."""
    if not data:
        return []
    if not data.endswith(b"\0"):
        raise ValueError("Incomplete NUL-delimited Git change list")
    fields = data[:-1].split(b"\0")
    paths = []
    offset = 0
    while offset < len(fields):
        status = fields[offset].decode("ascii")
        offset += 1
        if not re.fullmatch(r"[ACDMRTUXB](?:[0-9]{1,3})?", status):
            raise ValueError("Unknown Git change status")
        count = 2 if status[0] in "RC" else 1
        if offset + count > len(fields):
            raise ValueError("Incomplete Git change record")
        for item in fields[offset:offset + count]:
            # Git paths are bytes. Surrogate escape preserves unusual names;
            # the selector's unknown-path fallback keeps their changes covered.
            paths.append(item.decode("utf-8", "surrogateescape"))
        offset += count
    return paths


def commit(value, root=ROOT):
    if value != "HEAD" and not SHA.fullmatch(value):
        raise ValueError("Expected HEAD or a full Git commit SHA")
    return subprocess.check_output(
        ["git", "rev-parse", "--verify", value + "^{commit}"], cwd=root,
        stderr=subprocess.DEVNULL, text=True).strip()


def plan(base=None, head="HEAD", all_suites=False, root=ROOT):
    source = commit(head, root)
    comparison = None
    reason = "changed paths"
    paths = []
    if all_suites or base is None or set(base) == {"0"}:
        selected = list(SUITES)
        reason = "explicit full run or no comparison commit"
    else:
        try:
            comparison = commit(base, root)
            data = subprocess.check_output(
                # Compare the actual endpoint trees. A merge-base-only diff
                # misses inputs removed by a force push onto another branch.
                ["git", "diff", "--name-status", "-z", "--find-renames",
                 comparison, source, "--"], cwd=root,
                stderr=subprocess.DEVNULL)
            paths = parse_name_status(data)
            selected = select(paths)
        except (subprocess.CalledProcessError, ValueError, UnicodeError):
            selected = list(SUITES)
            reason = "comparison unavailable or invalid; full run required"
    return {
        "schema": 1,
        "source_sha": source,
        "base_sha": comparison,
        "reason": reason,
        "paths": paths,
        "suites": selected,
        **{suite: suite in selected for suite in SUITES},
        "portable_matrix": {"include": [{"suite": suite} for suite in PORTABLE if suite in selected]},
        "native_matrix": {"include": [{"suite": suite} for suite in NATIVE if suite in selected]},
        "has_native": any(suite in selected for suite in NATIVE),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="Full comparison SHA; absent/unavailable selects every suite")
    parser.add_argument("--head", default="HEAD", help="Full immutable source SHA, or HEAD locally")
    parser.add_argument("--all", action="store_true", help="Select every suite")
    parser.add_argument("--output", type=Path, help="Also save the complete selection record")
    parser.add_argument("--github-output", type=Path, help="Append scalar/matrix job outputs")
    args = parser.parse_args()
    result = plan(args.base, args.head, args.all)
    encoded = json.dumps(result, indent=2, ensure_ascii=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    if args.github_output:
        with args.github_output.open("a") as output:
            for key in ("source_sha", "portable_matrix", "native_matrix", "has_native"):
                value = result[key]
                output.write(key + "=" + (value if isinstance(value, str)
                             else json.dumps(value, separators=(",", ":"))) + "\n")
    print(encoded, end="")


if __name__ == "__main__":
    main()
