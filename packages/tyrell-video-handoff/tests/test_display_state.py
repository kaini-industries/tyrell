"""Actual SQLite authority barriers for display borrowers and VT dependencies."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from handoff_authority import Authority, AuthorityError
from handoff_display_state import Displays, SCOPES
from handoff_ledger import Ledger, LedgerError
from handoff_participant_state import Participants
from handoff_protocol import Message,CAPABILITIES


class Clock:
    now=0
    def __call__(self): return self.now


class DisplayStateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"ledger"
        self.clock=Clock()
        self.owner=Authority.create(self.path,disposable=True,clock=self.clock)
        self.addCleanup(lambda:self.owner.close())
        self.uid=max(1,os.getuid())
        self.app=self.owner.bootstrap_app("a"*32,uid=self.uid,pid=os.getpid())
        self.owner.negotiate(self.app,Message("hello",(self.app.instance,CAPABILITIES)))
        self.lease=self.owner.grant_disposable(self.app).fields[4]
        self.parts=Participants(self.owner)
        self.displays=Displays(self.owner)
        self.kms=self.parts.register(self.app,self.lease,domain="kms",profile="kms-fixture-v3",uid=self.uid)
        self.lcd=self.parts.register(self.app,self.lease,domain="lcd",profile="lcd-fixture-v3",uid=self.uid)
        self.vt=self.parts.register(self.app,self.lease,domain="vt-display",profile="vt-display-fixture-v3",uid=self.uid,
            required_displays=(self.kms.actor.identifier,self.lcd.actor.identifier))

    def acquire(self,cap):
        record=self.parts.snapshot(cap)
        if record["display"] is not None:
            prepared={"kind":"display-fixture-v3","domain":record["domain"],"epoch":record["display"]["epoch"],
                "resource":record["display"]["resource"],"generation":cap.generation,"scope":SCOPES[record["domain"]],
                "backend_pid":os.getpid(),"backend_uid":self.uid,"native_identity_sha256":hashlib.sha256(b"controlled descriptor identity").hexdigest()}
        else:
            prepared={"kind":"vt-v2","version":2,"backend":"f"*32,"managed_vt":2,"neutral_vt":1,
                      "token":cap.generation,"state":"NEW"}
        operation=self.parts.bind(cap,pid=os.getpid(),uid=self.uid,prepared=prepared)
        evidence={**prepared,"operation":operation,"admitted":True} if record["display"] is not None else {**prepared,"state":"OWNED"}
        self.parts.completed(cap,phase="acquire",evidence=evidence)

    def seal(self,budget_ms=10000):
        packet=self.owner.quiesce(self.app,self.lease,budget_ms=budget_ms)
        self.owner.local_done(self.app,Message("local-done",(*packet.fields[:4],"none")))

    def borrower_proof(self,cap,borrower,disposition="drained"):
        record=self.parts.snapshot(cap)
        return {"epoch":record["display"]["epoch"],"resource":record["display"]["resource"],"generation":cap.generation,
                "actor":borrower.actor.identifier,"operation":borrower.operation,"disposition":disposition,"receipt":"e"*32}

    def cleanup_proof(self,cap,operation):
        record=self.parts.snapshot(cap)
        return {**record["prepared"],"operation":operation,"sealed":True,"borrowers":0,"queued":0,"inflight":0,
                "driver_finality":True,"callbacks_suppressed":True,"native_receipt":cap.generation,
                "native_sha256":hashlib.sha256(b"controlled native facts").hexdigest()}

    def finish(self,cap):
        operation=self.parts.drain(cap)
        self.parts.completed(cap,phase="cleanup",evidence=self.cleanup_proof(cap,operation))
        self.parts.fence_ack(cap)
        self.parts.reaped(cap)
        self.parts.stop_post(cap)

    def test_graph_and_flush_borrowers_block_cleanup_after_app_local_done(self):
        graph=self.displays.register_borrower(self.kms,kind="graph")
        flush=self.displays.register_borrower(self.lcd,kind="flush")
        for cap in (self.kms,self.lcd,self.vt): self.acquire(cap)
        self.seal()
        for cap in (self.kms,self.lcd,self.vt):
            with self.assertRaises(AuthorityError): self.parts.drain(cap)
        self.displays.borrower_done(self.kms,graph,self.borrower_proof(self.kms,graph))
        self.finish(self.kms)
        with self.assertRaises(AuthorityError): self.parts.drain(self.vt)
        self.displays.borrower_done(self.lcd,flush,self.borrower_proof(self.lcd,flush,"suppressed-before-start"))
        self.finish(self.lcd)
        operation=self.parts.drain(self.vt)
        self.assertIsNotNone(operation)
        self.assertEqual(self.parts.snapshot(self.vt)["display_proofs"],{
            self.kms.actor.identifier:self.kms.generation,self.lcd.actor.identifier:self.lcd.generation})
        with self.assertRaises(AuthorityError): self.owner.release_receipt(self.app,self.lease)
        self.parts.completed(self.vt,phase="cleanup",evidence={**self.parts.snapshot(self.vt)["prepared"],"state":"RELEASED"})
        self.parts.fence_ack(self.vt)
        self.parts.reaped(self.vt)
        self.parts.stop_post(self.vt)
        self.owner.release_receipt(self.app,self.lease)

    def test_empty_missing_duplicate_and_foreign_display_dependencies_are_refused(self):
        for values in ((),(self.kms.actor.identifier,), (self.kms.actor.identifier,self.kms.actor.identifier),
                       (self.kms.actor.identifier,"b"*32)):
            with self.subTest(values=values),self.assertRaises(AuthorityError):
                self.parts.register(self.app,self.lease,domain="vt-display",profile="vt-display-fixture-v3",uid=self.uid,
                                    required_displays=values)

    def test_fence_and_readback_without_terminal_driver_finality_cannot_release(self):
        self.acquire(self.kms)
        self.seal()
        operation=self.parts.drain(self.kms)
        for change in ({"driver_finality":False},{"callbacks_suppressed":False},{"inflight":1},{"queued":False},
                       {"native_receipt":""},{"generation":"b"*32},{"operation":"c"*32}):
            with self.subTest(change=change),self.assertRaises(AuthorityError):
                self.parts.completed(self.kms,phase="cleanup",evidence={**self.cleanup_proof(self.kms,operation),**change})
        record=self.parts.snapshot(self.kms)
        self.assertEqual(self.owner.snapshot()["leases"][self.lease]["operations"][record["cleanup"]]["state"],"PENDING")

    def test_display_cleanup_alone_cannot_authorize_vt_before_future_cleanup_fence(self):
        for cap in (self.kms,self.lcd,self.vt): self.acquire(cap)
        self.seal()
        for cap in (self.kms,self.lcd):
            operation=self.parts.drain(cap)
            self.parts.completed(cap,phase="cleanup",evidence=self.cleanup_proof(cap,operation))
        with self.assertRaises(AuthorityError): self.parts.drain(self.vt)
        for cap in (self.kms,self.lcd):
            self.parts.fence_ack(cap)
            self.parts.reaped(cap)
        with self.assertRaises(AuthorityError): self.parts.drain(self.vt)
        for cap in (self.kms,self.lcd): self.parts.stop_post(cap)
        self.assertIsNotNone(self.parts.drain(self.vt))

    def test_seal_rejects_late_borrowers_and_stale_graph_completions(self):
        borrower=self.displays.register_borrower(self.kms,kind="graph")
        self.seal()
        with self.assertRaises(AuthorityError): self.displays.register_borrower(self.kms,kind="graph")
        proof=self.borrower_proof(self.kms,borrower)
        for change in ({"generation":"b"*32},{"epoch":2},{"resource":"d"*32},{"disposition":"suppressed-before-start"}):
            with self.subTest(change=change),self.assertRaises(AuthorityError):
                self.displays.borrower_done(self.kms,borrower,{**proof,**change})
        self.displays.borrower_done(self.kms,borrower,proof)
        revision=self.owner.ledger.revision
        self.displays.borrower_done(self.kms,borrower,proof)
        self.assertEqual(self.owner.ledger.revision,revision)

    def test_borrower_loss_and_fd_closure_are_unknown_not_drain(self):
        borrower=self.displays.register_borrower(self.kms,kind="graph")
        self.acquire(self.kms)
        self.seal()
        self.displays.borrower_lost(self.kms,borrower)
        record=self.parts.snapshot(self.kms)
        self.assertEqual(record["display"]["borrowers"][borrower.actor.identifier]["state"],"UNKNOWN")
        self.assertEqual(self.owner.snapshot()["leases"][self.lease]["state"],"QUARANTINED")
        with self.assertRaises(AuthorityError): self.parts.drain(self.kms)

    def test_restart_preserves_display_scope_and_unknown_submitted_work(self):
        borrower=self.displays.register_borrower(self.lcd,kind="flush")
        self.acquire(self.lcd)
        before=self.parts.snapshot(self.lcd)
        proof=self.borrower_proof(self.lcd,borrower)
        self.owner.close()
        self.owner=Authority.open(self.path,clock=self.clock)
        record=self.owner.snapshot()["participants"][self.lcd.actor.identifier]
        self.assertEqual(record["prepared"],before["prepared"])
        self.assertEqual(record["display"]["resource"],before["display"]["resource"])
        self.assertEqual(record["display"]["borrowers"][borrower.actor.identifier]["state"],"UNKNOWN")
        with self.assertRaises(AuthorityError): Displays(self.owner).borrower_done(self.lcd,borrower,proof)

    def test_expiry_winner_cannot_be_undone_by_late_display_and_vt_completion(self):
        for cap in (self.kms,self.lcd,self.vt): self.acquire(cap)
        self.seal(budget_ms=1)
        self.clock.now=1000000
        for cap in (self.kms,self.lcd): self.finish(cap)
        self.parts.drain(self.vt)
        self.parts.completed(self.vt,phase="cleanup",evidence={**self.parts.snapshot(self.vt)["prepared"],"state":"RELEASED"})
        self.parts.fence_ack(self.vt)
        self.parts.reaped(self.vt)
        self.parts.stop_post(self.vt)
        with self.assertRaises(AuthorityError): self.owner.release_receipt(self.app,self.lease)

    def test_schema_two_is_refused_without_rewriting(self):
        state=self.owner.snapshot()
        state["schema"]=2
        path=Path(self.temp.name)/"old"
        with Ledger.create(path,state): pass
        before=(path/"ledger.sqlite3").read_bytes()
        with self.assertRaises(AuthorityError): Authority.open(path)
        self.assertEqual((path/"ledger.sqlite3").read_bytes(),before)

    def test_storage_failure_cannot_return_a_borrower_permit(self):
        original=self.owner.ledger.commit
        def uncertain(*args,**kwargs):
            original(*args,**kwargs)
            raise LedgerError("acknowledgment lost")
        with patch.object(self.owner.ledger,"commit",side_effect=uncertain):
            with self.assertRaises(LedgerError): self.displays.register_borrower(self.lcd,kind="flush")
        with self.assertRaises(AuthorityError): self.owner.snapshot()
        self.owner.close()
        self.owner=Authority.open(self.path)
        record=self.owner.snapshot()["participants"][self.lcd.actor.identifier]
        self.assertEqual(len(record["display"]["borrowers"]),1)
        self.assertEqual(next(iter(record["display"]["borrowers"].values()))["state"],"UNKNOWN")


if __name__=="__main__": unittest.main()
