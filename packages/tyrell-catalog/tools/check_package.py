#!/usr/bin/env python3
"""Inspect the optional Catalog DEB without installing or executing its payload."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import struct
import subprocess
import sys
import tarfile

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[1]
sys.path.insert(0, str(PACKAGE))
import catalog_metadata as metadata


def require(condition, message):
    if not condition:
        raise ValueError(message)


def output(*args):
    return subprocess.check_output(list(map(str, args)))


def archive_name(name, directory):
    require(isinstance(name, str) and name and not PurePosixPath(name).is_absolute()
            and "\\" not in name and "\x00" not in name, "Unsafe archive path")
    if name in {".", "./"}:
        require(directory, "Unsafe archive path")
        return "."
    value = name.removeprefix("./")
    if directory and value.endswith("/"):
        value = value[:-1]
    require(value and all(part not in {"", ".", ".."} for part in value.split("/")), "Unsafe archive path")
    return value


def inspect(package):
    package = Path(package)
    require(package.is_file() and not package.is_symlink(), "Expected a regular package archive")
    fields = output("dpkg-deb", "--field", package).decode()
    control = dict(line.split(": ", 1) for line in fields.splitlines()
                   if ": " in line and not line.startswith(" "))
    require(all(control.get(k) == v for k, v in {"Package": "tyrell-catalog", "Version": "0.1.1-1",
                                               "Architecture": "arm64"}.items()), "Wrong Catalog package identity")
    depends = control.get("Depends", "")
    require(all(name in depends for name in ("python3", "openssl", "ca-certificates", "libc6", "libstdc++6", "libssl3")),
            "Catalog runtime dependencies are incomplete")
    require(not any(name in depends.lower() for name in ("sudo", "polkit", "applaunch", "x11", "libsdl", "libegl", "mesa")),
            "Unexpected privileged or desktop dependency")
    with tarfile.open(fileobj=io.BytesIO(output("dpkg-deb", "--ctrl-tarfile", package))) as archive:
        for member in archive:
            name = archive_name(member.name, member.isdir())
            require((member.isdir() and name == ".") or (member.isfile() and name in {"control", "md5sums"}),
                    "Catalog must have no maintainer scripts, conffiles or triggers")
    expected = {
        "usr/bin/tyrell-catalog",
        "usr/share/APPLaunch/applications/tyrell-catalog.desktop",
        "usr/share/APPLaunch/share/images/tyrell-catalog.png",
        "usr/share/applications/tyrell-catalog.desktop",
        "usr/share/icons/hicolor/80x80/apps/tyrell-catalog.png",
        *["usr/libexec/tyrell-catalog/" + name for name in ("backend.py", "catalog_cache.py", "catalog_metadata.py")],
        *["usr/share/tyrell-catalog/" + name for name in ("trust.json", "source.json", "seed.json")],
        *["usr/share/doc/tyrell-catalog/" + name for name in
          ("README.md", "LICENSE", "THIRD_PARTY.md", "lodepng-LICENSE.txt", "lvgl-LICENCE.txt", "app-common-LICENSE")],
    }
    artwork = {"usr/share/tyrell-catalog/artwork/" + path.name: path for path in (PACKAGE / "config/artwork").glob("*.png")}
    expected.update(artwork)
    directories = {"."}
    for name in expected:
        directories.update(str(p) for p in PurePosixPath(name).parents)
    data = {}
    with tarfile.open(fileobj=io.BytesIO(output("dpkg-deb", "--fsys-tarfile", package))) as archive:
        seen = set()
        for member in archive:
            name = archive_name(member.name, member.isdir())
            require(name not in seen, "Duplicate package path")
            seen.add(name)
            require(member.uid == member.gid == 0 and not member.mode & 0o6022,
                    "Package trust/data paths must be root-owned and not writable by other users")
            if member.isdir():
                require(name in directories, "Unexpected package directory")
            else:
                require(member.isfile() and name in expected, "Unexpected package file or link: " + name)
                require(member.size <= 16 * 1024 * 1024, "Unexpectedly large Catalog payload file")
                data[name] = archive.extractfile(member).read()
    require(set(data) == expected, "Missing Catalog package files")
    binary = data["usr/bin/tyrell-catalog"]
    require(binary[:6] == b"\x7fELF\x02\x01" and struct.unpack_from("<H", binary, 18)[0] == 183,
            "Expected AArch64 ELF64 GUI")
    for name in ("backend.py", "catalog_cache.py", "catalog_metadata.py"):
        require(data["usr/libexec/tyrell-catalog/" + name] == (PACKAGE / name).read_bytes(),
                "Packaged runtime module differs from reviewed source")
    for name in ("trust.json", "source.json", "seed.json"):
        require(data["usr/share/tyrell-catalog/" + name] == (PACKAGE / "config" / name).read_bytes(),
                "Packaged trust/source/seed differs from reviewed source")
    trust = metadata.load_json(data["usr/share/tyrell-catalog/trust.json"])
    seed = metadata.verify_envelope(data["usr/share/tyrell-catalog/seed.json"], trust, metadata.base_identity(ROOT))
    binding = metadata.load_json(data["usr/share/tyrell-catalog/source.json"])
    require(hashlib.sha256(metadata.canonical(binding["base_binding"])).hexdigest() == trust["base_id"],
            "Base binding differs from pinned catalog")
    require({row["artwork"]["sha256"] for row in seed["payload"]["packages"]} ==
            {Path(name).stem for name in artwork}, "Missing or extra seed artwork")
    for name, source in artwork.items():
        require(data[name] == source.read_bytes() and hashlib.sha256(data[name]).hexdigest() == source.stem,
                "Packaged artwork identity differs")
    desktop = data["usr/share/APPLaunch/applications/tyrell-catalog.desktop"].decode()
    require("Exec=/usr/bin/tyrell-catalog\n" in desktop and "Icon=share/images/tyrell-catalog.png\n" in desktop,
            "Wrong independent APPLaunch entry")
    icon = data["usr/share/APPLaunch/share/images/tyrell-catalog.png"]
    require(icon[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack_from(">II", icon, 16) == (80, 80), "Expected 80x80 Catalog launcher icon")
    require(data["usr/share/icons/hicolor/80x80/apps/tyrell-catalog.png"] == icon, "Launcher icons differ")
    return {"status": "passed", "package": "tyrell-catalog", "version": control["Version"],
            "architecture": control["Architecture"], "payload_files": len(data), "depends": depends,
            "seed_entries": len(seed["payload"]["packages"]), "base_id": trust["base_id"],
            "root_store_authorized": False, "installed": False, "hardware_qualified": False,
            "metadata_endpoint_configured": binding["metadata_url"] is not None}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    print(json.dumps(inspect(parser.parse_args().package), sort_keys=True))
