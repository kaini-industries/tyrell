"""Qualified, immutable-byte package operations for the pinned M5 Store protocol.

Ported protocol/state semantics from Store b02db122396a7d33c41c66199ab443437975cfee.
This is a base-integration guard, not a sandbox for trusted maintainer scripts.
"""
import argparse
from contextlib import contextmanager
import datetime
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import signal
import stat
import subprocess
import tarfile
import tempfile
import time

VERSION = "0.1.0"
STORE_COMMIT = "b02db122396a7d33c41c66199ab443437975cfee"
PUBLIC = "/usr/share/APPLaunch/bin/M5CardputerZero-AppStore"
ORIGINAL = PUBLIC + ".vendor"
POLICY = "/usr/share/tyrell-zero/store-policy.json"
CATALOG = "/usr/share/tyrell-zero/store-catalog.json"
INVARIANTS = "/usr/share/tyrell-zero/base-invariants.json"
STATE = "/var/lib/tyrell-zero/store"
HELPER = "/usr/libexec/tyrell-zero/appstore-package-helper"
MODULE = "/usr/lib/tyrell-zero/store/guard.py"
VENDOR_SHA = "e5fe24b1dd26cd224c64ef4df2e7a61e0fce3fb63b3b04ac90d38c5092ccc05e"
TRIGGER_PACKAGES = {"man-db", "desktop-file-utils", "hicolor-icon-theme", "shared-mime-info", "mime-support"}
SCRIPTS = {"preinst", "postinst", "prerm", "postrm", "config", "triggers"}
PROTECTED_PACKAGES = {"applaunch", "tyrell-zero-session", "tyrell-zero-store",
    "tyrell-zero-power-policy", "raspberrypi-sys-mods", "greetd", "systemd",
    "systemd-sysv", "udev", "dbus", "dbus-user-session", "dpkg", "apt",
    "libc6", "libgcc-s1", "libstdc++6", "libpam0g", "libpam-systemd",
    "network-manager", "wpasupplicant", "openssh-server", "bluez", "polkitd",
    "udisks2", "pipewire", "pipewire-pulse", "wireplumber", "init-system-helpers"}
PROTECTED_PREFIXES = ("linux-", "firmware-", "raspi", "rpi-", "libcamera", "mesa-")
PROTECTED_PATHS = ("boot", "etc/tyrell-zero", "etc/pam.d", "etc/udev", "etc/apt",
    "etc/sudoers", "etc/sudoers.d", "etc/security", "etc/polkit-1", "usr/share/polkit-1",
    "etc/tmpfiles.d", "etc/systemd", "usr/lib/tyrell-zero", "usr/libexec/tyrell-zero",
    "usr/share/tyrell-zero", "usr/lib/modules", "lib/modules", "usr/lib/firmware",
    "lib/firmware", "var/lib/dpkg", "var/lib/tyrell-zero")


class GuardError(RuntimeError):
    pass


def strict_json(data):
    def pairs(rows):
        out = {}
        for key, value in rows:
            if key in out:
                raise GuardError("duplicate JSON field")
            out[key] = value
        return out
    try:
        result = json.loads(data, object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as error:
        raise GuardError("invalid JSON") from error
    if not isinstance(result, dict):
        raise GuardError("JSON object required")
    return result


def digest(data):
    return hashlib.sha256(data).hexdigest()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def package_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]{0,254}", value):
        raise GuardError("invalid Debian package name")
    return value


def protected_package(value):
    return value in PROTECTED_PACKAGES or value.startswith(PROTECTED_PREFIXES)


def archive_name(value):
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or "\x00" in value:
        raise GuardError("unsafe package archive path")
    return str(p)


class Runner:
    """Fixed-environment executor. Tests substitute this object internally only."""
    def __call__(self, args, timeout=600):
        env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root",
               "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "DEBIAN_FRONTEND": "noninteractive"}
        child = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 env=env, start_new_session=True)
        try:
            out, _ = child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL); child.communicate()
            raise GuardError("package command timed out; repair may be required") from error
        if len(out) > 32 * 1024 * 1024:
            raise GuardError("package command output exceeded limit")
        return child.returncode, out


