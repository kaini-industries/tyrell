"""CLI boundaries: reports are new files, snapshots never become live commands."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
from fixtures import fixture_snapshot

SPEC = importlib.util.spec_from_file_location("power_rehearsal_cli", HERE / "run.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)
GENERATION = "a" * 32


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshot = self.root / "snapshot.json"
        self.snapshot.write_bytes(cli.encode(fixture_snapshot()))
        self.report = self.root / "report.json"

    def invoke(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.main(list(args))

    def plan(self, *extra):
        return self.invoke("plan", "--snapshot", str(self.snapshot), "--preset", "combined",
                           "--generation", GENERATION, "--output", str(self.report), *extra)

    def test_unverified_template_remains_blocked(self):
        template = self.root / "template.json"
        self.assertEqual(self.invoke("template", "--output", str(template)), 0)
        self.snapshot.write_bytes(template.read_bytes())
        self.assertEqual(self.plan(), 2)
        result = json.loads(self.report.read_text())
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["live_authorized"])

    def test_synthetic_plan_is_deterministic_private_and_source_bound(self):
        before = self.snapshot.read_bytes()
        self.assertEqual(self.plan(), 0)
        first = self.report.read_bytes()
        self.assertEqual(self.report.stat().st_mode & 0o777, 0o600)
        result = json.loads(first)
        self.assertEqual(result["implementation_sha256"], cli.source_binding())
        self.assertEqual(result["input_files"]["snapshot"]["sha256"], hashlib.sha256(before).hexdigest())
        self.assertFalse(result["live_authorized"])
        self.assertFalse(result["hardware_qualified"])
        self.assertFalse(result["battery_qualified"])
        self.report.unlink()
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.report.read_bytes(), first)
        self.assertEqual(self.snapshot.read_bytes(), before)

    def test_existing_or_symlink_report_is_preserved(self):
        self.report.write_text("earlier result")
        self.assertEqual(self.plan(), 1)
        self.assertEqual(self.report.read_text(), "earlier result")
        self.report.unlink()
        victim = self.root / "victim"
        victim.write_text("preserve")
        self.report.symlink_to(victim)
        self.assertEqual(self.plan(), 1)
        self.assertEqual(victim.read_text(), "preserve")

    def test_duplicate_or_nonfinite_json_does_not_create_a_report(self):
        for raw in (b'{"schema":1,"schema":2}', b'{"value":NaN}', b"[" * 1100 + b"0" + b"]" * 1100):
            self.snapshot.write_bytes(raw)
            self.assertEqual(self.plan(), 1)
            self.assertFalse(self.report.exists())

    def test_nonregular_symlink_and_oversized_inputs_are_refused(self):
        original = self.root / "original"
        self.snapshot.rename(original)
        self.snapshot.symlink_to(original)
        self.assertEqual(self.plan(), 1)
        self.snapshot.unlink()
        os.mkfifo(self.snapshot)
        self.assertEqual(self.plan(), 1)
        self.snapshot.unlink()
        with self.snapshot.open("wb") as stream:
            stream.truncate(cli.INPUT_BYTES + 1)
        self.assertEqual(self.plan(), 1)
        self.assertFalse(self.report.exists())

    def test_rehearsal_records_events_without_opening_control_paths(self):
        before = self.snapshot.read_bytes()
        events = self.root / "events.json"
        # Every optimized intent and reversal is simulated by a separate step.
        script = [{"type": "apply", "generation": GENERATION}]
        script += [{"type": "step", "generation": GENERATION} for _ in range(3)]
        script += [{"type": "invalidate", "generation": GENERATION}]
        script += [{"type": "recover", "generation": GENERATION} for _ in range(3)]
        events.write_bytes(cli.encode(script))
        self.assertEqual(self.invoke("rehearse", "--snapshot", str(self.snapshot), "--preset", "combined",
                                     "--generation", GENERATION, "--events", str(events), "--output", str(self.report)), 0)
        result = json.loads(self.report.read_text())
        self.assertEqual(result["phase"], "baseline-observed")
        self.assertEqual(result["hardware_writes"], 0)
        self.assertFalse(result["baseline_recovery_certified"])
        self.assertFalse(result["live_authorized"])
        self.assertEqual(self.snapshot.read_bytes(), before)
        self.assertEqual(result["input_files"]["events"]["sha256"], hashlib.sha256(events.read_bytes()).hexdigest())

    def test_events_are_bounded_and_cannot_carry_commands(self):
        events = self.root / "events.json"
        for value in ([{"type": "apply", "generation": GENERATION}] * 65,
                      [{"type": "apply", "generation": GENERATION, "command": "touch /tmp/not-a-power-command"}]):
            events.write_bytes(cli.encode(value))
            self.assertEqual(self.invoke("rehearse", "--snapshot", str(self.snapshot), "--preset", "combined",
                                         "--generation", GENERATION, "--events", str(events), "--output", str(self.report)), 1)
            self.assertFalse(self.report.exists())


if __name__ == "__main__":
    unittest.main()
