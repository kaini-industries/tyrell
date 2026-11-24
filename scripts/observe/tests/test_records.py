"""Fixture evidence cannot silently become a hardware or memory acceptance."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("observation_records", Path(__file__).parents[1] / "records.py")
records = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(records)


def plan(profile="zero"):
    value = records.prepare(profile, profile + "-fixture")
    value["conditions"] = {key: "fixture only" for key in records.CONDITIONS}
    value["collection"] = {"settle_seconds": 0, "duration_seconds": 2, "interval_seconds": 1, "pids": []}
    return value


def reading(raw, path=None):
    result = {"status": "ok", "raw": raw + "\n"}
    if path:
        result["resolved_path"] = path
    return result


def controls():
    prefix = "/sys/devices/system/cpu/cpufreq/"
    observed = {prefix + "policy0/" + key: reading(value) for key, value in (
        ("scaling_governor", "ondemand"), ("scaling_min_freq", "600000"), ("scaling_max_freq", "1000000"))}
    observed.update({prefix + "ondemand/" + key: reading(value) for key, value in (
        ("sampling_rate", "100000"), ("sampling_down_factor", "50"), ("up_threshold", "50"),
        ("ignore_nice_load", "0"), ("powersave_bias", "0"), ("io_is_busy", "0"))})
    observed["/sys/class/backlight/fake/brightness"] = reading("50")
    for group in ("/sys/bus/usb/devices/", "/sys/class/backlight/", "/sys/devices/system/cpu/cpufreq/"):
        observed[group] = {"status": "ok", "count": 1}
    # Synthetic topology deliberately uses bus9; the algorithm must not select
    # a bus number or a VID/PID without the observed ancestry and drivers.
    base = "/sys/bus/usb/devices/"
    canonical = "/sys/devices/platform/fixture/usb9"
    for name, path, mode in (("usb9", canonical, "auto"), ("9-1", canonical + "/9-1", "auto"),
                             ("9-1.2", canonical + "/9-1/9-1.2", "on")):
        observed[base + name + "/power/control"] = reading(mode, path + "/power/control")
        observed[base + name + "/power/autosuspend_delay_ms"] = reading("2000", path + "/power/autosuspend_delay_ms")
    leaf = base + "9-1.2"
    path = canonical + "/9-1/9-1.2"
    observed[leaf + "/idVendor"] = reading("0fe6")
    observed[leaf + "/idProduct"] = reading("9900")
    observed[leaf + "/uevent"] = reading("DRIVER=r8152-cfgselector", path + "/uevent")
    observed[leaf + ":1.0/uevent"] = reading("DRIVER=r8152", path + "/9-1.2:1.0/uevent")
    return observed


def bundle(directory, profile="zero", memory=100000, mutate=None, status="completed"):
    directory.mkdir()
    run_plan = plan(profile)
    samples = []
    for index in range(3):
        observed = controls()
        observed["/proc/meminfo"] = reading("MemAvailable: " + str(memory) + " kB")
        samples.append({"schema": 1, "index": index, "elapsed_seconds": index,
                        "readings": observed, "metrics": {"mem_available_kib": memory}})
    result = {"schema": 1, "kind": "tyrell-observation-result", "status": status,
              "claims_release_approval": False, "hardware_acceptance": False,
              "timing": {"settle_seconds": 0, "requested_duration_seconds": 2,
                         "elapsed_seconds_including_settle": 2, "observed_seconds": 2,
                         "observer_cpu_seconds": 0.01, "snapshot_child_cpu_seconds": 0.01, "missed_samples": 0},
              "identity": {"reference_proven": False, "kernel_release": reading(run_plan["reference"]["kernel_release"]),
                           "app_packages": {"tyrell-status": {"version": "0.1.0-1", "status": "install ok installed"}}},
              "tool_sha256": {name: records.sha(records.read_bytes(Path(records.__file__).with_name(name)))
                              for name in ("collect.py", "records.py")}}
    if mutate:
        mutate(run_plan, result, samples)
    plan_raw = records.encoded(run_plan)
    samples_raw = b"".join(records.encoded(row).replace(b"\n", b"") + b"\n" for row in samples)
    result.update(plan_sha256=records.sha(plan_raw), samples_sha256=records.sha(samples_raw), sample_count=len(samples))
    (directory / "plan.json").write_bytes(plan_raw)
    (directory / "samples.jsonl").write_bytes(samples_raw)
    (directory / "result.json").write_bytes(records.encoded(result))
    return directory


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_template_is_explicitly_not_ready_and_keeps_fifteen_gates(self):
        value = records.prepare("zero", "new-run")
        self.assertEqual(len(value["acceptance"]), 15)
        with self.assertRaisesRegex(ValueError, "unrecorded"):
            records.validate_plan(value)
        records.validate_plan(plan())

    def test_changed_export_or_new_app_cannot_be_relabelled_original_control(self):
        for key in ("raw_image_sha256", "source_commit", "evidence_sha256"):
            value = plan()
            value["reference"][key] = "0" * len(value["reference"][key])
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "retained baseline"):
                records.validate_plan(value)
        value = plan()
        value["reference"]["app_packages"][0]["version"] = "0.1.2-1"
        with self.assertRaises(ValueError):
            records.validate_plan(value)

    def test_plan_cannot_mark_physical_gates_passed(self):
        value = plan()
        value["acceptance"]["lcd_backlight"]["result"] = "passed"
        with self.assertRaisesRegex(ValueError, "hardware acceptance"):
            records.validate_plan(value)

    def test_plan_bounds_and_unknown_fields_are_not_silently_ignored(self):
        for key, bad in (("duration_seconds", True), ("duration_seconds", 1801),
                         ("settle_seconds", 601), ("interval_seconds", 0),
                         ("pids", [1, 1]), ("pids", [True])):
            value = plan()
            value["collection"][key] = bad
            with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                records.validate_plan(value)
        value = plan()
        value["root"] = "/arbitrary/path"
        with self.assertRaises(ValueError):
            records.validate_plan(value)

    def test_record_reads_refuse_duplicate_keys_nonfinite_fifo_link_and_oversize(self):
        target = self.root / "target"
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}'):
            target.write_bytes(raw)
            with self.assertRaises(ValueError):
                records.load_json(target)
        target.write_bytes(b"12345")
        with self.assertRaises(ValueError):
            records.load_json(target, 4)
        link = self.root / "link"
        link.symlink_to(target)
        with self.assertRaises(OSError):
            records.load_json(link)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "regular"):
            records.load_json(fifo)

    def test_existing_report_never_overwritten(self):
        target = self.root / "report"
        records.save_new(target, {"old": 1})
        with self.assertRaises(FileExistsError):
            records.save_new(target, {"new": 2})
        self.assertEqual(records.load_json(target), {"old": 1})

    def test_bundle_mutation_and_false_completion_fail(self):
        good = bundle(self.root / "good")
        records.inspect_bundle(good)
        (good / "samples.jsonl").write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            records.inspect_bundle(good)
        for name, mutate in (("empty", lambda p, r, s: s.clear()),
                             ("short", lambda p, r, s: s.__setitem__(slice(None), s[:1])),
                             ("metric", lambda p, r, s: s[0]["metrics"].update(mem_available_kib=999)),
                             ("badreading", lambda p, r, s: s[0]["readings"].update({"/bad": []})),
                             ("timing", lambda p, r, s: r["timing"].update(observed_seconds=0)),
                             ("schedule", lambda p, r, s: s[1].update(elapsed_seconds=0.5)),
                             ("nonmonotonic", lambda p, r, s: s[1].update(elapsed_seconds=0))):
            target = bundle(self.root / name, mutate=mutate)
            with self.subTest(name=name), self.assertRaises(ValueError):
                records.inspect_bundle(target)

    def test_synthetic_matched_pair_only_describes_target_and_denies_acceptance(self):
        zero = bundle(self.root / "zero", memory=140000)
        control = bundle(self.root / "control", "control", memory=100000)
        result = records.compare(zero, control)
        self.assertEqual(result["status"], "descriptive-only")
        self.assertEqual(result["median_mem_available_difference_kib"], 40000)
        self.assertTrue(result["target_observed_in_this_pair"])
        self.assertFalse(result["grants_release_approval"])
        self.assertFalse(result["hardware_acceptance"])

    def test_incomplete_mismatched_or_intervention_runs_block_target_interpretation(self):
        control = bundle(self.root / "control", "control")
        mutations = (
            lambda p, r, s: r.update(status="interrupted"),
            lambda p, r, s: p.update(purpose="h1-backlight"),
            lambda p, r, s: p["conditions"].update(audio="different"),
            lambda p, r, s: r["identity"]["kernel_release"].update(raw="different"),
            lambda p, r, s: r["identity"]["app_packages"]["tyrell-status"].update(version="0.1.2-1"),
            lambda p, r, s: r["tool_sha256"].update({"collect.py": "0" * 64}),
            lambda p, r, s: [row["readings"].pop("/sys/class/backlight/fake/brightness") for row in s],
            lambda p, r, s: [row["readings"].__setitem__("/sys/bus/usb/devices/9-1.2/uevent", reading("DRIVER=other")) for row in s],
        )
        for index, mutation in enumerate(mutations):
            zero = bundle(self.root / str(index), memory=140000, mutate=mutation)
            result = records.compare(zero, control)
            with self.subTest(index=index):
                self.assertEqual(result["status"], "blocked")
                self.assertIsNone(result["target_observed_in_this_pair"])

    def test_matching_optimized_policy_is_not_the_baseline(self):
        def optimized(p, r, samples):
            for sample in samples:
                sample["readings"]["/sys/devices/system/cpu/cpufreq/ondemand/sampling_down_factor"] = reading("1")
        zero = bundle(self.root / "zero", mutate=optimized)
        control = bundle(self.root / "control", "control", mutate=optimized)
        self.assertEqual(records.compare(zero, control)["status"], "blocked")

    def test_missing_or_disappearing_policy_readback_is_not_zero_or_equal(self):
        samples = [{"readings": controls()}, {"readings": controls()}]
        samples[1]["readings"]["/sys/bus/usb/devices/9-1.2/power/control"] = {"status": "denied"}
        self.assertIsNone(records.effective_controls(samples))

    def test_stable_unsupported_interface_and_duplicate_tunables_are_allowed(self):
        samples = [{"readings": controls()}, {"readings": controls()}]
        optional = ("/sys/devices/system/cpu/cpufreq/policy0/ondemand/sampling_down_factor",
                    "/sys/bus/usb/devices/9-1.2:1.0/power/autosuspend_delay_ms")
        for sample in samples:
            sample["readings"].update({name: {"status": "missing"} for name in optional})
        self.assertIsNotNone(records.effective_controls(samples))
        samples[1]["readings"].pop(optional[0])
        self.assertIsNone(records.effective_controls(samples))

    def test_truncated_device_enumeration_blocks_comparison(self):
        samples = [{"readings": controls()}]
        samples[0]["readings"]["/sys/bus/usb/devices/"] = {"status": "oversized", "limit": 16}
        self.assertIsNone(records.effective_controls(samples))


if __name__ == "__main__":
    unittest.main()
