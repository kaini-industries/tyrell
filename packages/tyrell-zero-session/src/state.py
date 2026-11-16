"""Versioned, fail-closed native boot/readiness state. No service activation."""
from dataclasses import dataclass
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import tempfile

VERSION = "0.2.0"
PROFILE = "tyrell-zero"
WORKFLOW = "native-setup-v1"
INTEGRATION = "/usr/share/tyrell-zero/session-integration.json"
QUALIFICATION = "/usr/share/tyrell-zero/pre-image-qualification.json"
ACCOUNT = "/etc/tyrell-zero/native-account.v1.json"
READY = "/var/lib/tyrell-zero/readiness.v1.json"
CHECKPOINT = "/var/lib/tyrell-zero/setup-apply.v1.json"
JOURNAL = "/var/lib/LaunchWizard/account-migration.state"
RUNTIME = "/run/tyrell-zero"
MARKERS = ("/var/lib/LaunchWizard/run-oobe", "/var/lib/applaunch/run-oobe")
GUIDE = "/var/lib/LaunchWizard/run-keyboard-guide"
ASSETS = {
    "launcher": "/usr/share/APPLaunch/bin/M5CardputerZero-APPLaunch",
    "wizard": "/usr/libexec/tyrell-zero/LaunchWizard",
    "store_dispatcher": "/usr/share/APPLaunch/bin/M5CardputerZero-AppStore",
    "store_original": "/usr/share/APPLaunch/bin/M5CardputerZero-AppStore.vendor",
    "store_backend": "/usr/libexec/tyrell-zero/appstore-package-helper",
}
LAUNCHER_SHA256 = "9f26df9329160385c68794d26e0c7153271c6a69bb048938580e1495418c968c"
STORE_SHA256 = "e5fe24b1dd26cd224c64ef4df2e7a61e0fce3fb63b3b04ac90d38c5092ccc05e"
REQUIRED_RUNTIME_FILES = frozenset({
    "/usr/lib/tyrell-zero/session/state.py",
    "/usr/lib/tyrell-zero/session/supervisor.py",
    "/usr/lib/tyrell-zero/session/wizard_complete.py",
    "/usr/libexec/tyrell-zero-session-state",
    "/usr/libexec/tyrell-zero-session",
    "/usr/libexec/tyrell-zero-session-recovery",
    "/usr/libexec/tyrell-zero-wizard-complete",
    "/usr/lib/tyrell-zero/store/guard.py",
    "/usr/share/tyrell-zero/store-policy.json",
    "/usr/share/tyrell-zero/store-catalog.json",
})
RUNTIME_PREFIXES = ("/usr/lib/tyrell-zero/", "/usr/libexec/tyrell-zero/",
                    "/usr/share/tyrell-zero/", "/usr/share/APPLaunch/")
MAX_RUNTIME_FILES = 256
MAX_RUNTIME_FILE_BYTES = 32 * 1024 * 1024
MAX_RUNTIME_TOTAL_BYTES = 128 * 1024 * 1024
NAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class StateError(RuntimeError):
    """A fixed diagnostic code; never include credentials or file contents."""


@dataclass(frozen=True)
class Account:
    name: str
    uid: int
    gid: int
    home: str

    def record(self):
        return {"name": self.name, "uid": self.uid, "gid": self.gid, "home": self.home}


