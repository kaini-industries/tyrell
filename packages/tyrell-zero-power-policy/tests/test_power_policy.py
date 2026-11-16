import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
import power_policy as policy

CONTRACT = Path(os.environ.get("TYRELL_POWER_CONTRACT", str(PACKAGE.parents[1] / "image/contracts/tyrell-zero-power.json")))


def write(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else value.encode())
    return path


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class SelectionTests(unittest.TestCase):
    def test_exact_four_initial_presets_and_contract(self):
        contract = policy.validate_contract(json.loads(CONTRACT.read_text()))
        for name in policy.PRESETS:
            value = policy.Selection.parse({"schema": 1, "preset": name})
            self.assertEqual(value.targets(), contract["presets"][name])
            self.assertEqual(value.targets()["ethernet_autosuspend_delay_ms"], 2000)

    def test_unsupported_and_ambiguous_selections_rejected(self):
        for value in [[], {}, {"schema": True, "preset": "baseline"},
                      {"schema": 1, "preset": "../cpu"},
                      {"schema": 1, "preset": "cpu", "factor": 5},
                      {"schema": 2, "preset": "cpu"}, {"schema": 1, "preset": []}]:
            with self.subTest(value=value), self.assertRaises(policy.PolicyError):
                policy.Selection.parse(value)

    def test_contract_drift_and_unsafe_activation_rejected(self):
        original = json.loads(CONTRACT.read_text())
        cases = []
        x = copy.deepcopy(original); x["activation_implemented"] = True; cases.append(x)
        x = copy.deepcopy(original); x["presets"]["cpu"]["ethernet_autosuspend_delay_ms"] = -1; cases.append(x)
        x = copy.deepcopy(original); x["baseline_controls"]["ondemand_tunables"]["io_is_busy"] = False; cases.append(x)
        x = copy.deepcopy(original); x["safety"]["install_sysfs_writes"] = True; cases.append(x)
        x = copy.deepcopy(original); x["release_gates"]["usb_only_promotion_allowed"] = True; cases.append(x)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(policy.PolicyError):
                policy.validate_contract(value)

    def test_cli_candidate_apply_is_closed_without_writes(self):
        for name in ["ethernet", "cpu", "combined"]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write(root, policy.CONFIG, json.dumps({"schema": 1, "preset": name}))
                before = tree(root)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    result = policy.main(["--root", str(root), "apply"])
                self.assertEqual(result, 78)
                self.assertFalse(json.loads(out.getvalue())["optimized_activation_implemented"])
                self.assertEqual(tree(root), before)
                self.assertFalse((root / "run").exists())

    def test_baseline_apply_is_explicit_noop_not_certification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root, policy.CONFIG, '{"schema":1,"preset":"baseline"}')
            before = tree(root)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(policy.main(["--root", str(root), "apply"]), 0)
            data = json.loads(out.getvalue())
            self.assertEqual(data["status"], "baseline-no-op")
            self.assertFalse(data["baseline_recovery_certified"])
            self.assertEqual(data["hardware_writes"], 0)
            self.assertEqual(tree(root), before)

    def test_inspection_of_unknown_identity_stays_unqualified(self):
        with tempfile.TemporaryDirectory() as directory:
            data = policy.inspect(Path(directory), policy.Selection("cpu"))
            self.assertFalse(data["read_only_identity_checks"]["kernel"])
            self.assertFalse(data["read_only_identity_checks"]["image_identity"])
            self.assertFalse(data["read_only_identity_checks"]["power_contract"])
            self.assertFalse(data["optimized_activation_implemented"])
            self.assertFalse(data["hardware_qualified"])
            self.assertFalse(data["battery_qualified"])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_root_paths_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"; outside.write_text("not config")
            (root / "link").symlink_to(outside)
            for name in ["/etc/passwd", "../outside", "link"]:
                with self.subTest(name=name), self.assertRaises(policy.PolicyError):
                    policy.read_bytes(root, name)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = policy.PolicyStore(self.root)
        self.generation = "a" * 32
        self.override = policy.PolicyStore.marker(self.generation) + b"w /sys/example - - - - 1\n"

    def tearDown(self):
        self.temp.cleanup()

    def recorded(self):
        path = write(self.root, policy.OVERRIDE, self.override)
        self.store.record_generation(policy.Selection("cpu"), self.generation, policy.digest(self.override))
        return path

    def test_owned_override_invalidated_before_removal(self):
        path = self.recorded()
        result = self.store.invalidate(self.generation)
        self.assertFalse(path.exists())
        self.assertEqual(result["phase"], "recovery-required")
        self.assertFalse(result["baseline_recovery_certified"])
        self.assertEqual(result["hardware_writes"], 0)
        self.assertFalse((self.root / "sys").exists())
        state = json.loads((self.root / policy.STATE_DIR / "state.json").read_text())
        self.assertIsNone(state["override_sha256"])

    def test_wrong_generation_cannot_remove_current_override(self):
        path = self.recorded()
        with self.assertRaises(policy.PolicyError):
            self.store.invalidate("b" * 32)
        self.assertEqual(path.read_bytes(), self.override)
        self.assertEqual(self.store._state()["phase"], "unqualified")

    def test_changed_or_administrator_override_preserved(self):
        path = self.recorded()
        path.write_text("# administrator configuration\n")
        with self.assertRaises(policy.PolicyError):
            self.store.invalidate(self.generation)
        self.assertEqual(path.read_text(), "# administrator configuration\n")
        self.assertEqual(self.store._state()["phase"], "invalidated")

    def test_unregistered_override_never_removed(self):
        path = write(self.root, policy.OVERRIDE, "# unrelated file\n")
        self.store.record_generation(policy.Selection("cpu"), self.generation)
        with self.assertRaises(policy.PolicyError):
            self.store.invalidate(self.generation)
        self.assertEqual(path.read_text(), "# unrelated file\n")

    def test_symlink_override_cannot_be_registered(self):
        victim = write(self.root, "victim", self.override)
        path = self.root / policy.OVERRIDE; path.parent.mkdir(parents=True)
        path.symlink_to(victim)
        with self.assertRaises(policy.PolicyError):
            self.store.record_generation(policy.Selection("cpu"), self.generation, policy.digest(self.override))
        self.assertEqual(victim.read_bytes(), self.override)

    def test_symlink_runtime_directory_cannot_escape(self):
        outside = self.root / "outside"; outside.mkdir()
        (self.root / "run").symlink_to(outside)
        with self.assertRaises(policy.PolicyError):
            self.store.record_generation(policy.Selection("cpu"), self.generation)
        self.assertEqual(list(outside.iterdir()), [])

    def test_concurrent_supported_reader_holds_generation_lock(self):
        path = self.recorded()
        code = "import sys; from pathlib import Path; from power_policy import PolicyStore; s=PolicyStore(Path(sys.argv[1]));\nwith s.lock():\n print('locked',flush=True); sys.stdin.read()"
        env = dict(os.environ, PYTHONPATH=str(PACKAGE))
        child = subprocess.Popen([sys.executable, "-c", code, str(self.root)],
                                 env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "locked")
            with self.assertRaises(policy.BusyError):
                self.store.invalidate(self.generation)
            self.assertTrue(path.exists())
            child.communicate("release", timeout=5)
            self.assertEqual(child.returncode, 0)
            self.store.invalidate(self.generation)
            self.assertFalse(path.exists())
        finally:
            if child.poll() is None:
                child.kill(); child.communicate()

    def test_cpu_readback_alone_never_certifies_full_recovery(self):
        self.recorded()
        for name, value in policy.BASELINE_TUNABLES.items():
            write(self.root, "sys/devices/system/cpu/cpufreq/ondemand/" + name, value)
        for name, value in {"scaling_governor": "ondemand", "scaling_min_freq": "600000", "scaling_max_freq": "1000000"}.items():
            write(self.root, "sys/devices/system/cpu/cpufreq/policy0/" + name, value)
        result = self.store.invalidate(self.generation)
        self.assertEqual(result["phase"], "cpu-baseline-observed")
        self.assertFalse(result["baseline_recovery_certified"])


if __name__ == "__main__":
    unittest.main()
