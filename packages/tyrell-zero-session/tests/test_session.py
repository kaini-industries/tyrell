from pathlib import Path
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import tomllib
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import state
import supervisor

BOOT = "12345678-1234-1234-1234-123456789abc"


class SessionFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = state.Files(self.root, owner=os.getuid())
        self.account = state.Account("native", 1000, 1000, "/home/native")
        self.lookup = lambda _uid: types.SimpleNamespace(pw_name="native", pw_uid=1000, pw_gid=1000, pw_dir="/home/native")
        for directory in ["/run", "/var/lib/tyrell-zero", "/var/lib/LaunchWizard", "/var/lib/applaunch", "/etc/tyrell-zero", "/usr/share/tyrell-zero"]:
            self.files.path(directory).mkdir(parents=True, exist_ok=True)
        assets = {}
        for name, path in state.ASSETS.items():
            file = self.files.path(path)
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(("fixture-" + name).encode())
            file.chmod(0o755)
            assets[name] = {"path": path, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
        # Tests use tiny binaries; production pin stays fixed and is asserted
        # separately below. No production environment variable can replace it.
        pin = patch.object(state, "LAUNCHER_SHA256", assets["launcher"]["sha256"])
        pin.start()
        self.addCleanup(pin.stop)
        store_pin = patch.object(state, "STORE_SHA256", assets["store_original"]["sha256"])
        store_pin.start()
        self.addCleanup(store_pin.stop)
        runtime = {}
        for path in state.REQUIRED_RUNTIME_FILES:
            file = self.files.path(path)
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(("fixture-runtime-" + path).encode())
            file.chmod(0o755 if path.startswith("/usr/libexec/") else 0o644)
            runtime[path] = hashlib.sha256(file.read_bytes()).hexdigest()
        self.integration = {"schema": 1, "profile": state.PROFILE, "workflow": state.WORKFLOW,
                            "complete": True, "applaunch_version": "0.6.105",
                            "assets": assets, "runtime_files": runtime}
        self.receipt = {"schema": 1, "profile": state.PROFILE, "workflow": state.WORKFLOW,
                        "scope": "pre-image-integration", "status": "passed",
                        "physical_hardware_qualified": False,
                        "suites": {"session": "passed", "wizard": "passed", "store": "passed"},
                        "assets": assets, "runtime_files": runtime}
        self.qualify()
        self.put(state.ACCOUNT, {"schema": 1, "profile": state.PROFILE, "completed": True,
                                 "account": self.account.record()})

    def put(self, path, value):
        self.files.write_json(path, value)

    def qualify(self):
        self.put(state.QUALIFICATION, self.receipt)
        self.integration["qualification_sha256"] = self.files.digest(state.QUALIFICATION)
        self.put(state.INTEGRATION, self.integration)

    def ready(self):
        return state.commit_readiness(self.files, self.lookup)

    def test_incomplete_integration_never_runs_setup_or_launcher(self):
        self.integration["complete"] = False
        self.put(state.INTEGRATION, self.integration)
        self.files.write(state.MARKERS[0], "")
        self.assertEqual(state.decide(self.files, self.lookup)["mode"], "recovery")
        with self.assertRaisesRegex(state.StateError, "integration-incomplete"):
            self.ready()

    def test_missing_readiness_requests_native_setup(self):
        self.assertEqual(state.decide(self.files, self.lookup)["mode"], "setup")

    def test_complete_flag_without_actual_receipt_cannot_commit(self):
        self.files.path(state.QUALIFICATION).unlink()
        with self.assertRaises(FileNotFoundError):
            self.ready()
        self.assertEqual(state.decide(self.files, self.lookup)["mode"], "recovery")

    def test_receipt_bytes_are_bound_to_manifest(self):
        self.receipt["status"] = "failed"
        self.put(state.QUALIFICATION, self.receipt)
        with self.assertRaisesRegex(state.StateError, "integration-qualification-changed"):
            self.ready()

    def test_rehashed_receipt_still_requires_all_suites_and_preimage_scope(self):
        for key, value in [("schema", True), ("scope", "physical-hardware"),
                           ("physical_hardware_qualified", True),
                           ("suites", {"session": "passed", "wizard": "passed"}),
                           ("suites", {"session": "passed", "wizard": "passed", "store": "failed"})]:
            original = self.receipt[key]
            self.receipt[key] = value
            self.qualify()
            with self.assertRaisesRegex(state.StateError, "integration-qualification-incomplete"):
                self.ready()
            self.receipt[key] = original

    def test_receipt_asset_and_runtime_maps_must_match_manifest(self):
        self.receipt["assets"] = dict(self.receipt["assets"], wizard={"path": state.ASSETS["wizard"], "sha256": "0" * 64})
        self.qualify()
        with self.assertRaisesRegex(state.StateError, "integration-qualification-assets"):
            self.ready()
        self.receipt["assets"] = self.integration["assets"]
        self.receipt["runtime_files"] = {}
        self.qualify()
        with self.assertRaisesRegex(state.StateError, "integration-qualification-runtime"):
            self.ready()

    def test_unchanged_wrappers_do_not_hide_supporting_module_tamper(self):
        self.ready()
        for path in ["/usr/lib/tyrell-zero/session/wizard_complete.py",
                     "/usr/lib/tyrell-zero/store/guard.py", "/usr/share/tyrell-zero/store-policy.json"]:
            file = self.files.path(path)
            original = file.read_bytes()
            file.write_text("changed supporting file")
            with self.assertRaisesRegex(state.StateError, "integration-runtime-changed"):
                self.ready()
            self.assertEqual(state.decide(self.files, self.lookup)["mode"], "recovery")
            file.write_bytes(original)

    def test_required_runtime_files_cannot_be_omitted_or_replaced_by_arbitrary_paths(self):
        path = "/usr/lib/tyrell-zero/store/guard.py"
        value = self.integration["runtime_files"].pop(path)
        self.qualify()
        with self.assertRaisesRegex(state.StateError, "integration-runtime-incomplete"):
            self.ready()
        self.integration["runtime_files"][path] = value
        for bad in ["/etc/passwd", "/usr/lib/tyrell-zero/../outside", state.INTEGRATION, state.QUALIFICATION]:
            self.integration["runtime_files"][bad] = "0" * 64
            self.qualify()
            with self.assertRaisesRegex(state.StateError, "integration-runtime-path"):
                self.ready()
            del self.integration["runtime_files"][bad]

    def test_runtime_resource_inventory_is_bounded(self):
        for constant, value in [("MAX_RUNTIME_FILES", 1), ("MAX_RUNTIME_FILE_BYTES", 1),
                                ("MAX_RUNTIME_TOTAL_BYTES", 1)]:
            with patch.object(state, constant, value):
                with self.assertRaises(state.StateError):
                    self.ready()

    def test_runtime_symlink_is_not_a_qualified_resource(self):
        path = self.files.path("/usr/lib/tyrell-zero/store/guard.py")
        original = path.read_bytes()
        elsewhere = self.root / "elsewhere"
        elsewhere.write_bytes(original)
        path.unlink()
        path.symlink_to(elsewhere)
        with self.assertRaisesRegex(state.StateError, "untrusted-path"):
            self.ready()

    def test_runtime_and_receipt_trust_modes_are_checked(self):
        file = self.files.path("/usr/lib/tyrell-zero/store/guard.py")
        file.chmod(0o666)
        with self.assertRaisesRegex(state.StateError, "untrusted-path"):
            self.ready()
        file.chmod(0o644)
        # macOS can strip set-ID bits on chmod. Inject stat metadata so both
        # host and ARM64 runs exercise the same rejection without a skip.
        real_stat = Path.stat
        for target, error in [(file, "integration-runtime-mode"),
                              (self.files.path(state.QUALIFICATION), "integration-qualification-changed")]:
            def metadata(path, *, follow_symlinks=True):
                info = real_stat(path, follow_symlinks=follow_symlinks)
                if follow_symlinks and path == target:
                    return os.stat_result((info.st_mode | 0o4000, *info[1:]))
                return info
            with patch.object(Path, "stat", autospec=True, side_effect=metadata):
                with self.assertRaisesRegex(state.StateError, error):
                    self.ready()

    def test_original_store_identity_is_fixed_even_with_matching_receipt(self):
        self.integration["assets"]["store_original"]["sha256"] = "0" * 64
        self.qualify()
        with self.assertRaisesRegex(state.StateError, "vendor-store-changed"):
            self.ready()

    def test_duplicate_fields_and_nonfinite_json_are_rejected(self):
        for data in ['{"schema":1,"schema":1}', '{"value":NaN}', '{"value":Infinity}',
                     '{"nested":{"a":1,"a":2}}']:
            self.files.write(state.QUALIFICATION, data)
            with self.assertRaisesRegex(state.StateError, "invalid-json"):
                self.files.json(state.QUALIFICATION)

    def test_ready_commit_and_account_revalidation(self):
        self.ready()
        self.assertEqual(state.decide(self.files, self.lookup)["mode"], "native-ready")
        changed = lambda _uid: types.SimpleNamespace(pw_name="renamed", pw_uid=1000, pw_gid=1000, pw_dir="/home/renamed")
        self.assertEqual(state.decide(self.files, changed)["mode"], "recovery")
        ready = self.files.json(state.READY)
        self.assertEqual(set(ready), {"schema", "profile", "workflow", "account", "account_sha256", "integration_sha256"})
        self.assertEqual(self.files.path(state.READY).stat().st_mode & 0o777, 0o600)

    def test_factory_and_rearm_markers_override_readiness_next_boot(self):
        self.ready()
        for marker in state.MARKERS:
            self.files.write(marker, "")
            self.assertEqual(state.decide(self.files, self.lookup)["mode"], "setup")
            self.files.path(marker).unlink()

    def test_exact_migration_record_and_corruption(self):
        self.files.write(state.JOURNAL, "version=1\nsource=old\ntarget=native\nstage=5\nrename_group=1\nupdate_subuid=0\nupdate_subgid=0\nupdate_sudoers=1\n", 0o600)
        self.assertEqual(state.decide(self.files, self.lookup)["mode"], "setup")
        with self.assertRaisesRegex(state.StateError, "setup-work-pending"):
            self.ready()
        self.files.write(state.JOURNAL, "version=1\nsource=old\ntarget=native\nstage=999\n")
        self.assertEqual(state.decide(self.files, self.lookup)["mode"], "recovery")

    def test_checkpoint_namespace_and_configuration_identity(self):
        checkpoint = {"schema": 1, "profile": state.PROFILE, "workflow": state.WORKFLOW,
                      "step": "ready", "configuration_sha256": self.files.digest(state.ACCOUNT)}
        self.put(state.CHECKPOINT, checkpoint)
        self.ready()  # Final step can commit before checkpoint cleanup.
        self.assertEqual(state.decide(self.files, self.lookup)["mode"], "setup")
        checkpoint["workflow"] = "legacy-numeric-checkpoint"
        self.put(state.CHECKPOINT, checkpoint)
        self.assertEqual(state.decide(self.files, self.lookup)["mode"], "recovery")

    def test_nested_checkpoint_step_fails_to_recovery_without_type_error(self):
        for bad in [[], {}, None, 1]:
            self.put(state.CHECKPOINT, {"schema": 1, "profile": state.PROFILE, "workflow": state.WORKFLOW,
                                       "step": bad, "configuration_sha256": "1" * 64})
            self.assertEqual(state.decide(self.files, self.lookup)["mode"], "recovery")

    def test_nested_boot_mode_fails_closed(self):
        value = state.latch_boot(self.files, BOOT, self.lookup)
        value["mode"] = []
        self.put(state.RUNTIME + "/boot-state.json", value)
        with self.assertRaisesRegex(state.StateError, "boot-latch-invalid"):
            state.latch_boot(self.files, BOOT, self.lookup)

    def test_old_vendor_checkpoint_cannot_skip_native_completion(self):
        self.ready()
        self.files.write("/var/lib/LaunchWizard/apply.checkpoint", "old numeric state")
        self.assertEqual(state.decide(self.files, self.lookup)["mode"], "recovery")

    def test_component_change_blocks_ready_and_latched_restart(self):
        self.ready()
        state.latch_boot(self.files, BOOT, self.lookup)
        self.files.path(state.ASSETS["store_backend"]).write_text("changed")
        self.assertEqual(state.decide(self.files, self.lookup)["mode"], "recovery")
        with self.assertRaises(state.StateError):
            state.latch_boot(self.files, BOOT, self.lookup)

    def test_symlink_or_writable_state_is_rejected(self):
        self.ready()
        original = self.files.path(state.READY)
        value = original.read_text()
        original.unlink()
        other = self.root / "other"
        other.write_text(value)
        original.symlink_to(other)
        self.assertEqual(state.decide(self.files, self.lookup)["mode"], "recovery")
        original.unlink()
        original.write_text(value)
        original.chmod(0o666)
        self.assertEqual(state.decide(self.files, self.lookup)["mode"], "recovery")

    def test_fifo_is_rejected_before_any_read_can_block(self):
        self.files.path(state.ACCOUNT).unlink()
        os.mkfifo(self.files.path(state.ACCOUNT), 0o600)
        with self.assertRaisesRegex(state.StateError, "unexpected-file-type"):
            self.files.secure(state.ACCOUNT)

    def test_boolean_schema_is_not_schema_version_one(self):
        self.integration["schema"] = True
        self.put(state.INTEGRATION, self.integration)
        self.assertEqual(state.decide(self.files, self.lookup)["mode"], "recovery")

    def test_setup_latch_does_not_become_autologin_in_same_boot(self):
        first = state.latch_boot(self.files, BOOT, self.lookup)
        self.assertEqual(first["mode"], "setup")
        self.ready()
        self.assertEqual(state.latch_boot(self.files, BOOT, self.lookup), first)
        self.assertNotIn("initial_session", tomllib.loads(self.files.read(state.RUNTIME + "/greetd.config").decode()))

    def test_mode_publish_repair_does_not_redecide(self):
        first = state.latch_boot(self.files, BOOT, self.lookup)
        self.files.path(state.RUNTIME + "/setup").unlink()
        self.ready()
        self.assertEqual(state.latch_boot(self.files, BOOT, self.lookup), first)
        self.assertTrue(self.files.exists(state.RUNTIME + "/setup"))

    def test_greetd_runfile_survives_helpers_and_ready_config_is_literal(self):
        self.ready()
        self.files.mkdir(state.RUNTIME)
        self.files.write(state.RUNTIME + "/greetd.initial-session", "already authorized\n", 0o600)
        state.latch_boot(self.files, BOOT, self.lookup)
        state.latch_boot(self.files, BOOT, self.lookup)
        self.assertEqual(self.files.read(state.RUNTIME + "/greetd.initial-session"), b"already authorized\n")
        config = tomllib.loads(self.files.read(state.RUNTIME + "/greetd.config").decode())
        self.assertEqual(config["terminal"]["vt"], 7)
        self.assertFalse(config["general"]["source_profile"])
        self.assertEqual(config["initial_session"]["user"], "native")
        self.assertEqual(config["initial_session"]["command"], "/usr/libexec/tyrell-zero-session")
        self.assertEqual(config["default_session"]["command"], "/usr/libexec/tyrell-zero-session-recovery")

    def test_username_injection_is_not_a_config_command(self):
        with self.assertRaises(state.StateError):
            state.greetd_config(state.Account('native"\ncommand="sh', 1000, 1000, "/home/native"))


class SupervisorRules(unittest.TestCase):
    def test_foreground_transfer_resumes_an_owned_stopped_child(self):
        child = subprocess.Popen([sys.executable, "-c", "import os,signal;os.kill(os.getpid(),signal.SIGSTOP)"], process_group=0)
        events = []
        terminal = types.SimpleNamespace(foreground=lambda group: events.append(("foreground", group)))
        original = os.killpg
        def send(group, sig):
            events.append(("signal", group, sig))
            return original(group, sig)
        try:
            waited, status = os.waitpid(child.pid, os.WUNTRACED)
            self.assertEqual(waited, child.pid)
            self.assertTrue(os.WIFSTOPPED(status))
            with patch.object(supervisor.os, "killpg", side_effect=send):
                supervisor.foreground_child(terminal, child)
            self.assertEqual(events, [("foreground", child.pid), ("signal", child.pid, signal.SIGCONT)])
            self.assertEqual(child.wait(timeout=5), 0)
        finally:
            supervisor.stop_process(child, grace=0.1)

    def test_malformed_boot_account_is_not_an_uncaught_type_error(self):
        for bad in [None, [], "account", {"uid": True}]:
            with self.assertRaises(state.StateError):
                supervisor.validate_boot({"mode": "native-ready", "account": bad, "integration_sha256": "pin"}, 1000, "pin")

    @unittest.skipUnless(sys.platform == "linux", "Linux subreaper and pidfd test runs in ARM64 builder")
    def test_adopted_application_in_separate_process_group_is_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            ready, stopped = Path(directory) / "ready", Path(directory) / "stopped"
            worker = "import pathlib,signal,time; ready=pathlib.Path(" + repr(str(ready)) + "); stopped=pathlib.Path(" + repr(str(stopped)) + ");\ndef end(*_):\n stopped.write_text('stopped'); raise SystemExit(0)\nsignal.signal(signal.SIGTERM,end);ready.write_text('ready');time.sleep(60)"
            leader = "import subprocess,sys,time,pathlib;subprocess.Popen([sys.executable,'-c'," + repr(worker) + "],process_group=0);\nwhile not pathlib.Path(" + repr(str(ready)) + ").exists():time.sleep(.01)"
            harness = "import sys,subprocess;sys.path.insert(0," + repr(str(Path(__file__).resolve().parents[1] / "src")) + ");import supervisor;supervisor.enable_subreaper();p=subprocess.Popen([sys.executable,'-c'," + repr(leader) + "],process_group=0);\ntry:\n p.wait(timeout=5);supervisor.stop_application(p)\nfinally:\n supervisor.stop_application(p)\n"
            result = subprocess.run([sys.executable, "-c", harness], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(stopped.exists())

    def test_terminal_entry_failure_restores_graphics_and_termios(self):
        modes = []
        def ioctl(_fd, command, value, *_):
            if command == supervisor.KDGETMODE:
                value[0] = 0
            else:
                modes.append(value)
                if len(modes) == 1:
                    raise OSError("simulated KD change failure")
        with patch.object(supervisor.os, "isatty", return_value=True), patch.object(supervisor.os, "ttyname", return_value="/dev/tty7"), patch.object(supervisor.os, "tcgetpgrp", return_value=123), patch.object(supervisor.termios, "tcgetattr", return_value=[1, 2, 3]), patch.object(supervisor.tty, "setraw"), patch.object(supervisor.fcntl, "ioctl", side_effect=ioctl), patch.object(supervisor.termios, "tcsetattr") as restore:
            with self.assertRaises(OSError):
                supervisor.Terminal().__enter__()
            self.assertEqual(modes, [supervisor.KD_GRAPHICS, 0])
            restore.assert_called_once_with(0, supervisor.termios.TCSANOW, [1, 2, 3])

    def test_descendant_is_stopped_after_its_leader_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            ready, stopped = Path(directory) / "ready", Path(directory) / "stopped"
            worker = "import pathlib,signal,time; ready=pathlib.Path(" + repr(str(ready)) + "); stopped=pathlib.Path(" + repr(str(stopped)) + ");\ndef end(*_):\n stopped.write_text('stopped'); raise SystemExit(0)\nsignal.signal(signal.SIGTERM,end);ready.write_text('ready');time.sleep(60)"
            leader = "import subprocess,sys,time,pathlib;subprocess.Popen([sys.executable,'-c'," + repr(worker) + "]);\nwhile not pathlib.Path(" + repr(str(ready)) + ").exists():time.sleep(.01)"
            child = subprocess.Popen([sys.executable, "-c", leader], process_group=0)
            try:
                child.wait(timeout=5)
                supervisor.stop_process(child, grace=0.5)
                self.assertTrue(stopped.exists())
            finally:
                supervisor.stop_process(child, grace=0.1)

    def test_only_the_real_active_local_vt7_user_session_is_accepted(self):
        correct = {"User": "1000", "Class": "user", "Seat": "seat0", "VTNr": "7", "Active": "yes", "Remote": "no"}
        supervisor.validate_logind(correct, 1000)
        for key, invalid in {"User": "0", "Class": "manager", "Seat": "", "VTNr": "1", "Active": "no", "Remote": "yes"}.items():
            with self.subTest(key=key), self.assertRaises(state.StateError):
                supervisor.validate_logind({**correct, key: invalid}, 1000)

    def test_restart_storm_has_a_budget_not_a_busy_loop(self):
        budget = supervisor.RestartBudget()
        self.assertTrue(budget.allow(1))
        self.assertTrue(budget.allow(2))
        self.assertTrue(budget.allow(3))
        self.assertFalse(budget.allow(4))
        self.assertTrue(budget.allow(62))

    def test_original_launcher_pin_is_the_recorded_vendor_binary(self):
        self.assertEqual(state.LAUNCHER_SHA256, "9f26df9329160385c68794d26e0c7153271c6a69bb048938580e1495418c968c")
        self.assertEqual(state.STORE_SHA256, "e5fe24b1dd26cd224c64ef4df2e7a61e0fce3fb63b3b04ac90d38c5092ccc05e")


if __name__ == "__main__":
    unittest.main()