class Files:
    """Tests supply an isolated root and owner. Runtime uses / and root only."""
    def __init__(self, root=Path("/"), owner=0):
        self.root = Path(root).absolute()
        self.owner = owner

    def path(self, absolute):
        p = Path(absolute)
        if not p.is_absolute() or ".." in p.parts:
            raise StateError("invalid-path")
        return self.root / str(p).lstrip("/")

    def secure(self, absolute, *, directory=False):
        target = self.path(absolute)
        relative = target.relative_to(self.root)
        current = self.root
        for part in ("", *relative.parts):
            if part:
                current /= part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != self.owner or info.st_mode & 0o022:
                raise StateError("untrusted-path")
            if current != target and not stat.S_ISDIR(info.st_mode):
                raise StateError("untrusted-parent")
        if (directory and not stat.S_ISDIR(info.st_mode)) or (not directory and not stat.S_ISREG(info.st_mode)):
            raise StateError("unexpected-file-type")
        return target

    def exists(self, absolute):
        # lexists catches broken links; callers then reject them rather than
        # treating an untrusted marker as absent.
        return os.path.lexists(self.path(absolute))

    def read(self, absolute, limit=65536):
        path = self.secure(absolute)
        with path.open("rb") as stream:
            value = stream.read(limit + 1)
        if len(value) > limit:
            raise StateError("oversized-state")
        return value

    def json(self, absolute):
        def pairs(values):
            result = {}
            for key, value in values:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result

        def constant(_value):
            raise ValueError("non-finite JSON constant")

        try:
            value = json.loads(self.read(absolute), object_pairs_hook=pairs,
                               parse_constant=constant)
        except (ValueError, UnicodeError, RecursionError):
            raise StateError("invalid-json") from None
        if not isinstance(value, dict):
            raise StateError("invalid-state-type")
        return value

    def digest(self, absolute):
        path = self.secure(absolute)
        h = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()

    def mkdir(self, absolute, mode=0o755):
        target = self.path(absolute)
        if not target.exists():
            self.secure(str(Path(absolute).parent), directory=True)
            target.mkdir(mode=mode)
        self.secure(absolute, directory=True)

    def write(self, absolute, value, mode=0o644):
        target = self.path(absolute)
        self.secure(str(Path(absolute).parent), directory=True)
        if self.exists(absolute):
            self.secure(absolute)
        encoded = value.encode("utf-8")
        fd, temporary = tempfile.mkstemp(prefix=".tyrell-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def write_json(self, absolute, value, mode=0o644):
        self.write(absolute, json.dumps(value, sort_keys=True, indent=2) + "\n", mode)


def integration(files):
    doc = files.json(INTEGRATION)
    if type(doc.get("schema")) is not int or doc["schema"] != 1 or doc.get("profile") != PROFILE or doc.get("complete") is not True:
        raise StateError("integration-incomplete")
    if doc.get("workflow") != WORKFLOW or doc.get("applaunch_version") != "0.6.105":
        raise StateError("integration-incompatible")
    qualification = doc.get("qualification_sha256")
    if not isinstance(qualification, str) or not DIGEST.fullmatch(qualification):
        raise StateError("integration-not-qualified")
    receipt = files.json(QUALIFICATION)
    if (files.secure(QUALIFICATION).stat().st_mode & 0o6000
            or files.digest(QUALIFICATION) != qualification):
        raise StateError("integration-qualification-changed")
    if (type(receipt.get("schema")) is not int or receipt["schema"] != 1
            or receipt.get("profile") != PROFILE or receipt.get("workflow") != WORKFLOW
            or receipt.get("status") != "passed"
            or receipt.get("scope") != "pre-image-integration"
            or receipt.get("physical_hardware_qualified") is not False
            or receipt.get("suites") != {"session": "passed", "wizard": "passed", "store": "passed"}):
        raise StateError("integration-qualification-incomplete")
    assets = doc.get("assets")
    if not isinstance(assets, dict) or set(assets) != set(ASSETS):
        raise StateError("integration-assets-incomplete")
    if receipt.get("assets") != assets:
        raise StateError("integration-qualification-assets")
    for name, expected_path in ASSETS.items():
        value = assets[name]
        if not isinstance(value, dict) or value.get("path") != expected_path:
            raise StateError("integration-asset-path")
        digest = value.get("sha256", "")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise StateError("integration-asset-identity")
        if name == "launcher" and digest != LAUNCHER_SHA256:
            raise StateError("vendor-launcher-changed")
        if name == "store_original" and digest != STORE_SHA256:
            raise StateError("vendor-store-changed")
        path = files.secure(expected_path)
        if not path.stat().st_mode & 0o111 or path.stat().st_mode & 0o6000:
            raise StateError("integration-asset-mode")
        if files.digest(expected_path) != digest:
            raise StateError("integration-asset-changed")
    runtime = doc.get("runtime_files")
    if (not isinstance(runtime, dict) or not REQUIRED_RUNTIME_FILES.issubset(runtime)
            or len(runtime) > MAX_RUNTIME_FILES):
        raise StateError("integration-runtime-incomplete")
    if receipt.get("runtime_files") != runtime:
        raise StateError("integration-qualification-runtime")
    total = 0
    for name, digest in runtime.items():
        if (not isinstance(name, str) or not name.startswith("/")
                or str(Path(name)) != name or ".." in Path(name).parts
                or name in {INTEGRATION, QUALIFICATION}
                or (name not in REQUIRED_RUNTIME_FILES and not name.startswith(RUNTIME_PREFIXES))):
            raise StateError("integration-runtime-path")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise StateError("integration-runtime-identity")
        path = files.secure(name)
        info = path.stat()
        total += info.st_size
        if info.st_mode & 0o6000:
            raise StateError("integration-runtime-mode")
        if info.st_size > MAX_RUNTIME_FILE_BYTES or total > MAX_RUNTIME_TOTAL_BYTES:
            raise StateError("integration-runtime-size")
        if files.digest(name) != digest:
            raise StateError("integration-runtime-changed")
    return files.digest(INTEGRATION)


def configured_account(files, lookup=None):
    doc = files.json(ACCOUNT)
    if type(doc.get("schema")) is not int or doc["schema"] != 1 or doc.get("profile") != PROFILE or doc.get("completed") is not True:
        raise StateError("account-incomplete")
    value = doc.get("account", {})
    if not isinstance(value, dict):
        raise StateError("account-invalid")
    account = Account(value.get("name", ""), value.get("uid"), value.get("gid"), value.get("home", ""))
    if not isinstance(account.name, str) or not NAME.fullmatch(account.name) or type(account.uid) is not int or account.uid != 1000:
        raise StateError("account-invalid")
    if type(account.gid) is not int or account.gid <= 0 or account.home != "/home/" + account.name:
        raise StateError("account-invalid")
    if lookup is None:
        lookup = lambda uid: pwd.getpwuid(uid)
    try:
        entry = lookup(1000)
    except KeyError:
        raise StateError("account-missing") from None
    if (entry.pw_name, entry.pw_uid, entry.pw_gid, entry.pw_dir) != (account.name, account.uid, account.gid, account.home):
        raise StateError("account-mismatch")
    return account


def validate_journal(files):
    lines = files.read(JOURNAL).decode("utf-8").splitlines()
    keys = ["version", "source", "target", "stage", "rename_group", "update_subuid", "update_subgid", "update_sudoers"]
    if len(lines) != len(keys):
        raise StateError("migration-corrupt")
    values = {}
    for key, line in zip(keys, lines):
        if not line.startswith(key + "="):
            raise StateError("migration-corrupt")
        values[key] = line[len(key) + 1:]
    if values["version"] != "1" or not all(NAME.fullmatch(values[k]) for k in ["source", "target"]):
        raise StateError("migration-corrupt")
    if values["stage"] not in {str(n) for n in range(8)} or any(values[k] not in {"0", "1"} for k in keys[4:]):
        raise StateError("migration-corrupt")


def validate_checkpoint(files):
    value = files.json(CHECKPOINT)
    if type(value.get("schema")) is not int or value["schema"] != 1 or value.get("profile") != PROFILE or value.get("workflow") != WORKFLOW:
        raise StateError("checkpoint-incompatible")
    if not isinstance(value.get("step"), str) or value["step"] not in {"network", "account", "locale", "ssh", "native-config", "ready", "reboot"}:
        raise StateError("checkpoint-incompatible")
    if not DIGEST.fullmatch(str(value.get("configuration_sha256", ""))):
        raise StateError("checkpoint-incompatible")


def decide(files, lookup=None):
    """Read-only decision. No migration, account or setup data is modified."""
    try:
        identity = integration(files)
        pending = False
        if files.exists(JOURNAL):
            validate_journal(files)
            pending = True
        if files.exists(CHECKPOINT):
            validate_checkpoint(files)
            pending = True
        # An old vendor numeric checkpoint cannot skip native completion.
        if files.exists("/var/lib/LaunchWizard/apply.checkpoint"):
            raise StateError("legacy-checkpoint-needs-recovery")
        for marker in (*MARKERS, GUIDE):
            if files.exists(marker):
                files.secure(marker)
                pending = True
        if pending:
            return {"mode": "setup", "reason": "setup-pending", "integration_sha256": identity}
        if not files.exists(READY):
            return {"mode": "setup", "reason": "native-setup-required", "integration_sha256": identity}
        ready = files.json(READY)
        if type(ready.get("schema")) is not int:
            raise StateError("readiness-mismatch")
        account = configured_account(files, lookup)
        if ready != {"schema": 1, "profile": PROFILE, "workflow": WORKFLOW,
                     "account": account.record(), "account_sha256": files.digest(ACCOUNT),
                     "integration_sha256": identity}:
            raise StateError("readiness-mismatch")
        return {"mode": "native-ready", "reason": "ready", "account": account.record(), "integration_sha256": identity}
    except (StateError, OSError, UnicodeError) as error:
        reason = str(error) if isinstance(error, StateError) else "state-unavailable"
        return {"mode": "recovery", "reason": reason}


def greetd_config(account=None):
    # TOML user strings are JSON encoded, with strict account-name validation.
    text = '[terminal]\nvt = 7\nswitch = true\n\n[general]\nsource_profile = false\nrunfile = "/run/tyrell-zero/greetd.initial-session"\n\n[default_session]\ncommand = "/usr/libexec/tyrell-zero-session-recovery"\nuser = "_greetd"\n'
    if account is not None:
        if not NAME.fullmatch(account.name) or account.uid != 1000:
            raise StateError("account-invalid")
        text += '\n[initial_session]\ncommand = "/usr/libexec/tyrell-zero-session"\nuser = ' + json.dumps(account.name) + '\n'
    return text


def latch_boot(files, boot_id, lookup=None):
    if not isinstance(boot_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", boot_id):
        raise StateError("invalid-boot-id")
    files.mkdir(RUNTIME)
    lock_path = files.path(RUNTIME + "/boot.lock")
    if files.exists(RUNTIME + "/boot.lock"):
        files.secure(RUNTIME + "/boot.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        files.secure(RUNTIME + "/boot.lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        latch = RUNTIME + "/boot-state.json"
        if files.exists(latch):
            value = files.json(latch)
            if type(value.get("schema")) is not int or value["schema"] != 1 or value.get("boot_id") != boot_id or not isinstance(value.get("mode"), str) or value["mode"] not in {"setup", "native-ready", "recovery"}:
                raise StateError("boot-latch-invalid")
            # A service restart must not recalculate setup into autologin.
            if value.get("config_sha256") != files.digest(RUNTIME + "/greetd.config"):
                raise StateError("boot-config-changed")
            if value["mode"] != "recovery":
                if integration(files) != value.get("integration_sha256"):
                    raise StateError("latched-integration-changed")
                if value["mode"] == "native-ready" and configured_account(files, lookup).record() != value.get("account"):
                    raise StateError("latched-account-changed")
            for mode in ["setup", "native-ready", "recovery"]:
                marker = RUNTIME + "/" + mode
                if mode == value["mode"] and not files.exists(marker):
                    files.write(marker, "1\n")
                elif mode != value["mode"] and files.exists(marker):
                    raise StateError("boot-mode-conflict")
            return value
        decision = decide(files, lookup)
        account = Account(**decision["account"]) if decision["mode"] == "native-ready" else None
        for mode in ["setup", "native-ready", "recovery"]:
            if files.exists(RUNTIME + "/" + mode):
                raise StateError("orphan-mode-marker")
        files.write(RUNTIME + "/greetd.config", greetd_config(account))
        value = {"schema": 1, "boot_id": boot_id, **decision,
                 "config_sha256": files.digest(RUNTIME + "/greetd.config")}
        files.write_json(latch, value)
        # Latch precedes the marker: an interrupted publish is recoverable by
        # repairing only that marker, never re-deciding into autologin.
        files.write(RUNTIME + "/" + decision["mode"], "1\n")
        return value
    finally:
        os.close(fd)


def commit_readiness(files, lookup=None):
    identity = integration(files)
    account = configured_account(files, lookup)
    if any(files.exists(p) for p in [JOURNAL, GUIDE, "/var/lib/LaunchWizard/apply.checkpoint"]):
        raise StateError("setup-work-pending")
    if files.exists(CHECKPOINT):
        validate_checkpoint(files)
        checkpoint = files.json(CHECKPOINT)
        if checkpoint["step"] not in {"ready", "reboot"} or checkpoint["configuration_sha256"] != files.digest(ACCOUNT):
            raise StateError("setup-work-pending")
    files.mkdir("/var/lib/tyrell-zero", 0o700)
    ready = {"schema": 1, "profile": PROFILE, "workflow": WORKFLOW,
             "account": account.record(), "account_sha256": files.digest(ACCOUNT),
             "integration_sha256": identity}
    files.write_json(READY, ready, 0o600)
    # The Wizard consumes factory/rearm markers and requests controlled reboot
    # only after this durable commit. We never transition the live boot latch.
    return ready


def main(argv=None):
    parser = argparse.ArgumentParser(description="Tyrell Zero native session state")
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("action", choices=["check-integration", "boot", "commit"])
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        parser.error("root is required; offline tests use the Files fixture API")
    files = Files()
    try:
        if args.action == "check-integration":
            result = {"integration_sha256": integration(files), "complete": True}
        elif args.action == "commit":
            result = commit_readiness(files)
        else:
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            result = latch_boot(files, boot_id)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (StateError, OSError, ValueError) as error:
        message = str(error) if isinstance(error, StateError) else "state-operation-failed"
        print(json.dumps({"error": message, "complete": False}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
