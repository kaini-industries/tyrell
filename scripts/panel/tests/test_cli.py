"""Inactive definitions and bounded reports cannot become driver operations."""
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
SPEC = importlib.util.spec_from_file_location("panel_cli", HERE / "run.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.definition = self.root / "definition.json"
        self.output = self.root / "report.json"

    def invoke(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.main(list(args))

    def check(self):
        return self.invoke("check", "--definition", str(self.definition), "--output", str(self.output))

    def test_template_and_check_preserve_non_deployable_boundary(self):
        self.assertEqual(self.invoke("template", "--output", str(self.definition)), 0)
        before = self.definition.read_bytes()
        self.assertEqual(self.check(), 2)
        result = json.loads(self.output.read_text())
        self.assertEqual(result["status"], "inactive-experiment")
        self.assertFalse(result["deployment_allowed"])
        self.assertEqual(result["definition_sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(result["implementation_sha256"], cli.source_binding())
        self.assertEqual(self.definition.read_bytes(), before)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)

    def test_existing_outputs_and_symlinks_never_replace_earlier_results(self):
        self.invoke("template", "--output", str(self.definition))
        self.output.write_text("retain")
        self.assertEqual(self.check(), 1)
        self.assertEqual(self.output.read_text(), "retain")
        self.output.unlink()
        victim = self.root / "victim"
        victim.write_text("preserve")
        self.output.symlink_to(victim)
        self.assertEqual(self.check(), 1)
        self.assertEqual(victim.read_text(), "preserve")

    def test_malformed_bounded_inputs_fail_without_a_report(self):
        for raw in (b'{"schema":1,"schema":2}', b'{"value":NaN}',
                    b"[" * 1100 + b"0" + b"]" * 1100):
            self.definition.write_bytes(raw)
            self.assertEqual(self.check(), 1)
            self.assertFalse(self.output.exists())
        self.definition.unlink()
        os.mkfifo(self.definition)
        self.assertEqual(self.check(), 1)
        self.definition.unlink()
        with self.definition.open("wb") as stream:
            stream.truncate(cli.INPUT_BYTES + 1)
        self.assertEqual(self.check(), 1)
        self.assertFalse(self.output.exists())

    def test_activation_flags_cannot_turn_a_definition_into_a_build(self):
        value = cli.experiment.template()
        value["enabled"] = True
        self.definition.write_text(json.dumps(value))
        self.assertEqual(self.check(), 1)
        self.assertFalse(self.output.exists())

    def test_spi_argument_cannot_be_a_device_path_or_command(self):
        for name in ("/dev/fb_lcd", "../spi0.0", "spi0.0; reboot"):
            self.assertEqual(self.invoke("counters", "--bundle", str(self.root), "--spi", name,
                                         "--output", str(self.output)), 1)
            self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
