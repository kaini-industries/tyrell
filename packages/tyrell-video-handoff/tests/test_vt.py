"""Production VT adapter through controlled ioctls; never opens a console."""
from dataclasses import replace
import fcntl
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from handoff_authority import Authority, AuthorityError
from handoff_protocol import CAPABILITIES, Message
from handoff_vt import VtBackend, VtError, VT_GETSTATE, VT_ACTIVATE, VT_WAITACTIVE


class ControlledConsole:
    def __init__(self):
        self.active = 1
        self.calls = []
        self.pending = None
        self.fail = None
        self.readback = None
        self.hold = None

    def __call__(self, fd, operation, value, mutate=False):
        self.calls.append((operation, value if type(value) is int else None))
        if operation == self.fail:
            raise OSError("controlled ioctl failure")
        if operation == VT_GETSTATE:
            value[0] = self.active if self.readback is None else self.readback
        elif operation == VT_ACTIVATE:
            self.pending = value
        elif operation == VT_WAITACTIVE:
            if self.hold is not None:
                started, release_fd = self.hold
                started.set()
                os.read(release_fd, 1)
            self.active = self.pending
        else:
            raise AssertionError("unqualified console operation")
        return 0


class Clock:
    now = 0
    def __call__(self):
        return self.now


class VtTests(unittest.TestCase):
    def setUp(self):
        self.file = tempfile.TemporaryFile()
        self.addCleanup(self.file.close)
        self.console = ControlledConsole()
        self.backend = self.adopt()
        self.addCleanup(self.backend.close)

    def adopt(self, **overrides):
        # Only admission's fstat is controlled. Duplicate ownership, flags,
        # threading and the actual production transition methods remain real.
        info = SimpleNamespace(st_mode=stat.S_IFCHR | 0o600, st_rdev=os.makedev(4, 0))
        arguments = dict(managed_vt=2, neutral_vt=1, ioctl=self.console)
        arguments.update(overrides)
        with patch("handoff_vt.os.fstat", return_value=info):
            return VtBackend.adopt(self.file.fileno(), **arguments)

    def test_adoption_owns_only_a_cloexec_duplicate(self):
        self.assertFalse(self.backend.disposable)
        self.assertNotEqual(self.file.fileno(), self.backend._fd)
        self.assertTrue(fcntl.fcntl(self.backend._fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC)
        self.backend.close()
        os.fstat(self.file.fileno())
        self.assertEqual(self.console.calls, [])

    def test_regular_and_wrong_console_fds_are_rejected_without_mutation(self):
        original = fcntl.fcntl(self.file.fileno(), fcntl.F_GETFD)
        with self.assertRaises(VtError):
            VtBackend.adopt(self.file.fileno(), managed_vt=2, neutral_vt=1)
        wrong = SimpleNamespace(st_mode=stat.S_IFCHR, st_rdev=os.makedev(4, 1))
        with patch("handoff_vt.os.fstat", return_value=wrong), self.assertRaises(VtError):
            VtBackend.adopt(self.file.fileno(), managed_vt=2, neutral_vt=1)
        self.assertEqual(fcntl.fcntl(self.file.fileno(), fcntl.F_GETFD), original)
        os.fstat(self.file.fileno())

    def test_scope_and_token_validation_cannot_select_arbitrary_consoles(self):
        for pair in ((0, 1), (64, 1), (2, 2), (True, 1), ("2", 1)):
            with self.subTest(pair=pair), self.assertRaises(VtError):
                self.adopt(managed_vt=pair[0], neutral_vt=pair[1])
        for fd in (0, 1, 2, True, -1):
            with self.subTest(fd=fd), self.assertRaises(VtError):
                VtBackend.adopt(fd, managed_vt=2, neutral_vt=1)
        for token in ("", "a" * 31, "A" * 32, "../tty", 1):
            with self.subTest(token=token), self.assertRaises(VtError):
                self.backend.acquire(token)
        self.assertEqual(self.console.calls, [])

    def test_acquire_and_release_require_wait_and_effective_readback(self):
        owned = self.backend.acquire("a" * 32)
        self.assertEqual(self.backend.evidence(owned)["state"], "OWNED")
        self.assertEqual(self.console.active, 2)
        self.backend.release(owned, display_drained=True)
        self.assertEqual(self.backend.evidence(owned)["state"], "RELEASED")
        self.assertEqual(self.console.active, 1)
        self.assertEqual(self.console.calls, [
            (VT_GETSTATE, None), (VT_ACTIVATE, 2), (VT_WAITACTIVE, 2), (VT_GETSTATE, None),
            (VT_GETSTATE, None), (VT_ACTIVATE, 1), (VT_WAITACTIVE, 1), (VT_GETSTATE, None)])

    def test_release_needs_trusted_display_drain_and_exact_owned_result(self):
        owned = self.backend.acquire("a" * 32)
        calls = list(self.console.calls)
        for value in (False, 1, "true", None):
            with self.subTest(value=value), self.assertRaises(VtError):
                self.backend.release(owned, display_drained=value)
        with self.assertRaises(VtError):
            self.backend.release(replace(owned), display_drained=True)
        self.assertEqual(self.console.calls, calls)
        self.assertEqual(self.backend.snapshot()["state"], "OWNED")

    def test_terminal_replay_never_runs_an_old_transition_again(self):
        owned = self.backend.acquire("a" * 32)
        self.backend.release(owned, display_drained=True)
        self.console.active = 3
        calls = list(self.console.calls)
        self.backend.release(owned, display_drained=True)
        self.assertEqual(self.console.calls, calls)
        self.assertEqual(self.console.active, 3)
        with self.assertRaises(VtError):
            self.backend.acquire("b" * 32)

    def test_external_vt_before_acquire_causes_no_activation(self):
        self.console.active = 3
        with self.assertRaises(VtError):
            self.backend.acquire("a" * 32)
        self.assertEqual(self.console.calls, [(VT_GETSTATE, None)])
        self.assertEqual(self.backend.snapshot()["state"], "UNKNOWN")
        self.assertEqual(self.console.active, 3)

    def test_external_vt_before_release_never_restores_old_foreground(self):
        owned = self.backend.acquire("a" * 32)
        self.console.active = 3
        calls = len(self.console.calls)
        with self.assertRaises(VtError):
            self.backend.release(owned, display_drained=True)
        self.assertEqual(self.console.calls[calls:], [(VT_GETSTATE, None)])
        self.assertEqual(self.console.active, 3)
        self.assertEqual(self.backend.snapshot()["state"], "UNKNOWN")

    def test_ioctl_failure_is_unknown_and_cannot_produce_evidence(self):
        self.console.fail = VT_WAITACTIVE
        with self.assertRaises(VtError):
            self.backend.acquire("a" * 32)
        self.assertEqual(self.backend.snapshot()["state"], "UNKNOWN")
        with self.assertRaises(VtError):
            self.backend.evidence(self.backend._owned)
        with self.assertRaises(VtError):
            self.backend.acquire("a" * 32)

    def test_post_transition_readback_failure_cannot_certify_release(self):
        owned = self.backend.acquire("a" * 32)
        original = self.console.__call__
        def wrong_readback(fd, operation, value, mutate=False):
            result = original(fd, operation, value, mutate)
            if operation == VT_WAITACTIVE:
                self.console.readback = 3
            return result
        self.backend._ioctl = wrong_readback
        with self.assertRaises(VtError):
            self.backend.release(owned, display_drained=True)
        self.assertEqual(self.backend.snapshot()["state"], "UNKNOWN")
        with self.assertRaises(VtError):
            self.backend.evidence(owned)

    def test_close_does_not_restore_any_console(self):
        self.backend.acquire("a" * 32)
        calls = list(self.console.calls)
        self.backend.close()
        self.assertEqual(self.console.calls, calls)
        self.assertEqual(self.console.active, 2)
        self.assertEqual(self.backend.snapshot()["state"], "CLOSED")

    def test_held_ioctl_keeps_authority_barrier_through_expiry_and_late_return(self):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.addCleanup(os.close, write_fd)
        started = threading.Event()
        self.console.hold = (started, read_fd)
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            owner = Authority.create(Path(directory) / "ledger", clock=clock)
            self.addCleanup(owner.close)
            app = owner.bootstrap_app("b" * 32, uid=os.getuid(), pid=os.getpid())
            owner.negotiate(app, Message("hello", (app.instance, CAPABILITIES)))
            lease = owner.grant_inert(app).fields[4]
            actor = owner.register_inert_actor(app, lease)
            operation = owner.begin_operation(actor, "acquire")
            result = []
            thread = threading.Thread(target=lambda: result.append(self.backend.acquire("a" * 32)))
            thread.start()
            try:
                self.assertTrue(started.wait(5), "worker did not reach held ioctl")
                self.assertEqual(self.backend.snapshot()["state"], "ACQUIRING")
                with self.assertRaises(VtError):
                    self.backend.close()
                packet = owner.quiesce(app, lease, budget_ms=1)
                owner.local_done(app, Message("local-done", (*packet.fields[:4], "none")))
                clock.now = 1000000
                owner.tick()
                self.assertEqual(owner.snapshot()["leases"][lease]["operations"][operation]["state"], "PENDING")
                with self.assertRaises(AuthorityError):
                    owner.release_receipt(app, lease)
            finally:
                os.write(write_fd, b"x")
                thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(result), 1)
            owner.complete_operation(actor, operation)
            self.assertEqual(owner.snapshot()["leases"][lease]["state"], "QUARANTINED")
            with self.assertRaises(AuthorityError):
                owner.grant_inert(app)
            self.assertEqual(self.console.active, 2)


if __name__ == "__main__":
    unittest.main()