class Root:
    def __init__(self, path=Path("/"), owner=0):
        # Alternate roots/owners are an internal test/offline API, never CLI/env input.
        self.path = Path(path).resolve()
        self.owner = owner

    def at(self, value):
        p = PurePosixPath(value)
        if ".." in p.parts:
            raise GuardError("unsafe root path")
        return self.path.joinpath(*p.parts[1:] if p.is_absolute() else p.parts)

    def secure(self, value, directory=False, absent=False):
        path = self.at(value)
        current = self.path
        for part in path.relative_to(self.path).parts:
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                if absent:
                    return path
                raise GuardError("required root-owned path is missing")
            if stat.S_ISLNK(info.st_mode) or info.st_uid != self.owner or info.st_mode & 0o022:
                raise GuardError("untrusted root-owned path")
            if current != path and not stat.S_ISDIR(info.st_mode):
                raise GuardError("invalid path parent")
        info = path.lstat()
        if directory and not stat.S_ISDIR(info.st_mode):
            raise GuardError("directory required")
        if not directory and not stat.S_ISREG(info.st_mode):
            raise GuardError("regular file required")
        return path

    def read_json(self, value):
        p = self.secure(value)
        if p.stat().st_size > 16 * 1024 * 1024:
            raise GuardError("policy exceeds size limit")
        return strict_json(p.read_bytes())

    def mkdir(self, value):
        p = self.at(value)
        current = self.path
        for part in p.relative_to(self.path).parts:
            current /= part
            created = False
            try:
                current.mkdir(mode=0o700)
                created = True
            except FileExistsError:
                pass
            self.secure(str(current.relative_to(self.path)), directory=True)
            if created:
                sync_directory(current.parent)
        return p


def inspect_deb(path, runner):
    rc, fields = runner(["/usr/bin/dpkg-deb", "--field", str(path)])
    if rc:
        raise GuardError("cannot inspect Debian control")
    control = {}
    for line in fields.decode().splitlines():
        if line[:1].isspace():
            continue
        if ":" in line:
            key, value = line.split(":", 1); control[key] = value.strip()
    package_name(control.get("Package"))
    if control.get("Architecture") not in {"arm64", "all"}:
        raise GuardError("unsupported package architecture")
    if protected_package(control["Package"]):
        raise GuardError("base-managed package requires base maintenance")
    if any(control.get(k, "").strip() for k in ["Provides", "Breaks", "Conflicts", "Replaces", "Protected", "Essential"]):
        raise GuardError("unsupported package relationship affects base identity")
    result = {"control": control, "files": {}, "scripts": {}, "directories": {}}
    for option, controls in [("--ctrl-tarfile", True), ("--fsys-tarfile", False)]:
        rc, blob = runner(["/usr/bin/dpkg-deb", option, str(path)])
        if rc:
            raise GuardError("cannot inspect Debian archive")
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tar:
            seen = set()
            for member in tar:
                name = archive_name(member.name)
                if name in seen:
                    raise GuardError("duplicate archive member")
                seen.add(name)
                if not (member.isfile() or member.isdir()) or member.mode & 0o6000:
                    raise GuardError("package links/special/set-ID files are not qualified")
                if member.uid != 0 or member.gid != 0:
                    raise GuardError("package ownership must be root")
                if member.isdir():
                    if member.mode & 0o022:
                        raise GuardError("writable package directory")
                    if not controls:result["directories"][name] = member.mode
                    continue
                content = tar.extractfile(member).read()
                if controls:
                    if name in SCRIPTS:
                        result["scripts"][name] = digest(content)
                else:
                    result["files"][name] = digest(content)
    return result


def make_catalog(paths, runner=None):
    runner = runner or Runner()
    records = []
    for path in paths:
        path = Path(path); data = path.read_bytes(); detail = inspect_deb(path, runner)
        name = detail["control"]["Package"]
        records.append({"package": name, "version": detail["control"]["Version"],
                        "architecture": detail["control"]["Architecture"],
                        "sha256": digest(data), "md5": hashlib.md5(data).hexdigest(),
                        "bytes": len(data), "control": detail["control"],
                        "files": detail["files"], "scripts": detail["scripts"], "directories":detail["directories"],
                        "desktop": "/usr/share/APPLaunch/applications/" + name + ".desktop",
                        "executables": ["/usr/bin/" + name, "/usr/lib/" + name + "/" + name,
                                        "/usr/lib/" + name + "/" + name + "_zero_device"]})
    return {"schema": 1, "store_source": STORE_COMMIT, "packages": records}


