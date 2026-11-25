"""Real authority/SQLite barriers with inert participants; no device adapters."""
import copy
from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import handoff_authority as authority
from handoff_ledger import LedgerError
from handoff_protocol import Message, CAPABILITIES


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "authority"
        self.clock = Clock()
        self.owner = authority.Authority.create(self.path, clock=self.clock)
        self.addCleanup(lambda: self.owner.close())
        self.app = self.bootstrap("a" * 32)

    def bootstrap(self, instance):
        return self.owner.bootstrap_app(instance, uid=os.getuid(), pid=os.getpid())

    def grant(self, app=None):
        app = app or self.app
        self.owner.negotiate(app, Message("hello", (app.instance, CAPABILITIES)))
        return self.owner.grant_inert(app).fields[4]

    def quiesce(self, lease, budget=1000):
        return self.owner.quiesce(self.app, lease, budget_ms=budget)

    def done(self, packet, checkpoint="none"):
        self.owner.local_done(self.app, Message("local-done", (*packet.fields[:4], checkpoint)))

    def test_no_grant_before_negotiation_or_from_forged_bootstrap(self):
        with self.assertRaises(authority.AuthorityError):
            self.owner.grant_inert(self.app)
        with self.assertRaises(authority.AuthorityError):
            self.owner.negotiate(replace(self.app, _token="0" * 32), Message("hello", (self.app.instance, CAPABILITIES)))
        self.assertFalse(self.owner.live_hardware_admission)
        self.assertEqual(self.owner.snapshot()["mode"], "inert")

    def test_permit_is_durable_before_return_and_actor_registry_seals(self):
        lease = self.grant()
        actor = self.owner.register_inert_actor(self.app, lease)
        operation = self.owner.begin_operation(actor, "acquire")
        self.assertEqual(self.owner.ledger.read()["leases"][lease]["operations"][operation]["state"], "PENDING")
        self.quiesce(lease)
        with self.assertRaises(authority.AuthorityError):
            self.owner.register_inert_actor(self.app, lease)
        with self.assertRaises(authority.AuthorityError):
            self.owner.begin_operation(actor, "acquire")
        cleanup = self.owner.begin_operation(actor, "cleanup")
        self.assertNotEqual(operation, cleanup)

    def test_local_done_and_eof_are_not_helper_completion(self):
        lease = self.grant()
        actor = self.owner.register_inert_actor(self.app, lease)
        operation = self.owner.begin_operation(actor, "use")
        packet = self.quiesce(lease)
        self.done(packet)
        with self.assertRaises(authority.AuthorityError):
            self.owner.release_receipt(self.app, lease)
        with self.assertRaises(authority.AuthorityError):
            self.owner.actor_released(actor)
        with self.assertRaises(authority.AuthorityError):
            self.owner.grant_inert(self.app)
        self.owner.complete_operation(actor, operation)
        with self.assertRaises(authority.AuthorityError):
            self.owner.release_receipt(self.app, lease)
        self.owner.actor_released(actor)
        receipt = self.owner.release_receipt(self.app, lease)
        self.assertEqual(receipt.verb, "released")
        self.assertIsNone(self.owner.ledger.read()["active_lease"])

    def test_app_cannot_self_certify_an_actor_or_outstanding_graph(self):
        lease = self.grant()
        actor = self.owner.app_actor(self.app, lease)
        operation = self.owner.begin_operation(actor, "use")
        packet = self.quiesce(lease)
        with self.assertRaises(authority.AuthorityError):
            self.owner.actor_released(actor)
        with self.assertRaises(authority.AuthorityError):
            self.done(packet)
        self.owner.complete_operation(actor, operation)
        self.done(packet)
        self.assertEqual(self.owner.release_receipt(self.app, lease).verb, "released")

    def test_actor_capabilities_cannot_complete_foreign_operations(self):
        lease = self.grant()
        first = self.owner.register_inert_actor(self.app, lease)
        second = self.owner.register_inert_actor(self.app, lease)
        operation = self.owner.begin_operation(first, "use")
        with self.assertRaises(authority.AuthorityError):
            self.owner.complete_operation(second, operation)
        with self.assertRaises(authority.AuthorityError):
            self.owner.complete_operation(replace(first, _token="0" * 32), operation)
        self.assertEqual(self.owner.snapshot()["leases"][lease]["operations"][operation]["state"], "PENDING")

    def test_terminal_failure_and_cancel_replays_cannot_mutate_the_winner(self):
        lease = self.grant()
        packet = self.quiesce(lease)
        failure = Message("failed", (*packet.fields[:4], "backend-failed"))
        self.owner.receive_app(self.app, failure)
        revision = self.owner.ledger.revision
        self.owner.receive_app(self.app, failure)
        self.owner.cancel(self.app, lease)
        self.assertEqual(self.owner.ledger.revision, revision)
        self.assertFalse(self.owner.snapshot()["leases"][lease]["cancelled"])

    def test_release_cannot_be_rewritten_by_late_failure_or_cancel(self):
        lease = self.grant()
        packet = self.quiesce(lease)
        self.done(packet)
        before = self.owner.snapshot()
        revision = self.owner.ledger.revision
        self.owner.receive_app(self.app, Message("failed", (*packet.fields[:4], "expired")))
        self.owner.cancel(self.app, lease)
        self.assertEqual(self.owner.snapshot(), before)
        self.assertEqual(self.owner.ledger.revision, revision)

    def test_expiry_wins_at_deadline_and_late_completion_cannot_resurrect_it(self):
        lease = self.grant()
        actor = self.owner.register_inert_actor(self.app, lease)
        operation = self.owner.begin_operation(actor, "acquire")
        packet = self.quiesce(lease, 1)
        self.clock.now = 1000000
        self.owner.complete_operation(actor, operation)
        self.owner.actor_released(actor)
        self.done(packet)
        state = self.owner.snapshot()
        self.assertEqual(state["leases"][lease]["state"], "QUARANTINED")
        self.assertEqual(state["leases"][lease]["quiesce"]["status"], "expired")
        with self.assertRaises(authority.AuthorityError):
            self.owner.release_receipt(self.app, lease)
        with self.assertRaises(authority.AuthorityError):
            self.owner.grant_inert(self.app)

    def test_release_winner_replays_after_deadline_and_next_grant_has_fresh_epoch(self):
        lease = self.grant()
        packet = self.quiesce(lease, 1)
        self.clock.now = 999999
        self.done(packet)
        receipt = self.owner.release_receipt(self.app, lease)
        revision = self.owner.ledger.revision
        self.clock.now = 9000000000
        self.assertEqual(self.owner.release_receipt(self.app, lease), receipt)
        self.assertEqual(self.owner.ledger.revision, revision)
        self.assertEqual(self.owner.commit_hidden(self.app, lease, receipt.fields[4]).verb, "commit")
        with self.assertRaises(authority.AuthorityError):
            self.owner.commit_hidden(self.app, lease, "0" * 32)
        grant = self.owner.grant_inert(self.app)
        self.assertGreater(int(grant.fields[2]), int(receipt.fields[2]))
        self.assertNotEqual(grant.fields[4], lease)

    def test_cancellation_drains_and_duplicates_do_not_repeat_effects_or_extend_deadline(self):
        lease = self.grant()
        packet = self.quiesce(lease)
        deadline = self.owner.snapshot()["leases"][lease]["quiesce"]["deadline_ns"]
        self.clock.now = 1000000
        repeated = self.quiesce(lease)
        self.assertEqual(packet.fields[:4], repeated.fields[:4])
        self.owner.cancel(self.app, lease)
        revision = self.owner.ledger.revision
        self.owner.cancel(self.app, lease)
        self.assertEqual(self.owner.ledger.revision, revision)
        self.owner.receive_app(self.app, Message("quiescing", (*packet.fields[:4], "audio")))
        self.assertEqual(self.owner.snapshot()["leases"][lease]["quiesce"]["deadline_ns"], deadline)
        with self.assertRaises(authority.AuthorityError):
            self.owner.grant_inert(self.app)
        self.done(packet)
        self.assertTrue(self.owner.snapshot()["leases"][lease]["cancelled"])

    def test_app_cannot_send_authority_receipts_or_stale_local_completion(self):
        lease = self.grant()
        packet = self.quiesce(lease)
        before = self.owner.snapshot()
        for message in (Message("released", (*packet.fields[:4], "c" * 32)),
                        Message("commit", (*packet.fields[:4], "c" * 32)),
                        Message("local-done", ("0" * 32, *packet.fields[1:4], "none"))):
            with self.subTest(verb=message.verb), self.assertRaises(authority.AuthorityError):
                self.owner.receive_app(self.app, message)
        self.assertEqual(self.owner.snapshot(), before)

    def test_failed_readback_and_disconnect_retain_barrier(self):
        lease = self.grant()
        actor = self.owner.register_inert_actor(self.app, lease)
        operation = self.owner.begin_operation(actor, "cleanup")
        self.quiesce(lease)
        self.owner.complete_operation(actor, operation, success=False)
        self.assertEqual(self.owner.snapshot()["leases"][lease]["state"], "QUARANTINED")
        self.owner.disconnect(self.app)
        successor = self.bootstrap("b" * 32)
        self.owner.negotiate(successor, Message("hello", (successor.instance, CAPABILITIES)))
        with self.assertRaises(authority.AuthorityError):
            self.owner.grant_inert(successor)

    def test_restart_preserves_unknown_work_and_rejects_old_capabilities(self):
        lease = self.grant()
        actor = self.owner.register_inert_actor(self.app, lease)
        operation = self.owner.begin_operation(actor, "acquire")
        old = self.app.incarnation
        self.owner.close()
        self.owner = authority.Authority.open(self.path, clock=self.clock)
        state = self.owner.snapshot()
        self.assertNotEqual(state["incarnation"], old)
        self.assertEqual(state["leases"][lease]["operations"][operation]["state"], "UNKNOWN")
        self.assertEqual(state["active_lease"], lease)
        with self.assertRaises(authority.AuthorityError):
            self.owner.complete_operation(actor, operation)
        successor = self.bootstrap("b" * 32)
        self.owner.negotiate(successor, Message("hello", (successor.instance, CAPABILITIES)))
        with self.assertRaises(authority.AuthorityError):
            self.owner.grant_inert(successor)

    def test_clean_released_restart_can_start_a_new_inert_incarnation(self):
        lease = self.grant()
        self.done(self.quiesce(lease))
        self.owner.close()
        self.owner = authority.Authority.open(self.path, clock=self.clock)
        self.app = self.bootstrap("b" * 32)
        self.assertNotEqual(self.grant(), lease)

    def test_commit_uncertainty_never_returns_a_permit(self):
        lease = self.grant()
        actor = self.owner.register_inert_actor(self.app, lease)
        commit = self.owner.ledger.commit
        def durable_then_lost(*args, **kwargs):
            commit(*args, **kwargs)
            raise LedgerError("commit reply lost")
        with patch.object(self.owner.ledger, "commit", side_effect=durable_then_lost):
            with self.assertRaises(LedgerError):
                self.owner.begin_operation(actor, "acquire")
        with self.assertRaises(authority.AuthorityError):
            self.owner.release_receipt(self.app, lease)
        self.owner.close()
        self.owner = authority.Authority.open(self.path, clock=self.clock)
        operations = self.owner.snapshot()["leases"][lease]["operations"]
        self.assertEqual(len(operations), 1)
        self.assertEqual(next(iter(operations.values()))["state"], "UNKNOWN")

    def test_known_storage_error_poisons_even_cached_receipt_queries(self):
        lease = self.grant()
        self.done(self.quiesce(lease))
        with patch.object(self.owner.ledger, "read", side_effect=LedgerError("storage lost")):
            with self.assertRaises(LedgerError):
                self.owner.snapshot()
        with self.assertRaises(authority.AuthorityError):
            self.owner.release_receipt(self.app, lease)

    def test_collision_never_overwrites_held_permit(self):
        lease = self.grant()
        actor = self.owner.register_inert_actor(self.app, lease)
        with patch.object(authority.secrets, "token_hex", return_value="d" * 32):
            operation = self.owner.begin_operation(actor, "use")
            with self.assertRaises(authority.AuthorityError):
                self.owner.begin_operation(actor, "use")
        self.assertEqual(list(self.owner.snapshot()["leases"][lease]["operations"]), [operation])

    def test_concurrent_terminal_actors_have_one_durable_release(self):
        lease = self.grant()
        actors = [self.owner.register_inert_actor(self.app, lease) for _ in range(2)]
        self.done(self.quiesce(lease))
        errors = []
        def release(actor):
            try:
                self.owner.actor_released(actor)
            except Exception as error:
                errors.append(error)
        workers = [threading.Thread(target=release, args=(actor,)) for actor in actors]
        for worker in workers: worker.start()
        for worker in workers: worker.join(5)
        self.assertFalse(errors)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        receipt = self.owner.release_receipt(self.app, lease)
        self.assertEqual(receipt.verb, "released")
        self.assertEqual(self.owner.ledger.read()["leases"][lease]["quiesce"]["receipt"], receipt.fields[4])

    def test_limits_and_terminal_actors_reject_new_work(self):
        lease = self.grant()
        actor = self.owner.register_inert_actor(self.app, lease)
        with patch.object(authority, "MAX_OPERATIONS", 1):
            operation = self.owner.begin_operation(actor, "use")
            with self.assertRaises(authority.AuthorityError):
                self.owner.begin_operation(actor, "use")
        packet = self.quiesce(lease)
        self.owner.complete_operation(actor, operation)
        self.owner.actor_released(actor)
        with self.assertRaises(authority.AuthorityError):
            self.owner.begin_operation(actor, "cleanup")
        self.done(packet)


if __name__ == "__main__":
    unittest.main()
