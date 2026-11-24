#!/usr/bin/env python3
"""Prepare and inspect bounded observations; never qualifies or writes a device."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "research/tyrell-zero-image-validation.json"
HARDWARE = ROOT / "image/contracts/tyrell-zero-hardware.json"
SHA = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}\Z")
CONDITIONS = ("device_id", "battery_id", "supply", "workload", "display", "audio",
              "network", "peripherals", "policy", "os_settings")
MAX_JSON = 1024 * 1024
MAX_SAMPLES = 32 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def decode(raw):
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def read_bytes(path, max_bytes=MAX_JSON):
    """Read bounded regular records; FIFOs, devices and final symlinks are refused."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), "expected a regular record")
        require(os.fstat(fd).st_size <= max_bytes, "record exceeds size limit")
        data = bytearray()
        while len(data) <= max_bytes:
            chunk = os.read(fd, min(65536, max_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        require(len(data) <= max_bytes, "record exceeds size limit")
        return bytes(data)
    finally:
        os.close(fd)


def load_json(path, max_bytes=MAX_JSON):
    return decode(read_bytes(path, max_bytes))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(data):
    return (json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def save_new(path, data):
    """Never replace an earlier plan/report, even with identical bytes."""
    raw = encoded(data)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise


def reference(profile):
    evidence_raw = read_bytes(EVIDENCE)
    evidence = decode(evidence_raw)
    require(profile in ("zero", "control"), "unknown reference profile")
    image = evidence["zero_image" if profile == "zero" else "full_source_control"]
    common = evidence["input_comparison"]["required_common_inputs"]
    return {"profile": profile, "raw_image_sha256": image["raw_image"]["sha256_after"],
            "export_manifest_sha256": image["export"]["host_manifest"]["sha256"],
            "source_commit": image["source_commit"], "evidence_sha256": sha(evidence_raw),
            "kernel_release": common["kernel_release"], "bsp_commit": common["bsp_commit"],
            "app_packages": [{"name": "tyrell-status", "version": common["status_version"],
                              "sha256": common["status_sha256"]}]}


def prepare(profile, run_id, purpose="f2-baseline"):
    require(isinstance(run_id, str) and ID.fullmatch(run_id), "unsafe run ID")
    require(purpose in ("f2-baseline", "h1-backlight"), "unknown purpose")
    return {"schema": 1, "kind": "tyrell-observation-plan", "run_id": run_id,
            "purpose": purpose, "reference": reference(profile),
            "conditions": {key: "UNRECORDED" for key in CONDITIONS},
            "collection": {"settle_seconds": 60, "duration_seconds": 120,
                           "interval_seconds": 5, "pids": []},
            "acceptance": {row["id"]: {"result": "not-tested", "evidence": []}
                           for row in load_json(HARDWARE)["capabilities"]},
            "notes": "Declared export identity is not attestation of the running image. "
                     "Record spare-card write/readback and observed BSP/controller evidence separately."}


def validate_plan(plan):
    require(isinstance(plan, dict), "plan must be an object")
    require(set(plan) == {"schema", "kind", "run_id", "purpose", "reference", "conditions",
                          "collection", "acceptance", "notes"}, "unexpected or missing plan fields")
    require(type(plan["schema"]) is int and plan["schema"] == 1, "unsupported plan schema")
    require(plan["kind"] == "tyrell-observation-plan", "wrong plan kind")
    require(isinstance(plan["run_id"], str) and ID.fullmatch(plan["run_id"]), "unsafe run ID")
    require(plan["purpose"] in ("f2-baseline", "h1-backlight"), "unknown purpose")
    ref = plan["reference"]
    require(isinstance(ref, dict) and ref == reference(ref.get("profile")),
            "reference must match the exact retained baseline evidence in this checkout")
    conditions = plan["conditions"]
    require(isinstance(conditions, dict) and set(conditions) == set(CONDITIONS),
            "record all comparison conditions")
    for key, value in conditions.items():
        require(isinstance(value, str) and 0 < len(value.strip()) <= 2048 and value != "UNRECORDED",
                "unrecorded or oversized condition: " + key)
        require(not any(ord(c) < 32 for c in value), "control characters in condition")
    collection = plan["collection"]
    require(isinstance(collection, dict) and set(collection) ==
            {"settle_seconds", "duration_seconds", "interval_seconds", "pids"}, "invalid collection")
    for key, low, high in (("settle_seconds", 0, 600), ("duration_seconds", 1, 1800),
                           ("interval_seconds", 1, 60)):
        require(type(collection[key]) is int and low <= collection[key] <= high,
                "out of bounds: " + key)
    require(collection["interval_seconds"] <= collection["duration_seconds"], "interval exceeds duration")
    require(collection["duration_seconds"] // collection["interval_seconds"] + 1 <= 181,
            "more than 181 requested samples")
    pids = collection["pids"]
    require(isinstance(pids, list) and len(pids) <= 16 and
            all(type(pid) is int and 1 <= pid <= 4194304 for pid in pids) and
            len(pids) == len(set(pids)), "invalid explicit PID selection")
    acceptance = plan["acceptance"]
    categories = {row["id"] for row in load_json(HARDWARE)["capabilities"]}
    require(isinstance(acceptance, dict) and set(acceptance) == categories,
            "all fifteen hardware categories must remain explicit")
    # Collecting counters can never turn a hardware gate green. Human observations
    # and exact-artifact qualification belong to a separate reviewed acceptance record.
    require(all(row == {"result": "not-tested", "evidence": []} for row in acceptance.values()),
            "observation plans cannot grant hardware acceptance")
    require(isinstance(plan["notes"], str) and len(plan["notes"]) <= 8192, "invalid notes")


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def inspect_bundle(directory):
    directory = Path(directory)
    require(not directory.is_symlink() and directory.is_dir(), "expected a regular run directory")
    plan_raw = read_bytes(directory / "plan.json")
    plan = decode(plan_raw)
    validate_plan(plan)
    result = load_json(directory / "result.json")
    samples_raw = read_bytes(directory / "samples.jsonl", MAX_SAMPLES)
    require(isinstance(result, dict) and type(result.get("schema")) is int and
            result.get("schema") == 1 and result.get("kind") == "tyrell-observation-result",
            "wrong observation result schema/kind")
    require(result.get("plan_sha256") == sha(plan_raw), "plan hash mismatch")
    require(result.get("samples_sha256") == sha(samples_raw), "samples hash mismatch")
    require(result.get("status") in ("completed", "interrupted", "failed"), "invalid result status")
    require(result.get("claims_release_approval") is False and
            result.get("hardware_acceptance") is False, "observations must deny acceptance")
    require(isinstance(result.get("identity"), dict) and result["identity"].get("reference_proven") is False,
            "observations cannot attest a running image")
    lines = samples_raw.splitlines()
    require(type(result.get("sample_count")) is int and
            len(lines) <= 181 and len(lines) == result.get("sample_count"), "sample count mismatch")
    timing = result.get("timing")
    require(isinstance(timing, dict), "missing collection timing")
    for name in ("settle_seconds", "requested_duration_seconds", "elapsed_seconds_including_settle",
                 "observed_seconds", "observer_cpu_seconds", "snapshot_child_cpu_seconds", "missed_samples"):
        require(finite(timing.get(name)), "invalid collection timing: " + name)
    require(timing["settle_seconds"] == plan["collection"]["settle_seconds"] and
            timing["requested_duration_seconds"] == plan["collection"]["duration_seconds"],
            "result timing differs from plan")
    if result["status"] == "completed":
        expected = plan["collection"]["duration_seconds"] // plan["collection"]["interval_seconds"] + 1
        require(len(lines) == expected and len(lines) >= 2, "completed run must contain all planned samples")
        require(timing["observed_seconds"] >= plan["collection"]["duration_seconds"] and
                timing["elapsed_seconds_including_settle"] >= timing["observed_seconds"] + timing["settle_seconds"] - 0.001
                and timing["missed_samples"] == 0, "completed run has incomplete collection timing")
    samples = []
    previous = -1.0
    for index, line in enumerate(lines):
        require(len(line) <= 3 * 1024 * 1024, "sample exceeds limit")
        sample = decode(line)
        require(isinstance(sample, dict) and sample.get("schema") == 1 and
                sample.get("index") == index, "invalid sample order/schema")
        elapsed = sample.get("elapsed_seconds")
        require(finite(elapsed) and elapsed > previous, "nonmonotonic sample times")
        previous = elapsed
        if result["status"] == "completed":
            interval = plan["collection"]["interval_seconds"]
            require(index * interval - 0.001 <= elapsed < (index + 1) * interval,
                    "completed run missed its sampling schedule")
        require(isinstance(sample.get("readings"), dict) and isinstance(sample.get("metrics"), dict),
                "missing sample readings/metrics")
        for name, reading in sample["readings"].items():
            require(isinstance(name, str) and (name.startswith("/") or name == "collector") and isinstance(reading, dict),
                    "invalid reading")
            require(reading.get("status") in {"ok", "missing", "denied", "unsafe", "oversized", "timeout", "error"},
                    "unknown reading status")
            if "raw" in reading:
                require(reading["status"] == "ok" and isinstance(reading["raw"], str), "invalid raw reading")
        mem = sample["readings"].get("/proc/meminfo", {})
        matches = re.findall(r"^MemAvailable:\s+([0-9]+) kB$", mem.get("raw", "")
                             if mem.get("status") == "ok" else "", re.MULTILINE)
        derived = int(matches[0]) if len(matches) == 1 else None
        require(sample["metrics"].get("mem_available_kib") == derived and
                (derived is None or type(sample["metrics"].get("mem_available_kib")) is int),
                "MemAvailable metric does not match raw evidence")
        samples.append(sample)
    if result["status"] == "completed":
        duration = plan["collection"]["duration_seconds"]
        interval = plan["collection"]["interval_seconds"]
        require(samples[-1]["elapsed_seconds"] - samples[0]["elapsed_seconds"] >=
                max(0, duration - interval), "completed run has a truncated sampling window")
    tools = result.get("tool_sha256")
    require(isinstance(tools, dict) and set(tools) == {"collect.py", "records.py"},
            "collector and record validator must be identified")
    for name, value in tools.items():
        require(isinstance(value, str) and SHA.fullmatch(value), "invalid tool digest")
    return plan, result, samples


def memory_summary(samples):
    values = [row.get("metrics", {}).get("mem_available_kib") for row in samples]
    if not values or any(type(value) is not int or value < 0 for value in values):
        return None
    return {"samples": len(values), "min_kib": min(values), "median_kib": statistics.median(values),
            "max_kib": max(values)}


def effective_controls(samples):
    """Compare actual readbacks, preserving raw text; never infer absent controls."""
    def selected(name):
        return ((name.startswith("/sys/devices/system/cpu/cpufreq/") and name.endswith(
            ("/scaling_governor", "/scaling_min_freq", "/scaling_max_freq", "/sampling_down_factor",
             "/sampling_rate", "/up_threshold", "/ignore_nice_load", "/powersave_bias", "/io_is_busy"))) or
            (name.startswith("/sys/bus/usb/devices/") and name.endswith(("/power/control", "/power/autosuspend_delay_ms"))) or
            ("/backlight/" in name and name.endswith(("/brightness", "/bl_power"))))
    snapshots = []
    availability = []
    for sample in samples:
        # Global/per-policy governor attributes and device/interface USB
        # attributes differ. Stable unsupported optional files are not failures;
        # required controls below must still have actual readable evidence.
        if any(value.get("status") not in ("ok", "missing") or
               (value.get("status") == "ok" and not isinstance(value.get("raw"), str))
               for name, value in sample["readings"].items() if selected(name)):
            return None
        for group in ("/sys/bus/usb/devices/", "/sys/class/backlight/", "/sys/devices/system/cpu/cpufreq/"):
            enumeration = sample["readings"].get(group, {})
            if enumeration.get("status") != "ok" or type(enumeration.get("count")) is not int or enumeration["count"] < 1:
                return None
        availability.append({name: value.get("status") for name, value in sample["readings"].items() if selected(name)})
        readings = {name: value["raw"] for name, value in sample["readings"].items()
                    if selected(name) and value.get("status") == "ok" and isinstance(value.get("raw"), str)}
        snapshots.append(readings)
    if not snapshots or any(row != snapshots[0] for row in snapshots) or any(row != availability[0] for row in availability):
        return None
    controls = snapshots[0]
    # Policy0 and its ondemand controls are part of this exact preserved BSP.
    required = ("/scaling_governor", "/scaling_min_freq", "/scaling_max_freq", "/sampling_down_factor",
                "/sampling_rate", "/up_threshold", "/ignore_nice_load", "/powersave_bias", "/io_is_busy",
                "/power/control", "/power/autosuspend_delay_ms", "/brightness")
    if any(not any(name.endswith(suffix) for name in controls) for suffix in required):
        return None
    # Matching optimized settings is not the unchanged baseline experiment.
    expected = {"/scaling_governor": "ondemand", "/scaling_min_freq": "600000",
                "/scaling_max_freq": "1000000", "/sampling_down_factor": "50",
                "/sampling_rate": "100000", "/up_threshold": "50", "/ignore_nice_load": "0",
                "/powersave_bias": "0", "/io_is_busy": "0"}
    for suffix, value in expected.items():
        if any(raw.strip() != value for name, raw in controls.items() if name.endswith(suffix)):
            return None
    # IDs alone do not distinguish the integrated Ethernet from an external
    # lookalike. Retain canonical ancestry and drivers before using any policy.
    for sample in samples:
        observed = sample["readings"]
        groups = {name.rsplit("/", 1)[0] for name, value in observed.items()
                  if name.startswith("/sys/bus/usb/devices/") and name.endswith("/idVendor") and
                  value.get("raw", "").strip() == "0fe6"}
        leaves = [base for base in groups if observed.get(base + "/idProduct", {}).get("raw", "").strip() == "9900"]
        if len(leaves) != 1:
            return None
        leaf = leaves[0]
        identity = observed.get(leaf + "/uevent", {})
        canonical = identity.get("resolved_path", "")
        if not canonical.startswith("/sys/devices/") or "DRIVER=r8152-cfgselector" not in identity.get("raw", "").splitlines():
            return None
        if controls.get(leaf + "/power/control", "").strip() != "on" or controls.get(
                leaf + "/power/autosuspend_delay_ms", "").strip() != "2000":
            return None
        leaf_path = canonical.rsplit("/", 1)[0]
        parents = []
        interface = False
        for name, reading in observed.items():
            path = reading.get("resolved_path", "")
            if name.endswith("/uevent") and path.startswith(leaf_path + "/") and path != canonical:
                interface |= "DRIVER=r8152" in reading.get("raw", "").splitlines()
            if name.endswith("/power/control") and path.endswith("/power/control"):
                device_path = path[:-len("/power/control")]
                if leaf_path.startswith(device_path + "/"):
                    parents.append(reading.get("raw", "").strip())
        if not interface or len(parents) < 2 or any(raw != "auto" for raw in parents):
            return None
    return {"values": controls, "availability": availability[0]}


def compare(zero_directory, control_directory):
    runs = [inspect_bundle(zero_directory), inspect_bundle(control_directory)]
    reasons = []
    if [plan["reference"]["profile"] for plan, _, _ in runs] != ["zero", "control"]:
        reasons.append("select one Zero run and one matched full-source control")
    for plan, result, samples in runs:
        if plan["purpose"] != "f2-baseline":
            reasons.append("H1 intervention observations cannot enter the base-only comparison")
        if result["status"] != "completed" or len(samples) < 2:
            reasons.append("completed, multi-sample runs required")
        if result["tool_sha256"] != {name: sha(read_bytes(Path(__file__).parent / name))
                                     for name in ("collect.py", "records.py")}:
            reasons.append("run tools differ from the reviewed checkout")
        identity = result.get("identity", {})
        require(isinstance(identity, dict), "invalid observed identity")
        observed = identity.get("kernel_release", {})
        require(isinstance(observed, dict) and isinstance(identity.get("app_packages", {}), dict), "invalid kernel/package identity")
        if observed.get("status") != "ok" or observed.get("raw", "").strip() != plan["reference"]["kernel_release"]:
            reasons.append("observed kernel does not match declared baseline")
        for package in plan["reference"]["app_packages"]:
            installed = identity.get("app_packages", {}).get(package["name"], {})
            require(isinstance(installed, dict), "invalid observed package")
            if installed.get("version") != package["version"] or installed.get("status") != "install ok installed":
                reasons.append("observed app version/state does not match reference")
        if samples and samples[-1]["elapsed_seconds"] - samples[0]["elapsed_seconds"] < max(
                0, plan["collection"]["duration_seconds"] - plan["collection"]["interval_seconds"]):
            reasons.append("sampling window shorter than declared duration")
    zero, control = runs
    if zero[0]["conditions"] != control[0]["conditions"]:
        reasons.append("device, workload or operating conditions differ")
    for name in ("settle_seconds", "duration_seconds", "interval_seconds"):
        if zero[0]["collection"][name] != control[0]["collection"][name]:
            reasons.append("collection timing differs")
    controls = [effective_controls(samples) for _, _, samples in runs]
    if any(item is None for item in controls) or controls[0] != controls[1]:
        reasons.append("effective controls unavailable, changed within a run, or differ between runs")
    summaries = [memory_summary(samples) for _, _, samples in runs]
    if any(summary is None for summary in summaries):
        reasons.append("MemAvailable missing or malformed")
    difference = None if reasons else summaries[0]["median_kib"] - summaries[1]["median_kib"]
    return {"schema": 1, "kind": "tyrell-baseline-comparison", "status": "blocked" if reasons else "descriptive-only",
            "reasons": sorted(set(reasons)), "zero": {"plan_sha256": zero[1]["plan_sha256"], "memory": summaries[0]},
            "control": {"plan_sha256": control[1]["plan_sha256"], "memory": summaries[1]},
            "median_mem_available_difference_kib": difference, "engineering_target_kib": 32768,
            "target_observed_in_this_pair": None if difference is None else difference >= 32768,
            "grants_release_approval": False, "hardware_acceptance": False,
            "limitations": ["Declared raw/export identities are not running-image attestation.",
                            "Integrated USB topology, installed bytes, Wi-Fi/EEE and GPU/CMA/swap require independent review.",
                            "One pair and median samples do not establish settled memory, causality or uncertainty.",
                            "PSS/pressure, activation/timer inventories, BSP/controller and all physical gates need review.",
                            "Counters cannot establish electrical off, optical refresh, energy or battery runtime."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    create = actions.add_parser("prepare")
    create.add_argument("--profile", choices=("zero", "control"), required=True)
    create.add_argument("--run-id", required=True)
    create.add_argument("--purpose", choices=("f2-baseline", "h1-backlight"), default="f2-baseline")
    create.add_argument("--output", type=Path, required=True)
    check = actions.add_parser("validate")
    choice = check.add_mutually_exclusive_group(required=True)
    choice.add_argument("--plan", type=Path)
    choice.add_argument("--bundle", type=Path)
    comparison = actions.add_parser("compare")
    comparison.add_argument("--zero", type=Path, required=True)
    comparison.add_argument("--control", type=Path, required=True)
    comparison.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            result = prepare(args.profile, args.run_id, args.purpose)
            save_new(args.output, result)
            print("Prepared template; fill every UNRECORDED condition before collecting.")
        elif args.action == "validate":
            if args.plan:
                validate_plan(load_json(args.plan))
                print("Plan valid; no hardware or release acceptance.")
            else:
                _, result, _ = inspect_bundle(args.bundle)
                print("Bundle integrity valid; collection status: " + result["status"] + "; no hardware or release acceptance.")
                return 0 if result["status"] == "completed" else 2
        else:
            result = compare(args.zero, args.control)
            save_new(args.output, result)
            print(json.dumps(result, indent=2))
            return 2 if result["status"] == "blocked" else 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("Observation record rejected: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