class Guard:
    def __init__(self, root=None, runner=None, policy=None, catalog=None):
        self.root = root or Root()
        self.run = runner or Runner()
        self.policy = policy if policy is not None else self.root.read_json(POLICY)
        self.catalog = catalog if catalog is not None else self.root.read_json(CATALOG)
        if type(self.policy.get("schema")) is not int or self.policy["schema"] != 1 \
                or self.policy.get("store_source") != STORE_COMMIT:
            raise GuardError("incompatible Store policy")
        if type(self.catalog.get("schema")) is not int or self.catalog["schema"] != 1 \
                or self.catalog.get("store_source") != STORE_COMMIT:
            raise GuardError("incompatible Store catalog")
        if self.policy.get("vendor_sha256") != VENDOR_SHA or not isinstance(self.policy.get("dependencies"), list):
            raise GuardError("invalid frozen Store policy")
        if not isinstance(self.catalog.get("packages"), list) or not self.catalog["packages"]:
            raise GuardError("empty Store catalog")
        seen = set()
        for item in self.catalog["packages"]:
            name = package_name(item["package"])
            identity = (name, item["version"])
            if identity in seen or protected_package(name) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                raise GuardError("invalid catalog identity")
            seen.add(identity)
            if item["control"].get("Package") != name or item["control"].get("Version") != item["version"] \
                    or item["architecture"] not in {"arm64", "all"} or not isinstance(item["files"], dict) \
                    or not isinstance(item["scripts"], dict) or not isinstance(item["directories"], dict) or not set(item["scripts"]).issubset(SCRIPTS):
                raise GuardError("invalid catalog metadata")

    def record(self, name, version):
        package_name(name)
        if protected_package(name):
            raise GuardError("base-managed package requires base maintenance")
        matches = [r for r in self.catalog.get("packages", []) if r.get("package") == name and r.get("version") == version]
        if len(matches) != 1:
            raise GuardError("package/version is not qualified for this Zero base")
        return matches[0]

    def dpkg(self, *args):
        prefix = ["/usr/bin/dpkg"]
        if self.root.path != Path("/"):
            prefix += ["--root=" + str(self.root.path), "--log=" + str(self.root.at("/var/log/dpkg.log"))]
        return self.run([*prefix, *args])

    def query(self, *args):
        prefix = ["/usr/bin/dpkg-query"]
        if self.root.path != Path("/"):
            prefix += ["--admindir=" + str(self.root.at("/var/lib/dpkg"))]
        return self.run([*prefix, *args])

    def installed(self, name):
        rc, out = self.query("-W", "-f=${db:Status-Abbrev}\t${Version}\t${Architecture}", package_name(name))
        if rc == 1:
            return {"known": True, "installed": False, "version": "", "state": "absent"}
        if rc:raise GuardError("package database query failed")
        values = out.decode().strip("\n").split("\t")
        if len(values) != 3 or len(values[0]) < 2:
            raise GuardError("cannot determine package state")
        status, version, _ = values
        return {"known": True, "installed": status[1] == "i" and status[2:3] != "R",
                "version": version if status[1] not in {"n", "c"} else "", "state": status}

    def assert_clean_audit(self, allowed=None):
        rc, out = self.dpkg("--audit")
        if rc:
            raise GuardError("cannot audit package database")
        text = out.decode()
        if not text.strip():
            return
        # Never broad configure/trigger a possibly unrelated broken transaction.
        lines = [l for l in text.splitlines() if l[:1].isspace() and l.split()]
        listed = {l.split()[0] for l in lines if re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", l.split()[0])}
        if not allowed or not listed or listed != {allowed}:
            raise GuardError("unrelated pending packages require base maintenance")

    def validate_installed_scripts(self, record):
        name = record["package"]
        for script in SCRIPTS:
            p = self.root.at("/var/lib/dpkg/info/" + name + "." + script)
            expected = record["scripts"].get(script)
            if expected is None:
                if p.exists() or p.is_symlink():
                    raise GuardError("unqualified installed maintainer metadata")
            elif not p.exists() or digest(self.root.secure(str(p.relative_to(self.root.path))).read_bytes()) != expected:
                raise GuardError("installed maintainer script is not qualified")

    def validate_payload(self, record):
        for value, mode in record["directories"].items():
            if value == ".":continue
            path = self.root.at(value);canonical = path.resolve()
            if not canonical.is_relative_to(self.root.path):raise GuardError("directory symlink escapes image root")
            name = str(canonical.relative_to(self.root.path))
            if canonical.exists() and (not canonical.is_dir() or stat.S_IMODE(canonical.stat().st_mode) != mode
                                       or canonical.stat().st_uid != self.root.owner):
                raise GuardError("package directory changes existing base directory identity")
            if any(name == p or name.startswith(p + "/") for p in PROTECTED_PATHS):
                if not canonical.is_dir() or stat.S_IMODE(canonical.stat().st_mode) != mode:
                    raise GuardError("package directory changes protected base path")
            if path.exists() and not path.is_dir():raise GuardError("package directory replaces a file")
        for value in record["files"]:
            canonical = self.root.at(value).resolve()
            if not canonical.is_relative_to(self.root.path):
                raise GuardError("payload symlink escapes image root")
            name = str(canonical.relative_to(self.root.path))
            if name in {PUBLIC.lstrip("/"), ORIGINAL.lstrip("/")} or any(name == p or name.startswith(p + "/") for p in PROTECTED_PATHS) \
                    or re.match(r"usr/lib/systemd/(system|user)/tyrell-zero[.@-]", name):
                raise GuardError("payload overlaps protected base path")
            p = self.root.at(value)
            if p.is_symlink() or (p.exists() and p.stat().st_nlink > 1):
                raise GuardError("payload aliases an existing file")
            current = p.parent
            while current != self.root.path:
                if current.exists() and (current.stat().st_uid != self.root.owner or current.stat().st_mode & 0o022):
                    raise GuardError("payload parent is mutable by an unprivileged user")
                current = current.parent

    def invariants(self):
        baseline = self.root.read_json(INVARIANTS)
        if baseline.get("schema") != 1 or baseline.get("complete") is not True \
                or not isinstance(baseline.get("packages"), dict) or not isinstance(baseline.get("files"), dict):
            raise GuardError("base invariants are not initialized by the image integrator")
        if not {"applaunch", "tyrell-zero-session", "tyrell-zero-store"}.issubset(baseline["packages"]) \
                or not {PUBLIC, ORIGINAL, HELPER, MODULE, POLICY, CATALOG}.issubset(baseline["files"]):
            raise GuardError("base invariants omit required integration identities")
        if baseline["files"][ORIGINAL] != VENDOR_SHA or not isinstance(baseline.get("triggers"), dict):
            raise GuardError("base invariants omit pinned vendor/trigger identities")
        for name, expected in baseline["packages"].items():
            state = self.installed(name)
            if not state["installed"] or state["version"] != expected:
                raise GuardError("base package identity changed; base maintenance required")
        for name, expected in baseline["files"].items():
            if isinstance(expected, dict) and set(expected) == {"symlink"}:
                path = self.root.at(name)
                self.root.secure(str(path.parent.relative_to(self.root.path)), directory=True)
                if not path.is_symlink() or path.lstat().st_uid != self.root.owner or os.readlink(path) != expected["symlink"]:
                    raise GuardError("protected base link identity changed")
            elif not isinstance(expected, str) or digest(self.root.secure(name).read_bytes()) != expected:
                raise GuardError("protected base file identity changed")
        return digest(json.dumps(baseline, sort_keys=True).encode())

    def settle_triggers(self):
        rc, out = self.query("-W", "-f=${Package}\\t${db:Status-Abbrev}\\t${Version}\\n")
        if rc:raise GuardError("cannot inspect pending trigger identities")
        baseline = self.root.read_json(INVARIANTS)
        for line in out.decode().splitlines():
            columns = line.split("\t")
            if len(columns) != 3:raise GuardError("invalid trigger state")
            name, state, version = columns
            if len(state) < 2 or state[1] != "t":continue
            qualification = baseline["triggers"].get(name)
            if name not in TRIGGER_PACKAGES or not qualification or qualification.get("version") != version:
                raise GuardError("pending trigger requires qualified base maintenance")
            for script in SCRIPTS:
                path = self.root.at("/var/lib/dpkg/info/" + name + "." + script)
                expected = qualification["scripts"].get(script)
                if expected is None:
                    if path.exists() or path.is_symlink():raise GuardError("unqualified trigger metadata")
                elif digest(self.root.secure(str(path.relative_to(self.root.path))).read_bytes()) != expected:
                    raise GuardError("trigger script identity changed")
            if self.dpkg("--no-triggers", "--triggers-only", package_name(name))[0]:
                raise GuardError("qualified cache trigger failed; base maintenance required")

    def journal(self, pending, directory, current):
        keys = ["transaction_id", "action", "package", "previously_installed", "previous_version",
                "expected_package_version", "expected_md5", "deb_path"]
        identity = {k: pending.get(k) for k in keys}
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", pending["transaction_id"]):raise GuardError("invalid root journal transaction")
        path = directory / (pending["transaction_id"] + ".json")
        if path.exists() or path.is_symlink():
            saved = strict_json(self.root.secure(str(path.relative_to(self.root.path))).read_bytes())
            if saved.get("identity") != identity:raise GuardError("pending transaction differs from root journal")
            if pending.get("repair_requested") is not True:raise GuardError("transaction already executed; use app-scoped repair")
            if saved.get("phase") == "complete":
                if saved.get("state") != current:raise GuardError("completed transaction no longer matches installed state")
                return saved
            if saved.get("phase") != "authorized":raise GuardError("invalid root journal phase")
        else:
            if pending.get("repair_requested") is True:raise GuardError("repair has no authenticated root transaction")
            # A newer transaction must never supersede unfinished root
            # authorization for the same package. Otherwise repairing the old
            # operation could remove or downgrade a later successful install.
            # The global transaction lock serializes this scan and publication.
            for candidate in directory.glob("*.json"):
                other = strict_json(self.root.secure(str(candidate.relative_to(self.root.path))).read_bytes())
                old_identity = other.get("identity")
                if not isinstance(old_identity, dict) or other.get("phase") not in {"authorized", "complete"}:
                    raise GuardError("invalid prior root transaction journal")
                if old_identity.get("package") == pending["package"] and other["phase"] == "authorized":
                    raise GuardError("unfinished package transaction requires its authenticated repair")
            if current["installed"] != pending["previously_installed"] \
                    or (current["installed"] and current["version"] != pending.get("previous_version")):
                raise GuardError("pending previous package identity differs from installed state")
            if current["version"] and not current["installed"]:raise GuardError("partial package needs its existing repair transaction")
            rollback_sha = None
            if current["installed"] and pending["action"] in {"upgrade", "install", "reinstall"}:
                previous = self.record(pending["package"], current["version"])
                if previous["version"] != pending.get("expected_package_version"):
                    frozen = directory / (previous["sha256"] + ".deb")
                    source = frozen if frozen.exists() else pending.get("rollback_deb_path", "")
                    if not source:raise GuardError("upgrade requires exact previous package bytes for rollback")
                    self.stage(source, directory, previous)
                    rollback_sha = previous["sha256"]
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(fd, "wb", closefd=False) as stream:
                    stream.write((json.dumps({"identity":identity, "phase":"authorized", "rollback_sha256":rollback_sha}, sort_keys=True) + "\n").encode());stream.flush();os.fsync(fd)
            finally:os.close(fd)
            sync_directory(directory)
        return None

    def complete_journal(self, pending, directory):
        path = directory / (pending["transaction_id"] + ".json")
        journal = strict_json(self.root.secure(str(path.relative_to(self.root.path))).read_bytes())
        journal.update(phase="complete", state=self.installed(pending["package"]),
                       result={k:v for k,v in pending.items() if k.startswith("repair_") or k.startswith("helper_completed")})
        temporary = directory / (".journal-" + str(time.monotonic_ns()))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write((json.dumps(journal, sort_keys=True) + "\n").encode());stream.flush();os.fsync(fd)
            os.replace(temporary, path)
            sync_directory(directory)
        finally:
            os.close(fd)
            if temporary.exists():temporary.unlink()

    @contextmanager
    def transaction(self):
        directory = self.root.mkdir(STATE)
        lock = os.open(directory / "package.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            info = os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != self.root.owner or info.st_mode & 0o077 or info.st_nlink != 1:
                raise GuardError("untrusted package transaction lock")
            deadline = time.monotonic() + 10
            while True:
                try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB);break
                except BlockingIOError:
                    if time.monotonic() >= deadline:raise GuardError("package transaction busy")
                    time.sleep(0.05)
            yield directory
        finally:
            os.close(lock)

    def stage(self, path, directory, record=None, caller_uid=None):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 256 * 1024**2:
                raise GuardError("invalid package input")
            if caller_uid is not None and info.st_uid not in {caller_uid, self.root.owner}:
                raise GuardError("package input owner mismatch")
            with os.fdopen(os.dup(fd), "rb") as stream:data = stream.read(256 * 1024**2 + 1)
        finally:os.close(fd)
        sha = digest(data)
        matches = [r for r in self.catalog.get("packages", []) if r.get("sha256") == sha]
        if len(matches) != 1:
            raise GuardError("package bytes are not qualified for this base")
        selected = matches[0]
        if record is not None and selected != record:
            raise GuardError("staged package differs from expected transaction")
        staged = directory / (sha + ".deb")
        if staged.exists():
            if digest(self.root.secure(str(staged.relative_to(self.root.path))).read_bytes()) != sha:
                raise GuardError("staged package changed")
        else:
            out = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
            try:
                with os.fdopen(out, "wb", closefd=False) as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            finally:os.close(out)
            sync_directory(directory)
        detail = inspect_deb(staged, self.run)
        if any(detail[key] != selected[key] for key in ["control", "files", "scripts", "directories"]):
            raise GuardError("package inspection differs from qualified catalog")
        self.validate_payload(selected)
        return staged, selected

    def dependencies(self, staged):
        rc, text = self.run(["/usr/bin/dpkg-deb", "--field", str(staged), "Pre-Depends", "Depends"])
        if rc:raise GuardError("cannot inspect dependencies")
        # Binary package relationships only; dpkg itself compares Debian versions.
        dependency = []
        for line in text.decode().splitlines():
            value = line.split(":", 1)[-1].strip()
            if value:dependency.append(value)
        if not dependency:return []
        if self.satisfied(", ".join(dependency)):return []
        # APT is a resolver only. Every resulting addition needs frozen bytes.
        command = ["/usr/bin/apt-get", "--simulate", "--no-remove", "--no-install-recommends", "install", str(staged)]
        if self.root.path != Path("/"):
            command[1:1] = ["-o", "Dir=" + str(self.root.path), "-o", "Dir::State::status=" + str(self.root.at("/var/lib/dpkg/status"))]
        rc, out = self.run(command)
        if rc:raise GuardError("dependency resolution failed")
        additions = []
        for line in out.decode().splitlines():
            if line.startswith(("Remv ", "Purg ")):raise GuardError("dependency transaction removes packages")
            if not line.startswith("Inst "):continue
            match = re.match(r"Inst ([a-z0-9+.-]+)(?: \[([^]]+)\])? \(([^ )]+)", line)
            if not match:raise GuardError("unsupported APT transaction output")
            name, old, version = match.groups()
            if name == self.run_field(staged, "Package"):
                if version != self.run_field(staged, "Version"):raise GuardError("APT substituted target package version")
                continue
            if old or protected_package(name) or self.installed(name)["installed"]:
                raise GuardError("dependency transaction changes the base")
            candidates = [x for x in self.policy.get("dependencies", []) if x.get("package") == name and x.get("version") == version]
            if len(candidates) != 1:raise GuardError("missing frozen dependency qualification")
            candidate = candidates[0]
            record = self.record(name, version)
            path = self.root.secure(candidate["path"])
            if digest(path.read_bytes()) != candidate.get("sha256") or record["sha256"] != candidate["sha256"]:
                raise GuardError("frozen dependency identity mismatch")
            additions.append(path)
        if not additions:raise GuardError("unsatisfied dependencies require a qualified base transaction")
        return additions

    def satisfied(self, expression):
        rc, rows = self.query("-W", "-f=${Package}\\t${db:Status-Abbrev}\\t${Version}\\t${Architecture}\\t${Multi-Arch}\\t${Provides}\\n")
        if rc:raise GuardError("cannot inspect installed dependency versions")
        installed = []
        for line in rows.decode().splitlines():
            fields = line.split("\t")
            if len(fields) != 6:raise GuardError("unsupported installed dependency metadata")
            name, state, version, arch, multi, provides = fields
            if state[1:2] != "i" or state[2:3] == "R":continue
            identities = [(name, version)]
            for value in provides.split(","):
                if not value.strip():continue
                match = re.fullmatch(r"\s*([a-z0-9][a-z0-9+.-]*)(?:\s*\(=\s*([^\s()]+)\))?\s*", value)
                if not match:raise GuardError("unsupported virtual dependency metadata")
                identities.append((match[1], match[2] or ""))
            installed.append((identities, arch, multi))
        for group in expression.split(","):
            satisfied = False
            for alternative in group.split("|"):
                match = re.fullmatch(r"\s*([a-z0-9][a-z0-9+.-]*)(?::(any|native|arm64))?(?:\s*\((<<|<=|=|>=|>>)\s*([^\s()]+)\))?\s*", alternative)
                if not match:raise GuardError("unsupported binary dependency syntax")
                name, qualifier, operator, required = match.groups()
                for identities, arch, multi in installed:
                    if qualifier == "any":
                        if multi != "allowed":continue
                    elif qualifier in {"native", "arm64"}:
                        if arch not in {"arm64", "all"}:continue
                    elif arch not in {"arm64", "all"} and multi != "foreign":continue
                    for candidate, version in identities:
                        if candidate != name or (operator and not version):continue
                        if not operator or self.dpkg("--compare-versions", version, operator, required)[0] == 0:
                            satisfied = True;break
                    if satisfied:break
                if satisfied:break
            if not satisfied:return False
        return True

    def run_field(self, path, field):
        rc, data = self.run(["/usr/bin/dpkg-deb", "--field", str(path), field])
        if rc:raise GuardError("cannot inspect field")
        return data.decode().strip()

    def operate(self, pending, args, directory, caller_uid=None, save=lambda value: None):
        name = package_name(pending.get("package"));action = args.action
        if pending.get("schema_version") != 2 or not isinstance(pending.get("previously_installed"), bool):
            raise GuardError("unsupported Store pending transaction")
        repairing = pending.get("repair_requested") is True
        expected_action = "repair" if repairing else ("uninstall" if pending.get("action") == "uninstall" else "install")
        expected_value = name if repairing or expected_action == "uninstall" else pending.get("deb_path")
        expected_reinstall = not repairing and pending.get("action") == "reinstall"
        if pending.get("action") not in {"install", "reinstall", "upgrade", "uninstall"} or action != expected_action \
                or args.value != expected_value or args.reinstall != expected_reinstall \
                or pending.get("transaction_id") != args.transaction or pending.get("helper_completed") is True:
            raise GuardError("helper request does not match pending transaction")
        if args.force_overwrite:raise GuardError("force-overwrite is not qualified")
        before = self.invariants()
        current = self.installed(name)
        if current["version"]:
            installed_record = self.record(name, current["version"])
            self.validate_installed_scripts(installed_record)
        else:installed_record = None
        target_version = pending.get("expected_package_version", "")
        record = self.record(name, target_version) if target_version else installed_record
        if record is None and pending.get("action") == "uninstall":record = self.record(name, pending.get("previous_version", ""))
        if record is None:raise GuardError("package transaction is not qualified")
        self.validate_payload(record)
        if args.desktop and args.desktop != record["desktop"]:raise GuardError("unqualified desktop path")
        if any(x and x not in record["executables"] for x in args.executables):raise GuardError("unqualified executable hint")
        self.assert_clean_audit(name if repairing else None)
        terminal = self.journal(pending, directory, current)
        if terminal:
            pending.update(terminal["result"])
            pending.update(repair_completed=True, repair_completed_at=now(),
                           repair_outcome=terminal["result"].get("repair_outcome", "completed"),
                           repair_detail="Root journal confirms completed package transaction",
                           helper_completed=True, helper_completed_at=now())
            save(pending);return pending
        pending.update(helper_started=True, helper_started_at=now())
        pending.pop("helper_failed", None);save(pending)
        if repairing:
            self.repair(pending, directory, record, save)
        elif action == "uninstall":
            if not current["installed"]:raise GuardError("package is not installed")
            rc, output = self.dpkg("--no-triggers", "--remove", name)
            if rc:raise GuardError("package removal failed; app-scoped repair required")
            if self.installed(name)["installed"]:raise GuardError("removal state verification failed")
        else:
            staged, staged_record = self.stage(args.value, directory, record, caller_uid)
            if hashlib.md5(staged.read_bytes()).hexdigest() != str(pending.get("expected_md5", "")).lower():
                raise GuardError("pending MD5 does not match qualified bytes")
            dependencies = self.dependencies(staged)
            approved = [self.stage(path, directory)[0] for path in dependencies]
            rc, output = self.dpkg("--no-triggers", "--install", *map(str, [*approved, staged]))
            if rc:raise GuardError("package installation failed; app-scoped repair required")
            state = self.installed(name)
            if not state["installed"] or state["version"] != target_version:raise GuardError("installed version verification failed")
        self.settle_triggers()
        self.assert_clean_audit()
        if self.invariants() != before:raise GuardError("protected base identity changed")
        pending.update(helper_completed=True, helper_completed_at=now())
        pending.pop("helper_failed", None);pending.pop("helper_exit_code", None)
        self.complete_journal(pending, directory);save(pending)
        return pending

    def repair(self, pending, directory, target, save):
        name = target["package"];state = self.installed(name)
        def finish(outcome, detail):
            pending.update(repair_completed=True, repair_completed_at=now(), repair_outcome=outcome,
                           repair_detail=detail)
            save(pending)
        if pending["action"] == "uninstall" and not state["installed"]:
            finish("completed", "Interrupted uninstall already completed");return
        if state["installed"] and state["version"] == pending.get("expected_package_version"):
            finish("completed", "Interrupted operation already completed");return
        if state["installed"] and pending.get("previously_installed") and state["version"] == pending.get("previous_version"):
            finish("restored", "Previous qualified version already restored");return
        if not state["version"]:
            finish("rolled_back", "Broken package already absent");return
        rc, _ = self.dpkg("--no-triggers", "--configure", name)
        state = self.installed(name)
        if rc == 0 and state["installed"] and state["version"] == pending.get("expected_package_version"):
            finish("completed", "Interrupted package configured");return
        if pending.get("previously_installed"):
            previous = self.record(name, pending.get("previous_version", ""))
            path = directory / (previous["sha256"] + ".deb")
            if path.exists():
                staged, _ = self.stage(path, directory, previous)
                dependencies = [self.stage(p, directory)[0] for p in self.dependencies(staged)]
                rc, _ = self.dpkg("--no-triggers", "--install", *map(str, [*dependencies, staged]))
                state = self.installed(name)
                if rc == 0 and state["installed"] and state["version"] == previous["version"]:
                    finish("restored", "Previous qualified package restored");return
        # Known scripts only; no --force-all, global configure or script quarantine.
        rc, _ = self.dpkg("--no-triggers", "--force-remove-reinstreq", "--remove", name)
        if rc == 0 and not self.installed(name)["installed"]:
            finish("completed" if pending["action"] == "uninstall" else "rolled_back", "Affected package removed");return
        raise GuardError("qualified app repair failed; base maintenance required")


def parse_args(argv):
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--package-helper", dest="action", choices=["install", "uninstall", "repair"], required=True)
    parser.add_argument("--package-value", dest="value", required=True)
    parser.add_argument("--package-transaction", dest="transaction", required=True)
    parser.add_argument("--package-pending-path", dest="pending", required=True)
    parser.add_argument("--package-reinstall", dest="reinstall", action="store_true")
    parser.add_argument("--package-force-overwrite", dest="force_overwrite", action="store_true")
    parser.add_argument("--package-desktop", dest="desktop", default="")
    parser.add_argument("--package-exec", dest="executables", action="append", default=[])
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", args.transaction):raise GuardError("invalid transaction identity")
    if any(any(c in value for c in "\n\r\t\0") for value in [args.value, args.pending, args.desktop, *args.executables]):
        raise GuardError("protocol values contain control characters")
    if args.action == "install" and not Path(args.value).is_absolute():raise GuardError("absolute package input required")
    return args


@contextmanager
def pending_file(path, user):
    expected = Path(user.pw_dir) / ".local/share/cardputerzero-appstore/pending-package.json"
    if Path(path) != expected or expected.parent == Path("/"):
        raise GuardError("pending path is not the configured user's Store transaction")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in expected.parent.parts[1:]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd);fd = new
            info = os.fstat(fd)
            if info.st_uid not in {0, user.pw_uid} or info.st_mode & 0o022:
                raise GuardError("untrusted pending path parent")
        lock = os.open("package-transaction.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=fd)
        try:
            info = os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, user.pw_uid} or info.st_mode & 0o022 or info.st_nlink != 1:
                raise GuardError("untrusted Store lock")
            if info.st_uid == 0:os.fchown(lock, user.pw_uid, user.pw_gid)
            deadline = time.monotonic() + 10
            while True:
                try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB);break
                except BlockingIOError:
                    if time.monotonic() >= deadline:raise GuardError("Store transaction busy")
                    time.sleep(0.05)
            pending = os.open(expected.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=fd)
            try:
                info = os.fstat(pending)
                if not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, user.pw_uid} or info.st_nlink != 1 or info.st_size > 4 * 1024**2 or info.st_mode & 0o022:
                    raise GuardError("untrusted pending transaction file")
                data = os.read(pending, 4 * 1024**2 + 1)
            finally:os.close(pending)
            value = strict_json(data)
            def save(updated):
                temporary = ".tyrell-pending-" + str(os.getpid()) + "-" + str(time.monotonic_ns())
                out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                try:
                    blob = (json.dumps(updated, indent=2) + "\n").encode()
                    os.write(out, blob);os.fchmod(out, info.st_mode & 0o777);os.fchown(out, user.pw_uid, user.pw_gid);os.fsync(out)
                    os.rename(temporary, expected.name, src_dir_fd=fd, dst_dir_fd=fd);os.fsync(fd)
                finally:
                    os.close(out)
                    try:os.unlink(temporary, dir_fd=fd)
                    except FileNotFoundError:pass
            yield value, save
        finally:os.close(lock)
    finally:os.close(fd)


