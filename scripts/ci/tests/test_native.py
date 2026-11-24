"""Trust-boundary tests for native checks, runnable without an ARM64 worker."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "tyrell_ci_native", Path(__file__).resolve().parents[1] / "native.py")
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


def ctest_log(names):
    lines = [f"{i}/{len(names)} Test #{i}: {name} ........ Passed 0.01 sec"
             for i, name in enumerate(names, 1)]
    lines.append(f"100% tests passed, 0 tests failed out of {len(names)}")
    return "\n".join(lines) + "\n"


class NativeGuards(unittest.TestCase):
    def test_wrong_host_fails_before_running_any_native_command(self):
        with patch.object(native.platform, "system", return_value="Darwin"), \
                patch.object(native.subprocess, "check_output") as command:
            with self.assertRaisesRegex(RuntimeError, "Linux ARM64"):
                native.preflight()
            command.assert_not_called()

    def test_missing_native_tool_is_an_error_not_a_skip(self):
        with patch.object(native.platform, "system", return_value="Linux"), \
                patch.object(native.platform, "machine", return_value="aarch64"), \
                patch.object(native.shutil, "which", side_effect=lambda n: None if n == "dpkg-divert" else "/bin/" + n), \
                patch.object(native.subprocess, "check_output") as command:
            with self.assertRaisesRegex(RuntimeError, "dpkg-divert"):
                native.preflight()
            command.assert_not_called()

    def test_other_linux_distribution_cannot_impersonate_debian_check(self):
        with patch.object(native.platform, "system", return_value="Linux"), \
                patch.object(native.platform, "machine", return_value="aarch64"), \
                patch.object(native.shutil, "which", return_value="/tool"), \
                patch.object(native.Path, "read_text", return_value='ID=ubuntu\nVERSION_ID="24.04"\n'), \
                patch.object(native.subprocess, "check_output") as command:
            with self.assertRaisesRegex(RuntimeError, "Debian 13"):
                native.preflight()
            command.assert_not_called()

    def test_stale_and_symlinked_outputs_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            work = root / "build/ci/old"
            work.mkdir(parents=True)
            sentinel = work / "native-foundation.json"
            sentinel.write_text("accepted old evidence")
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                native.fresh_work(root, work)
            self.assertEqual(sentinel.read_text(), "accepted old evidence")
            (root / "build/ci/link").symlink_to(root / "outside")
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                native.fresh_work(root, root / "build/ci/link/new")
            with self.assertRaisesRegex(RuntimeError, "below build/ci"):
                native.fresh_work(root, root / "packages/output")

    def test_outer_ctest_success_cannot_hide_nested_python_skip(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            spec = native.PACKAGES["tyrell-zero-session"]
            (output / spec["summary"]).write_text(ctest_log(spec["tests"]))
            (output / spec["detail"]).write_text("Ran 40 tests in 1.0s\nOK (skipped=1)\n")
            with self.assertRaisesRegex(RuntimeError, "skipped"):
                native.require_test_evidence(output, "tyrell-zero-session")

    def test_missing_registered_ctest_is_not_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            spec = native.PACKAGES["tyrell-zero-store"]
            (output / spec["summary"]).write_text(ctest_log(("store-routing",)))
            (output / spec["detail"]).write_text("no guard test ran")
            with self.assertRaisesRegex(RuntimeError, "store-guard"):
                native.require_test_evidence(output, "tyrell-zero-store")

    def test_no_tests_found_and_all_skipped_unittest_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            for text in ("No tests were found!!!\n", "Ran 5 tests in 0.01s\nOK (skipped=5)\n"):
                (output / "tests.txt").write_text(text)
                with self.subTest(text=text), self.assertRaises(RuntimeError):
                    native.require_test_evidence(output, "tyrell-zero-power-policy")


class NativeOrchestration(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.work = self.root / "build/ci/native-test"
        (self.root / "builder").mkdir()
        (self.root / "builder/zero-guest.py").write_text("# captured helper\n")
        (self.root / "image/contracts").mkdir(parents=True)
        (self.root / "image/contracts/tyrell-zero-power.json").write_text("{}\n")
        (self.root / "locks").mkdir()
        (self.root / "locks/build-inputs.json").write_text(json.dumps({"source_date_epoch": 1}))
        for package in native.PACKAGES:
            directory = self.root / "packages" / package
            directory.mkdir(parents=True)
            (directory / "build-package.py").write_text("# build fixture\n")
        self.operations = []
        self.states = {name: "" for name in native.PACKAGES}
        self.api = {"host_packages": lambda _: self.states.copy(),
                    "inspect_package": self.inspect, "isolated_dpkg": self.install}
        self.real_sha = native.sha

    def inspect(self, deb, package, version):
        self.operations.append(("inspect", package))
        return {"Package": package, "Version": version, "Architecture": "arm64"}

    def install(self, deb, package, log):
        self.operations.append(("isolated", package))
        log.write("fixture empty-root lifecycle\n")

    def fake_build(self, root, target, package, env):
        target.mkdir()
        (target / (package + ".deb")).write_bytes(b"fixture package")
        spec = native.PACKAGES[package]
        if spec["tests"]:
            (target / spec["summary"]).write_text(ctest_log(spec["tests"]))
            (target / spec["detail"]).write_text("Ran 20 tests in 0.1s\nOK\n")
        else:
            (target / spec["summary"]).write_text("Ran 20 tests in 0.1s\nOK\n")

    def run_check(self):
        def digest(path):
            return "worker-status" if Path(path) == Path("/var/lib/dpkg/status") else self.real_sha(path)

        with patch.object(native, "preflight"), \
                patch.object(native.runpy, "run_path", return_value=self.api), \
                patch.object(native, "build_package", side_effect=self.fake_build), \
                patch.object(native, "sha", side_effect=digest), \
                patch.object(native.subprocess, "run"):
            return native.run_zero_native(self.root, self.work)

    def test_archive_inspection_precedes_every_disposable_install(self):
        report = self.run_check()
        self.assertEqual(self.operations, [operation for package in native.PACKAGES
                         for operation in (("inspect", package), ("isolated", package))])
        self.assertFalse(report["image_qualification"])
        self.assertFalse(report["hardware_qualification"])
        self.assertTrue(all(not p["dependency_closure_verified"] for p in report["packages"]))
        self.assertTrue((self.work / "native-foundation.json").is_file())

    def test_unsafe_package_never_reaches_dpkg_and_leaves_no_pass_report(self):
        def refuse(*args):
            raise RuntimeError("unsafe archive contains maintainer activation")

        self.api["inspect_package"] = refuse
        with self.assertRaisesRegex(RuntimeError, "maintainer activation"):
            self.run_check()
        self.assertEqual(self.operations, [])
        self.assertFalse((self.work / "native-foundation.json").exists())

    def test_live_package_state_change_fails_even_if_build_commands_succeed(self):
        original_install = self.api["isolated_dpkg"]

        def altered_host(*args):
            original_install(*args)
            self.states["tyrell-zero-session"] = "installed 0.3.0-1"

        self.api["isolated_dpkg"] = altered_host
        with self.assertRaisesRegex(RuntimeError, "Live worker dpkg state changed"):
            self.run_check()
        self.assertFalse((self.work / "native-foundation.json").exists())

    def test_already_installed_foundation_is_rejected_before_output_creation(self):
        self.states["tyrell-zero-session"] = "installed 0.3.0-1"
        with self.assertRaisesRegex(RuntimeError, "already installed"):
            self.run_check()
        self.assertFalse(self.work.exists())

    def test_changed_input_during_build_cannot_receive_pass_receipt(self):
        original_install = self.api["isolated_dpkg"]

        def changed_source(*args):
            original_install(*args)
            (self.root / "packages/tyrell-zero-session/build-package.py").write_text("changed\n")

        self.api["isolated_dpkg"] = changed_source
        with self.assertRaisesRegex(RuntimeError, "sources changed"):
            self.run_check()
        self.assertFalse((self.work / "native-foundation.json").exists())


if __name__ == "__main__":
    unittest.main()
