"""False-green checks for scoped execution and candidate evidence."""
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import subprocess
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

CI = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location("ci_" + name, CI / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


runner = module("run")
python_tests = module("python_tests")
candidate = module("candidate")


class PythonEvidenceTests(unittest.TestCase):
    def test_empty_suite_is_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(python_tests.run(root, "test*.py", root / "result.json"))

    def test_required_skip_is_preserved_and_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test_skipping.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n"
                " @unittest.skip('required peripheral unavailable')\n def test_required(self): pass\n")
            self.assertFalse(python_tests.run(root, "test*.py", root / "result.json"))
            data = json.loads((root / "result.json").read_text())
            self.assertEqual(data["skipped"][0]["reason"], "required peripheral unavailable")
            self.assertEqual(data["status"], "failed")


class RunnerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        for key, value in (("user.name", "CI test"), ("user.email", "ci@example.invalid")):
            subprocess.run(["git", "-C", str(self.root), "config", key, value], check=True)
        (self.root / ".gitignore").write_text("build/\n.env\n")
        (self.root / "README.md").write_text("source\n")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "fixture"], check=True)

    def test_changed_source_cannot_inherit_successful_checks(self):
        def change(_):
            (self.root / "README.md").write_text("changed while checking\n")
            return [{"status": "passed"}]
        # Bind explicitly: source_identity's default root was set on import.
        identity = runner.source_identity
        with patch.object(runner, "ROOT", self.root), \
                patch.object(runner, "source_identity", side_effect=lambda: identity(self.root)), \
                patch.object(runner, "run_docs", side_effect=change):
            self.assertEqual(runner.main(["--suite", "docs"]), 1)
        data = json.loads((self.root / "build/ci/docs.json").read_text())
        self.assertIn("Source inputs changed", data["error"])
        self.assertFalse(data["hardware_qualified"])

    def test_wrong_revision_fails_before_checks(self):
        identity = runner.source_identity
        with patch.object(runner, "ROOT", self.root), \
                patch.object(runner, "source_identity", side_effect=lambda: identity(self.root)), \
                patch.object(runner, "run_docs") as check:
            self.assertEqual(runner.main(["--suite", "docs", "--expected-revision", "0" * 40]), 1)
            check.assert_not_called()

    def test_ignored_credentials_are_not_source_inputs(self):
        before = runner.source_identity(self.root)
        (self.root / ".env").write_text("not a source input")
        self.assertEqual(before, runner.source_identity(self.root))

    def test_nested_ui_previews_survive_export_without_build_tree(self):
        work = self.root / "build/ci/example"
        image = work / "chess/u1-previews/chess/promotion.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"representative preview")
        with patch.object(runner, "ROOT", self.root):
            _, bindings = runner.export_evidence(work, "chess")
        self.assertEqual(len(bindings), 1)
        shutil.rmtree(work)
        exported = self.root / "build/ci" / bindings[0]["artifact_path"]
        self.assertEqual(exported.read_bytes(), b"representative preview")
        self.assertEqual(runner.digest(exported), bindings[0]["sha256"])

    def test_lifecycle_report_survives_export_without_dependency_cache(self):
        work = self.root / "build/ci/example"
        report = work / "video/a2-lifecycle-blocking.json"
        report.parent.mkdir(parents=True)
        report.write_bytes(b'{"within_shell_100ms": false}')
        dependency = work / "dependencies/a2-unrelated.json"
        dependency.parent.mkdir()
        dependency.write_bytes(b'not suite evidence')
        with patch.object(runner, "ROOT", self.root):
            _, bindings = runner.export_evidence(work, "video")
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["original_path"], str(report.relative_to(self.root)))
        shutil.rmtree(work)
        exported = self.root / "build/ci" / bindings[0]["artifact_path"]
        self.assertEqual(exported.read_bytes(), b'{"within_shell_100ms": false}')
        self.assertEqual(runner.digest(exported), bindings[0]["sha256"])

    def test_separate_candidate_archive_and_extracted_trial_survive_export(self):
        work = self.root / "build/ci/example"
        package = work / "candidate-packages/tyrell-video-handoff-candidate_0.1.0-1_arm64.deb"
        package.parent.mkdir(parents=True)
        package.write_bytes(b"separate inactive candidate archive")
        report = work / "a2-d5-extracted-integration.json"
        report.write_bytes(b'{"status":"passed","live_admission":false}')
        with patch.object(runner, "ROOT", self.root):
            _, bindings = runner.export_evidence(work, "video")
        self.assertEqual(len(bindings), 2)
        shutil.rmtree(work)
        for binding in bindings:
            path = self.root / "build/ci" / binding["artifact_path"]
            self.assertEqual(runner.digest(path), binding["sha256"])

    def test_untested_candidate_binary_is_refused_before_packaging(self):
        work = self.root / "build/ci/example"
        build = work / "video"; build.mkdir(parents=True)
        (build / "video-draft2-app-peer").write_bytes(b"different executable")
        (build / "a2-d5-draft2-integration.json").write_text(json.dumps({"status": "passed", "native_peer_sha256": "a" * 64}))
        with patch.object(runner, "command") as command:
            with self.assertRaisesRegex(RuntimeError, "differs from the executed"):
                runner.run_video_candidate(build, work, "b" * 40)
            command.assert_not_called()

    def test_nested_ai_lifecycle_evidence_survives_without_dependency_cache(self):
        work = self.root / "build/ci/example"
        report = work / "chess-native-shell/engine/a3-ai-cancellation.json"
        report.parent.mkdir(parents=True)
        report.write_bytes(b'{"scope":"cooperative-cancellation-only"}')
        dependency = work / "dependencies/raylib/a3-unrelated.json"
        dependency.parent.mkdir(parents=True)
        dependency.write_bytes(b'not suite evidence')
        with patch.object(runner, "ROOT", self.root):
            _, bindings = runner.export_evidence(work, "chess-native")
        self.assertEqual(len(bindings), 1)
        shutil.rmtree(work)
        exported = self.root / "build/ci" / bindings[0]["artifact_path"]
        self.assertEqual(exported.read_bytes(), b'{"scope":"cooperative-cancellation-only"}')
        self.assertEqual(runner.digest(exported), bindings[0]["sha256"])


