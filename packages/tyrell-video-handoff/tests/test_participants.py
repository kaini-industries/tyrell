"""Actual durable participant transitions and disposable backend processes."""
import copy
import hashlib
import os
from pathlib import Path
import select
import signal
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from handoff_authority import Authority, AuthorityError
from handoff_channel import _supported
from handoff_ledger import Ledger, LedgerError
from handoff_participant_state import Participants
from handoff_participants import Packet, ParticipantCodec, ParticipantWorker, AudioWorkerConfig
from handoff_protocol import Message, CAPABILITIES
import handoff_audio as audio
import test_audio as audio_fixtures


class Clock:
    now = 0
    def __call__(self):
        return self.now


class ParticipantStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger"
        self.clock = Clock()
        self.owner = Authority.create(self.path, disposable=True, clock=self.clock)
        self.addCleanup(lambda: self.owner.close())
        self.app = self.owner.bootstrap_app("a" * 32, uid=max(1, os.getuid()), pid=os.getpid())
        self.owner.negotiate(self.app, Message("hello", (self.app.instance, CAPABILITIES)))
        self.lease = self.owner.grant_disposable(self.app).fields[4]
        self.records = Participants(self.owner)
        self.cap = self.records.register(self.app, self.lease, domain="audio", profile="null-v2", uid=max(1, os.getuid()))

    def prepared(self):
        token = self.cap.generation
        return {"generation": "f" * 32, "cookie": 7, "token": token,
                "name": "module-null-sink", "arguments": "sink_name=tyrell_v2_" + token + " rate=48000 channels=2",
                "server_uid": max(1, os.getuid()), "server_pid": os.getpid(), "socket_dev": 0, "socket_ino": 1}

    def acquired(self):
        prepared = self.prepared()
        self.records.bind(self.cap, pid=os.getpid(), uid=max(1, os.getuid()), prepared=prepared)
        proof = {**prepared, "index": 7}
        self.records.completed(self.cap, phase="acquire", evidence=proof)
        return proof

    def seal(self, budget_ms=10000):
        request = self.owner.quiesce(self.app, self.lease, budget_ms=budget_ms)
        self.owner.local_done(self.app, Message("local-done", (*request.fields[:4], "none")))

    def cleaned(self):
        acquired = self.acquired()
        self.seal()
        self.records.drain(self.cap)
        proof = {**acquired, "unload_confirmed": True, "absence_confirmed": True}
        self.records.completed(self.cap, phase="cleanup", evidence=proof)
        return proof

    def test_explicit_profile_and_independent_stop_post_barrier(self):
        self.cleaned()
        self.assertEqual(self.records.stop_post(self.cap), "held")
        self.records.fence_ack(self.cap)
        self.records.reaped(self.cap)
        with self.assertRaises(AuthorityError):
            self.owner.release_receipt(self.app, self.lease)
        self.assertEqual(self.records.stop_post(self.cap), "suppressed")
        receipt = self.owner.release_receipt(self.app, self.lease)
        revision = self.owner.ledger.revision
        self.assertEqual(self.records.stop_post(self.cap), "suppressed")
        self.assertEqual(self.owner.release_receipt(self.app, self.lease), receipt)
        self.assertEqual(self.owner.ledger.revision, revision)
        self.assertFalse(self.owner.live_hardware_admission)
        with self.assertRaises(AuthorityError):
            self.owner.grant_inert(self.app)

    def test_launch_registry_is_durable_and_acquire_provenance_precedes_dispatch(self):
        state = self.owner.ledger.read()
        record = state["participants"][self.cap.actor.identifier]
        self.assertEqual(state["leases"][self.lease]["operations"][record["launch"]]["state"], "PENDING")
        self.assertFalse(state["leases"][self.lease]["actors"][record["post_actor"]]["released"])
        self.records.bind(self.cap, pid=os.getpid(), uid=max(1, os.getuid()), prepared=self.prepared())
        state = self.owner.ledger.read()
        record = state["participants"][self.cap.actor.identifier]
        self.assertEqual(record["prepared"], self.prepared())
        self.assertEqual(state["leases"][self.lease]["operations"][record["acquire"]]["state"], "PENDING")

    def test_forged_backend_proofs_cannot_retire_a_permit(self):
        prepared = self.prepared()
        self.records.bind(self.cap, pid=os.getpid(), uid=max(1, os.getuid()), prepared=prepared)
        for evidence in ({"ok": True}, {**prepared, "index": 7, "token": "b" * 32},
                         {**prepared, "index": 7, "server_pid": os.getpid()+1}):
            with self.subTest(evidence=evidence), self.assertRaises(AuthorityError):
                self.records.completed(self.cap, phase="acquire", evidence=evidence)
        record = self.records.snapshot(self.cap)
        self.assertEqual(self.owner.snapshot()["leases"][self.lease]["operations"][record["acquire"]]["state"], "PENDING")

    def test_exit_without_backend_cleanup_and_fence_ack_is_not_release(self):
        self.acquired()
        self.seal()
        for action in (self.records.fence_ack, self.records.reaped):
            with self.assertRaises(AuthorityError):
                action(self.cap)
        self.records.lost(self.cap)
        self.assertEqual(self.records.snapshot(self.cap)["phase"], "unknown")
        self.assertEqual(self.records.stop_post(self.cap), "held")
        with self.assertRaises(AuthorityError):
            self.owner.grant_disposable(self.app)

    def test_expiry_winner_survives_late_backend_and_stop_post_completion(self):
        acquired = self.acquired()
        self.seal(budget_ms=1)
        self.records.drain(self.cap)
        self.clock.now = 1000000
        self.records.completed(self.cap, phase="cleanup", evidence={**acquired, "unload_confirmed": True, "absence_confirmed": True})
        self.records.fence_ack(self.cap)
        self.records.reaped(self.cap)
        self.records.stop_post(self.cap)
        self.assertEqual(self.owner.snapshot()["leases"][self.lease]["quiesce"]["status"], "expired")
        with self.assertRaises(AuthorityError):
            self.owner.release_receipt(self.app, self.lease)

    def test_restart_preserves_prepared_server_and_pending_load_as_unknown(self):
        self.records.bind(self.cap, pid=os.getpid(), uid=max(1, os.getuid()), prepared=self.prepared())
        self.owner.close()
        self.owner = Authority.open(self.path, clock=self.clock)
        record = self.owner.snapshot()["participants"][self.cap.actor.identifier]
        self.assertEqual(record["prepared"], self.prepared())
        self.assertEqual(record["phase"], "unknown")
        lease = self.owner.snapshot()["leases"][self.lease]
        self.assertEqual(lease["operations"][record["acquire"]]["state"], "UNKNOWN")
        self.assertEqual(lease["state"], "QUARANTINED")
        with self.assertRaises(AuthorityError):
            Participants(self.owner).stop_post(self.cap)

    def test_schema_one_is_refused_without_rewriting_or_initializing(self):
        state = self.owner.snapshot()
        state["schema"] = 1
        del state["participants"]
        path = Path(self.temp.name)/"old"
        with Ledger.create(path, state):
            pass
        before = (path/"ledger.sqlite3").read_bytes()
        with self.assertRaises(AuthorityError):
            Authority.open(path)
        self.assertEqual((path/"ledger.sqlite3").read_bytes(), before)

    def test_old_inert_grants_and_live_profiles_cannot_register_participants(self):
        with self.assertRaises(AuthorityError):
            self.records.register(self.app, self.lease, domain="audio", profile="hdmi", uid=max(1, os.getuid()))
        with self.assertRaises(AuthorityError):
            self.owner.grant_inert(self.app)
        self.seal()
        with self.assertRaises(AuthorityError):
            self.records.register(self.app, self.lease, domain="vt", profile="vt-fixture-v2", uid=max(1, os.getuid()))

    def test_cleanup_identity_and_positive_absence_are_required(self):
        acquired = self.acquired()
        self.seal()
        self.records.drain(self.cap)
        for evidence in ({**acquired, "unload_confirmed": True, "absence_confirmed": False},
                         {**acquired, "index": 8, "unload_confirmed": True, "absence_confirmed": True}):
            with self.assertRaises(AuthorityError):
                self.records.completed(self.cap, phase="cleanup", evidence=evidence)
        self.assertEqual(self.records.stop_post(self.cap), "held")


