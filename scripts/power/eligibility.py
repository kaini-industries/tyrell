"""Bounded offline power-policy inputs and plans; never authorizes live tuning.

Only explicitly synthetic snapshots can be ready for rehearsal. No supplied
snapshot proves board identity, installed bytes or physical qualification.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import PurePosixPath
import re

SCHEMA = 1
KERNEL = "6.18.39+rpt-rpi-v8"
BSP = "75518549a2640486f5d13ea72ca4935b198d7c20"
RAW_IMAGE = "207fcf45b222ec834468b6ecbe81cef663b0e709df282052b8450c5b5f85372a"
CONTRACT_SHA256 = "99803052bb1f7e03c5ccdeab8b14c831d019bee290248e199dd3bf0e4885465a"
VENDOR_HASHES = {
    "usr/lib/tmpfiles.d/raspberrypi-sys-mods-ondemand-governor.conf":
        "4851009554de1894d22d8ea0cea6a22226bf37a90a24c65b4b0a09df41ee16c2",
    "usr/lib/udev/rules.d/60-ondemand-governor.rules":
        "21d7b91c3b324fc381eea1466419222e49bdef4e36883a9848c460f98be2aeea",
}
BASELINE_TUNABLES = {"up_threshold": "50", "ignore_nice_load": "0",
                     "sampling_down_factor": "50", "powersave_bias": "0",
                     "sampling_rate": "100000", "io_is_busy": "0"}
PRESETS = {"baseline": ("on", "50"), "ethernet": ("auto", "50"),
           "cpu": ("on", "1"), "combined": ("auto", "1")}
CPU_FACTOR = "/sys/devices/system/cpu/cpufreq/ondemand/sampling_down_factor"
GENERATION = re.compile(r"[0-9a-f]{32}\Z")
HEX256 = re.compile(r"[0-9a-f]{64}\Z")
HEX4 = re.compile(r"[0-9a-f]{4}\Z")
MAX_SNAPSHOT_BYTES = 65536


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def shape(value, keys, label):
    require(isinstance(value, dict) and set(value) == set(keys), "unexpected or missing " + label + " fields")


def text(value, label, maximum=256):
    require(isinstance(value, str) and 0 < len(value) <= maximum and
            not any(ord(char) < 32 or ord(char) == 127 for char in value), "invalid " + label)


def path(value, label):
    text(value, label, 512)
    require(value.startswith("/sys/devices/") and "\\" not in value and
            all(part not in ("", ".", "..") for part in value.split("/")[1:]) and
            PurePosixPath(value).as_posix() == value, "noncanonical " + label)


def prepare_snapshot():
    """Produce an unverified template. Historical references are not observations."""
    return {
        "schema": 1, "kind": "tyrell-power-rehearsal-snapshot", "scope": "unverified-observation",
        "boot_id": "0" * 32,
        "image": {"profile": "tyrell-zero", "raw_sha256": RAW_IMAGE, "kernel_release": KERNEL,
                  "bsp_commit": BSP, "contract_sha256": CONTRACT_SHA256},
        "board": {"family": "UNRECORDED", "hardware_id": "UNRECORDED", "overlay": "UNRECORDED",
                  "model": "UNRECORDED", "compatible": ["UNRECORDED"], "controller_firmware": "UNRECORDED"},
        "policy": {"vendor_hashes": dict(VENDOR_HASHES), "administrator_override": "unknown",
                   "generated_override": "unknown"},
        "cpu": {"policy_path": "/sys/devices/system/cpu/cpufreq/policy0", "related_cpus": [],
                "driver": "UNRECORDED", "governor": "UNRECORDED", "min_khz": 0, "max_khz": 0,
                "tunables": {key: "unknown" for key in BASELINE_TUNABLES}},
        "usb": {"mode": "unknown", "controller_path": "/sys/devices/platform/UNRECORDED",
                "controller_driver": "UNRECORDED", "nodes": []},
    }


def validate_snapshot(value):
    """Validate syntax/limits, allowing mutable optimized readbacks for replay."""
    shape(value, ("schema", "kind", "scope", "boot_id", "image", "board", "policy", "cpu", "usb"), "snapshot")
    require(type(value["schema"]) is int and value["schema"] == 1, "unsupported snapshot schema")
    require(value["kind"] == "tyrell-power-rehearsal-snapshot", "unsupported snapshot kind")
    require(value["scope"] in ("synthetic-fixture", "unverified-observation"), "unsupported snapshot scope")
    require(isinstance(value["boot_id"], str) and GENERATION.fullmatch(value["boot_id"]), "invalid boot identity")
    image = value["image"]
    shape(image, ("profile", "raw_sha256", "kernel_release", "bsp_commit", "contract_sha256"), "image")
    for key in ("profile", "kernel_release"):
        text(image[key], key)
    require(isinstance(image["bsp_commit"], str) and re.fullmatch(r"[0-9a-f]{40}", image["bsp_commit"]), "invalid BSP digest")
    for key in ("raw_sha256", "contract_sha256"):
        require(isinstance(image[key], str) and HEX256.fullmatch(image[key]), "invalid " + key)
    board = value["board"]
    shape(board, ("family", "hardware_id", "overlay", "model", "compatible", "controller_firmware"), "board")
    for key in ("family", "hardware_id", "overlay", "model", "controller_firmware"):
        text(board[key], "board " + key)
    require(isinstance(board["compatible"], list) and 1 <= len(board["compatible"]) <= 8,
            "invalid board compatible list")
    for entry in board["compatible"]:
        text(entry, "compatible value")
    require(len(set(board["compatible"])) == len(board["compatible"]), "duplicate compatible values")
    policy = value["policy"]
    shape(policy, ("vendor_hashes", "administrator_override", "generated_override"), "policy")
    shape(policy["vendor_hashes"], VENDOR_HASHES, "vendor hash")
    for sha in policy["vendor_hashes"].values():
        require(isinstance(sha, str) and HEX256.fullmatch(sha), "invalid vendor SHA-256")
    for key in ("administrator_override", "generated_override"):
        require(policy[key] in ("absent", "present", "unknown"), "invalid override observation")
    cpu = value["cpu"]
    shape(cpu, ("policy_path", "related_cpus", "driver", "governor", "min_khz", "max_khz", "tunables"), "CPU")
    path(cpu["policy_path"], "CPU policy path")
    require(re.fullmatch(r"/sys/devices/system/cpu/cpufreq/policy[0-9]{1,4}", cpu["policy_path"]), "invalid CPU policy path")
    require(isinstance(cpu["related_cpus"], list) and len(cpu["related_cpus"]) <= 4 and
            all(type(item) is int and 0 <= item <= 3 for item in cpu["related_cpus"]) and
            cpu["related_cpus"] == sorted(set(cpu["related_cpus"])), "invalid related CPU set")
    for key in ("driver", "governor"):
        text(cpu[key], "CPU " + key)
    for key in ("min_khz", "max_khz"):
        require(type(cpu[key]) is int and 0 <= cpu[key] <= 2000000, "invalid CPU frequency")
    shape(cpu["tunables"], BASELINE_TUNABLES, "CPU tunable")
    for raw in cpu["tunables"].values():
        require(isinstance(raw, str) and (raw == "unknown" or re.fullmatch(r"[0-9]{1,9}", raw)), "invalid CPU tunable")
    usb = value["usb"]
    shape(usb, ("mode", "controller_path", "controller_driver", "nodes"), "USB")
    require(usb["mode"] in ("host", "gadget", "unknown"), "invalid USB mode")
    path(usb["controller_path"], "USB controller path")
    text(usb["controller_driver"], "USB controller driver")
    require(isinstance(usb["nodes"], list) and len(usb["nodes"]) <= 16, "USB inventory exceeds limit")
    paths = set()
    for node in usb["nodes"]:
        shape(node, ("path", "parent", "kind", "vendor_id", "product_id", "driver", "devpath", "control", "delay_ms"), "USB node")
        path(node["path"], "USB node path")
        path(node["parent"], "USB parent path")
        require(node["path"] not in paths, "duplicate USB paths")
        paths.add(node["path"])
        require(node["kind"] in ("root", "hub", "device", "interface"), "invalid USB node kind")
        for key in ("vendor_id", "product_id"):
            require(isinstance(node[key], str) and (node[key] == "none" or HEX4.fullmatch(node[key])), "invalid USB ID")
        text(node["driver"], "USB driver")
        require(isinstance(node["devpath"], str) and re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,3}){0,7}", node["devpath"]), "invalid USB port path")
        require(node["control"] in ("on", "auto", "unknown"), "invalid USB control")
        require(isinstance(node["delay_ms"], str) and (node["delay_ms"] == "unknown" or re.fullmatch(r"-?[0-9]{1,9}", node["delay_ms"])), "invalid USB delay")
    require(len(canonical(value)) <= MAX_SNAPSHOT_BYTES, "snapshot exceeds byte limit")
    return value


def _leaf(snapshot):
    leaves = [node for node in snapshot["usb"]["nodes"]
              if node["vendor_id"] == "0fe6" and node["product_id"] == "9900"]
    return leaves[0] if len(leaves) == 1 else None


def control_values(snapshot):
    """Return only controls owned by this model; parent controls are immutable."""
    validate_snapshot(snapshot)
    result = {CPU_FACTOR: snapshot["cpu"]["tunables"]["sampling_down_factor"]}
    leaf = _leaf(snapshot)
    if leaf:
        result[leaf["path"] + "/power/autosuspend_delay_ms"] = leaf["delay_ms"]
        result[leaf["path"] + "/power/control"] = leaf["control"]
    return result


def identity_digest(snapshot):
    """Bind boot/config/topology and every control not owned by the model."""
    validate_snapshot(snapshot)
    value = copy.deepcopy(snapshot)
    value["cpu"]["tunables"].pop("sampling_down_factor")
    leaf = _leaf(value)
    if leaf:
        leaf.pop("control")
        leaf.pop("delay_ms")
    value["usb"]["nodes"].sort(key=lambda node: node["path"])
    return digest(value)


def _blockers(snapshot):
    blockers = []
    def check(condition, message):
        if not condition:
            blockers.append(message)
    check(snapshot["scope"] == "synthetic-fixture", "No qualified live observer or accepted physical baseline; only synthetic rehearsal is supported")
    check(snapshot["boot_id"] != "0" * 32, "Boot identity is unknown")
    image = snapshot["image"]
    check(image == {"profile": "tyrell-zero", "raw_sha256": RAW_IMAGE, "kernel_release": KERNEL,
                    "bsp_commit": BSP, "contract_sha256": CONTRACT_SHA256}, "Image/kernel/BSP/contract reference differs")
    board = snapshot["board"]
    check(board["family"] == "cardputerzero" and board["hardware_id"] == "0x06" and
          board["overlay"] == "cardputerzero-v5" and
          set(board["compatible"]) == {"raspberrypi,0-compute-module", "brcm,bcm2837"} and
          board["model"] not in ("UNRECORDED", "unknown") and
          board["controller_firmware"] not in ("UNRECORDED", "unknown"), "Board/controller reference is unknown or differs")
    policy = snapshot["policy"]
    check(policy["vendor_hashes"] == VENDOR_HASHES, "Distribution power-policy bytes differ")
    check(policy["administrator_override"] == "absent" and policy["generated_override"] == "absent", "An override is present or unknown")
    cpu = snapshot["cpu"]
    check(cpu["related_cpus"] == [0, 1, 2, 3] and cpu["driver"] == "cpufreq-dt" and
          cpu["governor"] == "ondemand" and cpu["min_khz"] == 600000 and cpu["max_khz"] == 1000000,
          "CPU policy/driver/governor/frequency baseline differs")
    check(cpu["tunables"] == BASELINE_TUNABLES, "CPU tunables are not the original baseline")
    usb = snapshot["usb"]
    check(usb["mode"] == "host" and usb["controller_driver"] == "dwc2", "USB host/controller observation differs")
    nodes = {node["path"]: node for node in usb["nodes"]}
    leaf = _leaf(snapshot)
    if leaf is None:
        blockers.append("Integrated Ethernet is absent or ambiguous")
        return blockers
    hub = nodes.get(leaf["parent"], {})
    root = nodes.get(hub.get("parent"), {})
    check(leaf["kind"] == "device" and leaf["driver"] == "r8152-cfgselector" and
          leaf["devpath"] == "1.1" and hub.get("kind") == "hub" and hub.get("driver") == "hub" and
          hub.get("devpath") == "1" and root.get("kind") == "root" and root.get("driver") == "usb" and
          root.get("devpath") == "0" and root.get("parent") == usb["controller_path"],
          "Ethernet driver/integrated port ancestry differs")
    chain = [root, hub, leaf]
    check(all(node.get("path", "").rsplit("/", 1)[0] == node.get("parent") for node in chain),
          "USB canonical ancestry is inconsistent")
    check(len([node for node in nodes.values() if node["kind"] == "root"]) == 1 and
          all(node["path"].startswith(usb["controller_path"] + "/") for node in nodes.values()),
          "USB inventory is outside the selected controller")
    interfaces = [node for node in nodes.values() if node["parent"] == leaf["path"] and node["kind"] == "interface"]
    check(len(interfaces) == 1 and interfaces[0]["driver"] == "r8152" and
          interfaces[0]["path"].rsplit("/", 1)[0] == leaf["path"], "Ethernet bound interface differs")
    check(leaf["control"] == "on" and leaf["delay_ms"] == "2000" and
          hub.get("control") == "auto" and root.get("control") == "auto", "Ethernet/parent controls are not the original baseline")
    return blockers


def plan(snapshot, preset, generation):
    validate_snapshot(snapshot)
    require(isinstance(preset, str) and preset in PRESETS, "unknown power preset")
    require(isinstance(generation, str) and GENERATION.fullmatch(generation), "invalid generation")
    blockers = _blockers(snapshot)
    actions = []
    if not blockers and preset != "baseline":
        leaf = _leaf(snapshot)
        ethernet, factor = PRESETS[preset]
        desired = [("ethernet-delay", leaf["path"] + "/power/autosuspend_delay_ms", "2000"),
                   ("ethernet-control", leaf["path"] + "/power/control", ethernet),
                   ("cpu-factor", CPU_FACTOR, factor)]
        originals = control_values(snapshot)
        actions = [{"control_id": key, "path": path, "before": originals[path], "after": after}
                   for key, path, after in desired if originals[path] != after]
    return {"schema": 1, "kind": "tyrell-power-rehearsal-plan", "scope": "offline-model-only",
            "status": "blocked" if blockers else "ready", "preset": preset, "generation": generation,
            "snapshot_sha256": digest(snapshot), "identity_sha256": identity_digest(snapshot),
            "actions": actions, "blockers": blockers, "live_authorized": False,
            "hardware_qualified": False, "battery_qualified": False, "baseline_recovery_certified": False,
            "limitations": ["Synthetic readiness is not eligibility for a live system.",
                            "Typed image/board/controller fields do not attest running hardware or installed bytes.",
                            "No sysfs writer, boot ordering, maintenance integration or physical qualification is implemented."]}
