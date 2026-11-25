"""Actual production channel/authority/ledger through Linux child processes."""
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
from handoff_session import Session


class Clock:
    now = 0
    def __call__(self):
        return self.now


@unittest.skipUnless(handoff_channel._supported() and hasattr(os, "pidfd_open"), "Linux credential and pidfd integration required")
class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.owner = Authority.create(Path(self.temp.name) / "ledger", clock=self.clock)
        self.addCleanup(self.owner.close)
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.leftover = right.dup()  # Keeps EOF separate from registered-process exit.
        self.addCleanup(self.leftover.close)
        self.child = subprocess.Popen([sys.executable, str(HERE / "tests/session_peer.py"), str(right.fileno()), str(os.getpid())],
            pass_fds=(right.fileno(),), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        right.close()
        self.addCleanup(self.stop_child)
        self.assertEqual(self.read_line(), "started")
        self.session = Session(self.owner, left.detach(), app_instance="a" * 32,
                               expected_uid=os.getuid(), expected_pid=self.child.pid, clock=self.clock)
        self.addCleanup(self.session.close)

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
        self.child.stdin.write(value + "\n")
        self.child.stdin.flush()
        return json.loads(self.read_line())

    def negotiate(self):
        self.assertTrue(self.command("hello"))
        self.assertEqual(self.session.pump(), 1)
        self.assertEqual(self.command("receive")["verb"], "welcome")

    def grant(self):
        self.negotiate()
        grant = self.session.grant_inert()
        self.assertEqual(self.command("receive")["verb"], "grant")
        return grant.fields[4]

    def test_real_child_hello_grant_release_commit_and_successor(self):
        lease = self.grant()
        actor = self.owner.register_inert_actor(self.session.app, lease)
        operation = self.owner.begin_operation(actor, "acquire")
        self.session.quiesce(lease)
        self.assertEqual(self.command("receive")["verb"], "quiesce")
        self.assertTrue(self.command("done"))
        self.session.pump()
        with self.assertRaises(AuthorityError):
            self.owner.release_receipt(self.session.app, lease)
        self.owner.complete_operation(actor, operation)
        self.owner.actor_released(actor)
        receipt = self.owner.release_receipt(self.session.app, lease)
        self.session.commit_hidden(lease, receipt.fields[4])
        self.assertEqual(self.command("receive")["verb"], "commit")
        next_grant = self.session.grant_inert()
        self.assertNotEqual(next_grant.fields[4], lease)
        self.assertFalse(self.owner.live_hardware_admission)

    def test_held_real_worker_blocks_release_and_death_is_unknown(self):
        lease = self.grant()
        actor = self.owner.register_inert_actor(self.session.app, lease)
        operation = self.owner.begin_operation(actor, "acquire")
        worker = subprocess.Popen([sys.executable, "-c", "import sys; print('held',flush=True); sys.stdin.read()"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([worker.stdout], [], [], 5)[0])
            self.assertEqual(worker.stdout.readline().strip(), "held")
            self.session.quiesce(lease)
            self.command("receive")
            self.command("done")
            self.session.pump()
            with self.assertRaises(AuthorityError):
                self.owner.actor_released(actor)
            with self.assertRaises(AuthorityError):
                self.owner.release_receipt(self.session.app, lease)
            worker.kill()
            worker.communicate(timeout=5)
            self.owner.actor_lost(actor)
            record = self.owner.snapshot()["leases"][lease]
            self.assertEqual(record["operations"][operation]["state"], "UNKNOWN")
            self.assertEqual(record["state"], "QUARANTINED")
            with self.assertRaises(AuthorityError):
                self.session.grant_inert()
        finally:
            if worker.poll() is None: worker.kill()
            worker.communicate(timeout=5)

    def test_app_cannot_forge_the_authority_release(self):
        lease = self.grant()
        self.session.quiesce(lease)
        self.command("receive")
        self.command("forge-released")
        self.session.pump()
        self.assertTrue(self.session.closed)
        self.assertEqual(self.owner.snapshot()["leases"][lease]["state"], "QUARANTINED")

    def test_registered_pidfd_death_revokes_even_when_socket_copy_survives(self):
        lease = self.grant()
        self.assertFalse(os.get_inheritable(self.session.pidfd))
        self.child.kill()
        self.child.communicate(timeout=5)
        self.session.pump()
        self.assertTrue(self.session.closed)
        self.assertEqual(self.session.last_error, "registered-process-exited")
        self.assertEqual(self.owner.snapshot()["leases"][lease]["state"], "QUARANTINED")

    def test_socket_creator_cannot_send_as_the_registered_child(self):
        self.leftover.send(b"TVH2\thello\t" + b"a" * 32 + b"\tasync-drain,epoch-barrier,helper-receipts")
        self.session.pump()
        self.assertTrue(self.session.closed)
        self.assertEqual(self.session.last_error, "sender-identity-mismatch")
        self.assertIsNone(self.owner.snapshot()["active_lease"])

    def test_negotiation_deadline_and_prehello_messages_are_closed(self):
        self.clock.now = 1000000000
        self.session.pump()
        self.assertTrue(self.session.closed)
        self.assertEqual(self.session.last_error, "negotiation-expired")
        self.assertIsNone(self.owner.snapshot()["active_lease"])

    def test_nonhello_app_packet_cannot_negotiate_or_acquire(self):
        self.command("before-hello")
        self.session.pump()
        self.assertTrue(self.session.closed)
        self.assertIsNone(self.owner.snapshot()["active_lease"])

    def test_late_local_done_does_not_turn_expiry_into_release(self):
        lease = self.grant()
        self.session.quiesce(lease, budget_ms=1)
        self.command("receive")
        self.clock.now = 1000000
        self.command("done")
        self.session.pump()
        with self.assertRaises(AuthorityError):
            self.owner.release_receipt(self.session.app, lease)
        self.assertEqual(self.owner.snapshot()["leases"][lease]["quiesce"]["status"], "expired")

    def test_storage_failure_closes_channel_without_success_response(self):
        self.negotiate()
        self.command("hello")
        with patch.object(self.owner.ledger, "read", side_effect=LedgerError("storage failed")):
            with self.assertRaises(ValueError):
                self.session.pump()
        self.assertTrue(self.session.closed)
        self.assertEqual(self.session.channel.fd, -1)


if __name__ == "__main__":
    unittest.main()
