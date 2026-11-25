#!/usr/bin/env python3
"""Build/test a DEB in the existing ARM64 builder; never install or activate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--wizard-binary", type=Path, help="Native Wizard from the verified offline build")
    args = parser.parse_args()
    if args.wizard_binary:
        if args.wizard_binary.is_symlink() or not args.wizard_binary.is_file():
            parser.error("Wizard must be a regular file")
        header = args.wizard_binary.read_bytes()[:20]
        if header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != 183:
            parser.error("Wizard must be a little-endian ARM64 ELF")
        args.wizard_binary = args.wizard_binary.resolve()
    if platform.system() != "Linux" or platform.machine() not in {"aarch64", "arm64"}:
        parser.error("the package requires the Linux ARM64 builder")
    architecture = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    if architecture != "arm64":
        parser.error("native Debian ARM64 is required")
    source = Path(__file__).resolve().parent
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    epoch = env.setdefault("SOURCE_DATE_EPOCH", "1794528000")
    if not epoch.isdigit():
        parser.error("SOURCE_DATE_EPOCH must be a nonnegative integer")
    env.update(TZ="UTC", LC_ALL="C.UTF-8", PYTHONDONTWRITEBYTECODE="1")
    def run(command, **kwargs):
        return subprocess.run(command, check=True, env=env, **kwargs)
    with tempfile.TemporaryDirectory(prefix=".session-build-", dir=output) as temporary:
        build = Path(temporary)
        options = ["-DTYRELL_NATIVE_WIZARD=" + str(args.wizard_binary)] if args.wizard_binary else []
        run(["cmake", "-S", str(source), "-B", str(build), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_TESTING=ON", *options])
        run(["cmake", "--build", str(build), "--parallel", "4"])
        result = subprocess.run(["ctest", "--test-dir", str(build), "--output-on-failure"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        (output / "session-tests.txt").write_text(result.stdout)
        print(result.stdout, end="")
        detail = build / "Testing/Temporary/LastTest.log"
        if detail.exists():
            (output / "session-test-detail.txt").write_bytes(detail.read_bytes())
        result.check_returncode()
        run(["cpack", "--config", str(build / "CPackConfig.cmake"), "-G", "DEB", "-B", str(output)])
        (output / "recovery.ppm").write_bytes((build / "recovery.ppm").read_bytes())
    deb = output / "tyrell-zero-session_0.3.1-1_arm64.deb"
    manifest = {"package": "tyrell-zero-session", "version": "0.3.1-1", "architecture": "arm64",
                "source_date_epoch": int(epoch), "foundation_only": not bool(args.wizard_binary),
                "native_wizard_sha256": hashlib.sha256(args.wizard_binary.read_bytes()).hexdigest() if args.wizard_binary else None,
                "integration_complete": False, "service_activation_performed": False,
                "deb_sha256": hashlib.sha256(deb.read_bytes()).hexdigest(),
                "sources": {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in sorted(source.rglob("*")) if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}}
    (output / "session-build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