class RequiredCTestTests(unittest.TestCase):
    def test_missing_registered_group_fails_before_execution(self):
        response = subprocess.CompletedProcess([], 0, '{"tests":[{"name":"help"}]}')
        with patch.object(runner, "command", return_value=response) as command:
            with self.assertRaisesRegex(RuntimeError, "Required CTest groups omitted"):
                runner.ctest(Path("unused"), Path("unused"), {"playback"})
            self.assertEqual(command.call_count, 1)


class CandidatePreflightTests(unittest.TestCase):
    def fixture(self, root):
        # Real bundle verifier, synthetic input bytes, no extracted execution.
        (root / "image").mkdir()
        (root / "image/zero_pipeline.py").write_bytes((CI.parents[1] / "image/zero_pipeline.py").read_bytes())
        content = {"image/profiles/tyrell-zero.json": b"profile", "apt-repository/apt-lock.json": b"apt"}
        api = candidate.runpy.run_path(str(root / "image/zero_pipeline.py"))
        for name in api["LOCKS"]:
            content["locks/" + name] = b"apt" if name == "tyrell-zero-apt.json" else name.encode()
        identities = {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "mode": 0o644}
                      for name, data in content.items()}
        for name, data in content.items():
            if name.startswith(("image/", "locks/")):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        metadata = {"profile_id": "tyrell-zero", "build_mode": "zero-native", "files": identities,
                    "source_commit": "1" * 40, "source_dirty": False,
                    "capture_sha256": hashlib.sha256(json.dumps(identities, sort_keys=True).encode()).hexdigest(),
                    "profile_sha256": identities["image/profiles/tyrell-zero.json"]["sha256"],
                    "locks_sha256": {name: identities["locks/" + name]["sha256"] for name in api["LOCKS"]}}
        content["image-metadata.json"] = json.dumps(metadata).encode()
        bundle = root / "input.tar"
        with tarfile.open(bundle, "w") as archive:
            for name, data in content.items():
                member = tarfile.TarInfo(name)
                member.mode = 0o644
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        return bundle, hashlib.sha256(bundle.read_bytes()).hexdigest()

    def test_exact_capture_preflight_does_not_qualify_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, sha = self.fixture(root)
            with patch.object(candidate.subprocess, "check_output", side_effect=["1" * 40 + "\n", b""]):
                result = candidate.verify(bundle, sha, "1" * 40, root)
            self.assertEqual(result["status"], "passed")
            self.assertFalse(result["promotable"])
            self.assertFalse(result["image_built"])

    def test_self_consistent_archive_cannot_hide_checkout_recipe_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, sha = self.fixture(root)
            (root / "image/profiles/tyrell-zero.json").write_bytes(b"different recipe")
            with patch.object(candidate.subprocess, "check_output", side_effect=["1" * 40 + "\n", b""]):
                with self.assertRaisesRegex(ValueError, "differs from checkout"):
                    candidate.verify(bundle, sha, "1" * 40, root)

    def test_missing_bundle_cannot_pass_or_request_reconstruction(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "restore the exact retained bytes"):
                candidate.verify(Path(directory) / "missing.tar", "0" * 64, "1" * 40)

    def test_changed_bundle_is_rejected_before_recipe_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "input.tar"
            bundle.write_bytes(b"changed retained input")
            with patch.object(candidate.runpy, "run_path") as load:
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    candidate.verify(bundle, "0" * 64, "1" * 40)
                load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
