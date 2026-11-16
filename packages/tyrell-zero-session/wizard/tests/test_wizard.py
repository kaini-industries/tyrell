# SPDX-License-Identifier: MIT
from importlib.util import module_from_spec, spec_from_file_location
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))
import state
import completion

spec = spec_from_file_location("wizard_patch", HERE / "apply-patches.py")
patcher = module_from_spec(spec)
spec.loader.exec_module(patcher)
BOOT = "12345678-1234-1234-1234-123456789abc"


class Cut(BaseException):
    pass


class WizardFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.files = state.Files(Path(temporary.name), os.getuid())
        for directory in ["/etc/tyrell-zero", "/var/lib/tyrell-zero", "/var/lib/LaunchWizard",
                          "/var/lib/applaunch", "/run", "/usr/share/tyrell-zero", "/proc/sys/kernel/random"]:
            self.files.path(directory).mkdir(parents=True, exist_ok=True)
        self.files.write_json(state.INTEGRATION, {"test": "qualification validated by state integration tests"})
        # Keep transaction tests independent of integration receipt schema;
        # integration validation itself has separate fail-closed state tests.
        pin = patch.object(state, "integration", lambda files: files.digest(state.INTEGRATION))
        pin.start()
        self.addCleanup(pin.stop)
        self.files.write("/proc/sys/kernel/random/boot_id", BOOT + "\n")
        self.lookup = lambda _uid: types.SimpleNamespace(pw_name="native", pw_uid=1000,
                                                        pw_gid=1000, pw_dir="/home/native")
        self.reboots = []
        self.tx = completion.Completion(self.files, self.lookup, lambda: self.reboots.append(True))
        state.latch_boot(self.files, BOOT, self.lookup)
        for marker in state.MARKERS:
            self.files.write(marker, "1\n", 0o600)

    def normal(self):
        self.tx.authorize()
        self.tx.begin("native")
        for n in range(1, 6):
            self.tx.authorize()
            self.tx.advance("native", n)
        for action in ["configure", "commit", "finalize"]:
            self.tx.authorize()
            getattr(self.tx, action)("native")

    def test_normal_native_commit_cleanup_reboot_and_unchanged_latch(self):
        self.normal()
        self.assertEqual(state.decide(self.files, self.lookup)["mode"], "native-ready")
        self.assertEqual(self.reboots, [True])
        self.assertEqual(self.files.json(state.RUNTIME + "/boot-state.json")["mode"], "setup")
        self.assertTrue(self.files.exists(state.RUNTIME + "/setup"))
        self.assertFalse(self.files.exists(state.CHECKPOINT))
        self.assertEqual(self.files.path(state.ACCOUNT).stat().st_mode & 0o777, 0o600)

    def test_imager_uses_same_readiness_and_controlled_reboot(self):
        self.tx.authorize()
        self.tx.complete("native")
        self.assertEqual(state.decide(self.files, self.lookup)["mode"], "native-ready")
        self.assertEqual(self.reboots, [True])

    def test_no_username_or_legacy_checkpoint_bypass(self):
        for bad in ["root;reboot", "../native", "Native", "", "native\n", None]:
            with self.assertRaises(state.StateError):
                self.tx.begin(bad)
        self.files.write(completion.LEGACY, "old numeric checkpoint")
        with self.assertRaisesRegex(state.StateError, "legacy-checkpoint-needs-recovery"):
            self.tx.authorize()
        self.assertTrue(self.files.exists(completion.LEGACY))

    def test_only_latched_setup_of_current_boot_is_authorized(self):
        latch = self.files.json(state.RUNTIME + "/boot-state.json")
        for field, value in [("boot_id", "another-boot"), ("mode", "native-ready"),
                             ("integration_sha256", "0" * 64)]:
            self.files.write_json(state.RUNTIME + "/boot-state.json", {**latch, field: value})
            with self.assertRaisesRegex(state.StateError, "wizard-not-in-setup-boot"):
                self.tx.authorize()
        self.files.write_json(state.RUNTIME + "/boot-state.json", latch)
        self.files.write(state.RUNTIME + "/native-ready", "1\n")
        with self.assertRaisesRegex(state.StateError, "wizard-boot-mode-conflict"):
            self.tx.authorize()

    def test_keyboard_guide_and_migration_are_never_consumed(self):
        self.files.write(state.GUIDE, "1\n")
        with self.assertRaisesRegex(state.StateError, "setup-work-pending"):
            self.tx.begin("native")
        with self.assertRaisesRegex(state.StateError, "setup-work-pending"):
            self.tx.complete("native")
        self.files.path(state.GUIDE).unlink()
        journal = "version=1\nsource=pi\ntarget=native\nstage=5\nrename_group=1\nupdate_subuid=0\nupdate_subgid=0\nupdate_sudoers=1\n"
        self.files.write(state.JOURNAL, journal, 0o600)
        self.tx.authorize()
        self.tx.begin("native")
        with self.assertRaisesRegex(state.StateError, "setup-work-pending"):
            self.tx.complete("native")
        self.assertEqual(self.files.read(state.JOURNAL).decode(), journal)

    def test_checkpoints_cannot_skip_unconfirmed_provisioning(self):
        self.tx.begin("native")
        with self.assertRaisesRegex(state.StateError, "wizard-step-out-of-order"):
            self.tx.advance("native", 5)
        with self.assertRaisesRegex(state.StateError, "wizard-provisioning-incomplete"):
            self.tx.complete("native")
        with self.assertRaisesRegex(state.StateError, "wizard-provisioning-incomplete"):
            self.tx.configure("native")
        checkpoint = self.files.json(state.CHECKPOINT)
        checkpoint["target_name"] = "someone_else"
        self.files.write_json(state.CHECKPOINT, checkpoint)
        with self.assertRaises(state.StateError):
            self.tx.complete("native")

    def test_existing_username_cannot_bypass_unconfirmed_password_change(self):
        self.tx.begin("native")
        for n in range(1, 5):
            self.tx.advance("native", n)
        with self.assertRaisesRegex(state.StateError, "wizard-provisioning-incomplete"):
            self.tx.complete("native")
        self.assertEqual(self.reboots, [])

    def test_failed_reboot_keeps_durable_ready_and_can_retry(self):
        def failed():
            raise state.StateError("controlled-reboot-failed")
        self.tx.reboot_command = failed
        with self.assertRaisesRegex(state.StateError, "controlled-reboot-failed"):
            self.normal()
        self.assertEqual(state.decide(self.files, self.lookup)["mode"], "native-ready")
        self.assertEqual(self.files.json(state.RUNTIME + "/boot-state.json")["mode"], "setup")
        self.tx.reboot_command = lambda: self.reboots.append(True)
        self.tx.complete("native")
        self.assertEqual(self.reboots, [True])

    def test_account_change_and_stale_readiness_block_reboot(self):
        self.normal()
        self.files.write_json(state.READY, {"schema": 1})
        with self.assertRaisesRegex(state.StateError, "readiness-mismatch"):
            self.tx.restart("native")
        self.tx.lookup = lambda _uid: types.SimpleNamespace(pw_name="other", pw_uid=1000,
                                                           pw_gid=1000, pw_dir="/home/other")
        with self.assertRaises(state.StateError):
            self.tx.complete("native")

    def test_each_durable_transaction_boundary_is_setup_or_ready(self):
        # Interrupt immediately after every durable JSON write or cleanup unlink.
        # Each interrupted fixture is independently recreated, like a power cut.
        for cut_at in range(1, 16):
            with self.subTest(cut_at=cut_at):
                fixture = WizardFixture()
                fixture.setUp()
                try:
                    count = 0
                    write = fixture.files.write_json
                    unlink = completion._unlink
                    def cut_write(*args, **kwargs):
                        nonlocal count
                        write(*args, **kwargs)
                        count += 1
                        if count == cut_at:
                            raise Cut()
                    def cut_unlink(files, path):
                        nonlocal count
                        existed = files.exists(path)
                        unlink(files, path)
                        if existed:
                            count += 1
                            if count == cut_at:
                                raise Cut()
                    with patch.object(fixture.files, "write_json", cut_write), patch.object(completion, "_unlink", cut_unlink):
                        try:
                            fixture.normal()
                        except Cut:
                            pass
                    self.assertIn(state.decide(fixture.files, fixture.lookup)["mode"], {"setup", "native-ready"})
                    self.assertEqual(fixture.files.json(state.RUNTIME + "/boot-state.json")["mode"], "setup")
                    if fixture.files.exists(state.CHECKPOINT):
                        n = fixture.tx.checkpoint("native")
                        if n < 5:
                            with self.assertRaisesRegex(state.StateError, "wizard-provisioning-incomplete"):
                                fixture.tx.complete("native")
                        else:
                            fixture.tx.complete("native")
                            self.assertEqual(state.decide(fixture.files, fixture.lookup)["mode"], "native-ready")
                finally:
                    fixture.doCleanups()


