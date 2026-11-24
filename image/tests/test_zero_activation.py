"""Offline activation boundaries, durable publication, and real Store snapshots."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import socket
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("test_zero_activation_module", REPO / "image/zero_activation.py")
activation = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = activation
spec.loader.exec_module(activation)


class ActivationTests(unittest.TestCase):
    def setUp(self):
        previous_umask = os.umask(0o022)
        self.addCleanup(os.umask, previous_umask)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "root"
        self.root.mkdir()
        self.owner = os.getuid()
        self.packages = {"applaunch": "0.6.105", "greetd": "0.10.3-4",
                         "tyrell-zero-session": "0.3.0-1", "tyrell-zero-store": "0.1.0-1",
                         "tyrell-zero-power-policy": "0.1.0-1", "tyrell-status": "0.1.0-1"}
        self.put("/var/lib/dpkg/status", "fixture Debian database\n")
        self.put("/var/lib/dpkg/diversions", activation.STORE.PUBLIC + "\n" + activation.STORE.ORIGINAL + "\ntyrell-zero-store\n")
        self.put("/etc/passwd", "root:x:0:0:root:/root:/usr/sbin/nologin\n_greetd:x:990:990::/var/lib/greetd:/usr/sbin/nologin\n")
        self.put("/etc/group", "root:x:0:\nvideo:x:44:native\ninput:x:104:\n")
        self.put("/etc/gshadow", "root:*::\nvideo:*::native\ninput:*::\n", 0o640)
        self.put("/etc/pam.d/greetd", "@include common-session\n")
        for name in ("common-session", "common-auth", "common-account", "common-password"):
            self.put("/etc/pam.d/" + name, "fixture PAM configuration\n")
        self.put("/etc/apt/sources.list", "deb https://example.invalid/debian trixie main\n")
        self.put("/etc/tyrell-zero/power-policy.json", '{"schema":1,"preset":"baseline"}\n')
        self.put("/usr/share/tyrell-zero/power-policy/contract.json", '{"qualification":"not-tested-for-Zero"}\n')
        self.put("/usr/share/tyrell-zero/profile/logind.conf", "[Login]\nNAutoVTs=0\nReserveVT=0\n")
        for unit in activation.SERVICE_FILES:
            self.put("/usr/lib/systemd/system/" + unit, "[Unit]\nDescription=Fixture unit\n")
        self.put("/usr/lib/systemd/system/multi-user.target", "[Unit]\nDescription=Multi user\n")
        assets = {}
        for role, name in activation.STATE.ASSETS.items():
            self.put(name, "fixture-" + role, 0o755)
            assets[role] = dict(path=name, sha256=self.sha(name))
        for obj, attribute, value in ((activation.STATE, "LAUNCHER_SHA256", assets["launcher"]["sha256"]),
                                      (activation.STATE, "STORE_SHA256", assets["store_original"]["sha256"]),
                                      (activation.STORE, "VENDOR_SHA", assets["store_original"]["sha256"])):
            patched = patch.object(obj, attribute, value)
            patched.start();self.addCleanup(patched.stop)
        runtime = {}
        for name in activation.STATE.REQUIRED_RUNTIME_FILES:
            if name == activation.STORE.CATALOG:
                content = json.dumps({"packages": [{"package": "tyrell-status"}]})
            else:
                content = "fixture-runtime-" + name
            self.put(name, content, 0o755 if name.startswith("/usr/libexec/") else 0o644)
            runtime[name] = self.sha(name)
        self.put("/usr/share/APPLaunch/share/font/native.ttf", "font")
        self.put("/usr/lib/libfixture.so.1", "library")
        (self.root / "usr/lib/libfixture.so").symlink_to("libfixture.so.1")
        self.source = dict(schema=1, profile="tyrell-zero", workflow="native-setup-v1", status="passed",
                           scope="pre-image-integration", physical_hardware_qualified=False,
                           suites={"session": "passed", "wizard": "passed", "store": "passed"},
                           assets=assets, runtime_files=runtime)
        self.source_path = self.base / "qualification.json"
        self.source_path.write_text(json.dumps(self.source, indent=2) + "\n")
        self.compat = dict(schema=1, profile="tyrell-zero", complete=True, assets=assets,
                           runtime_files=runtime, required_packages=self.packages.copy(),
                           resource_files={"/usr/share/APPLaunch/share/font/native.ttf": self.sha("/usr/share/APPLaunch/share/font/native.ttf")},
                           configuration_files={"/etc/pam.d/" + name: self.sha("/etc/pam.d/" + name)
                                                for name in ("greetd", "common-session", "common-auth", "common-account", "common-password")},
                           library_files={"/usr/lib/libfixture.so.1": {"sha256": self.sha("/usr/lib/libfixture.so.1")},
                                          "/usr/lib/libfixture.so": {"symlink": "libfixture.so.1"}},
                           source_qualification_sha256=hashlib.sha256(self.source_path.read_bytes()).hexdigest(),
                           qualification={"scope": "disposable-offline-runtime-root", "assembled_image_qualified": False,
                                          "hardware_qualified": False})
        self.compat_path = self.base / "compat.json"
        self.save_compat()
        self.metadata = dict(profile_id="tyrell-zero", stages=["stage0", "stage1", "stage2"],
                             profile_sha256="1" * 64, locks_sha256={"tyrell-zero-native-compat.json": hashlib.sha256(self.compat_path.read_bytes()).hexdigest()})
        self.put("/etc/tyrell-image.json", json.dumps(self.metadata))
        self.put(activation.STATE.INTEGRATION, '{"complete":false}\n')

    def put(self, name, value, mode=0o644):
        path = self.root / name.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        path.chmod(mode)
        return path

    def sha(self, name):
        return hashlib.sha256((self.root / name.lstrip("/")).read_bytes()).hexdigest()

    def save_compat(self):
        self.compat_path.write_text(json.dumps(self.compat, sort_keys=True) + "\n")

    def execute(self, root, *args):
        self.assertEqual(root, self.root)
        if "--listpackage" in args: return "tyrell-zero-store"
        if "--truename" in args: return activation.STORE.ORIGINAL
        self.assertEqual(args[0], "/usr/bin/dpkg-query")
        return "\n".join(name + "\tii \t" + version for name, version in sorted(self.packages.items()))

    def activate(self):
        return activation.activate(self.root, self.source_path, self.compat_path,
                                   owner=self.owner, execute=self.execute)

    def assert_unpublished(self):
        self.assertFalse((self.root / activation.RECEIPT.lstrip("/")).exists())

    def test_qualified_root_activates_and_store_baseline_precedes_session_receipts(self):
        result = self.activate()
        self.assertTrue(result["complete"])
        self.assertFalse(result["physical_hardware_qualified"])
        self.assertEqual(result["locks_sha256"], self.metadata["locks_sha256"])
        for unit in (*activation.SYSTEM_MASKS, "lightdm.service", "display-manager.service"):
            self.assertEqual(os.readlink(self.root / "etc/systemd/system" / unit), "/dev/null")
        baseline = json.loads((self.root / activation.STORE.INVARIANTS.lstrip("/")).read_text())
        self.assertNotIn(activation.STATE.INTEGRATION, baseline["files"])
        self.assertNotIn(activation.STATE.QUALIFICATION, baseline["files"])
        self.assertNotIn(activation.RECEIPT, baseline["files"])
        self.assertIn("/etc/apt/sources.list", baseline["files"])
        self.assertNotIn("/etc/group", baseline["files"])
        self.assertNotIn("tyrell-status", baseline["packages"])
        self.assertEqual(activation.STATE.integration(activation.STATE.Files(self.root, self.owner)),
                         result["session_integration_sha256"])
        self.assertEqual((self.root / activation.SOURCE_RECEIPT.lstrip("/")).read_bytes(), self.source_path.read_bytes())
        self.assertEqual((self.root / "var/lib/LaunchWizard/run-oobe").stat().st_size, 0)
        self.assertIn("video:x:44:_greetd,native", (self.root / "etc/group").read_text())
        self.assertEqual((self.root / "etc/gshadow").stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.activate(), result)

    def test_mutated_dependency_fails_before_any_activation_write(self):
        self.put("/usr/lib/libfixture.so.1", "tampered")
        with self.assertRaisesRegex(activation.ActivationError, "dependency identity"):
            self.activate()
        self.assert_unpublished()
        self.assertFalse((self.root / "etc/systemd/system/default.target").exists())

    def test_changed_helper_cannot_hide_behind_unchanged_wrapper(self):
        self.put("/usr/lib/tyrell-zero/session/supervisor.py", "tampered")
        with self.assertRaisesRegex(activation.ActivationError, "dependency identity"):
            self.activate()
        self.assert_unpublished()

    def test_unqualified_receipt_is_not_promoted(self):
        self.source["suites"]["wizard"] = "not-run"
        self.source_path.write_text(json.dumps(self.source))
        self.compat["source_qualification_sha256"] = hashlib.sha256(self.source_path.read_bytes()).hexdigest()
        self.save_compat()
        with self.assertRaisesRegex(activation.ActivationError, "source qualification"):
            self.activate()
        self.assert_unpublished()

    def test_legacy_wizard_checkpoint_requires_explicit_recovery(self):
        self.put("/var/lib/LaunchWizard/apply.checkpoint", "4\n")
        with self.assertRaisesRegex(activation.ActivationError, "already provisioned"):
            self.activate()
        with self.assertRaisesRegex(activation.ActivationError, "fresh unprovisioned"):
            activation.prepare_runtime(self.root, owner=self.owner)
        self.assert_unpublished()

    def test_changed_source_receipt_hash_fails(self):
        self.source_path.write_text(self.source_path.read_text() + "\n")
        with self.assertRaisesRegex(activation.ActivationError, "receipt identity"):
            self.activate()
        self.assert_unpublished()

    def test_runtime_mount_or_live_state_is_rejected(self):
        with patch.object(activation, "mountpoints", return_value=[self.root / "dev/pts"]):
            with self.assertRaisesRegex(activation.ActivationError, "runtime mount"):
                self.activate()
        self.put("/run/session-state", "live")
        with self.assertRaisesRegex(activation.ActivationError, "live state"):
            self.activate()
        self.assert_unpublished()

    def test_nested_bind_mount_cannot_redirect_writes_to_host(self):
        with patch.object(activation, "mountpoints", return_value=[self.root / "etc"]):
            with self.assertRaisesRegex(activation.ActivationError, "nested image mount"):
                self.activate()
        self.assert_unpublished()

    def test_static_runtime_preparation_never_follows_links(self):
        outside = self.base / "outside";outside.write_text("preserve")
        self.put("/dev/static", "debootstrap placeholder")
        (self.root / "dev/fd").symlink_to(outside)
        (self.root / "proc/empty").mkdir(parents=True)
        report = activation.prepare_runtime(self.root, owner=self.owner)
        self.assertEqual(report["removed_entries"], 3)
        self.assertEqual(outside.read_text(), "preserve")
        self.assertEqual(list((self.root / "dev").iterdir()), [])
        self.assertTrue(self.activate()["complete"])

    def test_runtime_preparation_rejects_socket_without_partial_deletion(self):
        first = self.put("/dev/static", "preserve on failed preflight")
        path = self.root / "run/live.sock";path.parent.mkdir(exist_ok=True)
        endpoint = socket.socket(socket.AF_UNIX)
        self.addCleanup(endpoint.close)
        try: endpoint.bind(str(path))
        except PermissionError: self.skipTest("host sandbox does not allow a temporary Unix socket")
        with self.assertRaisesRegex(activation.ActivationError, "runtime socket"):
            activation.prepare_runtime(self.root, owner=self.owner)
        self.assertTrue(first.exists())

    def test_runtime_preparation_refuses_mount_and_activated_root(self):
        self.put("/dev/static", "preserve")
        with patch.object(activation, "mountpoints", return_value=[self.root / "dev"]):
            with self.assertRaises(activation.ActivationError):
                activation.prepare_runtime(self.root, owner=self.owner)
        (self.root / "dev/static").unlink()
        self.activate()
        with self.assertRaisesRegex(activation.ActivationError, "fresh unprovisioned"):
            activation.prepare_runtime(self.root, owner=self.owner)

    def test_static_network_runtime_directories_require_exact_account_and_empty_tree(self):
        passwd = self.root / "etc/passwd"
        passwd.write_text(passwd.read_text() + "systemd-network:x:998:998::/:/usr/sbin/nologin\n")
        network = self.root / "run/systemd/netif"
        (network / "leases").mkdir(parents=True)
        (network / "links").mkdir()
        original_lstat = Path.lstat
        def image_lstat(path):
            result = original_lstat(path)
            if str(path.relative_to(self.root)) in {"run/systemd/netif", "run/systemd/netif/leases", "run/systemd/netif/links"}:
                values = list(result);values[4] = 998
                return os.stat_result(values)
            return result
        with patch.object(Path, "lstat", image_lstat):
            report = activation.prepare_runtime(self.root, owner=self.owner)
        self.assertEqual(report["removed_entries"], 4)
        (network / "leases").mkdir(parents=True)
        self.put("/run/systemd/netif/leases/live", "refuse")
        with self.assertRaisesRegex(activation.ActivationError, "root may be active"):
            activation.prepare_runtime(self.root, owner=self.owner)

    def test_live_root_alias_and_relative_root_are_rejected(self):
        with self.assertRaises(activation.ActivationError): activation.OfflineRoot("/")
        with self.assertRaises(activation.ActivationError): activation.OfflineRoot("relative")
        alias = self.base / "alias";alias.symlink_to(self.root)
        with self.assertRaises(activation.ActivationError): activation.OfflineRoot(alias, owner=self.owner)

    def test_group_writable_dependency_parent_is_rejected(self):
        (self.root / "usr/lib").chmod(0o775)
        with self.assertRaisesRegex(activation.ActivationError, "mutable|untrusted"):
            self.activate()
        self.assert_unpublished()

    def test_library_link_escape_and_cycle_are_rejected(self):
        link = self.root / "usr/lib/libfixture.so";link.unlink();link.symlink_to("../../../outside")
        self.compat["library_files"]["/usr/lib/libfixture.so"] = {"symlink": "../../../outside"};self.save_compat()
        with self.assertRaises(activation.ActivationError): self.activate()
        link.unlink();link.symlink_to("libfixture.so")
        self.compat["library_files"]["/usr/lib/libfixture.so"] = {"symlink": "libfixture.so"};self.save_compat()
        with self.assertRaisesRegex(activation.ActivationError, "cyclic"): self.activate()
        self.assert_unpublished()

    def test_absolute_usrmerge_links_are_resolved_inside_image(self):
        (self.root / "lib").symlink_to("/usr/lib")
        self.compat["library_files"]["/lib"] = {"symlink": "/usr/lib"}
        self.compat["library_files"]["/lib/libfixture.so"] = {"symlink": "libfixture.so.1"}
        self.save_compat()
        self.metadata["locks_sha256"]["tyrell-zero-native-compat.json"] = hashlib.sha256(self.compat_path.read_bytes()).hexdigest()
        self.put("/etc/tyrell-image.json", json.dumps(self.metadata))
        result = self.activate()
        self.assertEqual(result["files"]["/lib"]["target"], "/usr/lib")
        baseline = json.loads((self.root / activation.STORE.INVARIANTS.lstrip("/")).read_text())
        self.assertIn("/lib", baseline["files"])
        self.assertNotIn("/./lib", baseline["files"])
        self.assertIn("/usr/lib/libfixture.so.1", baseline["files"])

    def test_package_and_diversion_mismatches_are_rejected(self):
        self.packages["greetd"] = "unexpected"
        with self.assertRaisesRegex(activation.ActivationError, "package closure"):
            self.activate()
        self.packages["greetd"] = "0.10.3-4"
        def bad_diversion(root, *args):
            return "other-package" if "--listpackage" in args else self.execute(root, *args)
        with self.assertRaisesRegex(activation.ActivationError, "Store diversion"):
            activation.activate(self.root, self.source_path, self.compat_path, owner=self.owner, execute=bad_diversion)
        self.assert_unpublished()

    def test_capture_failure_leaves_incomplete_readiness_and_can_resume(self):
        with patch.object(activation.STORE, "capture", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"): self.activate()
        self.assert_unpublished()
        self.assertFalse(json.loads((self.root / activation.STATE.INTEGRATION.lstrip("/")).read_text())["complete"])
        self.assertTrue(self.activate()["complete"])

    def test_modified_activated_root_is_not_silently_requalified(self):
        self.activate()
        self.put("/etc/apt/sources.list", "changed")
        with self.assertRaisesRegex(activation.ActivationError, "activated image changed"):
            self.activate()

    def test_incomplete_final_receipt_cannot_bypass_revalidation(self):
        receipt = self.activate()
        receipt["files"] = {}
        self.put(activation.RECEIPT, json.dumps(receipt))
        with self.assertRaisesRegex(activation.ActivationError, "file closure is incomplete"):
            self.activate()

    def test_legacy_enabled_links_are_removed_without_touching_user_audio(self):
        folder = self.root / "etc/systemd/user/default.target.wants";folder.mkdir(parents=True)
        (folder / "APPLaunch.service").symlink_to("../APPLaunch.service")
        (folder / "pipewire.service").symlink_to("/usr/lib/systemd/user/pipewire.service")
        self.activate()
        self.assertFalse(os.path.lexists(folder / "APPLaunch.service"))
        self.assertTrue((folder / "pipewire.service").is_symlink())

    def test_vendor_userconfig_is_masked_removed_and_bound_in_final_receipts(self):
        folder = self.root / "etc/systemd/system/multi-user.target.wants"
        folder.mkdir(parents=True)
        (folder / "userconfig.service").symlink_to("/usr/lib/systemd/system/userconfig.service")
        result = self.activate()
        self.assertFalse(os.path.lexists(folder / "userconfig.service"))
        mask = "/etc/systemd/system/userconfig.service"
        self.assertEqual(os.readlink(self.root / mask.lstrip("/")), "/dev/null")
        self.assertEqual(result["files"][mask]["target"], "/dev/null")
        baseline = json.loads((self.root / activation.STORE.INVARIANTS.lstrip("/")).read_text())
        self.assertEqual(baseline["files"][mask], {"symlink": "/dev/null"})

    def test_duplicate_lock_fields_and_nonroot_cli_fail(self):
        self.compat_path.write_text('{"schema":1,"schema":1}')
        with self.assertRaisesRegex(activation.ActivationError, "duplicate"):
            self.activate()
        with patch.object(activation.os, "geteuid", return_value=999):
            with self.assertRaises(SystemExit):
                activation.main(["--root", str(self.root), "--qualification", str(self.source_path),
                                 "--native-compat", str(self.compat_path)])


if __name__ == "__main__": unittest.main()