def main(argv=None):
    try:
        args = parse_args(argv)
        if os.geteuid() != 0:raise GuardError("package helper requires authenticated root execution")
        uid = os.environ.get("SUDO_UID", "")
        if not re.fullmatch(r"[0-9]+", uid) or int(uid) != 1000:raise GuardError("configured normal-user authorization required")
        user = pwd.getpwuid(int(uid))
        if user.pw_uid != 1000 or user.pw_dir != "/home/" + user.pw_name:raise GuardError("unsupported configured account")
        guard = Guard()
        with guard.transaction() as directory:
            with pending_file(args.pending, user) as (pending, save):
                print("PROGRESS\tverify\t0\t0\t-1\tChecking Tyrell base compatibility", flush=True)
                try:
                    guard.operate(pending, args, directory, user.pw_uid, save)
                except GuardError:
                    if pending.get("helper_started") is True and pending.get("transaction_id") == args.transaction:
                        pending.update(helper_failed=True, helper_exit_code=1, helper_failed_at=now());save(pending)
                    raise
                print("PACKAGE_HELPER\t" + args.action + "\t" + args.value, flush=True)
        return 0
    except (GuardError, OSError, ValueError, KeyError, TypeError) as error:
        print("ERROR\tTyrell base compatibility: " + str(error).replace("\n", " ").replace("\t", " "), flush=True)
        return 1