class SourcePatch(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for relative in patcher.EXPECTED:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(HERE / "tests/fixtures" / Path(relative).name, target)

    def test_exact_source_patch_is_idempotent_and_removes_both_legacy_routes(self):
        patcher.apply(self.root)
        patcher.apply(self.root)
        service = (self.root / "projects/LaunchWizard/main/ui/wizard_service.cpp").read_text()
        for forbidden in ["handoff_to_applaunch", "configure_desktop_startup", "enable_applaunch_service",
                          '"B4"', "lightdm.conf", "run_apply_steps(", "ApplyCheckpointStore checkpoint"]:
            self.assertNotIn(forbidden, service)
        for action in ["begin", "checkpoint", "configure", "commit", "finalize", "complete", "reboot"]:
            self.assertIn('native_completion("' + action + '"', service.replace("\n                ", ""))
        before = (HERE / "tests/fixtures/wizard_service.cpp").read_text()
        guide = lambda text: text[text.index("void launch_wizard::WizardService::run_keyboard_guide()"):
                                   text.index("int launch_wizard::WizardService::finish_configured_system()")]
        self.assertEqual(guide(before), guide(service))
        account = lambda text: text[text.index("uid_t configure_account("):text.index("std::string disable_piwiz(")] if "std::string disable_piwiz(" in text else text[text.index("uid_t configure_account("):text.index("// ---------------------------------------------------------------------------\n// System integration")]
        # Account code and its independent journal remain byte-for-byte vendor code.
        self.assertEqual(account(before).rstrip(), account(service).rstrip())

    def test_unknown_and_partial_sources_are_rejected_without_changes(self):
        target = self.root / next(iter(patcher.EXPECTED))
        target.write_text(target.read_text() + "\n// unknown change\n")
        other = self.root / list(patcher.EXPECTED)[1]
        before = other.read_bytes()
        with self.assertRaisesRegex(ValueError, "exact .105 source"):
            patcher.apply(self.root)
        self.assertEqual(other.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
