"""Actual credential-bound shell transitions, not app-issued resource grants."""
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import handoff_channel
from handoff_authority import Authority, AuthorityError
from handoff_ledger import LedgerError
from handoff_shell import ShellSession


class Clock:
    now = 0
    def __call__(self):
        return self.now


@unittest.skipUnless(handoff_channel._supported() and hasattr(os, "pidfd_open"),
                     "Linux credential and pidfd integration required")
class ShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.owner = Authority.create(Path(self.temp.name) / "ledger", clock=self.clock)
        self.addCleanup(self.owner.close)
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.child = subprocess.Popen([sys.executable, str(HERE / "tests/session_peer.py"),
            str(right.fileno()), str(os.getpid())], pass_fds=(right.fileno(),),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        right.close()
        self.addCleanup(self.stop_child)
        self.assertEqual(self.read_line(), "started")
        self.shell = ShellSession(self.owner, left.detach(), profile="inert", app_instance="a" * 32,
            expected_uid=os.getuid(), expected_pid=self.child.pid, clock=self.clock)
        self.addCleanup(self.shell.close)

    def stop_child(self):
        if self.child.poll() is None:
            self.child.kill()
        self.child.communicate(timeout=5)

    def read_line(self):
        self.assertTrue(select.select([self.child.stdout], [], [], 5)[0], "peer response timed out")
        line = self.child.stdout.readline().strip()
        self.assertTrue(line, "peer ended without a response")
        return line

    def command(self, value):
        self.child.stdin.write(value + "\n"); self.child.stdin.flush()
        return json.loads(self.read_line())

    def grant(self):
        self.command("hello"); self.shell.pump()
        self.assertEqual(self.command("receive")["verb"], "welcome")
        grant = self.shell.grant()
        self.assertEqual(self.command("receive")["verb"], "grant")
        return grant

    def done(self):
        self.command("done"); self.shell.pump()

    def test_composite_receipt_and_commit_precede_fresh_grant(self):
        grant = self.grant()
        actor = self.owner.register_inert_actor(self.shell.app, self.shell.lease)
        operation = self.owner.begin_operation(actor, "use")
        request = self.shell.quiesce()
        self.assertEqual(self.command("receive")["verb"], "quiesce")
        self.shell.cancel()
        self.assertEqual(self.command("receive")["verb"], "cancel")
        self.done()
        status = self.shell.status()
        self.assertEqual((status["state"], status["pending_operations"], status["pending_actors"]),
                         ("DRAINING", 1, 1))
        self.assertTrue(status["cancelled"])
        for action in (self.shell.grant, self.shell.release_receipt,
                       lambda: self.shell.commit_hidden("f" * 32)):
            with self.assertRaises(AuthorityError): action()
        self.owner.complete_operation(actor, operation); self.owner.actor_released(actor)
        receipt = self.shell.release_receipt()
        self.assertEqual(receipt.fields[:4], request.fields[:4])
        self.assertTrue(self.shell.status()["receipt_available"])
        with self.assertRaises(AuthorityError): self.shell.grant()
        commit = self.shell.commit_hidden(receipt.fields[4])
        self.assertEqual(commit.verb, "commit")
        self.assertEqual(self.command("receive")["verb"], "commit")
        fresh = self.shell.grant()
        self.assertGreater(int(fresh.fields[2]), int(grant.fields[2]))
        self.assertNotEqual(fresh.fields[4], grant.fields[4])
        self.assertFalse(self.shell.status()["commit_queued"])
        self.assertFalse(self.shell.live_hardware_admission)

    def test_expiry_sends_one_failure_without_signalling_or_retiring_work(self):
        self.grant()
        actor = self.owner.register_inert_actor(self.shell.app, self.shell.lease)
        operation = self.owner.begin_operation(actor, "use")
        self.shell.quiesce(budget_ms=1)
        self.command("receive")
        self.clock.now = 1000000
        with patch("os.kill", side_effect=AssertionError("shell must not signal")), \
             patch("signal.pidfd_send_signal", side_effect=AssertionError("shell must not signal")):
            self.shell.pump()
            failure = self.command("receive")
            self.assertEqual((failure["verb"], failure["fields"][-1]), ("failed", "expired"))
            self.shell.pump()
        self.assertIsNone(self.child.poll())
        self.assertEqual(self.shell.status()["pending_packets"], 0)
        self.assertEqual(self.shell.status()["request_status"], "expired")
        self.owner.complete_operation(actor, operation); self.owner.actor_released(actor)
        # The peer's last packet is failed, but contains the same transaction.
        self.done()
        self.assertEqual(self.shell.status()["state"], "QUARANTINED")
        with self.assertRaises(AuthorityError): self.shell.release_receipt()
        with self.assertRaises(AuthorityError): self.shell.grant()

    def test_output_uncertainty_keeps_new_durable_grant_quarantined(self):
        self.command("hello"); self.shell.pump(); self.command("receive")
        with patch.object(self.shell.session.channel, "send", return_value=False):
            with self.assertRaises(AuthorityError): self.shell.grant()
        self.assertTrue(self.shell.closed)
        self.assertEqual(self.shell.last_error, "grant-output-unavailable")
        state = self.owner.snapshot()
        self.assertEqual(state["active_lease"], self.shell.lease)
        self.assertEqual(state["leases"][self.shell.lease]["state"], "QUARANTINED")
        self.assertFalse(self.shell.status()["receipt_available"])

    def test_fixed_stop_reasons_revoke_without_forging_held_backend_completion(self):
        self.grant()
        actor = self.owner.register_inert_actor(self.shell.app, self.shell.lease)
        operation = self.owner.begin_operation(actor, "use")
        with self.assertRaises(AuthorityError): self.shell.stop("run-recovery-command")
        self.assertFalse(self.shell.closed)
        self.shell.stop("sigterm")
        self.assertTrue(self.shell.closed)
        self.assertEqual(self.shell.last_error, "sigterm")
        record = self.owner.snapshot()["leases"][self.shell.lease]
        self.assertEqual(record["state"], "QUARANTINED")
        self.assertEqual(record["operations"][operation]["state"], "PENDING")
        self.assertFalse(record["actors"][actor.identifier]["released"])
        self.assertIsNone(self.child.poll())

    def test_profile_refusal_precedes_descriptor_adoption_and_registration(self):
        self.assertFalse(self.shell.negotiated)
        with self.assertRaises(AuthorityError): self.shell.grant()
        before = self.owner.snapshot()
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        with left, right:
            for change in ({"profile": "live"}, {"profile": "disposable-v2"},
                           {"expected_uid": os.getuid() + 1}):
                options = {"profile": "inert", "app_instance": "b" * 32,
                    "expected_uid": os.getuid(), "expected_pid": self.child.pid, **change}
                with self.subTest(change=change), self.assertRaises(AuthorityError):
                    ShellSession(self.owner, left.fileno(), **options)
                self.assertGreaterEqual(left.fileno(), 3)
                self.assertEqual(self.owner.snapshot(), before)
                left.send(b"still-owned"); self.assertEqual(right.recv(32), b"still-owned")

    def test_uncertain_commit_never_authorizes_commit_or_successor(self):
        self.grant()
        self.shell.quiesce(); self.command("receive")
        with patch.object(self.owner.ledger, "commit", side_effect=LedgerError("durability unavailable")):
            self.command("done")
            with self.assertRaises(ValueError): self.shell.pump()
        self.assertTrue(self.shell.closed)
        with self.assertRaises(AuthorityError): self.shell.commit_hidden("f" * 32)
        with self.assertRaises(AuthorityError): self.shell.grant()


if __name__ == "__main__": unittest.main()
