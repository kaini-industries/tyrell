"""H3 metadata is never a deployable BSP or a substitute for exact bytes."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("panel_experiment", Path(__file__).parents[1] / "experiment.py")
experiment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(experiment)


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audit = self.root / "audit.json"
        self.audit.write_text(json.dumps({"schema": 1, "kind": "panel-source-audit",
                                         "scope": "source-inspected-only",
                                         "upstream": {"commit": experiment.UPSTREAM},
                                         "grants_release_approval": False, "hardware_qualified": False}))
        patcher = patch.object(experiment, "AUDIT_PATH", self.audit)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.value = experiment.template()

    def candidate(self):
        self.value["candidate"] = {"build_source_commit": "c" * 40,
                                   "kernel_release": "6.18.60+rpt-rpi-v8", "artifacts": [],
                                   "license_notice": {"path": "sources/NOTICE", "sha256": "d" * 64, "bytes": 100},
                                   "source_export": {"path": "sources/source.tar.xz", "sha256": "e" * 64, "bytes": 200}}
        for role in sorted(experiment.ROLES):
            self.value["candidate"]["artifacts"].append({"role": role, "path": "artifacts/" + role,
                                                        "sha256": "f" * 64, "bytes": 1000,
                                                        "build_source_commit": "c" * 40,
                                                        "kernel_release": "6.18.60+rpt-rpi-v8"})
        return self.value["candidate"]

    def test_template_keeps_all_gates_open_and_cannot_deploy(self):
        result = experiment.inspect(self.value)
        self.assertEqual(result["status"], "inactive-experiment")
        self.assertEqual(result["scope"], "metadata-only")
        self.assertTrue(result["metadata_consistent"])
        self.assertFalse(result["candidate_metadata_complete"])
        self.assertFalse(result["deployment_allowed"])
        self.assertFalse(result["hardware_qualified"])
        self.assertFalse(result["grants_release_approval"])
        self.assertEqual(result["hardware_writes"], 0)
        self.assertEqual(len(self.value["gates"]["hardware"]), 15)
        self.assertEqual(len(self.value["gates"]["apps"]), 7)
        self.assertGreaterEqual(len(result["blocking_gates"]), 5)

    def test_filled_candidate_is_only_coherent_metadata_not_bytes_or_abi(self):
        self.candidate()
        before = copy.deepcopy(self.value)
        with patch.object(experiment, "read_reference", wraps=experiment.read_reference) as reader:
            result = experiment.inspect(self.value)
        self.assertEqual(self.value, before)
        self.assertTrue(result["candidate_metadata_complete"])
        self.assertFalse(result["deployment_allowed"])
        self.assertTrue(any("ELF/module/header ABI" in item for item in result["blocking_gates"]))
        self.assertTrue(all(call.args[0] in {self.audit,
                           experiment.ROOT / "research/tyrell-zero-image-validation.json",
                           experiment.ROOT / "image/contracts/tyrell-zero-hardware.json"}
                            for call in reader.call_args_list))
        self.assertFalse((self.root / "artifacts").exists())

    def test_mixed_build_or_kernel_identities_are_rejected(self):
        for key, value in (("build_source_commit", "b" * 40), ("kernel_release", "6.18.39+rpt-rpi-v8")):
            self.candidate()
            self.value["candidate"]["artifacts"][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "mixed"):
                experiment.inspect(self.value)

    def test_missing_or_duplicate_component_roles_are_rejected(self):
        candidate = self.candidate()
        candidate["artifacts"].pop()
        with self.assertRaisesRegex(ValueError, "four"):
            experiment.inspect(self.value)
        candidate = self.candidate()
        candidate["artifacts"][1]["role"] = candidate["artifacts"][0]["role"]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            experiment.inspect(self.value)

    def test_duplicate_artifact_and_source_paths_are_rejected(self):
        candidate = self.candidate()
        candidate["artifacts"][1]["path"] = candidate["artifacts"][0]["path"]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            experiment.inspect(self.value)
        candidate = self.candidate()
        candidate["source_export"]["path"] = candidate["license_notice"]["path"]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            experiment.inspect(self.value)

    def test_source_and_license_exports_must_be_recorded(self):
        for key in ("source_export", "license_notice"):
            candidate = self.candidate()
            candidate[key] = None
            with self.subTest(key=key), self.assertRaises(ValueError):
                experiment.inspect(self.value)

    def test_paths_cannot_escape_or_be_normalized_into_a_different_reference(self):
        for path in ("/dev/fb0", "../outside", "a/../b", "a//b", "./a", "a/", "C:/data", "a\\b", ""):
            candidate = self.candidate()
            candidate["artifacts"][0]["path"] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                experiment.inspect(self.value)

    def test_booleans_and_oversized_values_do_not_satisfy_integer_fields(self):
        for count in (True, 0, -1, 1.0, 2**40 + 1):
            candidate = self.candidate()
            candidate["artifacts"][0]["bytes"] = count
            with self.subTest(count=count), self.assertRaises(ValueError):
                experiment.inspect(self.value)
        self.value = experiment.template()
        self.value["schema"] = True
        with self.assertRaises(ValueError):
            experiment.inspect(self.value)

    def test_artifact_inventory_and_text_are_bounded(self):
        candidate = self.candidate()
        candidate["artifacts"] *= 5
        with self.assertRaisesRegex(ValueError, "limit"):
            experiment.inspect(self.value)
        self.value = experiment.template()
        self.value["notes"] = "x" * 4097
        with self.assertRaises(ValueError):
            experiment.inspect(self.value)

    def test_runtime_or_qualification_claims_are_not_accepted(self):
        for key in ("enabled", "grants_release_approval"):
            value = copy.deepcopy(self.value)
            value[key] = True
            with self.subTest(key=key), self.assertRaises(ValueError):
                experiment.inspect(value)
        for section in ("hardware", "apps", "scenarios"):
            value = copy.deepcopy(self.value)
            value["gates"][section][next(iter(value["gates"][section]))] = "passed"
            with self.subTest(section=section), self.assertRaises(ValueError):
                experiment.inspect(value)

    def test_stale_baseline_and_audit_bindings_are_rejected(self):
        for section, key in (("baseline", "raw_image_sha256"), ("baseline", "bsp_commit"),
                             ("source_audit", "sha256")):
            value = copy.deepcopy(self.value)
            value[section][key] = "0" * len(value[section][key])
            with self.subTest(key=key), self.assertRaises(ValueError):
                experiment.inspect(value)
        self.audit.write_text(self.audit.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "binding changed"):
            experiment.inspect(self.value)

    def test_upstream_audit_must_remain_the_pinned_unqualified_source_review(self):
        audit = json.loads(self.audit.read_text())
        audit["upstream"]["commit"] = "0" * 40
        self.audit.write_text(json.dumps(audit))
        with self.assertRaisesRegex(ValueError, "unsupported"):
            experiment.template()

    def test_unknown_fields_and_unrecorded_partial_candidate_are_rejected(self):
        self.value["candidate"]["build_source_commit"] = "c" * 40
        with self.assertRaises(ValueError):
            experiment.inspect(self.value)
        self.value = experiment.template()
        self.value["runtime_command"] = "not executable"
        with self.assertRaises(ValueError):
            experiment.inspect(self.value)

    def test_reference_duplicate_keys_and_nonfinite_json_are_rejected(self):
        for raw in ('{"schema":1,"schema":2}', '{"value":NaN}', '[]', 'null', '{"schema":true}'):
            self.audit.write_text(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                experiment.template()

    def test_committed_experiment_is_bound_to_the_actual_source_review(self):
        with patch.object(experiment, "AUDIT_PATH", experiment.ROOT / "research/panel-source-audit.json"):
            _, committed = experiment.read_reference(experiment.ROOT / "research/panel-experiment-template.json")
            result = experiment.inspect(committed)
        self.assertEqual(result["status"], "inactive-experiment")
        self.assertFalse(result["candidate_metadata_complete"])
        self.assertFalse(result["deployment_allowed"])


if __name__ == "__main__":
    unittest.main()
