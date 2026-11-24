"""Fixture-only observation checks: no host proc/sys access or tuning."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import records

SPEC = importlib.util.spec_from_file_location("observation_collector", HERE / "collect.py")
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


class VirtualClock:
    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "root"
        self.root.mkdir()
        self.plan = records.prepare("zero", "fixture-run")
        self.plan["conditions"] = {name: "fixture only" for name in records.CONDITIONS}
        self.plan["collection"] = {"settle_seconds": 0, "duration_seconds": 2,
                                   "interval_seconds": 1, "pids": [42]}
        self.write("/proc/meminfo", "MemTotal:       352000 kB\nMemAvailable: 123456 kB\n")
        self.write("/proc/stat", "cpu  10 2 3 40 5 6 7 8 9 10\nintr 200 1 2\nctxt 100\n")
        self.write("/proc/vmstat", "pgmajfault 2\npswpin 3\npswpout 4\n")
        self.write("/proc/42/stat", "42 (app with ) name) S " + "0 " * 18 + "123456 0\n")
        self.write("/proc/42/smaps_rollup", "0000-ffff ---p [rollup]\nPss: 37 kB\n")
        self.write("/proc/sys/kernel/osrelease", self.plan["reference"]["kernel_release"] + "\n")
        self.status = ("Package: tyrell-status\nStatus: install ok installed\nVersion: " +
                       self.plan["reference"]["app_packages"][0]["version"] + "\n\n" +
                       "Package: unrelated\nVersion: 1\nStatus: install ok installed\n\n")
        self.write("/var/lib/dpkg/status", self.status)

    def write(self, name, text):
        destination = self.root / name.lstrip("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text)
        return destination

    def output(self):
        return self.base / "output"

    def run_collection(self, **kwargs):
        clock = VirtualClock()
        return collector.collect(self.plan, self.output(), self.root, clock=clock.clock,
                                 sleep=clock.sleep, **kwargs)

    def test_fixed_metrics_do_not_relabel_irq_or_guest_ticks(self):
        sample = collector.snapshot(self.root, [42], self.plan["reference"]["app_packages"], True)
        self.assertEqual(sample["metrics"]["mem_available_kib"], 123456)
        self.assertEqual(sample["metrics"]["pss_kib"], {"42": 37})
        self.assertEqual(sample["metrics"]["process_identity"], {"42": {"start_ticks": 123456}})
        self.assertEqual(sample["metrics"]["cpu"], {"total_ticks": 81, "idle_ticks": 45, "intr": 200, "ctxt": 100})
        self.assertNotIn("wakeups", sample["metrics"])
        identity = sample["identity"]
        self.assertFalse(identity["reference_proven"])
        self.assertEqual(identity["kernel_release"]["raw"].strip(), self.plan["reference"]["kernel_release"])
        self.assertEqual(identity["dpkg_status_sha256"], hashlib.sha256(self.status.encode()).hexdigest())
        self.assertEqual(identity["app_packages"], {"tyrell-status": {"version": "0.1.0-1", "status": "install ok installed"}})
        self.assertNotIn("unrelated", json.dumps(identity))
        self.assertNotIn("/proc/42/cmdline", sample["readings"])
        self.assertNotIn("/proc/42/environ", sample["readings"])

    def test_missing_denied_oversized_and_fifo_are_explicit(self):
        reader = collector.Reader(self.root)
        self.assertEqual(reader.read("/proc/no-such-file")["status"], "missing")
        target = self.write("/proc/large", "x" * 20)
        self.assertEqual(reader.read("/proc/large", 10)["status"], "oversized")
        os.mkfifo(self.root / "proc/fifo")
        self.assertEqual(reader.read("/proc/fifo")["status"], "unsafe")
        with patch.object(reader, "open", side_effect=PermissionError()):
            self.assertEqual(reader.read("/proc/meminfo")["status"], "denied")
        self.assertEqual(target.read_text(), "x" * 20)

    def test_sysfs_alias_must_end_under_devices_and_records_resolved_path(self):
        self.write("/sys/devices/platform/display/backlight/panel/brightness", "0\n")
        alias = self.root / "sys/class/backlight"
        alias.mkdir(parents=True)
        (alias / "panel").symlink_to("../../devices/platform/display/backlight/panel")
        name = "/sys/class/backlight/panel/brightness"
        reading = collector.Reader(self.root).read(name)
        self.assertEqual(reading["status"], "ok")
        self.assertEqual(reading["raw"], "0\n")
        self.assertEqual(reading["resolved_path"], "/sys/devices/platform/display/backlight/panel/brightness")
        escaped = alias / "escaped"
        escaped.symlink_to(self.root / "proc")
        self.assertEqual(collector.Reader(self.root).read("/sys/class/backlight/escaped/meminfo")["status"], "unsafe")
        (self.root / "proc/link").symlink_to(self.root / "proc/meminfo")
        self.assertEqual(collector.Reader(self.root).read("/proc/link")["status"], "unsafe")

    def test_regular_directory_budget_and_no_follow_ancestors(self):
        reader = collector.Reader(self.root)
        reader.remaining = 1
        self.assertEqual(reader.read("/proc/meminfo")["status"], "oversized")
        self.assertEqual(reader.read("/proc/stat")["status"], "oversized")
        self.assertEqual(collector.Reader(self.root).read("/proc")["status"], "unsafe")
        (self.root / "other").symlink_to(self.root / "proc")
        self.assertEqual(collector.Reader(self.root).read("/other/meminfo")["status"], "unsafe")
        self.assertEqual(collector.Reader(self.root).read("/proc/../etc/passwd")["status"], "unsafe")

    def test_reported_canonical_path_is_the_path_actually_opened(self):
        self.write("/sys/devices/panel/brightness", "0\n")
        reader = collector.Reader(self.root)
        original = reader.resolve
        calls = [0]
        def changing(name):
            calls[0] += 1
            return original(name) if calls[0] == 1 else Path("sys/devices/different/brightness")
        with patch.object(reader, "resolve", side_effect=changing):
            reading = reader.read("/sys/devices/panel/brightness")
        self.assertEqual(reading["resolved_path"], "/sys/devices/panel/brightness")
        self.assertEqual(calls[0], 1)

    def test_enumeration_is_bounded_and_reported(self):
        directory = self.root / "sys/class/backlight"
        directory.mkdir(parents=True)
        for number in range(collector.MEMBERS + 2):
            (directory / ("panel" + str(number))).mkdir()
        names, report = collector.Reader(self.root).members("/sys/class/backlight")
        self.assertEqual(len(names), collector.MEMBERS)
        self.assertEqual(report["status"], "oversized")

    def test_malformed_counters_never_become_zero(self):
        self.write("/proc/meminfo", "MemAvailable: broken kB\n")
        self.write("/proc/42/smaps_rollup", "Pss: -4 kB\n")
        self.write("/proc/stat", "cpu broken\nintr x\nctxt -2\n")
        self.write("/proc/vmstat", "pswpin NaN\n")
        sample = collector.snapshot(self.root, [42], [], False)
        self.assertNotIn("mem_available_kib", sample["metrics"])
        self.assertNotIn("cpu", sample["metrics"])
        self.assertNotIn("vmstat", sample["metrics"])
        self.assertEqual(sample["metrics"]["pss_status"], {"42": "malformed"})
        self.assertEqual(sample["metrics"]["pss_kib"], {})

    def test_pid_reuse_during_snapshot_invalidates_pss(self):
        original = collector.Reader.read
        seen = [0]
        def replaced(reader, name, *args, **kwargs):
            result = original(reader, name, *args, **kwargs)
            if name == "/proc/42/stat":
                seen[0] += 1
                if seen[0] == 2:
                    result["raw"] = result["raw"].replace("123456", "654321")
            return result
        with patch.object(collector.Reader, "read", new=replaced):
            sample = collector.snapshot(self.root, [42], [], False)
        self.assertEqual(sample["readings"]["/proc/42/smaps_rollup"]["status"], "unsafe")
        self.assertEqual(sample["metrics"]["pss_kib"], {})

    def test_completed_run_hashes_and_private_new_files(self):
        before = {str(path.relative_to(self.root)): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}
        result = self.run_collection()
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["sample_count"], 3)
        self.assertFalse(result["hardware_acceptance"])
        self.assertFalse(result["claims_release_approval"])
        self.assertEqual(result["timing"]["observed_seconds"], 2)
        self.assertEqual(result["plan_sha256"], hashlib.sha256((self.output() / "plan.json").read_bytes()).hexdigest())
        self.assertEqual(result["samples_sha256"], hashlib.sha256((self.output() / "samples.jsonl").read_bytes()).hexdigest())
        self.assertEqual(set(result["tool_sha256"]), {"collect.py", "records.py"})
        plan, inspected, samples = records.inspect_bundle(self.output())
        self.assertEqual(inspected["status"], "completed")
        self.assertEqual([s["elapsed_seconds"] for s in samples], [0, 1, 2])
        self.assertEqual(plan, self.plan)
        for path in self.output().iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.output().stat().st_mode & 0o777, 0o700)
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes()
                                 for path in self.root.rglob("*") if path.is_file()})

    def test_existing_output_is_preserved(self):
        self.output().mkdir()
        (self.output() / "earlier-result").write_text("retain")
        with self.assertRaises(FileExistsError):
            self.run_collection()
        self.assertEqual((self.output() / "earlier-result").read_text(), "retain")

    def test_unfilled_or_unbounded_plan_never_creates_output(self):
        self.plan["conditions"]["device_id"] = "UNRECORDED"
        with self.assertRaises(ValueError):
            self.run_collection()
        self.assertFalse(self.output().exists())
        self.plan["conditions"]["device_id"] = "fixture"
        self.plan["collection"]["duration_seconds"] = 1801
        with self.assertRaises(ValueError):
            self.run_collection()
        self.assertFalse(self.output().exists())

    def test_interrupt_retains_partial_result_without_pass(self):
        self.plan["collection"]["settle_seconds"] = 1
        result = self.run_collection(cancelled=lambda: True)
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["sample_count"], 0)
        self.assertEqual((self.output() / "samples.jsonl").read_bytes(), b"")
        self.assertEqual(records.inspect_bundle(self.output())[1]["status"], "interrupted")

    def test_snapshot_timeout_is_bounded_and_incomplete(self):
        with patch.object(collector, "snapshot", side_effect=lambda *args: time.sleep(10)):
            start = time.monotonic()
            result = collector.supervised_snapshot(self.root, [], [], False, timeout=0.1)
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertTrue(result["snapshot_failed"])
        self.assertIn(result["readings"]["collector"]["status"], ("timeout", "error"))
        run = self.run_collection(take_snapshot=lambda *args: result)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["sample_count"], 1)

    def test_child_closes_unrelated_inherited_descriptors(self):
        fd = os.open(self.root / "proc/meminfo", os.O_RDONLY)
        self.addCleanup(os.close, fd)
        def probe(*args):
            try:
                os.fstat(fd)
                status = "inherited"
            except OSError:
                status = "closed"
            return {"readings": {"probe": {"status": status}}, "metrics": {}}
        with patch.object(collector, "snapshot", side_effect=probe):
            result = collector.supervised_snapshot(self.root, [], [], False)
        self.assertEqual(result["readings"]["probe"]["status"], "closed")

    def test_oversized_snapshot_or_total_output_cannot_complete(self):
        payload = {"readings": {"fixture": {"status": "ok", "raw": "x" * collector.SNAPSHOT_BYTES}}, "metrics": {}}
        with patch.object(collector, "snapshot", return_value=payload):
            self.assertTrue(collector.supervised_snapshot(self.root, [], [], False)["snapshot_failed"])
        budget = collector.RESULT_BYTES + len(collector.encoded(self.plan)) + 1
        with patch.object(collector, "OUTPUT_BYTES", budget):
            self.assertEqual(self.run_collection()["status"], "failed")
            self.assertLessEqual(sum(p.stat().st_size for p in self.output().iterdir()), budget)

    def test_slow_snapshot_misses_schedule_and_cannot_complete(self):
        clock = VirtualClock()
        def slow(*args):
            clock.now += 1.1
            return {"readings": {}, "metrics": {}}
        result = collector.collect(self.plan, self.output(), self.root, clock=clock.clock,
                                   sleep=clock.sleep, take_snapshot=slow)
        self.assertEqual(result["status"], "failed")
        self.assertIn(result["error"], ("collection deadline", "sampling schedule missed"))

    def test_identity_status_hash_is_exact_even_for_invalid_utf8(self):
        data = self.status.encode() + b"\xff"
        (self.root / "var/lib/dpkg/status").write_bytes(data)
        identity = collector.installed_identity(collector.Reader(self.root), self.plan["reference"]["app_packages"])
        self.assertEqual(identity["dpkg_status_sha256"], hashlib.sha256(data).hexdigest())
        self.assertNotIn("raw", identity["dpkg_status"])


if __name__ == "__main__":
    unittest.main()