class ParticipantCodecTests(unittest.TestCase):
    def test_fixed_exec_config_cannot_select_remote_servers_or_commands(self):
        self.assertEqual(AudioWorkerConfig("unix:/tmp/private/native",1000,7).expected_pid,7)
        for server,uid,pid in (("tcp:host",1000,7),("unix:/tmp/../server",1000,7),
                               ("unix:/tmp/server x",1000,7),("unix:/tmp/native",True,7),
                               ("unix:/tmp/native",0,7),("unix:/tmp/native",1000,True)):
            with self.subTest(server=server,uid=uid,pid=pid),self.assertRaises(ValueError):
                AudioWorkerConfig(server,uid,pid)

    def test_helper_grammar_is_canonical_bound_and_separate_from_app(self):
        binding = ("a"*32, "b"*32, "c"*32, "d"*32)
        packet = Packet("acquired", binding, "e"*32, {"ok": True})
        raw = ParticipantCodec.encode(packet)
        self.assertEqual(ParticipantCodec.parse(raw), packet)
        for bad in (raw+b"\n", raw.replace(b"TVP2", b"TVH2"), raw+b"\t", b"x"*2049,
                    raw.replace(b'{"ok":true}', b'{"ok":true,"ok":true}')):
            with self.subTest(raw=bad), self.assertRaises(ValueError):
                ParticipantCodec.parse(bad)
        with self.assertRaises(ValueError):
            ParticipantCodec.encode(Packet("release", binding, "e"*32, {"target": 7}))


