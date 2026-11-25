"""Real files/processes exercise D1 durability; no device or service is used."""
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
import handoff_ledger as ledger


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root / "ledger"

    def create(self):
        result = ledger.Ledger.create(self.directory, {"epoch": 1, "operations": []})
        self.addCleanup(result.close)
        return result

    def child(self, code, *args):
        return subprocess.run([sys.executable, "-c", "import sys;sys.path.insert(0,sys.argv[1]);" + code,
                               str(PACKAGE), str(self.directory), *args],
                              capture_output=True, text=True, timeout=10)

    def test_state_and_event_commit_atomically_with_copied_results(self):
        value = self.create()
        state, event = {"epoch": 2, "operations": [{"status": "pending"}]}, {"kind": "permit"}
        self.assertEqual(value.commit(state, event, expected_revision=0), 1)
        state["operations"].clear()
        event["kind"] = "changed"
        self.assertEqual(value.read()["operations"], [{"status": "pending"}])
        history = value.history()
        self.assertEqual([row["revision"] for row in history], [0, 1])
        self.assertEqual(history[-1]["event"], {"kind": "permit"})
        history[-1]["event"]["kind"] = "foreign"
        self.assertEqual(value.history()[-1]["event"], {"kind": "permit"})
        value.close()
        with ledger.Ledger.open(self.directory) as reopened:
            self.assertEqual(reopened.revision, 1)
            self.assertEqual(reopened.read(), {"epoch": 2, "operations": [{"status": "pending"}]})

    def test_private_files_and_only_known_sidecars(self):
        value = self.create()
        value.commit({"epoch": 2}, {"kind": "change"})
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        for path in self.directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_nlink, 1)
        self.assertEqual({p.name for p in self.directory.iterdir()}, {"lock", "ledger.sqlite3"})

    def test_create_never_reuses_existing_directory_even_if_empty(self):
        self.directory.mkdir()
        with self.assertRaises(ledger.LedgerError):
            ledger.Ledger.create(self.directory, {})
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_open_missing_state_does_not_create_anything(self):
        with self.assertRaises(ledger.LedgerError):
            ledger.Ledger.open(self.directory)
        self.assertFalse(self.directory.exists())
        value = self.create()
        value.close()
        (self.directory / "ledger.sqlite3").unlink()
        with self.assertRaises(ledger.LedgerError):
            ledger.Ledger.open(self.directory)
        self.assertEqual({p.name for p in self.directory.iterdir()}, {"lock"})

    def test_same_process_and_other_process_writers_are_excluded(self):
        value = self.create()
        with self.assertRaises(ledger.BusyError):
            ledger.Ledger.open(self.directory)
        child = self.child("from handoff_ledger import Ledger,BusyError\ntry: Ledger.open(sys.argv[2])\nexcept BusyError: sys.exit(7)\nsys.exit(9)")
        self.assertEqual(child.returncode, 7, child.stderr)
        self.assertEqual(value.read()["epoch"], 1)
        value.close()
        child = self.child("from handoff_ledger import Ledger\nwith Ledger.open(sys.argv[2]) as l: l.commit({'epoch':2},{'kind':'child'})")
        self.assertEqual(child.returncode, 0, child.stderr)
        with ledger.Ledger.open(self.directory) as reopened:
            self.assertEqual(reopened.read(), {"epoch": 2})

    def test_stale_revision_and_invalid_input_do_not_mutate_or_poison(self):
        value = self.create()
        for bad in (True, 2, -1):
            with self.subTest(bad=bad), self.assertRaises(ledger.LedgerError):
                value.commit({}, {}, expected_revision=bad)
        for bad in ({"x": float("nan")}, {1: "coerced key"}, {"x": object()}, []):
            with self.subTest(bad=bad), self.assertRaises(ledger.LedgerError):
                value.commit(bad, {})
        with self.assertRaises(ledger.LedgerError):
            value.commit({"x": "a" * ledger.MAX_STATE_BYTES}, {})
        with self.assertRaises(ledger.LedgerError):
            value.commit({}, {"x": "a" * ledger.MAX_EVENT_BYTES})
        self.assertEqual(value.revision, 0)
        self.assertEqual(value.commit({"ok": True}, {}, expected_revision=0), 1)

    def test_history_limit_never_discards_earlier_events(self):
        with patch.object(ledger, "MAX_COMMITS", 2):
            value = self.create()
            value.commit({"n": 1}, {"n": 1})
            value.commit({"n": 2}, {"n": 2})
            with self.assertRaisesRegex(ledger.LedgerError, "limit"):
                value.commit({"n": 3}, {"n": 3})
            self.assertEqual(value.read(), {"n": 2})
            self.assertEqual(len(value.history()), 3)

    def test_database_quota_fails_without_an_acknowledged_revision(self):
        value = self.create()
        value._db.execute("PRAGMA max_page_count=4")
        with self.assertRaises(ledger.LedgerError):
            value.commit({"large": "x" * 60000}, {"kind": "too-large-for-db"})
        with self.assertRaisesRegex(ledger.LedgerError, "poisoned"):
            value.read()
        value.close()
        with ledger.Ledger.open(self.directory) as reopened:
            self.assertEqual(reopened.revision, 0)
            self.assertEqual(reopened.read()["epoch"], 1)

    def test_failure_before_and_after_commit_poison_until_reopen(self):
        for point in ("before-commit", "after-commit"):
            directory = self.root / point
            with ledger.Ledger.create(directory, {"old": True}) as value:
                def fail(name):
                    if name == point:
                        raise OSError("injected IO failure")
                value._fault = fail
                with self.assertRaisesRegex(ledger.LedgerError, "commit failed"):
                    value.commit({"new": True}, {"kind": "write"})
                for operation in (value.read, value.history, lambda: value.revision,
                                  lambda: value.commit({}, {})):
                    with self.assertRaisesRegex(ledger.LedgerError, "poisoned"):
                        operation()
            with ledger.Ledger.open(directory) as reopened:
                expected = {"old": True} if point == "before-commit" else {"new": True}
                self.assertEqual(reopened.read(), expected)

    def test_subprocess_death_recovers_atomic_old_or_new_state_and_event(self):
        value = self.create()
        value.close()
        script = ("import os\nfrom handoff_ledger import Ledger\nl=Ledger.open(sys.argv[2])\n"
                  "def fault(point):\n if point==sys.argv[3]: os._exit(23)\n"
                  "l._fault=fault\nl.commit({'epoch':2,'operations':['retained']},{'kind':'accepted'})")
        child = self.child(script, "before-commit")
        self.assertEqual(child.returncode, 23, child.stderr)
        with ledger.Ledger.open(self.directory) as reopened:
            self.assertEqual(reopened.revision, 0)
            self.assertEqual(reopened.read()["epoch"], 1)
        child = self.child(script, "after-commit")
        self.assertEqual(child.returncode, 23, child.stderr)
        with ledger.Ledger.open(self.directory) as reopened:
            self.assertEqual(reopened.revision, 1)
            self.assertEqual(reopened.read(), {"epoch": 2, "operations": ["retained"]})
            self.assertEqual(reopened.history()[-1]["event"], {"kind": "accepted"})

    def test_threads_have_one_serial_commit_order(self):
        value = self.create()
        failures = []
        def work(number):
            try:
                value.commit({"writer": number}, {"writer": number})
            except Exception as error:
                failures.append(error)
        threads = [threading.Thread(target=work, args=(number,)) for number in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertFalse(failures)
        self.assertEqual(value.revision, 12)
        self.assertEqual({r["event"]["writer"] for r in value.history()[1:]}, set(range(12)))

    def test_corruption_wrong_version_and_unexpected_schema_fail_closed(self):
        mutations = (
            ("version", "PRAGMA user_version=999"),
            ("application", "PRAGMA application_id=0"),
            ("state", "UPDATE head SET state=x'7b7d0a'"),
            ("event", "UPDATE events SET event=x'7b7d0a' WHERE revision=0"),
            ("missing", "DELETE FROM events"),
            ("schema", "CREATE TABLE injected (value TEXT)"),
        )
        for name, sql in mutations:
            directory = self.root / name
            ledger.Ledger.create(directory, {"old": True}).close()
            db = sqlite3.connect(directory / "ledger.sqlite3")
            try:
                with db:
                    db.execute(sql)
            finally:
                db.close()
            with self.subTest(name=name), self.assertRaises(ledger.LedgerError):
                ledger.Ledger.open(directory)
        directory = self.root / "corrupt"
        ledger.Ledger.create(directory, {}).close()
        (directory / "ledger.sqlite3").write_bytes(b"not sqlite")
        with self.assertRaises(ledger.LedgerError):
            ledger.Ledger.open(directory)

    def test_symlinks_hardlinks_wrong_modes_and_unknown_files_fail_closed(self):
        value = self.create()
        value.close()
        alias = self.root / "alias"
        alias.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(ledger.LedgerError):
            ledger.Ledger.open(alias)
        db = self.directory / "ledger.sqlite3"
        saved = self.root / "saved"
        db.rename(saved)
        db.symlink_to(saved)
        with self.assertRaises(ledger.LedgerError):
            ledger.Ledger.open(self.directory)
        db.unlink()
        os.link(saved, db)
        with self.assertRaises(ledger.LedgerError):
            ledger.Ledger.open(self.directory)
        db.unlink()
        saved.rename(db)
        for mode in (0o644, 0o400, 0o660):
            db.chmod(mode)
            with self.subTest(mode=mode), self.assertRaises(ledger.LedgerError):
                ledger.Ledger.open(self.directory)
        db.chmod(0o600)
        self.directory.chmod(0o750)
        with self.assertRaises(ledger.LedgerError):
            ledger.Ledger.open(self.directory)
        self.directory.chmod(0o700)
        (self.directory / "ledger.sqlite3-wal").write_bytes(b"unknown")
        with self.assertRaises(ledger.LedgerError):
            ledger.Ledger.open(self.directory)

    def test_live_inode_replacement_or_permission_drift_poison_the_instance(self):
        value = self.create()
        db = self.directory / "ledger.sqlite3"
        other = self.root / "other"
        shutil.copyfile(db, other)
        other.chmod(0o600)
        os.replace(other, db)
        with self.assertRaisesRegex(ledger.LedgerError, "identity changed"):
            value.read()
        with self.assertRaisesRegex(ledger.LedgerError, "poisoned"):
            value.commit({}, {})

    def test_wrong_owner_and_page_size_are_not_silently_repaired(self):
        value = self.create()
        value.close()
        with patch.object(ledger.os, "geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(ledger.LedgerError):
                ledger.Ledger.open(self.directory)
        db = sqlite3.connect(self.directory / "ledger.sqlite3")
        try:
            db.execute("PRAGMA page_size=8192")
            db.execute("VACUUM")
        finally:
            db.close()
        with self.assertRaisesRegex(ledger.LedgerError, "page size"):
            ledger.Ledger.open(self.directory)

    @unittest.skipUnless(hasattr(os, "fork"), "fork guard requires POSIX")
    def test_forked_child_cannot_use_or_unlock_parent_ledger(self):
        value = self.create()
        pid = os.fork()
        if pid == 0:
            try:
                for call in (value.read, value.close):
                    try:
                        call()
                    except ledger.LedgerError:
                        pass
                    else:
                        os._exit(9)
                os._exit(0)
            except BaseException:
                os._exit(10)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        with self.assertRaises(ledger.BusyError):
            ledger.Ledger.open(self.directory)
        self.assertEqual(value.commit({"parent": True}, {}), 1)


if __name__ == "__main__":
    unittest.main()
