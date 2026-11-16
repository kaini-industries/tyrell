# SPDX-License-Identifier: MIT
"""Private Wizard transaction. This module never starts a launcher or desktop.

The CLI has no alternate root, command, environment, or identity override.
Tests exercise the Files API against an isolated filesystem instead.
"""
import argparse
import fcntl
import hashlib
import json
import os
import pwd
import subprocess

import state

FORMAT = "wizard-transaction-v1"
LEGACY = "/var/lib/LaunchWizard/apply.checkpoint"
STEPS = {0: "locale", 1: "locale", 2: "locale", 3: "network", 4: "ssh",
         5: "account", 6: "native-config", 7: "ready", 8: "reboot"}


def _unlink(files, path):
    if not files.exists(path):
        return
    target = files.secure(path)
    target.unlink()
    fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fingerprint(name):
    # This identifies a replay target, never credentials or an authorization to
    # skip provisioning. All vendor actions replay after begin().
    return hashlib.sha256(json.dumps({"profile": state.PROFILE,
        "workflow": state.WORKFLOW, "target_name": name}, sort_keys=True).encode()).hexdigest()


class Completion:
    def __init__(self, files, lookup=None, reboot=None):
        self.files = files
        self.lookup = lookup or pwd.getpwuid
        self.reboot_command = reboot or self._system_reboot

    @staticmethod
    def _system_reboot():
        result = subprocess.run(["/usr/bin/systemctl", "reboot"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=20, check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"})
        if result.returncode:
            raise state.StateError("controlled-reboot-failed")

    def authorize(self):
        identity = state.integration(self.files)
        latch = self.files.json(state.RUNTIME + "/boot-state.json")
        boot_id = self.files.read("/proc/sys/kernel/random/boot_id").decode().strip()
        if (latch.get("mode") != "setup" or latch.get("boot_id") != boot_id or
                latch.get("integration_sha256") != identity or
                latch.get("config_sha256") != self.files.digest(state.RUNTIME + "/greetd.config")):
            raise state.StateError("wizard-not-in-setup-boot")
        self.files.secure(state.RUNTIME + "/setup")
        if any(self.files.exists(state.RUNTIME + "/" + mode) for mode in ["native-ready", "recovery"]):
            raise state.StateError("wizard-boot-mode-conflict")
        if self.files.exists(LEGACY):
            raise state.StateError("legacy-checkpoint-needs-recovery")
        for marker in (*state.MARKERS, state.GUIDE):
            if self.files.exists(marker):
                self.files.secure(marker)
        if self.files.exists(state.JOURNAL):
            state.validate_journal(self.files)

    @staticmethod
    def name(value):
        if not isinstance(value, str) or not state.NAME.fullmatch(value):
            raise state.StateError("wizard-target-invalid")
        return value

    def current(self, name):
        self.name(name)
        try:
            entry = self.lookup(1000)
        except KeyError:
            raise state.StateError("account-missing") from None
        if (entry.pw_name != name or type(entry.pw_uid) is not int or entry.pw_uid != 1000 or
                type(entry.pw_gid) is not int or entry.pw_gid <= 0 or entry.pw_dir != "/home/" + name):
            raise state.StateError("account-mismatch")
        return state.Account(name, 1000, entry.pw_gid, entry.pw_dir)

    def checkpoint(self, name):
        state.validate_checkpoint(self.files)
        doc = self.files.json(state.CHECKPOINT)
        expected = {"schema", "profile", "workflow", "step", "configuration_sha256",
                    "format", "target_name", "completed_steps"}
        n = doc.get("completed_steps")
        if (set(doc) != expected or doc.get("format") != FORMAT or
                doc.get("target_name") != self.name(name) or type(n) is not int or
                n not in STEPS or doc["step"] != STEPS[n]):
            raise state.StateError("wizard-checkpoint-incompatible")
        digest = _fingerprint(name) if n < 6 else self.files.digest(state.ACCOUNT)
        if doc["configuration_sha256"] != digest:
            raise state.StateError("wizard-checkpoint-identity")
        return n

    def save(self, name, n):
        digest = _fingerprint(name) if n < 6 else self.files.digest(state.ACCOUNT)
        self.files.write_json(state.CHECKPOINT, {"schema": 1, "profile": state.PROFILE,
            "workflow": state.WORKFLOW, "format": FORMAT, "target_name": name,
            "completed_steps": n, "step": STEPS[n], "configuration_sha256": digest}, 0o600)

    def begin(self, name):
        self.name(name)
        if self.files.exists(state.GUIDE):
            raise state.StateError("setup-work-pending")
        if self.files.exists(state.JOURNAL):
            state.validate_journal(self.files)
            target = self.files.read(state.JOURNAL).decode().splitlines()[2].split("=", 1)[1]
            if target != name:
                raise state.StateError("migration-target-conflict")
        if self.files.exists(state.CHECKPOINT):
            # A valid existing transaction can replay, but another workflow or
            # target may not silently discard a pending account migration.
            existing = self.files.json(state.CHECKPOINT)
            old_name = existing.get("target_name")
            self.checkpoint(old_name)
            if old_name != name and self.files.exists(state.JOURNAL):
                raise state.StateError("migration-target-conflict")
        self.save(name, 0)
        # Publish pending work before invalidating any previous readiness.
        _unlink(self.files, state.READY)

    def advance(self, name, n):
        previous = self.checkpoint(name)
        if type(n) is not int or n not in range(1, 6) or n != previous + 1:
            raise state.StateError("wizard-step-out-of-order")
        if n == 5:
            self.current(name)
            self.no_pending_work()
        self.save(name, n)

    def no_pending_work(self):
        if self.files.exists(state.JOURNAL) or self.files.exists(state.GUIDE):
            raise state.StateError("setup-work-pending")

    def configure(self, name):
        self.no_pending_work()
        n = self.checkpoint(name)
        if n < 5:
            raise state.StateError("wizard-provisioning-incomplete")
        account = self.current(name)
        self.files.mkdir("/etc/tyrell-zero", 0o755)
        self.files.write_json(state.ACCOUNT, {"schema": 1, "profile": state.PROFILE,
            "completed": True, "account": account.record()}, 0o600)
        self.save(name, 6)

    def commit(self, name):
        self.no_pending_work()
        if self.checkpoint(name) < 6:
            raise state.StateError("wizard-provisioning-incomplete")
        self.current(name)
        # A ready checkpoint is durable before the state API commits readiness.
        self.save(name, 7)
        state.commit_readiness(self.files, self.lookup)

    def finalized_ready(self, name):
        account = state.configured_account(self.files, self.lookup)
        if account.name != self.name(name):
            raise state.StateError("account-mismatch")
        expected = {"schema": 1, "profile": state.PROFILE, "workflow": state.WORKFLOW,
                    "account": account.record(), "account_sha256": self.files.digest(state.ACCOUNT),
                    "integration_sha256": state.integration(self.files)}
        if self.files.json(state.READY) != expected:
            raise state.StateError("readiness-mismatch")

    def finalize(self, name):
        self.no_pending_work()
        if self.checkpoint(name) < 7:
            raise state.StateError("wizard-provisioning-incomplete")
        self.finalized_ready(name)
        self.save(name, 8)
        # Leave the checkpoint until all marker removals are durable. A power
        # cut during either removal still boots into setup and resumes safely.
        for marker in state.MARKERS:
            _unlink(self.files, marker)
        _unlink(self.files, state.CHECKPOINT)
        self.restart(name)

    def complete(self, name):
        self.current(name)
        self.no_pending_work()
        if self.files.exists(state.CHECKPOINT):
            n = self.checkpoint(name)
            # A matching username cannot prove that a requested password change
            # completed. Before the account checkpoint, return to the GUI and
            # explicitly replay provisioning, even if migration has finished.
            if n < 5:
                raise state.StateError("wizard-provisioning-incomplete")
        elif self.files.exists(state.READY):
            self.finalized_ready(name)
            self.restart(name)
            return
        else:
            # Imager already configured the normal account. The GUI only calls
            # this branch after its first-boot/account policy and keyboard guide.
            self.save(name, 5)
        self.configure(name)
        self.commit(name)
        self.finalize(name)

    def restart(self, name):
        self.no_pending_work()
        if self.files.exists(state.CHECKPOINT) or any(self.files.exists(p) for p in state.MARKERS):
            raise state.StateError("setup-work-pending")
        self.finalized_ready(name)
        self.reboot_command()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private Tyrell Zero Wizard completion")
    parser.add_argument("action", choices=["begin", "checkpoint", "configure", "commit", "finalize", "complete", "reboot"])
    parser.add_argument("name", nargs="?")
    parser.add_argument("step", nargs="?", type=int)
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        parser.error("root is required")
    if ((args.action == "checkpoint") != (args.step is not None) or
            (args.action != "reboot" and args.name is None) or
            (args.action == "reboot" and args.name is not None)):
        parser.error("invalid arguments")
    files = state.Files()
    fd = None
    try:
        files.mkdir("/var/lib/tyrell-zero", 0o700)
        lock = "/var/lib/tyrell-zero/wizard-complete.lock"
        if files.exists(lock):
            files.secure(lock)
        fd = os.open(files.path(lock), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        transaction = Completion(files)
        transaction.authorize()
        if args.action == "reboot":
            transaction.restart(state.configured_account(files).name)
        elif args.action == "checkpoint":
            transaction.advance(args.name, args.step)
        else:
            getattr(transaction, args.action)(args.name)
        print(json.dumps({"ok": True, "action": args.action}, sort_keys=True))
        return 0
    except (state.StateError, OSError, UnicodeError, subprocess.SubprocessError) as error:
        reason = str(error) if isinstance(error, state.StateError) else "wizard-completion-unavailable"
        print(json.dumps({"ok": False, "reason": reason}, sort_keys=True))
        return 1
    finally:
        if fd is not None:
            os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