class PipeGate:
    def __init__(self, started, gate):
        self.started, self.gate = started, gate
    def set(self):
        os.write(self.started, b"L")
    def wait(self, _timeout):
        return os.read(self.gate, 1) == b"G"


def audio_factory(started, gate, trace):
    def factory():
        lib = audio_fixtures.FakeLib()
        pipe = PipeGate(started, gate)
        lib.accepted = pipe
        # FakeLib tests call is_set to simulate an optional connection fault.
        pipe.is_set = lambda: False
        lib.finish_load = pipe
        original_load = lib.pa_context_load_module
        original_unload = lib.pa_context_unload_module
        def load(*arguments):
            os.write(trace, b"A")
            return original_load(*arguments)
        def unload(*arguments):
            os.write(trace, b"U")
            return original_unload(*arguments)
        lib.pa_context_load_module, lib.pa_context_unload_module = load, unload
        def bind(connection, _uid, _pid):
            connection._binding = {"server_uid": os.getuid(), "server_pid": os.getppid(), "socket_dev": 1, "socket_ino": 7}
        with patch.object(audio.PulseConnection, "_bind_server", bind), patch.object(audio.PulseConnection, "_server_alive"):
            connection = audio.PulseConnection("unix:/controlled", library=lib)
        connection._server_alive = lambda: None
        return audio.PulseAudioBackend("unix:/controlled", profile="null", connection_factory=lambda _server: connection)
    return factory


@unittest.skipUnless(_supported() and hasattr(os,"pidfd_open") and os.getuid()>0,
                     "Linux normal-user participant process required")
