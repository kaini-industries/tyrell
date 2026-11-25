#!/usr/bin/env python3
"""Native ARM64 foundation checks; never activate Zero or install into the host.

Run in a disposable Debian 13 ARM64 worker. The existing package builders and
empty-root dpkg harness are reused. Their --force-depends lifecycle is explicitly
not a runtime dependency, native Wizard, image, service or hardware qualification.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import runpy
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
PACKAGES = {
    "tyrell-zero-session": {
        "version": "0.3.1-1", "summary": "session-tests.txt",
        "detail": "session-test-detail.txt",
        "tests": ("session-state", "recovery-version", "recovery-render",
                  "updates-help-text", "updates-help-render", "native-wizard-transaction"),
    },
    "tyrell-zero-store": {
        "version": "0.1.1-1", "summary": "store-tests.txt",
        "detail": "store-test-detail.txt", "tests": ("store-routing", "store-guard"),
    },
    "tyrell-zero-power-policy": {
        "version": "0.1.1-1", "summary": "tests.txt", "detail": "tests.txt",
        "tests": (),
    },
}
LIMITATIONS = [
    "Foundation builds and empty disposable dpkg roots only; --force-depends does not verify dependencies.",
    "No native Wizard binary, preserved vendor executable, full runtime closure or offline activation is qualified.",
    "No image construction, boot, service activation, optimized power application or device acceptance.",
    "Toolchain packages are recorded, not frozen; byte-identical package builds are not claimed.",
]


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def preflight():
    """Fail before generating outputs on a host that cannot perform this scope."""
    if platform.system() != "Linux" or platform.machine() not in {"aarch64", "arm64"}:
        raise RuntimeError("zero-native requires a disposable native Linux ARM64 worker")
    required = ("cmake", "ctest", "cpack", "ninja", "c++", "dpkg", "dpkg-deb",
                "dpkg-query", "dpkg-divert")
    missing = [name for name in required if not shutil.which(name)]
    if missing:
        raise RuntimeError("Missing required native prerequisites: " + ", ".join(missing))
    release = Path("/etc/os-release").read_text()
    fields = dict(line.split("=", 1) for line in release.splitlines()
                  if "=" in line and not line.startswith("#"))
    if fields.get("ID", "").strip('"') != "debian" or fields.get("VERSION_ID", "").strip('"') != "13":
        raise RuntimeError("zero-native requires Debian 13; worker OS is not qualified for this check")
    arch = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    if arch != "arm64":
        raise RuntimeError("zero-native requires native Debian arm64, not " + arch)


def fresh_work(root, work):
    """Reject reused output or a symlink before package builders can write."""
    root = root.resolve()
    work = Path(os.path.abspath(work))
    expected = root / "build/ci"
    if not work.is_relative_to(expected) or work == expected:
        raise RuntimeError("Native output must be a fresh directory below build/ci")
    for path in [work, *work.parents]:
        if path == root:
            break
        if path.is_symlink():
            raise RuntimeError("Native output cannot traverse a symlink")
    if work.exists():
        raise RuntimeError("Native output already exists; choose a fresh directory")
    return work


def source_identity(root):
    files = [root / "builder/zero-guest.py", root / "image/contracts/tyrell-zero-power.json",
             root / "locks/build-inputs.json"]
    for name in PACKAGES:
        files.extend(sorted((root / "packages" / name).rglob("*")))
    result = {}
    for path in files:
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise RuntimeError("Symlinked native input: " + str(path))
        if path.is_file():
            result[str(path.relative_to(root))] = sha(path)
    for name in PACKAGES:
        if "packages/" + name + "/build-package.py" not in result:
            raise RuntimeError("Missing native package builder: " + name)
    return result


def require_test_evidence(output, package):
    """CTest can pass while a nested unittest suite skips required coverage."""
    spec = PACKAGES[package]
    summary = (output / spec["summary"]).read_text()
    detail = (output / spec["detail"]).read_text()
    for text in (summary, detail):
        if re.search(r"\bskipped\s*[=:]\s*[1-9]\d*|\*+[^\n]*(?:Skipped|Not Run)", text):
            raise RuntimeError(package + ": required native tests were skipped")
    if spec["tests"]:
        passed = re.search(r"100% tests passed, 0 tests failed out of ([1-9]\d*)", summary)
        if not passed:
            raise RuntimeError(package + ": missing successful CTest evidence")
        for name in spec["tests"]:
            if not re.search(r"Test\s+#\d+:\s+" + re.escape(name) + r"\s+[^\n]*Passed", summary):
                raise RuntimeError(package + ": missing required test " + name)
        count = int(passed[1])
    else:
        ran = re.search(r"\bRan ([1-9]\d*) tests? in ", summary)
        if not ran or not re.search(r"^OK\s*$", summary, re.MULTILINE):
            raise RuntimeError(package + ": missing successful unittest evidence")
        count = int(ran[1])
    return {"registered_test_count": count, "required_skips": 0,
            "summary": spec["summary"], "detail": spec["detail"]}


def build_package(root, target, package, env):
    target.mkdir()
    with (target / "build.log").open("w") as log:
        result = subprocess.run([sys.executable, str(root / "packages" / package / "build-package.py"),
                                 "--output-dir", str(target)], cwd=root, env=env,
                                stdout=log, stderr=subprocess.STDOUT, timeout=1200)
    if result.returncode:
        tail = (target / "build.log").read_text()[-8000:]
        raise RuntimeError(package + ": build failed; see " + str(target / "build.log") + "\n" + tail)


def run_zero_native(root, work):
    root = Path(root).resolve()
    work = fresh_work(root, work)
    preflight()
    api = runpy.run_path(str(root / "builder/zero-guest.py"))
    selected_before = api["host_packages"](PACKAGES)
    if any(value.startswith("installed") for value in selected_before.values()):
        raise RuntimeError("A foundation package is already installed in this worker; use a fresh worker")
    database = Path("/var/lib/dpkg/status")
    status_before = sha(database)
    sources = source_identity(root)
    lock = json.loads((root / "locks/build-inputs.json").read_text())
    epoch = lock["source_date_epoch"]
    if type(epoch) is not int or epoch < 0:
        raise RuntimeError("Invalid SOURCE_DATE_EPOCH in locked inputs")
    env = dict(os.environ, SOURCE_DATE_EPOCH=str(epoch), TZ="UTC", LC_ALL="C.UTF-8",
               PYTHONDONTWRITEBYTECODE="1")
    work.mkdir(parents=True)
    packages = []
    try:
        for package, spec in PACKAGES.items():
            target = work / package
            build_package(root, target, package, env)
            tests = require_test_evidence(target, package)
            debs = list(target.glob("*.deb"))
            if len(debs) != 1 or debs[0].is_symlink():
                raise RuntimeError(package + ": expected exactly one regular DEB")
            deb = debs[0]
            # Inspection must precede dpkg: only files-only packages enter an
            # empty root, never arbitrary maintainer scripts or enabled units.
            fields = api["inspect_package"](deb, package, spec["version"])
            with (target / "isolated-install.log").open("w") as log:
                api["isolated_dpkg"](deb, package, log)
            packages.append({"package": package, "version": spec["version"],
                             "file": str(deb.relative_to(work)), "sha256": sha(deb),
                             "control": fields, "tests": tests,
                             "isolated_install_reinstall_remove_purge": "passed",
                             "dependency_closure_verified": False})
        if source_identity(root) != sources:
            raise RuntimeError("Native sources changed during checks; start a fresh run")
    finally:
        if sha(database) != status_before or api["host_packages"](PACKAGES) != selected_before:
            raise RuntimeError("Live worker dpkg state changed during isolated foundation checks")
    with (work / "worker-packages.tsv").open("w") as stream:
        subprocess.run(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${Architecture}\n"],
                       check=True, stdout=stream)
    report = {"schema_version": 1, "suite": "zero-native", "result": "passed",
              "scope": "native-foundation-build-tests-and-empty-root-lifecycle",
              "architecture": "arm64", "worker_os": "Debian 13",
              "source_files_sha256": sources, "source_date_epoch": epoch,
              "packages": packages, "live_dpkg_status_before_sha256": status_before,
              "live_dpkg_status_after_sha256": sha(database),
              "live_foundation_packages_before": selected_before,
              "live_foundation_packages_after": selected_before,
              "image_qualification": False, "hardware_qualification": False,
              "limitations": LIMITATIONS}
    (work / "native-foundation.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("zero-native",), required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = run_zero_native(ROOT, args.work_dir)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print("native CI: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
