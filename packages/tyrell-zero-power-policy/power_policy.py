"""Tyrell Zero power-policy foundation. Never writes hardware controls.

The package intentionally contains no boot activation. Optimized apply remains
closed until identity, ordering, Ethernet matching and hardware gates are built.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

VERSION = "0.1.0"
CONFIG = "etc/tyrell-zero/power-policy.json"
CONTRACT = "usr/share/tyrell-zero/power-policy/contract.json"
STATE_DIR = "run/tyrell-zero-power-policy"
OVERRIDE = "run/tmpfiles.d/raspberrypi-sys-mods-ondemand-governor.conf"
KERNEL = "6.18.39+rpt-rpi-v8"
BSP = "75518549a2640486f5d13ea72ca4935b198d7c20"
VENDOR_TMPFILES = "usr/lib/tmpfiles.d/raspberrypi-sys-mods-ondemand-governor.conf"
VENDOR_UDEV = "usr/lib/udev/rules.d/60-ondemand-governor.rules"
VENDOR_HASHES = {
    VENDOR_TMPFILES: "4851009554de1894d22d8ea0cea6a22226bf37a90a24c65b4b0a09df41ee16c2",
    VENDOR_UDEV: "21d7b91c3b324fc381eea1466419222e49bdef4e36883a9848c460f98be2aeea",
}
BASELINE_TUNABLES = {
    "up_threshold": "50", "ignore_nice_load": "0",
    "sampling_down_factor": "50", "powersave_bias": "0",
    "sampling_rate": "100000", "io_is_busy": "0",
}
PRESETS = {
    "baseline": ("on", 50), "ethernet": ("auto", 50),
    "cpu": ("on", 1), "combined": ("auto", 1),
}
GENERATION = re.compile(r"[0-9a-f]{32}\Z")


class PolicyError(ValueError):
    pass


class BusyError(PolicyError):
    pass


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def checked_path(root: Path, relative: str, *, missing: bool = False) -> Path:
    """Reject path escape and every symlink below the selected root."""
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(x in {".", ".."} for x in parts):
        raise PolicyError("Unsafe relative policy path")
    current = root
    for part in parts:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if missing:
                continue
            raise PolicyError("Missing policy input: " + relative) from None
        if stat.S_ISLNK(mode):
            raise PolicyError("Symlink policy input: " + relative)
    return current


def read_bytes(root: Path, relative: str, maximum: int = 65536) -> bytes:
    path = checked_path(root, relative)
    if not stat.S_ISREG(path.stat().st_mode):
        raise PolicyError("Policy input must be a regular file: " + relative)
    with path.open("rb") as stream:
        value = stream.read(maximum + 1)
    if len(value) > maximum:
        raise PolicyError("Policy input is too large: " + relative)
    return value


def read_json(root: Path, relative: str) -> object:
    try:
        return json.loads(read_bytes(root, relative))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PolicyError("Invalid JSON: " + relative) from error


def validate_contract(value: object) -> dict:
    """Bind this foundation to the captured revision-3 baseline contract."""
    if not isinstance(value, dict):
        raise PolicyError("Power contract must be an object")
    for key, expected in {"schema": 1, "id": "tyrell-zero-power", "plan_revision": 3,
                          "default_preset": "baseline", "package": "tyrell-zero-power-policy",
                          "selection_config": "/" + CONFIG,
                          "activation_implemented": False}.items():
        if type(value.get(key)) is not type(expected) or value[key] != expected:
            raise PolicyError("Unsupported power contract field: " + key)
    expected = {name: Selection(name).targets() for name in PRESETS}
    if value.get("presets") != expected:
        raise PolicyError("Power contract preset values differ from the foundation")
    for name in PRESETS:
        for key, expected_value in expected[name].items():
            if type(value["presets"][name][key]) is not type(expected_value):
                raise PolicyError("Power contract preset value has the wrong type")
    baseline = value.get("baseline_controls", {})
    if not isinstance(baseline, dict) or baseline.get("governor") != "ondemand" or type(baseline.get("cpu_min_khz")) is not int or baseline.get("cpu_min_khz") != 600000 or type(baseline.get("cpu_max_khz")) is not int or baseline.get("cpu_max_khz") != 1000000:
        raise PolicyError("Power contract CPU baseline differs")
    if baseline.get("ondemand_tunables") != {k: int(v) for k, v in BASELINE_TUNABLES.items()}:
        raise PolicyError("Power contract CPU tunables differ")
    if any(type(v) is not int for v in baseline["ondemand_tunables"].values()):
        raise PolicyError("Power contract CPU tunable has the wrong type")
    safety = value.get("safety", {})
    if not isinstance(safety, dict) or any(safety.get(k) is not False for k in ["install_sysfs_writes", "install_udev_triggers", "polling_daemon"]):
        raise PolicyError("Unsafe power contract installation policy")
    gates = value.get("release_gates", {})
    if not isinstance(gates, dict) or gates.get("battery_promotion_required") is not True or gates.get("usb_only_promotion_allowed") is not False:
        raise PolicyError("Power contract battery gates differ")
    return value


@dataclass(frozen=True)
class Selection:
    preset: str

    def __post_init__(self):
        if not isinstance(self.preset, str) or self.preset not in PRESETS:
            raise PolicyError("Unknown power preset")

    @classmethod
    def parse(cls, value: object) -> "Selection":
        if not isinstance(value, dict) or set(value) != {"schema", "preset"}:
            raise PolicyError("Selection requires exactly schema and preset")
        if type(value["schema"]) is not int or value["schema"] != 1:
            raise PolicyError("Unsupported selection schema")
        if not isinstance(value["preset"], str) or value["preset"] not in PRESETS:
            raise PolicyError("Unknown power preset")
        return cls(value["preset"])

    def targets(self) -> dict:
        ethernet, factor = PRESETS[self.preset]
        return {
            "ethernet_control": ethernet,
            "ethernet_autosuspend_delay_ms": 2000,
            "ondemand_sampling_down_factor": factor,
        }

    def data(self) -> dict:
        return {"schema": 1, "preset": self.preset}


def inspect(root: Path, selection: Selection) -> dict:
    """Read inputs only. A matching snapshot never authorizes activation."""
    checks = {}
    try:
        validate_contract(read_json(root, CONTRACT))
        checks["power_contract"] = True
    except PolicyError:
        checks["power_contract"] = False
    for relative, expected in VENDOR_HASHES.items():
        try:
            checks[relative] = digest(read_bytes(root, relative)) == expected
        except PolicyError:
            checks[relative] = False
    try:
        checks["kernel"] = read_bytes(root, "proc/sys/kernel/osrelease").decode().strip() == KERNEL
    except (PolicyError, UnicodeError):
        checks["kernel"] = False
    try:
        metadata = read_json(root, "etc/tyrell-image.json")
        checks["image_identity"] = (
            isinstance(metadata, dict) and metadata.get("profile") == "tyrell-zero"
            and metadata.get("drivers_commit") == BSP
            and metadata.get("kernel_release") == KERNEL
        )
    except PolicyError:
        checks["image_identity"] = False
    try:
        checks["administrator_override_absent"] = not checked_path(
            root, "etc/tmpfiles.d/raspberrypi-sys-mods-ondemand-governor.conf", missing=True
        ).exists()
    except PolicyError:
        checks["administrator_override_absent"] = False
    return {
        "schema": 1, "version": VERSION, "selection": selection.data(),
        "targets": selection.targets(), "selection_sha256": digest(canonical(selection.data())),
        "read_only_identity_checks": checks,
        "optimized_activation_implemented": False,
        "hardware_qualified": False, "battery_qualified": False,
        "automatic_units_or_rules_installed": False,
        "blocking_gates": [
            "Board and CPU-policy eligibility plus activation ordering are not implemented",
            "Integrated Ethernet topology and runtime write handling are not implemented",
            "Spare-card hardware/performance and battery qualification are incomplete",
        ],
        "hardware_writes": 0,
    }


def baseline_readback(root: Path) -> dict:
    """Read only the CPU controls for which this foundation defines a baseline."""
    checks = {}
    for relative, expected in {
        "sys/devices/system/cpu/cpufreq/policy0/scaling_governor": "ondemand",
        "sys/devices/system/cpu/cpufreq/policy0/scaling_min_freq": "600000",
        "sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq": "1000000",
        **{"sys/devices/system/cpu/cpufreq/ondemand/" + key: value
           for key, value in BASELINE_TUNABLES.items()},
    }.items():
        try:
            checks[relative] = read_bytes(root, relative).decode().strip() == expected
        except (PolicyError, UnicodeError):
            checks[relative] = False
    return checks


class PolicyStore:
    """Serialize supported state/invalidation flows, without hardware writes.

    This does not serialize arbitrary administrator commands or an external
    tmpfiles reader. Real activation must join this lock/generation protocol.
    """
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.directory = self.root / STATE_DIR

    def _private_directory(self):
        current = self.root
        for part in Path(STATE_DIR).parts:
            current /= part
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            mode = current.lstat()
            if stat.S_ISLNK(mode.st_mode) or not stat.S_ISDIR(mode.st_mode):
                raise PolicyError("Unsafe policy state directory")
        owner = self.directory.stat()
        if owner.st_uid != os.geteuid() or owner.st_mode & 0o077:
            raise PolicyError("Policy state directory must be private and caller-owned")

    @contextmanager
    def lock(self):
        self._private_directory()
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.directory / "lock", flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise PolicyError("Unsafe policy lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise BusyError("Policy transaction is busy") from error
            yield
        finally:
            os.close(fd)

    def _state(self) -> dict | None:
        path = checked_path(self.root, STATE_DIR + "/state.json", missing=True)
        if not path.exists():
            return None
        info = path.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise PolicyError("Unsafe policy state ownership")
        value = read_json(self.root, STATE_DIR + "/state.json")
        if not isinstance(value, dict) or set(value) != {"schema", "generation", "preset", "phase", "override_sha256"}:
            raise PolicyError("Invalid policy state shape")
        if type(value["schema"]) is not int or value["schema"] != 1 or not isinstance(value["generation"], str) or not GENERATION.fullmatch(value["generation"]):
            raise PolicyError("Invalid policy state generation")
        if not isinstance(value["preset"], str) or value["preset"] not in PRESETS or not isinstance(value["phase"], str) or value["phase"] not in {"unqualified", "invalidated", "recovery-required", "cpu-baseline-observed"}:
            raise PolicyError("Invalid policy state value")
        sha = value["override_sha256"]
        if sha is not None and (not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)):
            raise PolicyError("Invalid owned override digest")
        return value

    def _write(self, state: dict):
        fd, name = tempfile.mkstemp(prefix=".state-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(canonical(state)); stream.flush(); os.fsync(stream.fileno())
            os.replace(name, self.directory / "state.json")
            parent = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            Path(name).unlink(missing_ok=True)

    @staticmethod
    def marker(generation: str) -> bytes:
        if not isinstance(generation, str) or not GENERATION.fullmatch(generation):
            raise PolicyError("Invalid generation")
        return ("# Managed by tyrell-zero-power-policy; generation=" + generation + "\n").encode()

    def record_generation(self, selection: Selection, generation: str, owned_sha256: str | None = None):
        """Record a future integration's generation; never creates an override."""
        self.marker(generation)
        if owned_sha256 is not None and (not isinstance(owned_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", owned_sha256)):
            raise PolicyError("Invalid owned override digest")
        with self.lock():
            if self._state() is not None:
                raise PolicyError("Invalidate the previous generation before recording another")
            if owned_sha256 is not None:
                info = checked_path(self.root, OVERRIDE).stat()
                if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                    raise PolicyError("Unsafe owned override permissions")
                raw = read_bytes(self.root, OVERRIDE)
                if not raw.startswith(self.marker(generation)) or digest(raw) != owned_sha256:
                    raise PolicyError("Override generation or digest differs")
            self._write({"schema": 1, "generation": generation, "preset": selection.preset,
                         "phase": "unqualified", "override_sha256": owned_sha256})

    def invalidate(self, expected_generation: str) -> dict:
        """Remove only the exact owned generated file; never claims restoration."""
        with self.lock():
            state = self._state()
            if state is None or state["generation"] != expected_generation:
                raise PolicyError("Generation changed or was not recorded")
            state["phase"] = "invalidated"
            self._write(state)  # Invalidate authorization before removing its file.
            path = checked_path(self.root, OVERRIDE, missing=True)
            if path.exists():
                raw = read_bytes(self.root, OVERRIDE)
                if not state["override_sha256"] or digest(raw) != state["override_sha256"] or not raw.startswith(self.marker(expected_generation)):
                    raise PolicyError("Refusing to remove an unowned or changed override")
                path.unlink()
            checks = baseline_readback(self.root)
            # CPU readback alone does not certify Ethernet or external tmpfiles readers.
            state["phase"] = "cpu-baseline-observed" if all(checks.values()) else "recovery-required"
            state["override_sha256"] = None
            self._write(state)
            return {"generation": expected_generation, "phase": state["phase"],
                    "cpu_baseline_checks": checks, "hardware_writes": 0,
                    "baseline_recovery_certified": False,
                    "next_step": "Controlled baseline boot and full effective-policy verification required"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/"), help="read/fixture root; default /")
    parser.add_argument("--config", default=CONFIG, help="relative selection path under root")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("validate"); sub.add_parser("inspect"); sub.add_parser("apply")
    invalid = sub.add_parser("invalidate")
    invalid.add_argument("--generation", required=True)
    sub.add_parser("version")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        if args.action == "version":
            print(VERSION); return 0
        if args.action == "invalidate":
            if root == Path("/") and os.geteuid() != 0:
                raise PolicyError("Live runtime-state invalidation requires root")
            result = PolicyStore(root).invalidate(args.generation)
        else:
            selection = Selection.parse(read_json(root, args.config))
            if args.action == "validate":
                validate_contract(read_json(root, CONTRACT))
            result = inspect(root, selection)
            if args.action == "apply":
                if selection.preset != "baseline":
                    result["status"] = "blocked-unimplemented-activation"
                    print(json.dumps(result, indent=2)); return 78
                result["status"] = "baseline-no-op"
                result["baseline_recovery_certified"] = False
        print(json.dumps(result, indent=2)); return 0
    except (PolicyError, OSError) as error:
        print("tyrell-zero-power-policy: " + str(error), file=sys.stderr); return 78


if __name__ == "__main__":
    raise SystemExit(main())
