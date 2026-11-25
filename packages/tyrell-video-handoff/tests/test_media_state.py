"""Real SQLite media permits remain held through graph and actor finality."""
import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from handoff_authority import Authority,AuthorityError
from handoff_ledger import Ledger,LedgerError
from handoff_media_state import MediaJournal,sha,LIMIT
import test_display_state as display_fixtures


class MediaStateTests(unittest.TestCase):
    def setUp(self):
        display_fixtures.DisplayStateTests.setUp(self)
        display_fixtures.DisplayStateTests.acquire(self,self.lcd)
        self.borrower=self.displays.register_borrower(self.lcd,kind="graph")
        self.journal=MediaJournal(self.owner)
        self.journal.attach(self.lcd,self.borrower)

    def entry(self):
        return self.parts.snapshot(self.lcd)["display"]["borrowers"][self.borrower.actor.identifier]

    def identity(self):
        record=self.parts.snapshot(self.lcd)
        return {"kind":"media-fixture-v4","epoch":record["display"]["epoch"],"resource":record["display"]["resource"],
            "generation":self.lcd.generation,"pid":os.getpid(),"uid":self.uid,"profile":"lcd-fake-audio"}

    def bind(self):
        self.journal.bind(self.lcd,self.borrower,pid=os.getpid(),uid=self.uid,identity=self.identity())

    def permit(self,kind,request,payload=None):
        return self.journal.permit(self.lcd,self.borrower,kind=kind,request=request,payload={} if payload is None else payload)

    def operation(self,name):
        return self.owner.snapshot()["leases"][self.lease]["operations"][name]

    def event(self,kind,request,operation,outcome="completed",graph=None):
        identity=self.identity()
        return {"kind":kind,"epoch":identity["epoch"],"resource":identity["resource"],"generation":identity["generation"],
                "request":request,"operation":operation,"outcome":outcome,"graph":graph}

    def graph(self,drain):
        identity=self.identity()
        return {"kind":"graph-drain-v4","epoch":identity["epoch"],"resource":identity["resource"],"generation":identity["generation"],
                "operation":drain,"graph_serial":1,"callbacks_started":7,"callbacks_completed":7,"active_callbacks":0,
                "callback_contexts":0,"live_objects":0,"pipeline_finalized":True,"local_handles_released":True,
                "legacy_helpers_unused":True,"finality_receipt":"e"*32}

    def seal(self):
        display_fixtures.DisplayStateTests.seal(self)
        return self.permit("drain",100)

    def finish_graph(self):
        drain=self.seal()
        graph=self.graph(drain)
        self.journal.event(self.lcd,self.borrower,self.event("drain",100,drain,graph=graph))
        return drain,graph

    def parked(self):
        identity=self.identity()
        return {"epoch":identity["epoch"],"resource":identity["resource"],"generation":identity["generation"],
            "lifetime_operation":self.borrower.operation,"epoch_fenced":True,"unknown":False,"active":False,"queued":0,"events_pending":0}

    def test_command_execution_and_graph_done_do_not_retire_process_lifetime(self):
        self.bind()
        load=self.permit("load",1,{"path":"controlled.mp4"})
        self.journal.event(self.lcd,self.borrower,self.event("load",1,load))
        self.assertEqual(self.operation(load)["state"],"PENDING")
        drain,_graph=self.finish_graph()
        self.assertEqual(self.operation(drain)["state"],"PENDING")
        with self.assertRaises(AuthorityError):self.parts.drain(self.lcd)
        shutdown=self.permit("shutdown",101)
        self.journal.event(self.lcd,self.borrower,self.event("shutdown",101,shutdown))
        self.journal.fence(self.lcd,self.borrower,{"finished":True,"unknown":False,"thread_joined":True})
        with self.assertRaises(AuthorityError):self.parts.drain(self.lcd)
        self.journal.reaped(self.lcd,self.borrower)
        self.assertEqual(self.entry()["state"],"DONE")
        self.assertTrue(self.entry()["media"]["reaped"])
        for operation in (load,drain,shutdown,self.borrower.operation):self.assertEqual(self.operation(operation)["state"],"DONE")
        self.assertIsNotNone(self.parts.drain(self.lcd))

    def test_positive_parked_epoch_fences_old_work_without_claiming_process_death(self):
        self.bind()
        accepted=self.permit("pause",1)
        self.journal.event(self.lcd,self.borrower,self.event("pause",1,accepted))
        self.finish_graph()
        for change in ({"queued":1},{"events_pending":1},{"active":True},{"epoch_fenced":False},
                       {"generation":"c"*32},{"queued":False}):
            with self.subTest(change=change),self.assertRaises(AuthorityError):
                self.journal.epoch_fence(self.lcd,self.borrower,{**self.parked(),**change})
        self.journal.epoch_fence(self.lcd,self.borrower,self.parked())
        entry=self.entry()
        self.assertEqual(entry["state"],"DONE")
        self.assertEqual(entry["media"]["fence_mode"],"epoch")
        self.assertFalse(entry["media"]["reaped"])
        with self.assertRaises(AuthorityError):self.permit("play",102)

    def test_suppressed_undispatched_operation_is_the_only_early_terminal_permit(self):
        self.bind()
        executed=self.permit("seek",1,{"seconds":2})
        suppressed=self.permit("volume",2,{"volume":.4})
        self.journal.event(self.lcd,self.borrower,self.event("seek",1,executed))
        event=self.event("volume",2,suppressed,outcome="suppressed")
        self.journal.event(self.lcd,self.borrower,event)
        self.assertEqual(self.operation(executed)["state"],"PENDING")
        self.assertEqual(self.operation(suppressed)["state"],"DONE")
        self.assertEqual(self.entry()["state"],"PENDING")
        revision=self.owner.ledger.revision
        self.journal.event(self.lcd,self.borrower,event)
        self.assertEqual(self.owner.ledger.revision,revision)
        with self.assertRaises(AuthorityError):self.journal.event(self.lcd,self.borrower,{**event,"outcome":"completed"})

    def test_native_profile_identity_and_stale_results_cannot_certify_graph(self):
        expected=self.identity()
        for change in ({"pid":os.getpid()+1},{"uid":True},{"profile":"hdmi"},{"resource":"c"*32}):
            with self.subTest(change=change),self.assertRaises(AuthorityError):
                self.journal.bind(self.lcd,self.borrower,pid=os.getpid(),uid=self.uid,identity={**expected,**change})
        self.bind()
        drain=self.seal()
        for change in ({"active_callbacks":1},{"callback_contexts":1},{"live_objects":1},{"pipeline_finalized":False},
                       {"callbacks_completed":6},{"legacy_helpers_unused":False},{"operation":"b"*32},{"epoch":2},
                       {"graph_serial":True},{"finality_receipt":"unqualified"}):
            with self.subTest(change=change),self.assertRaises(AuthorityError):
                self.journal.event(self.lcd,self.borrower,self.event("drain",100,drain,graph={**self.graph(drain),**change}))
        event=self.event("drain",100,drain,graph=self.graph(drain))
        for change in ({"request":True},{"generation":"b"*32},{"operation":"c"*32}):
            with self.subTest(change=change),self.assertRaises(AuthorityError):
                self.journal.event(self.lcd,self.borrower,{**event,**change})
        self.assertEqual(self.operation(drain)["state"],"PENDING")
        self.assertIsNone(self.entry()["media"]["graph"])

    def test_command_quota_and_seal_preserve_lifecycle_reserve_and_payload_binding(self):
        self.bind()
        for request in range(1,LIMIT+1):self.permit("play",request,{"intent":request})
        with self.assertRaises(AuthorityError):self.permit("play",LIMIT+1)
        with self.assertRaises(AuthorityError):self.permit("pause",LIMIT)
        drain=self.seal()
        revision=self.owner.ledger.revision
        self.assertEqual(self.permit("drain",100),drain)
        self.assertEqual(self.owner.ledger.revision,revision)
        with self.assertRaises(AuthorityError):self.permit("drain",100,{"changed":True})
        with self.assertRaises(AuthorityError):self.permit("load",101,{"path":"late.mp4"})
        self.assertIsNotNone(self.permit("shutdown",101))
        self.assertEqual(self.entry()["media"]["commands"][drain]["payload_sha256"],sha({}))

    def test_unknown_native_result_quarantines_and_cannot_be_overwritten(self):
        self.bind()
        accepted=self.permit("seek",1)
        self.journal.event(self.lcd,self.borrower,self.event("seek",1,accepted,outcome="unknown"))
        self.assertEqual(self.operation(accepted)["state"],"UNKNOWN")
        self.assertEqual(self.owner.snapshot()["leases"][self.lease]["state"],"QUARANTINED")
        with self.assertRaises(AuthorityError):self.journal.event(self.lcd,self.borrower,self.event("seek",1,accepted))
        with self.assertRaises(AuthorityError):self.journal.epoch_fence(self.lcd,self.borrower,self.parked())
        with self.assertRaises(AuthorityError):self.journal.reaped(self.lcd,self.borrower)

    def test_restart_preserves_native_identity_and_pending_graph_commands_as_unknown(self):
        self.bind()
        accepted=self.permit("load",1,{"path":"controlled.mp4"})
        identity_hash=self.entry()["media"]["identity_sha256"]
        self.owner.close()
        self.owner=Authority.open(self.path,clock=self.clock)
        state=self.owner.snapshot()
        entry=state["participants"][self.lcd.actor.identifier]["display"]["borrowers"][self.borrower.actor.identifier]
        self.assertEqual(entry["state"],"UNKNOWN")
        self.assertEqual(entry["media"]["phase"],"unknown")
        self.assertEqual(entry["media"]["identity_sha256"],identity_hash)
        self.assertEqual(state["leases"][self.lease]["operations"][accepted]["state"],"UNKNOWN")
        with self.assertRaises(AuthorityError):MediaJournal(self.owner).reaped(self.lcd,self.borrower)

    def test_lost_display_worker_marks_media_and_held_commands_unknown_after_graph_done(self):
        self.bind()
        loaded=self.permit("load",1,{"path":"controlled.mp4"})
        paused=self.permit("pause",2)
        unused=self.permit("volume",3,{"volume":.4})
        self.journal.event(self.lcd,self.borrower,self.event("load",1,loaded))
        self.journal.event(self.lcd,self.borrower,self.event("pause",2,paused))
        self.journal.event(self.lcd,self.borrower,self.event("volume",3,unused,outcome="suppressed"))
        drain,graph=self.finish_graph()
        held=(loaded,paused,drain,self.borrower.operation)
        for operation in held:self.assertEqual(self.operation(operation)["state"],"PENDING")

        self.parts.lost(self.lcd)

        record=self.parts.snapshot(self.lcd)
        entry=self.entry()
        self.assertEqual(record["phase"],"unknown")
        self.assertEqual(entry["state"],"UNKNOWN")
        self.assertEqual(entry["media"]["phase"],"unknown")
        self.assertEqual(entry["media"]["graph"],graph)
        self.assertIsNone(entry["proof"])
        for operation in held:self.assertEqual(self.operation(operation)["state"],"UNKNOWN")
        self.assertEqual(self.operation(unused)["state"],"DONE")
        self.assertEqual(self.owner.snapshot()["leases"][self.lease]["state"],"QUARANTINED")
        self.assertIsNone(self.parts.drain(self.lcd))
        with self.assertRaises(AuthorityError):self.journal.epoch_fence(self.lcd,self.borrower,self.parked())
        with self.assertRaises(AuthorityError):self.journal.reaped(self.lcd,self.borrower)
        with self.assertRaises(AuthorityError):self.owner.release_receipt(self.app,self.lease)

    def test_uncertain_permit_commit_never_returns_a_dispatch_right(self):
        self.bind()
        original=self.owner.ledger.commit
        def uncertain(*args,**kwargs):
            original(*args,**kwargs)
            raise LedgerError("acknowledgment lost")
        with patch.object(self.owner.ledger,"commit",side_effect=uncertain):
            with self.assertRaises(LedgerError):self.permit("load",1,{"path":"controlled.mp4"})
        with self.assertRaises(AuthorityError):self.owner.snapshot()
        self.owner.close()
        self.owner=Authority.open(self.path,clock=self.clock)
        state=self.owner.snapshot()
        media=state["participants"][self.lcd.actor.identifier]["display"]["borrowers"][self.borrower.actor.identifier]["media"]
        self.assertEqual(media["phase"],"unknown")
        self.assertEqual(len(media["commands"]),1)
        operation=next(iter(media["commands"]))
        self.assertEqual(state["leases"][self.lease]["operations"][operation]["state"],"UNKNOWN")

    def test_old_schema_three_is_refused_without_erasing_media_barriers(self):
        self.bind()
        self.permit("play",1)
        state=self.owner.snapshot();state["schema"]=3
        path=Path(self.temp.name)/"schema-three"
        with Ledger.create(path,state):pass
        before=(path/"ledger.sqlite3").read_bytes()
        with self.assertRaises(AuthorityError):Authority.open(path)
        self.assertEqual((path/"ledger.sqlite3").read_bytes(),before)


if __name__=="__main__":unittest.main()
