"""Package payload checks cannot be promoted into live device admission."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
from handoff_candidate import CandidateError, MANIFEST, PROFILE, PEER, inspect, validate_profile, require_live_admission
spec = importlib.util.spec_from_file_location("candidate_builder", PACKAGE / "build-package.py")
builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)

class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.root = self.base / "root"; self.root.mkdir()
        self.peer = self.base / "native-peer"; self.peer.write_bytes(b"fixed qualification bytes, never executed")
        self.source = builder.source_bindings()
        self.locks = {name: builder.digest(builder.ROOT / name) for name in ("locks/build-inputs.json", "locks/video-build-inputs.json")}
        self.facts = builder.assemble(self.root, self.peer, revision="a" * 40, epoch=123, source=self.source, locks=self.locks)

    def rewrite(self, change):
        path = self.root / MANIFEST; data = json.loads(path.read_text()); change(data)
        path.write_text(json.dumps(data))

    def test_staged_exact_payload_remains_non_authorizing(self):
        self.assertEqual(inspect(self.root), self.facts)
        self.assertFalse(self.facts["live_admission"])
        self.assertFalse(self.facts["hardware_qualified"])
        self.assertEqual(self.facts["physical_testing"], "pending")
        with self.assertRaises(CandidateError): require_live_admission(self.facts)
        with self.assertRaises(CandidateError): require_live_admission({"qualified": True, "live_admission": True})

    def test_profile_cannot_enable_live_or_relabel_physical_evidence(self):
        profile = json.loads((PACKAGE / "candidate-profile.json").read_text())
        for change in ({"live_admission": True}, {"physical_testing": "passed"}, {"automatic_activation": True},
                       {"profile": "live"}, {"schema": True}, {"required_device_evidence": []}):
            with self.subTest(change=change), self.assertRaises(CandidateError):
                validate_profile({**profile, **change})

    def test_changed_native_module_or_profile_is_detected(self):
        for name in (PEER, PROFILE, "usr/lib/tyrell-video-handoff/handoff_shell.py"):
            path = self.root / name; original = path.read_bytes(); path.write_bytes(original + b"changed")
            with self.subTest(name=name), self.assertRaises(CandidateError): inspect(self.root)
            path.write_bytes(original)

    def test_foreign_file_or_symlink_in_private_namespace_is_detected(self):
        extra = self.root / "usr/lib/tyrell-video-handoff/foreign.py"; extra.write_text("pass")
        with self.assertRaises(CandidateError): inspect(self.root)
        extra.unlink(); extra.symlink_to(self.peer)
        with self.assertRaises(CandidateError): inspect(self.root)

    def test_payload_symlink_mode_change_and_traversal_are_refused(self):
        target = self.root / PEER; target.unlink(); target.symlink_to(self.peer)
        with self.assertRaises(CandidateError): inspect(self.root)
        target.unlink(); target.write_bytes(self.peer.read_bytes()); target.chmod(0o700)
        with self.assertRaises(CandidateError): inspect(self.root)
        target.chmod(0o755)
        self.rewrite(lambda data: data["payload"].update({"usr/lib/tyrell-video-handoff/../../foreign": data["payload"][PEER]}))
        with self.assertRaises(CandidateError): inspect(self.root)

    def test_duplicate_fields_incomplete_dependency_or_false_source_are_refused(self):
        path = self.root / MANIFEST; original = path.read_text()
        path.write_text(original.replace('"schema": 1,', '"schema": 1, "schema": 1,', 1))
        with self.assertRaises(CandidateError): inspect(self.root)
        path.write_text(original)
        self.rewrite(lambda data: data["dependency_locks_sha256"].pop("locks/video-build-inputs.json"))
        with self.assertRaises(CandidateError): inspect(self.root)
        path.write_text(original)
        self.rewrite(lambda data: data["source_files_sha256"].update({"packages/tyrell-video-handoff/handoff_shell.py": "b" * 64}))
        with self.assertRaises(CandidateError): inspect(self.root)

if __name__ == "__main__": unittest.main()
