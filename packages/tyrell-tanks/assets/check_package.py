#!/usr/bin/env python3
"""Inspect the shell-disabled native app archive without installing it."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import tarfile

def output(*args):
    return subprocess.check_output(args)

def require(value, message):
    if not value: raise ValueError(message)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    raw = args.package.read_bytes()
    fields = output("dpkg-deb", "--field", str(args.package)).decode()
    values = dict(line.split(": ", 1) for line in fields.splitlines() if ": " in line and not line.startswith(" "))
    require((values["Package"], values["Version"], values["Architecture"]) == ("tyrell-tanks", "0.1.1-1", "arm64"), values)
    dependencies = values.get("Depends", "")
    require(all(value in dependencies for value in ("libc6", "libstdc++6")), dependencies)
    require(not any(value in dependencies.lower() for value in ("x11", "glfw", "mesa", "libegl", "libgles")), dependencies)
    with tarfile.open(fileobj=io.BytesIO(output("dpkg-deb", "--ctrl-tarfile", str(args.package)))) as archive:
        members = [member for member in archive if not member.isdir()]
        names = [member.name.removeprefix("./") for member in members]
        require(all(member.isfile() for member in members) and len(names) == len(set(names)) and
            "control" in names and set(names) <= {"control", "md5sums"}, "Unexpected maintainer/configuration payload")
    expected = {
        "usr/bin/tyrell-tanks", "usr/share/APPLaunch/applications/tyrell-tanks.desktop",
        "usr/share/applications/tyrell-tanks.desktop", "usr/share/APPLaunch/share/images/tyrell-tanks.png",
        "usr/share/icons/hicolor/80x80/apps/tyrell-tanks.png", "usr/share/doc/tyrell-tanks/LICENSE",
        "usr/share/doc/tyrell-tanks/enet-LICENSE.txt", "usr/share/doc/tyrell-tanks/raylib-LICENSE.txt",
        "usr/share/doc/tyrell-tanks/raylib-third-party-notices.txt",
    }
    with tarfile.open(fileobj=io.BytesIO(output("dpkg-deb", "--fsys-tarfile", str(args.package)))) as archive:
        members = {member.name.removeprefix("./"): member for member in archive if not member.isdir()}
        require(set(members) == expected, {"missing": sorted(expected-set(members)), "extra": sorted(set(members)-expected)})
        require(all(member.isfile() and not member.mode & 0o6000 for member in members.values()), "Privileged or nonregular payload")
        def read(name): return archive.extractfile(members[name]).read()
        executable = read("usr/bin/tyrell-tanks")
        require(len(executable) >= 20 and executable[:6] == b"\x7fELF\x02\x01" and struct.unpack_from("<H", executable, 18)[0] == 183, "Expected ARM64 ELF")
        require(members["usr/bin/tyrell-tanks"].mode & 0o111, "Native app not executable")
        require(b"GH0ST_SHELL_FD" not in executable, "Default package includes shell environment adoption")
        entry = read("usr/share/APPLaunch/applications/tyrell-tanks.desktop").decode()
        require("Exec=/usr/bin/tyrell-tanks\n" in entry and "Icon=share/images/tyrell-tanks.png\n" in entry, "Launcher identity changed")
        png = read("usr/share/APPLaunch/share/images/tyrell-tanks.png")
        require(len(png) >= 24 and png[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack_from(">II", png, 16) == (80, 80), "Launcher icon changed")
        require(read("usr/share/icons/hicolor/80x80/apps/tyrell-tanks.png") == png, "Icon payloads differ")
        for license in ("LICENSE", "enet-LICENSE.txt", "raylib-LICENSE.txt", "raylib-third-party-notices.txt"):
            require(read("usr/share/doc/tyrell-tanks/" + license), "Missing license notice")
    print(json.dumps({"kind": "sandline-default-package-inspection", "status": "passed", "version": values["Version"],
        "architecture": "arm64", "package_sha256": hashlib.sha256(raw).hexdigest(),
        "binary_sha256": hashlib.sha256(executable).hexdigest(), "payload_files": len(expected),
        "shell_link_enabled": False, "hardware_qualified": False, "installed": False}))

if __name__ == "__main__": main()