class ParticipantWorkerTests(unittest.TestCase):
    setUp = ParticipantStateTests.setUp
    prepared = ParticipantStateTests.prepared
    seal = ParticipantStateTests.seal
    def pipes(self):
        pairs = [os.pipe() for _ in range(3)]
        for pair in pairs:
            for fd in pair:
                self.addCleanup(os.close, fd)
        return pairs

    def worker(self):
        (started_r, started_w), (gate_r, gate_w), (trace_r, trace_w) = self.pipes()
        worker = ParticipantWorker(self.owner, self.cap, audio_factory(started_w,gate_r,trace_w),
                                   inherited_fds=(started_w,gate_r,trace_w))
        self.addCleanup(worker.close)
        def reap():
            worker.close()
            deadline=time.monotonic()+5
            while not worker.poll_exit() and time.monotonic()<deadline:
                time.sleep(.002)
            self.assertTrue(worker.reaped, "disposable test worker did not exit")
        self.addCleanup(reap)
        return worker, started_r, gate_w, trace_r

    def until(self, worker, condition):
        deadline=time.monotonic()+5
        while not condition() and time.monotonic()<deadline:
            worker.pump()
            time.sleep(.002)
        self.assertTrue(condition(), "participant did not reach expected state")

    def pending(self, worker, started):
        self.until(worker, lambda: bool(select.select([started],[],[],0)[0]))
        self.assertEqual(os.read(started,1),b"L")

    def test_pending_ctypes_callback_cancel_drains_then_fences_before_stop_post(self):
        worker, started, gate, trace = self.worker()
        self.pending(worker, started)
        self.seal()
        self.assertEqual(worker.stop_post(), "held")
        record=self.records.snapshot(self.cap)
        self.assertEqual(record["phase"],"bound")
        self.assertIsNotNone(record["prepared"])
        with self.assertRaises(AuthorityError): self.owner.release_receipt(self.app,self.lease)
        os.write(gate,b"G")
        self.until(worker,lambda: worker.closed)
        self.assertEqual(self.records.snapshot(self.cap)["phase"],"fenced")
        with self.assertRaises(AuthorityError): self.owner.release_receipt(self.app,self.lease)
        self.assertEqual(worker.stop_post(),"suppressed")
        receipt=self.owner.release_receipt(self.app,self.lease)
        self.assertEqual(os.read(trace,2),b"AU")
        revision=self.owner.ledger.revision
        for _ in range(10): self.assertEqual(worker.stop_post(),"suppressed")
        self.assertEqual(self.owner.ledger.revision,revision)
        self.assertEqual(self.owner.release_receipt(self.app,self.lease),receipt)
        self.assertFalse(select.select([trace],[],[],0)[0])

    def test_worker_death_mid_load_keeps_original_permit_unknown(self):
        worker,started,_gate,trace=self.worker()
        self.pending(worker,started)
        self.seal()
        signal.pidfd_send_signal(worker.pidfd,signal.SIGKILL)
        with self.assertRaises(AuthorityError):
            self.until(worker,lambda:worker.closed)
        record=self.records.snapshot(self.cap)
        self.assertEqual(record["phase"],"unknown")
        self.assertEqual(self.owner.snapshot()["leases"][self.lease]["operations"][record["acquire"]]["state"],"UNKNOWN")
        self.assertEqual(worker.stop_post(),"held")
        self.assertEqual(os.read(trace,1),b"A")
        self.assertFalse(select.select([trace],[],[],0)[0])

    def test_expiry_does_not_block_control_or_turn_late_cleanup_into_release(self):
        worker,started,gate,_trace=self.worker()
        self.pending(worker,started)
        self.seal(budget_ms=1)
        worker.request_drain()
        self.clock.now=1000000
        worker.pump()
        self.assertFalse(worker.closed)
        self.assertEqual(self.owner.snapshot()["leases"][self.lease]["state"],"QUARANTINED")
        os.write(gate,b"G")
        self.until(worker,lambda:worker.closed)
        worker.stop_post()
        with self.assertRaises(AuthorityError): self.owner.release_receipt(self.app,self.lease)

    def test_storage_failure_closes_and_reaps_worker_even_when_loss_cannot_commit(self):
        worker,started,_gate,_trace=self.worker()
        self.pending(worker,started)
        with patch.object(self.owner.ledger,"read",side_effect=LedgerError("storage unavailable")):
            with self.assertRaises(ValueError): worker.pump()
        self.assertTrue(worker.closed)
        self.until(worker, lambda: worker.reaped)
        self.assertEqual(worker.pidfd,-1)

    def test_close_never_waits_for_a_killed_worker_and_retains_exact_pidfd(self):
        worker,started,_gate,_trace=self.worker()
        self.pending(worker,started)
        with patch("handoff_participants.os.waitpid",return_value=(0,0)) as wait:
            worker.close()
            self.assertTrue(worker.closed and not worker.reaped)
            self.assertGreaterEqual(worker.pidfd,0)
            self.assertTrue(wait.call_args_list)
            self.assertTrue(all(call.args==(worker.pid,os.WNOHANG) for call in wait.call_args_list))
        self.assertEqual(self.records.snapshot(self.cap)["phase"],"unknown")
        self.until(worker,lambda:worker.reaped)

    def test_failed_startup_closes_allocations_and_preserves_unknown_launch(self):
        before=set(os.listdir("/proc/self/fd"))
        with patch("handoff_participants.os.pidfd_open",side_effect=OSError("pidfds unavailable")):
            with self.assertRaises(OSError): ParticipantWorker(self.owner,self.cap,lambda:None)
        self.assertEqual(set(os.listdir("/proc/self/fd")),before)
        record=self.records.snapshot(self.cap)
        self.assertEqual(record["phase"],"unknown")
        self.assertEqual(self.owner.snapshot()["leases"][self.lease]["operations"][record["launch"]]["state"],"UNKNOWN")

    def test_vt_worker_holds_real_wait_path_until_authorized_cleanup_and_fence(self):
        import stat
        from types import SimpleNamespace
        import handoff_vt as vt
        import test_vt as vt_fixtures
        # Use a separate registered VT participant in the same composite lease.
        cap=self.records.register(self.app,self.lease,domain="vt",profile="vt-fixture-v2",uid=os.getuid())
        (started_r,started_w),(gate_r,gate_w),(trace_r,trace_w)=self.pipes()
        file=tempfile.TemporaryFile()
        self.addCleanup(file.close)
        def factory():
            console=vt_fixtures.ControlledConsole()
            console.hold=(PipeGate(started_w,gate_r),gate_r)
            def ioctl(fd,operation,value,mutate=False):
                if operation==vt.VT_ACTIVATE: os.write(trace_w,b"A" if value==2 else b"R")
                result=console(fd,operation,value,mutate)
                if operation==vt.VT_WAITACTIVE: console.hold=None
                return result
            info=SimpleNamespace(st_mode=stat.S_IFCHR|0o600,st_rdev=os.makedev(4,0))
            with patch.object(vt.os,"fstat",return_value=info):
                backend=vt.VtBackend.adopt(file.fileno(),managed_vt=2,neutral_vt=1,ioctl=ioctl)
            backend.disposable,backend.profile=True,"vt-fixture"
            return backend
        worker=ParticipantWorker(self.owner,cap,factory,inherited_fds=(file.fileno(),started_w,gate_r,trace_w))
        def finish():
            worker.close()
            deadline=time.monotonic()+5
            while not worker.poll_exit() and time.monotonic()<deadline: time.sleep(.002)
            self.assertTrue(worker.reaped)
        self.addCleanup(finish)
        self.until(worker,lambda:bool(select.select([started_r],[],[],0)[0]))
        self.assertEqual(os.read(started_r,1),b"L")
        self.seal()
        worker.request_drain()
        worker.pump()
        self.assertEqual(self.records.snapshot(cap)["phase"],"bound")
        self.assertEqual(worker.stop_post(),"held")
        os.write(gate_w,b"G")
        self.until(worker,lambda:worker.closed)
        self.assertEqual(self.records.snapshot(cap)["evidence"]["cleanup"]["state"],"RELEASED")
        self.assertEqual(os.read(trace_r,2),b"AR")
        worker.stop_post()
        # The independent audio participant has not finished: no partial grant.
        with self.assertRaises(AuthorityError): self.owner.release_receipt(self.app,self.lease)


