#!/usr/bin/env python3
"""Offline image diversion and baseline capture. Never operates on the live root.

prepare -> install Store DEB -> capture; remove/purge Store DEB -> restore.
This is a build tool, not a user-authorized privileged runtime interface.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from guard import (Root, GuardError, digest, PUBLIC, ORIGINAL, POLICY, CATALOG,
                   MODULE, HELPER, INVARIANTS, VENDOR_SHA, TRIGGER_PACKAGES, SCRIPTS)


def image_root(value):
    supplied = Path(value)
    root = supplied.resolve()
    if not supplied.is_absolute() or supplied != root or root == Path("/"):
        raise GuardError("an absolute canonical offline image root is required")
    if not (root / "var/lib/dpkg/status").is_file():
        raise GuardError("offline Debian package database is missing")
    for name in ["proc", "sys", "dev"]:
        path = root / name
        if path.is_mount():raise GuardError("detach image runtime mounts before offline activation")
    return root


def command(root, *args):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       env={"PATH":"/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL":"C.UTF-8"}, timeout=120)
    if p.returncode:raise GuardError("offline command failed: " + p.stderr.strip())
    return p.stdout.strip()


def diversion(root, action, execute=command):
    base = ["/usr/bin/dpkg-divert", "--root=" + str(root), "--admindir=" + str(root / "var/lib/dpkg")]
    package = execute(root, *base, "--listpackage", PUBLIC)
    true_name = execute(root, *base, "--truename", PUBLIC)
    public = root / PUBLIC.lstrip("/");original = root / ORIGINAL.lstrip("/")
    if action == "prepare":
        if package:
            if package != "tyrell-zero-store" or true_name != ORIGINAL or not original.is_file() or digest(original.read_bytes()) != VENDOR_SHA:
                raise GuardError("existing Store diversion identity differs")
            return
        if original.exists() or original.is_symlink() or public.is_symlink() or not public.is_file() \
                or digest(public.read_bytes()) != VENDOR_SHA:
            raise GuardError("offline vendor Store bytes differ from locked applaunch")
        execute(root, *base, "--package", "tyrell-zero-store", "--add", "--rename", "--divert", ORIGINAL, PUBLIC)
        if public.exists() or digest(original.read_bytes()) != VENDOR_SHA:
            raise GuardError("Store diversion did not preserve original bytes")
    elif action == "restore":
        if package != "tyrell-zero-store" or true_name != ORIGINAL or public.exists() or public.is_symlink() \
                or original.is_symlink() or not original.is_file() or digest(original.read_bytes()) != VENDOR_SHA:
            raise GuardError("remove/purge Store before restoring the pinned vendor diversion")
        execute(root, *base, "--package", "tyrell-zero-store", "--remove", "--rename", "--divert", ORIGINAL, PUBLIC)
        if digest(public.read_bytes()) != VENDOR_SHA or original.exists():
            raise GuardError("vendor Store restoration failed")


def capture(root, protect_files, execute=command):
    owner = root.stat().st_uid
    trusted = Root(root, owner)
    rows = execute(root, "/usr/bin/dpkg-query", "--admindir=" + str(root / "var/lib/dpkg"),
                   "-W", "-f=${Package}\\t${db:Status-Abbrev}\\t${Version}\\n")
    packages = {};triggers = {}
    catalog = trusted.read_json(CATALOG)
    ordinary = {item["package"] for item in catalog["packages"]}
    for row in rows.splitlines():
        name, state, version = row.split("\t")
        if state[1:2] in {"c", "n"}:continue
        if state[1:2] != "i" or state[2:3] == "R":raise GuardError("capture requires clean installed package state")
        if name not in ordinary:packages[name] = version
        if name in TRIGGER_PACKAGES:
            scripts = {}
            for script in SCRIPTS:
                p = root / ("var/lib/dpkg/info/" + name + "." + script)
                if p.exists():scripts[script] = digest(trusted.secure(str(p.relative_to(root))).read_bytes())
            triggers[name] = {"version":version, "scripts":scripts}
    required = {PUBLIC, ORIGINAL, POLICY, CATALOG, MODULE, HELPER, "/var/lib/dpkg/diversions"}
    # Freeze every shipped policy/helper module, plus image-integrator masks/config.
    for prefix in ["usr/lib/tyrell-zero", "usr/libexec/tyrell-zero", "usr/share/tyrell-zero", "etc/tyrell-zero"]:
        folder = root / prefix
        if folder.exists():
            required.update("/" + str(p.relative_to(root)) for p in folder.rglob("*") if p.is_file() or p.is_symlink())
    required.discard(INVARIANTS)
    # The session receipt binds this snapshot. Excluding that generated receipt
    # avoids a circular digest dependency; the Store still path-protects it.
    required.discard("/usr/share/tyrell-zero/session-integration.json")
    required.discard("/usr/share/tyrell-zero/pre-image-qualification.json")
    files = {}
    for name in sorted(required | set(protect_files)):
        path = trusted.at(name)
        if path.is_symlink():
            trusted.secure(str(path.parent.relative_to(root)), directory=True)
            if path.lstat().st_uid != owner:raise GuardError("untrusted protected image link")
            files[name] = {"symlink":os.readlink(path)}
        else:files[name] = digest(trusted.secure(name).read_bytes())
    if files[ORIGINAL] != VENDOR_SHA:raise GuardError("captured vendor bytes do not match source lock")
    if not {"applaunch", "tyrell-zero-store", "tyrell-zero-session"}.issubset(packages):
        raise GuardError("capture requires installed native integration packages")
    result = {"schema":1, "complete":True, "packages":packages, "files":files, "triggers":triggers}
    output = trusted.at(INVARIANTS)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n");output.chmod(0o644)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("action", choices=["prepare", "restore", "capture"])
    parser.add_argument("--root", required=True)
    parser.add_argument("--protect-file", action="append", default=[])
    args = parser.parse_args()
    try:
        root = image_root(args.root)
        if args.action == "capture":capture(root, args.protect_file)
        else:diversion(root, args.action)
    except (GuardError, OSError, ValueError, KeyError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":main()
