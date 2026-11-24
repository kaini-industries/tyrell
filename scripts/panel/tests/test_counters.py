"""Recorded fixtures only: counter analysis cannot establish physical refresh."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("panel_counter_analysis", HERE / "counters.py")
counters = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(counters)
records = counters.records


def fixture(directory, mutate=None, status="completed"):
    directory.mkdir()
    plan = records.prepare("zero", "panel-counter-fixture")
    plan["conditions"] = {name: "synthetic fixture only" for name in records.CONDITIONS}
    plan["collection"] = {"settle_seconds": 0, "duration_seconds": 2, "interval_seconds": 1, "pids": []}
    samples = []
    for index in range(3):
        readings = {}
        for name, number in {"bytes": index * 108800, "transfers": index * 2, "errors": 5, "timedout": 0}.items():
            readings["/sys/bus/spi/devices/spi9.1/statistics/" + name] = {
                "status": "ok", "raw": str(number) + "\n",
                "resolved_path": "/sys/devices/platform/fixture/spi9/spi9.1/statistics/" + name}
        samples.append({"schema": 1, "index": index, "elapsed_seconds": index,
                        "read_seconds": 0.01, "capture_started_seconds": index,
                        "capture_finished_seconds": index + 0.1, "readings": readings, "metrics": {}})
    result = {"schema": 1, "kind": "tyrell-observation-result", "status": status,
              "claims_release_approval": False, "hardware_acceptance": False,
              "identity": {"reference_proven": False},
              "tool_sha256": {name: records.sha(records.read_bytes(Path(records.__file__).with_name(name)))
                              for name in ("collect.py", "records.py")},
              "timing": {"settle_seconds": 0, "requested_duration_seconds": 2,
                         "elapsed_seconds_including_settle": 2.1, "observed_seconds": 2.1,
                         "observer_cpu_seconds": 0.01, "snapshot_child_cpu_seconds": 0.01, "missed_samples": 0}}
    if mutate:
        mutate(samples, result)
    plan_raw = records.encoded(plan)
    sample_raw = b"".join((json.dumps(sample, sort_keys=True) + "\n").encode() for sample in samples)
    result.update(plan_sha256=records.sha(plan_raw), samples_sha256=records.sha(sample_raw), sample_count=len(samples))
    (directory / "plan.json").write_bytes(plan_raw)
    (directory / "samples.jsonl").write_bytes(sample_raw)
    (directory / "result.json").write_bytes(records.encoded(result))
    return directory


class CounterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def analyse(self, mutate=None, status="completed", spi="spi9.1"):
        return counters.analyse(fixture(self.root / "bundle", mutate, status), spi)

    def change(self, samples, index, name, **fields):
        samples[index]["readings"]["/sys/bus/spi/devices/spi9.1/statistics/" + name].update(fields)

    def test_delta_and_conditional_bounds_are_distinct_from_optical_refresh(self):
        report = self.analyse()
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["totals"], {"bytes": 217600, "transfers": 4, "errors": 0, "timedout": 0})
        self.assertEqual(report["bus_byte_frame_equivalents"], 2)
        rate = report["rate_bounds"]["bytes_per_second"]
        self.assertAlmostEqual(rate["nominal"], 108800)
        self.assertLess(rate["lower"], rate["nominal"])
        self.assertGreater(rate["upper"], rate["nominal"])
        self.assertIsNone(report["physical_refresh_hz"])
        for key in ("hardware_qualified", "device_identity_proven", "claims_release_approval"):
            self.assertFalse(report[key])
        self.assertTrue(report["capture_window_bounds_verified"])
        self.assertEqual(set(report["source"]), {"plan_sha256", "result_sha256", "samples_sha256"})
        self.assertFalse(report["new_errors_or_timeouts_observed"])

    def test_alias_is_explicit_and_never_a_path(self):
        for value in ("/dev/spidev0.0", "../spi9.1", "spi9.1/extra", "spi01.1", "spi0.0\0", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                counters.analyse(self.root / "unopened", value)

    def test_wrong_selected_device_is_missing_evidence(self):
        report = self.analyse(spi="spi0.0")
        self.assertEqual(report["status"], "incomplete")
        self.assertIsNone(report["totals"])
        self.assertIsNone(report["rate_bounds"])

    def test_missing_denied_and_malformed_counters_never_become_zero(self):
        for label, update in (("missing", {"status": "missing", "raw": None}),
                              ("denied", {"status": "denied", "raw": None}),
                              ("negative", {"raw": "-1\n"}), ("oversized", {"raw": str(2**64) + "\n"}),
                              ("fractional", {"raw": "1.5\n"}), ("space", {"raw": " 1\n"})):
            directory = self.root / label
            def mutate(samples, result, update=update):
                reading = samples[1]["readings"]["/sys/bus/spi/devices/spi9.1/statistics/bytes"]
                reading.update(update)
                if reading["status"] != "ok":
                    reading.pop("raw", None)
            report = counters.analyse(fixture(directory, mutate), "spi9.1")
            with self.subTest(label=label):
                self.assertEqual(report["status"], "incomplete")
                self.assertIsNone(report["totals"])

    def test_counter_reset_is_not_unwrapped_or_averaged_away(self):
        report = self.analyse(lambda samples, result: self.change(samples, 2, "bytes", raw="1\n"))
        self.assertEqual(report["status"], "counter-reset")
        self.assertTrue(report["counter_reset_observed"])
        self.assertIsNone(report["totals"])
        self.assertIsNone(report["rate_bounds"])

    def test_new_error_or_timeout_delta_is_visible(self):
        report = self.analyse(lambda samples, result: self.change(samples, 2, "timedout", raw="1\n"))
        self.assertEqual(report["status"], "errors-observed")
        self.assertEqual(report["totals"]["timedout"], 1)
        self.assertTrue(report["new_errors_or_timeouts_observed"])
        self.assertFalse(report["hardware_qualified"])

    def test_device_path_drift_and_noncanonical_paths_are_unusable(self):
        for label, path in (("drift", "/sys/devices/different/spi9.1/statistics/bytes"),
                            ("escape", "/sys/devices/../private/spi9.1/statistics/bytes"),
                            ("other", "/sys/devices/fixture/spi9.2/statistics/bytes")):
            directory = self.root / label
            report = counters.analyse(fixture(directory, lambda samples, result, path=path:
                                              self.change(samples, 1, "bytes", resolved_path=path)), "spi9.1")
            with self.subTest(label=label):
                self.assertEqual(report["status"], "incomplete")
                self.assertIsNone(report["totals"])

    def test_overlapping_or_missing_capture_windows_cannot_bound_a_rate(self):
        for label, duration in (("overlap", 1.0), ("negative", -1), ("missing", None), ("huge", 1e308)):
            directory = self.root / label
            report = counters.analyse(fixture(directory, lambda samples, result, duration=duration:
                                              samples[0].update(capture_finished_seconds=duration)), "spi9.1")
            with self.subTest(label=label):
                self.assertEqual(report["status"], "incomplete")
                self.assertIsNone(report["rate_bounds"])

    def test_child_timing_and_legacy_sources_cannot_establish_capture_bounds(self):
        for label, mutate in (
                ("legacy", lambda samples, result: result["tool_sha256"].update({"collect.py": "a" * 64})),
                ("missing", lambda samples, result: [sample.pop("capture_finished_seconds") for sample in samples]),
                ("offset", lambda samples, result: samples[0].update(capture_started_seconds=0.01))):
            report = counters.analyse(fixture(self.root / label, mutate), "spi9.1")
            with self.subTest(label=label):
                self.assertEqual(report["status"], "incomplete")
                self.assertFalse(report["capture_window_bounds_verified"])
                self.assertIsNone(report["rate_bounds"])
                self.assertEqual(report["intervals"][0]["delta"]["bytes"], 108800)
                self.assertIsNone(report["intervals"][0]["rate_bounds"])
                if label != "offset":
                    self.assertTrue(all(row["rate_bounds"] is None for row in report["intervals"]))

    def test_capture_envelopes_must_fit_run_and_contain_child_duration(self):
        for label, mutate in (
                ("ends-after-run", lambda samples, result: samples[-1].update(capture_finished_seconds=3)),
                ("child-outside", lambda samples, result: samples[0].update(read_seconds=0.5))):
            report = counters.analyse(fixture(self.root / label, mutate), "spi9.1")
            with self.subTest(label=label):
                self.assertEqual(report["status"], "incomplete")
                self.assertFalse(report["capture_window_bounds_verified"])
                self.assertIsNone(report["rate_bounds"])

    def test_new_error_delta_remains_visible_when_timing_is_incomplete(self):
        def mutate(samples, result):
            self.change(samples, 2, "errors", raw="6\n")
            samples[2].pop("capture_finished_seconds")
        report = self.analyse(mutate)
        self.assertEqual(report["status"], "incomplete")
        self.assertTrue(report["new_errors_or_timeouts_observed"])
        self.assertIsNone(report["totals"])

    def test_interrupted_and_empty_runs_do_not_have_aggregate_measurements(self):
        for label, mutate in (("interrupted", None), ("empty", lambda samples, result: samples.clear())):
            report = counters.analyse(fixture(self.root / label, mutate, "interrupted"), "spi9.1")
            with self.subTest(label=label):
                self.assertEqual(report["status"], "incomplete")
                self.assertIsNone(report["totals"])

    def test_corrupt_source_is_rejected_before_analysis(self):
        directory = fixture(self.root / "bundle")
        (directory / "samples.jsonl").write_text("{}\n")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            counters.analyse(directory, "spi9.1")

    def test_analysis_reads_only_records_and_does_not_mutate_them(self):
        directory = fixture(self.root / "bundle")
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        original = records.read_bytes
        seen = []
        def recorded(path, *args, **kwargs):
            seen.append(Path(path))
            return original(path, *args, **kwargs)
        with patch.object(records, "read_bytes", side_effect=recorded):
            counters.analyse(directory, "spi9.1")
        self.assertTrue(seen)
        self.assertFalse(any(str(path).startswith(("/sys/", "/proc/", "/dev/")) for path in seen))
        self.assertEqual(before, {path.name: path.read_bytes() for path in directory.iterdir()})


if __name__ == "__main__":
    unittest.main()