@unittest.skipUnless(audio_fixtures.REAL_PULSE and _supported() and hasattr(os,"pidfd_open"),
                     "Linux private Pulse server and participant process required")
class RealParticipantWorkerTests(unittest.TestCase):
    prepared = ParticipantStateTests.prepared
    seal = ParticipantStateTests.seal
    until = ParticipantWorkerTests.until
    stop_server = audio_fixtures.RealPulseAudioTests.stop_server

    def setUp(self):
        ParticipantStateTests.setUp(self)
        audio_fixtures.RealPulseAudioTests.setUp(self)

    def launch(self):
        server, pid, uid = "unix:"+str(self.server), self.process.pid, os.getuid()
        worker = ParticipantWorker(self.owner,self.cap,AudioWorkerConfig(server,uid,pid))
        def finish():
            worker.close()
            deadline=time.monotonic()+5
            while not worker.poll_exit() and time.monotonic()<deadline: time.sleep(.002)
            self.assertTrue(worker.reaped)
        self.addCleanup(finish)
        return worker

    def test_private_real_server_worker_receipts_and_stop_post_preserve_foreign_module(self):
        foreign=audio.PulseAudioBackend("unix:"+str(self.server),profile="null",
            expected_uid=os.getuid(),expected_pid=self.process.pid)
        self.addCleanup(foreign.close)
        foreign_lease=foreign.acquire("b"*32)
        worker=self.launch()
        self.until(worker,lambda:self.records.snapshot(self.cap)["phase"]=="acquired")
        record=self.records.snapshot(self.cap)
        self.assertEqual(record["prepared"]["server_pid"],self.process.pid)
        self.assertEqual(record["prepared"]["socket_ino"],self.server.stat().st_ino)
        self.seal()
        worker.request_drain()
        self.until(worker,lambda:worker.closed)
        with self.assertRaises(AuthorityError): self.owner.release_receipt(self.app,self.lease)
        self.assertEqual(worker.stop_post(),"suppressed")
        self.owner.release_receipt(self.app,self.lease)
        self.assertIn(audio.Module(foreign_lease.index,foreign_lease.name,foreign_lease.arguments),foreign._connection.modules())
        self.assertFalse(any("tyrell_v2_"+self.cap.generation in row.arguments for row in foreign._connection.modules()))
        foreign.release(foreign_lease)

    def test_real_server_death_leaves_both_participant_obligations_unreleased(self):
        worker=self.launch()
        self.until(worker,lambda:self.records.snapshot(self.cap)["phase"]=="acquired")
        self.seal()
        self.process.kill()
        self.process.communicate(timeout=5)
        worker.request_drain()
        with self.assertRaises(AuthorityError): self.until(worker,lambda:worker.closed)
        record=self.records.snapshot(self.cap)
        lease=self.owner.snapshot()["leases"][self.lease]
        self.assertEqual(record["phase"],"unknown")
        self.assertFalse(lease["actors"][self.cap.actor.identifier]["released"])
        self.assertFalse(lease["actors"][self.cap.post_actor.identifier]["released"])
        self.assertEqual(worker.stop_post(),"held")
        with self.assertRaises(AuthorityError): self.owner.release_receipt(self.app,self.lease)


if __name__=="__main__":
    unittest.main()
