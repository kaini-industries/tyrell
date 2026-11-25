#!/usr/bin/env python3
"""Activate the Zero profile in a qualified, detached offline Debian root only.

No service is started and no image/device is mounted. The final receipt is
published only after the Store snapshot and session qualification validate.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
RECEIPT = "/usr/share/tyrell-zero/image-activation.json"
SOURCE_RECEIPT = "/usr/share/tyrell-zero/source-pre-image-qualification.json"
UPDATE_POLICY = "/etc/tyrell-zero/base-update-policy.json"
UPDATER_STATUS = "/var/lib/applaunch-updater/status"
APPS_UPDATER_STATUS = "/var/lib/applaunch-updater/apps-status"
UPDATES_DESKTOP = "/usr/share/APPLaunch/applications/tyrell-zero-updates.desktop"
UPDATES_TEXT = "/usr/share/tyrell-zero/updates.txt"
SYSTEM_MASKS = ("greetd.service", "getty@tty1.service", "getty@tty7.service",
                "LaunchWizard.service", "applaunch-updater.service", "applaunch-apps-updater.service", "userconfig.service")
SERVICE_FILES = ("tyrell-zero.target", "tyrell-zero-boot-state.service",
                 "tyrell-zero-session.service", "tyrell-zero-wizard.service")
INITIAL_BOOT_LINKS = {
    "/etc/systemd/system/sysinit.target.wants/rpi-resize.service": "/usr/lib/systemd/system/rpi-resize.service"}
FRESH_BOOT_ABSENT = (
    "/var/lib/cardputerzero/diskid-randomized", "/var/lib/cardputerzero/diskid-state",
    "/var/lib/LaunchWizard/rootfs-expansion-attempted", "/etc/init.d/resize2fs_once",
    "/etc/rc3.d/S01resize2fs_once")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class ActivationError(RuntimeError):
    pass


def module(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


STATE = module("tyrell_zero_activation_state", REPO / "packages/tyrell-zero-session/src/state.py")
STORE = module("tyrell_zero_activation_store", REPO / "packages/tyrell-zero-store/activate-image.py")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def document(path, raw=False):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 32 * 1024 * 1024:
            raise ActivationError("a bounded regular input document is required")
        data = stream.read(32 * 1024 * 1024 + 1)
    if len(data) > 32 * 1024 * 1024:
        raise ActivationError("input document changed beyond its size bound")
    def pairs(rows):
        value = {}
        for key, item in rows:
            if key in value:
                raise ActivationError("duplicate input JSON field")
            value[key] = item
        return value
    def invalid(_value):
        raise ValueError("non-finite JSON constant")
    try:
        value = json.loads(data, object_pairs_hook=pairs,
                           parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ActivationError("invalid input JSON") from error
    if not isinstance(value, dict):
        raise ActivationError("input JSON must be an object")
    return (value, sha(data), data) if raw else (value, sha(data))


def absolute(value):
    if (not isinstance(value, str) or not value.startswith("/") or "\x00" in value
            or ".." in PurePosixPath(value).parts or str(PurePosixPath(value)) != value):
        raise ActivationError("noncanonical image path")
    return value


def mountpoints():
    path = Path("/proc/self/mountinfo")
    if not path.exists():
        return []
    points = []
    for row in path.read_text().splitlines():
        fields = row.split()
        if len(fields) < 6:
            raise ActivationError("invalid host mount inventory")
        points.append(Path(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4])))
    return points


class OfflineRoot:
    def __init__(self, value, owner=0, *, detached_only=False):
        supplied = Path(value)
        root = supplied.resolve()
        if (not supplied.is_absolute() or supplied != root or root == Path("/")
                or os.path.samefile(root, "/")):
            raise ActivationError("an absolute canonical offline image root is required")
        self.root, self.owner = root, owner
        self.files = STATE.Files(root, owner=owner)
        self.files.secure("/", directory=True)
        self.files.secure("/var/lib/dpkg/status")
        points = mountpoints()
        if any(root in point.parents for point in points):
            raise ActivationError("detach every runtime mount or nested image mount before activation")
        for name in ("proc", "sys", "dev", "run"):
            folder = root / name
            if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
                raise ActivationError("unsafe image runtime directory")
            if folder.is_mount() or any(p == folder or folder in p.parents for p in points):
                raise ActivationError("detach every runtime mount before activation")
            if not detached_only and folder.exists() and any(p.is_file() or p.is_symlink() or not p.is_dir()
                                       for p in folder.rglob("*")):
                raise ActivationError("offline runtime directories must contain no live state or devices")

    def at(self, name):
        return self.root / absolute(name).lstrip("/")

    def mkdir(self, name):
        current = Path("/")
        for part in PurePosixPath(absolute(name)).parts[1:]:
            current /= part
            p = self.at(str(current))
            if not os.path.lexists(p):
                p.mkdir(mode=0o755)
            self.files.secure(str(current), directory=True)

    def write(self, name, data, mode=0o644):
        self.mkdir(str(PurePosixPath(name).parent))
        previous_gid = self.at(name).lstat().st_gid if os.path.lexists(self.at(name)) else None
        self.files.write(name, data, mode)
        if previous_gid is not None and self.at(name).stat().st_gid != previous_gid:
            os.chown(self.at(name), -1, previous_gid)
            fd = os.open(self.at(name), os.O_RDONLY | os.O_NOFOLLOW)
            try: os.fsync(fd)
            finally: os.close(fd)

    def write_json(self, name, value):
        self.write(name, json.dumps(value, sort_keys=True, indent=2) + "\n")

    def link(self, name, target):
        self.mkdir(str(PurePosixPath(name).parent))
        path = self.at(name)
        if os.path.lexists(path):
            info = path.lstat()
            if info.st_uid != self.owner or (not stat.S_ISLNK(info.st_mode)
                    and (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022)):
                raise ActivationError("refusing to replace an untrusted activation path")
        fd, temporary = tempfile.mkstemp(prefix=".zero-link-", dir=path.parent)
        os.close(fd)
        os.unlink(temporary)
        try:
            os.symlink(target, temporary)
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if os.path.lexists(temporary): os.unlink(temporary)

    def resolve(self, name):
        """Resolve Debian's merged-/usr and library links within this root."""
        remaining = list(PurePosixPath(absolute(name)).parts[1:])
        resolved = []
        count = 0
        while remaining:
            part = remaining.pop(0)
            if part in {"", "."}: continue
            if part == "..":
                if not resolved: raise ActivationError("image link escapes root")
                resolved.pop(); continue
            path = self.root.joinpath(*resolved, part)
            info = path.lstat()
            if info.st_uid != self.owner:
                raise ActivationError("untrusted image dependency owner")
            if stat.S_ISLNK(info.st_mode):
                count += 1
                if count > 40: raise ActivationError("cyclic image dependency link")
                target = PurePosixPath(os.readlink(path))
                if target.is_absolute(): resolved = []
                remaining = list(target.parts[1:] if target.is_absolute() else target.parts) + remaining
                continue
            if info.st_mode & 0o022:
                raise ActivationError("mutable image dependency")
            if remaining and not stat.S_ISDIR(info.st_mode):
                raise ActivationError("invalid image dependency parent")
            resolved.append(part)
        final = self.root.joinpath(*resolved)
        return "/" + "/".join(resolved), final

    def snapshot(self, name):
        absolute(name)
        _, parent = self.resolve(str(PurePosixPath(name).parent))
        path = parent / PurePosixPath(name).name
        info = path.lstat()
        if info.st_uid != self.owner:
            raise ActivationError("untrusted activation receipt path")
        result = dict(mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
        if stat.S_ISLNK(info.st_mode):
            if os.readlink(path) != "/dev/null": self.resolve(name)
            result.update(type="symlink", target=os.readlink(path))
        elif stat.S_ISDIR(info.st_mode):
            if info.st_mode & 0o022: raise ActivationError("mutable receipt directory")
            result.update(type="directory")
        else:
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o6022:
                raise ActivationError("unsafe activation receipt file")
            with path.open("rb") as stream:
                result.update(type="file", sha256=hashlib.file_digest(stream, "sha256").hexdigest())
        return result


def validate_fresh_boot(root):
    # These one-shot journals are legitimate on a running card, but retaining
    # them in a fresh export can replay stale disk IDs or suppress card growth.
    for name in FRESH_BOOT_ABSENT:
        if root.files.exists(name):
            raise ActivationError("fresh unprovisioned assembly contains first-boot state: " + name)


def prepare_runtime(value, *, owner=0):
    """Remove static debootstrap runtime contents only from a detached build root.

    This is explicit build preparation, never package installation or repair of
    an activated image. Preflight the entire tree before deleting any entry.
    """
    root = OfflineRoot(value, owner, detached_only=True)
    validate_fresh_boot(root)
    for name in (RECEIPT, STATE.READY, STATE.ACCOUNT, STATE.CHECKPOINT, STATE.JOURNAL,
                 "/var/lib/LaunchWizard/apply.checkpoint"):
        if root.files.exists(name):
            raise ActivationError("runtime preparation requires a fresh unprovisioned assembly")
    network_dirs = {"run/systemd/netif", "run/systemd/netif/leases", "run/systemd/netif/links"}
    network_accounts = [row.split(":") for row in root.files.read("/etc/passwd").decode().splitlines()
                        if row.startswith("systemd-network:")]
    network_uid = None
    if network_accounts:
        if (len(network_accounts) != 1 or len(network_accounts[0]) != 7
                or not network_accounts[0][2].isdigit() or int(network_accounts[0][2]) <= 0):
            raise ActivationError("invalid image systemd-network account")
        network_uid = int(network_accounts[0][2])
    entries = []
    for name in ("proc", "sys", "dev", "run"):
        folder = root.root / name
        if not folder.exists(): continue
        root.files.secure("/" + name, directory=True)
        pending = [folder]
        while pending:
            parent = pending.pop()
            for entry in parent.iterdir():
                info = entry.lstat()
                relative = str(entry.relative_to(root.root))
                network_directory = (relative in network_dirs and stat.S_ISDIR(info.st_mode)
                                     and not info.st_mode & 0o022 and info.st_uid == network_uid)
                if (relative.startswith("run/systemd/netif/") and relative not in network_dirs
                        or info.st_uid != owner and not network_directory or stat.S_ISSOCK(info.st_mode)):
                    raise ActivationError("foreign ownership or runtime socket; root may be active")
                if stat.S_ISDIR(info.st_mode): pending.append(entry)
                entries.append((entry, stat.S_ISDIR(info.st_mode)))
    # A second mount inventory protects against a runtime mount added during
    # the read-only preflight. The build caller still owns its assembly lock.
    if any(root.root in point.parents for point in mountpoints()):
        raise ActivationError("runtime mounts changed during preparation")
    for path, directory in sorted(entries, key=lambda item: len(item[0].parts), reverse=True):
        if directory: path.rmdir()
        else: path.unlink()
    for name in ("proc", "sys", "dev", "run"):
        folder = root.root / name
        if folder.exists():
            fd = os.open(folder, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
    return {"removed_entries": len(entries), "root": str(root.root), "services_started": False}


def installed(root, execute):
    rows = execute(root.root, "/usr/bin/dpkg-query", "--admindir=" + str(root.root / "var/lib/dpkg"),
                   "-W", "-f=${Package}\\t${db:Status-Abbrev}\\t${Version}\\n")
    packages = {}
    for row in rows.splitlines():
        name, state, version = row.split("\t")
        if state[1:2] in {"c", "n"}: continue
        if state[1:2] != "i" or state[2:3] == "R" or name in packages:
            raise ActivationError("activation requires a clean package database")
        packages[name] = version
    return packages


def validate(root, compat, qualification, execute):
    if (type(compat.get("schema")) is not int or compat["schema"] != 1
            or compat.get("profile") != "tyrell-zero" or compat.get("complete") is not True):
        raise ActivationError("native compatibility lock is incomplete")
    if compat.get("qualification") != {"scope": "disposable-offline-runtime-root",
            "assembled_image_qualified": False, "hardware_qualified": False}:
        raise ActivationError("native compatibility qualification scope differs")
    assets = compat.get("assets")
    if not isinstance(assets, dict) or set(assets) != set(STATE.ASSETS):
        raise ActivationError("native compatibility asset closure is incomplete")
    names = set()
    for role, expected in STATE.ASSETS.items():
        record = assets[role]
        if not isinstance(record, dict) or record.get("path") != expected:
            raise ActivationError("native asset path differs")
        info = root.snapshot(expected)
        if info["type"] != "file" or not info["mode"] & 0o111 or info["sha256"] != record.get("sha256"):
            raise ActivationError("native executable identity differs")
        names.add(expected)
    if (assets["launcher"]["sha256"] != STATE.LAUNCHER_SHA256
            or assets["store_original"]["sha256"] != STATE.STORE_SHA256):
        raise ActivationError("original vendor executable identity differs")
    for kind in ("runtime_files", "resource_files", "library_files", "configuration_files"):
        records = compat.get(kind)
        if not isinstance(records, dict) or not records:
            raise ActivationError("missing native dependency closure: " + kind)
        if len(records) > 100000:
            raise ActivationError("native dependency map exceeds supported size")
        for name, expected in records.items():
            absolute(name)
            if name in INITIAL_BOOT_LINKS and (kind != "configuration_files"
                    or expected != {"symlink": INITIAL_BOOT_LINKS[name]}):
                raise ActivationError("initial boot link identity differs: " + name)
            record = root.snapshot(name)
            if isinstance(expected, dict):
                matches = (record["type"] == "file" and expected == {"sha256": record["sha256"]}
                           or record["type"] == "symlink" and expected == {"symlink": record["target"]})
            else:
                matches = record["type"] == "file" and isinstance(expected, str) and DIGEST.fullmatch(expected) and record["sha256"] == expected
            if not matches: raise ActivationError("native dependency identity differs: " + name)
            names.add(name)
    if not STATE.REQUIRED_RUNTIME_FILES.issubset(compat["runtime_files"]):
        raise ActivationError("native helper closure is incomplete")
    pam = {"/etc/pam.d/greetd", "/etc/pam.d/common-session", "/etc/pam.d/common-auth",
           "/etc/pam.d/common-account", "/etc/pam.d/common-password"}
    if not pam.issubset(compat["configuration_files"]):
        raise ActivationError("native PAM configuration closure is incomplete")
    if any(compat["configuration_files"].get(name) != {"symlink": target}
           for name, target in INITIAL_BOOT_LINKS.items()):
        raise ActivationError("initial boot link closure is incomplete")
    if (type(qualification.get("schema")) is not int or qualification["schema"] != 1 or qualification.get("profile") != STATE.PROFILE
            or qualification.get("workflow") != STATE.WORKFLOW or qualification.get("scope") != "pre-image-integration"
            or qualification.get("status") != "passed" or qualification.get("physical_hardware_qualified") is not False
            or qualification.get("suites") != {"session": "passed", "wizard": "passed", "store": "passed"}
            or qualification.get("assets") != assets):
        raise ActivationError("native source qualification is missing or incompatible")
    source_runtime = qualification.get("runtime_files")
    if not isinstance(source_runtime, dict) or not STATE.REQUIRED_RUNTIME_FILES.issubset(source_runtime):
        raise ActivationError("source qualification helper closure is incomplete")
    if any(compat["runtime_files"].get(p) != h for p, h in source_runtime.items()):
        raise ActivationError("source qualification runtime differs from compatibility lock")
    required = compat.get("required_packages")
    if not isinstance(required, dict) or not {"applaunch", "greetd", "tyrell-zero-session", "tyrell-zero-store"}.issubset(required):
        raise ActivationError("native package closure is incomplete")
    packages = installed(root, execute)
    if packages != required:
        raise ActivationError("installed native package closure differs")
    command = ("/usr/bin/dpkg-divert", "--root=" + str(root.root),
               "--admindir=" + str(root.root / "var/lib/dpkg"))
    if (execute(root.root, *command, "--listpackage", STORE.PUBLIC) != "tyrell-zero-store"
            or execute(root.root, *command, "--truename", STORE.PUBLIC) != STORE.ORIGINAL):
        raise ActivationError("Store diversion was not registered before package installation")
    for unit in SERVICE_FILES:
        root.files.secure("/usr/lib/systemd/system/" + unit)
        names.add("/usr/lib/systemd/system/" + unit)
    root.files.secure("/etc/pam.d/greetd")
    names.add("/etc/pam.d/greetd")
    if root.files.read("/usr/share/tyrell-zero/profile/logind.conf") != b"[Login]\nNAutoVTs=0\nReserveVT=0\n":
        raise ActivationError("native logind policy differs")
    for name, expected in compat.get("protected_files", {}).items():
        if root.snapshot(name) != expected:
            raise ActivationError("protected hardware identity differs: " + name)
        names.add(name)
    for prefix in ("/etc/apt", "/etc/pam.d", "/etc/udev", "/etc/tmpfiles.d",
                   "/etc/sudoers.d", "/etc/polkit-1"):
        folder = root.at(prefix)
        if folder.exists():
            root.resolve(prefix)
            names.update("/" + str(p.relative_to(root.root)) for p in folder.rglob("*")
                         if p.is_file() or p.is_symlink())
    for name in ("/etc/sudoers", "/usr/share/tyrell-zero/profile/activation.json",
                 "/usr/share/tyrell-zero/profile/logind.conf"):
        if root.files.exists(name): names.add(name)
    return names


def recovery_groups(root):
    passwd = root.files.read("/etc/passwd").decode().splitlines()
    matches = [row.split(":") for row in passwd if row.startswith("_greetd:")]
    if len(matches) != 1 or len(matches[0]) != 7 or not matches[0][2].isdigit() or int(matches[0][2]) <= 0:
        raise ActivationError("the unprivileged greetd recovery account is required")
    for name in ("/etc/group", "/etc/gshadow"):
        if name == "/etc/gshadow" and not root.files.exists(name): continue
        rows = [row.split(":") for row in root.files.read(name).decode().splitlines()]
        if any(len(row) != 4 for row in rows): raise ActivationError("invalid group database")
        for group in ("video", "input"):
            matches = [row for row in rows if row[0] == group]
            if len(matches) != 1: raise ActivationError("missing native recovery device group")
            members = set(filter(None, matches[0][3].split(",")));members.add("_greetd")
            matches[0][3] = ",".join(sorted(members))
        root.write(name, "\n".join(":".join(row) for row in rows) + "\n", 0o640 if name.endswith("gshadow") else 0o644)


def activation_links():
    links = {"/etc/systemd/system/default.target": "/usr/lib/systemd/system/multi-user.target",
             "/etc/systemd/system/multi-user.target.wants/tyrell-zero.target": "/usr/lib/systemd/system/tyrell-zero.target",
             "/etc/systemd/user/APPLaunch.service": "/dev/null"}
    links.update({"/etc/systemd/system/" + unit: "/dev/null"
                  for unit in (*SYSTEM_MASKS, "lightdm.service", "display-manager.service")})
    return links


def activate(value, qualification_path, native_compat_path, *, owner=0, execute=STORE.command):
    root = OfflineRoot(value, owner=owner)
    validate_fresh_boot(root)
    compat, compat_sha = document(native_compat_path)
    qualification, source_sha, qualification_bytes = document(qualification_path, raw=True)
    if compat.get("source_qualification_sha256") != source_sha:
        raise ActivationError("source qualification receipt identity differs")
    names = validate(root, compat, qualification, execute)
    metadata, _ = document(root.files.secure("/etc/tyrell-image.json"))
    if (metadata.get("profile_id") != "tyrell-zero" or metadata.get("stages") != ["stage0", "stage1", "stage2"]
            or not isinstance(metadata.get("locks_sha256"), dict)
            or metadata["locks_sha256"].get("tyrell-zero-native-compat.json") != compat_sha
            or not DIGEST.fullmatch(str(metadata.get("profile_sha256", "")))):
        raise ActivationError("captured image metadata is missing or incompatible")
    if root.files.exists(RECEIPT):
        existing, _ = document(root.files.secure(RECEIPT))
        if (existing.get("native_compat_sha256") != compat_sha or existing.get("source_qualification_sha256") != source_sha
                or existing.get("schema") != 1 or existing.get("phase") != "assembled-root"
                or existing.get("complete") is not True or existing.get("profile_id") != "tyrell-zero"
                or existing.get("physical_hardware_qualified") is not False
                or existing.get("locks_sha256") != metadata["locks_sha256"]
                or existing.get("profile_sha256") != metadata["profile_sha256"]
                or existing.get("stages") != ["stage0", "stage1", "stage2"]
                or existing.get("power_preset") != "baseline"):
            raise ActivationError("an incompatible activation receipt already exists")
        entries = existing.get("files")
        mandatory = names | set(activation_links()) | {STORE.INVARIANTS, STATE.INTEGRATION, STATE.QUALIFICATION,
            SOURCE_RECEIPT, UPDATE_POLICY, UPDATER_STATUS, APPS_UPDATER_STATUS, UPDATES_DESKTOP, UPDATES_TEXT,
            STATE.MARKERS[0], STATE.GUIDE, "/etc/group", "/etc/tyrell-image.json",
            "/etc/tyrell-zero/power-policy.json", "/usr/share/tyrell-zero/power-policy/contract.json",
            "/etc/systemd/logind.conf.d/90-tyrell-zero.conf"}
        if root.files.exists("/etc/gshadow"): mandatory.add("/etc/gshadow")
        if not isinstance(entries, dict) or not mandatory.issubset(entries) or RECEIPT in entries:
            raise ActivationError("activation receipt file closure is incomplete")
        if (any(root.snapshot(name) != record for name, record in entries.items())
                or any(root.snapshot(name).get("target") != target for name, target in activation_links().items())):
            raise ActivationError("activated image changed; use a fresh assembly attempt")
        if (root.files.digest(STORE.INVARIANTS) != existing.get("base_invariants_sha256")
                or root.files.digest(STATE.INTEGRATION) != existing.get("session_integration_sha256")
                or root.files.digest(STATE.QUALIFICATION) != existing.get("session_qualification_sha256")):
            raise ActivationError("activation receipt binding differs")
        STATE.integration(root.files)
        return existing
    for name in (STATE.READY, STATE.ACCOUNT, STATE.CHECKPOINT, STATE.JOURNAL,
                 "/var/lib/LaunchWizard/apply.checkpoint"):
        if root.files.exists(name): raise ActivationError("refusing to activate an already provisioned image")
    # Readiness is invalidated durably before the first profile mutation.
    root.write_json(STATE.INTEGRATION, {"schema": 1, "profile": STATE.PROFILE,
        "workflow": STATE.WORKFLOW, "applaunch_version": "0.6.111", "complete": False,
        "missing": ["Offline activation has not completed"]})
    # Bind the bytes as supplied, rather than a reformatted JSON identity.
    root.write(SOURCE_RECEIPT, qualification_bytes.decode("utf-8"))
    links = activation_links()
    for name, target in links.items(): root.link(name, target)
    for prefix in ("/etc/systemd/system", "/etc/systemd/user"):
        for folder in root.at(prefix).glob("*"):
            if not folder.name.endswith((".wants", ".requires")): continue
            root.files.secure("/" + str(folder.relative_to(root.root)), directory=True)
            for name in ("lightdm.service", "labwc.service", "APPLaunch.service", "LaunchWizard.service", "userconfig.service"):
                old = folder / name
                if os.path.lexists(old):
                    if not old.is_symlink() or old.lstat().st_uid != owner:
                        raise ActivationError("untrusted legacy session activation")
                    old.unlink()
    root.write("/etc/systemd/logind.conf.d/90-tyrell-zero.conf",
               root.files.read("/usr/share/tyrell-zero/profile/logind.conf").decode())
    root.write(UPDATER_STATUS, "failed:incompatible\n")
    root.write(APPS_UPDATER_STATUS, "failed:incompatible\n")
    root.write_json(UPDATE_POLICY, {"schema": 1, "profile": "tyrell-zero",
        "base_update_method": "reviewed-offline-image", "native_compat_sha256": compat_sha,
        "launcher_updater": "masked-incompatible", "vendor_app_updater": "masked-unqualified",
        "ordinary_apt_security_maintenance": "preserve",
        "automatic_security_deferral": False, "application_updates": "qualified-store-catalog"})
    root.write(UPDATES_TEXT, "Tyrell Zero Updates\nUse a reviewed base image for OS/session updates.\n"
               "Keep the known reference card. The Store manages qualified applications.\n"
               "Vendor launcher/app auto-updaters are not qualified; normal APT/security maintenance is preserved.\n")
    root.write(UPDATES_DESKTOP, "[Desktop Entry]\nType=Application\nName=Tyrell Zero Updates\n"
               "Comment=Reviewed base images and qualified app updates\n"
               "Exec=/usr/libexec/tyrell-zero-session-recovery --updates\nTerminal=false\n"
               "Categories=System;\nStartupNotify=false\n")
    recovery_groups(root)
    for name in (STATE.MARKERS[0], STATE.GUIDE): root.write(name, "")
    generated = set(links) | {"/etc/systemd/logind.conf.d/90-tyrell-zero.conf", UPDATE_POLICY,
        UPDATER_STATUS, APPS_UPDATER_STATUS, UPDATES_TEXT, UPDATES_DESKTOP, SOURCE_RECEIPT}
    # Capture only stable base state. Account/group and setup records change
    # legitimately during first boot and must not make ordinary Store work fail.
    protected = set()
    for name in names | generated:
        # rpi-resize disables itself after its first-boot chain completes. Keep
        # its fresh-image identity in the receipt, but protect its stable unit
        # and drop-in rather than the enablement link at runtime.
        if name in INITIAL_BOOT_LINKS: continue
        record = root.snapshot(name)
        if record["type"] == "directory": continue
        parent, _ = root.resolve(str(PurePosixPath(name).parent))
        protected.add(parent.rstrip("/") + "/" + PurePosixPath(name).name)
        if record["type"] == "symlink" and record["target"] == "/dev/null": continue
        resolved, target = root.resolve(name)
        if target.is_file(): protected.add(resolved)
    STORE.capture(root.root, sorted(protected), execute=execute)
    baseline_sha = root.files.digest(STORE.INVARIANTS)
    runtime = dict(compat["runtime_files"])
    runtime.update({name: root.files.digest(name) for name in (STORE.INVARIANTS, SOURCE_RECEIPT, UPDATES_TEXT)})
    receipt = dict(qualification, runtime_files=runtime, source_qualification_sha256=source_sha,
                   native_compat_sha256=compat_sha, base_invariants_sha256=baseline_sha,
                   assembled_runtime_verified=True)
    root.write_json(STATE.QUALIFICATION, receipt)
    session = dict(schema=1, profile=STATE.PROFILE, workflow=STATE.WORKFLOW, complete=True,
                   applaunch_version="0.6.111", assets=compat["assets"], runtime_files=runtime,
                   qualification_sha256=root.files.digest(STATE.QUALIFICATION))
    root.write_json(STATE.INTEGRATION, session)
    integration_sha = STATE.integration(root.files)
    names |= generated | {STORE.INVARIANTS, STATE.INTEGRATION, STATE.QUALIFICATION,
                          STATE.MARKERS[0], STATE.GUIDE, "/etc/group", "/etc/tyrell-image.json",
                          "/etc/tyrell-zero/power-policy.json",
                          "/usr/share/tyrell-zero/power-policy/contract.json"}
    if root.files.exists("/etc/gshadow"): names.add("/etc/gshadow")
    final = dict(schema=1, profile_id="tyrell-zero", phase="assembled-root", complete=True,
                 physical_hardware_qualified=False, native_compat_sha256=compat_sha,
                 source_qualification_sha256=source_sha, base_invariants_sha256=baseline_sha,
                 locks_sha256=metadata["locks_sha256"], profile_sha256=metadata["profile_sha256"],
                 stages=["stage0", "stage1", "stage2"], power_preset="baseline",
                 session_integration_sha256=integration_sha,
                 session_qualification_sha256=root.files.digest(STATE.QUALIFICATION),
                 files={name: root.snapshot(name) for name in sorted(names)})
    root.write_json(RECEIPT, final)
    return final


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", required=True)
    parser.add_argument("--prepare-runtime", action="store_true")
    parser.add_argument("--qualification", type=Path)
    parser.add_argument("--native-compat", type=Path)
    args = parser.parse_args(argv)
    if os.geteuid() != 0: parser.error("root is required for offline image activation")
    try:
        if args.prepare_runtime:
            if args.qualification or args.native_compat:
                parser.error("runtime preparation does not accept qualification inputs")
            print(json.dumps(prepare_runtime(args.root), sort_keys=True))
            return
        if not args.qualification or not args.native_compat:
            parser.error("activation requires --qualification and --native-compat")
        result = activate(args.root, args.qualification, args.native_compat)
        print(json.dumps({"complete": result["complete"], "receipt": RECEIPT,
                          "files": len(result["files"]), "physical_hardware_qualified": False}, sort_keys=True))
    except (ActivationError, STATE.StateError, STORE.GuardError, OSError, ValueError, KeyError) as error:
        parser.exit(1, "Zero activation failed: " + str(error) + "\n")


if __name__ == "__main__":
    main()
