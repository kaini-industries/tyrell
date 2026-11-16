#!/usr/bin/env python3
"""Build, but never install, the config-only power foundation on Debian ARM64."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

PACKAGE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(PACKAGE))
import power_policy


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy(source, target, mode=0o644):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--contract", type=Path,
                        default=PACKAGE.parents[1] / "image/contracts/tyrell-zero-power.json")
    parser.add_argument("--source-revision", default="worktree")
    args = parser.parse_args()
    os.umask(0o022)
    if platform.system() != "Linux" or platform.machine() not in {"aarch64", "arm64"}:
        parser.error("Build natively in the Debian ARM64 builder")
    arch = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    if arch != "arm64":
        parser.error("Expected Debian arm64")
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "")
    if not epoch.isdigit():
        parser.error("Set SOURCE_DATE_EPOCH to the captured nonnegative build epoch")
    if not args.source_revision or len(args.source_revision) > 128 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in args.source_revision):
        parser.error("Invalid source revision")
    if args.contract.is_symlink() or not args.contract.is_file():
        parser.error("Power contract must be a regular captured file")
    contract = power_policy.validate_contract(json.loads(args.contract.read_text()))
    power_policy.Selection.parse(json.loads((PACKAGE / "config/power-policy.json").read_text()))
    files = ["power_policy.py", "tyrell-zero-power-policy", "config/power-policy.json",
             "templates/ondemand-factor-1.conf.inactive", "README.md", "LICENSE",
             "build-package.py", "tests/test_power_policy.py"]
    for name in files:
        path = PACKAGE / name
        if path.is_symlink() or not path.is_file():
            parser.error("Unsafe source input: " + name)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    deb = output / "tyrell-zero-power-policy_0.1.0-1_arm64.deb"
    if deb.exists():
        parser.error("Output already exists; use a fresh artifact directory")
    env = dict(os.environ, TYRELL_POWER_CONTRACT=str(args.contract.resolve()),
               PYTHONDONTWRITEBYTECODE="1", LC_ALL="C.UTF-8", TZ="UTC")
    tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s",
                            str(PACKAGE / "tests"), "-v"], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    (output / "tests.txt").write_text(tests.stdout)
    if tests.returncode:
        sys.stderr.write(tests.stdout)
        return tests.returncode
    inputs = {name: sha(PACKAGE / name) for name in files}
    provenance = {
        "schema": 1, "package": "tyrell-zero-power-policy", "version": "0.1.0-1",
        "architecture": arch, "source_revision": args.source_revision,
        "source_date_epoch": int(epoch), "source_files_sha256": inputs,
        "power_contract_sha256": sha(args.contract), "plan_revision": contract["plan_revision"],
        "applicability": "foundation-only; optimized activation unavailable",
        "default_preset": "baseline", "optimized_activation_implemented": False,
        "hardware_qualified": False, "battery_qualified": False,
        "automatic_units_or_rules": [], "maintainer_scripts": [], "installation_sysfs_writes": False,
    }
    with tempfile.TemporaryDirectory(prefix="tyrell-zero-power-package-") as directory:
        stage = Path(directory) / "root"
        control = stage / "DEBIAN"; control.mkdir(parents=True)
        (control / "control").write_text(
            "Package: tyrell-zero-power-policy\nVersion: 0.1.0-1\nArchitecture: arm64\n"
            "Maintainer: Tyrell contributors <tyrell@localhost>\nSection: admin\nPriority: optional\n"
            "Depends: python3 (>= 3.11)\n"
            "Description: Inactive power-policy foundation for Tyrell Zero\n"
            " Config-only baseline, validation and generation-bound cleanup.\n"
            " Optimized activation and hardware/battery qualification are unavailable.\n")
        (control / "conffiles").write_text("/etc/tyrell-zero/power-policy.json\n")
        copy(PACKAGE / "power_policy.py", stage / "usr/lib/tyrell-zero/power_policy.py")
        copy(PACKAGE / "tyrell-zero-power-policy", stage / "usr/libexec/tyrell-zero-power-policy", 0o755)
        copy(PACKAGE / "config/power-policy.json", stage / "etc/tyrell-zero/power-policy.json")
        resource = stage / "usr/share/tyrell-zero/power-policy"
        copy(args.contract, resource / "contract.json")
        power_policy.validate_contract(json.loads((resource / "contract.json").read_text()))
        copy(PACKAGE / "templates/ondemand-factor-1.conf.inactive", resource / "ondemand-factor-1.conf.inactive")
        for name in ["README.md", "LICENSE"]:
            copy(PACKAGE / name, stage / "usr/share/doc/tyrell-zero-power-policy" / name)
        (resource / "source-manifest.json").write_text(json.dumps(provenance, indent=2) + "\n")
        # Freeze contents after staging so changed build inputs cannot be certified.
        if any(sha(PACKAGE / name) != value for name, value in inputs.items()):
            raise RuntimeError("Package sources changed during assembly")
        if sha(args.contract) != provenance["power_contract_sha256"]:
            raise RuntimeError("Power contract changed during assembly")
        if sha(resource / "contract.json") != provenance["power_contract_sha256"]:
            raise RuntimeError("Staged power contract differs from captured input")
        for path in sorted(stage.rglob("*")):
            os.utime(path, (int(epoch), int(epoch)), follow_symlinks=False)
        os.utime(stage, (int(epoch), int(epoch)))
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(stage), str(deb)], check=True, env=env)
    provenance["package_sha256"] = sha(deb)
    provenance["package_bytes"] = deb.stat().st_size
    (output / "manifest.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps({"package": str(deb), "sha256": provenance["package_sha256"],
                      "applicability": provenance["applicability"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
