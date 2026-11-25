"""A retained socket copy cannot outlive the registered coordinator's pidfd."""
import array
import ctypes as C
import json
import os
from pathlib import Path
import select
import signal
import socket
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from handoff_authority import Authority, AuthorityError
from handoff_channel import _supported
from handoff_participant_state import Participants
from handoff_participants import Packet, ParticipantWorker
from handoff_protocol import Message, CAPABILITIES
import test_participants as fixtures


@unittest.skipUnless(_supported() and hasattr(os, "pidfd_open") and os.getuid() > 0,
                     "Linux normal-user coordinator lifetime integration required")
class CoordinatorLifetimeTests(unittest.TestCase):
    def test_coordinator_death_with_retained_socket_fences_queued_cleanup(self):
        # Become the orphan's reaper only for this test, so every disposable
        # descendant is collected instead of relying on the container's PID 1.
        libc = C.CDLL(None, use_errno=True)
        libc.prctl.restype = C.c_int
        previous = C.c_int()
        self.assertEqual(libc.prctl(37, C.byref(previous), 0, 0, 0), 0)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)
        self.addCleanup(lambda: self.assertEqual(libc.prctl(36, previous.value, 0, 0, 0), 0))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        ledger_path = Path(temporary.name) / "ledger"
        report, child_report = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(report.close)
        self.addCleanup(child_report.close)
        (started_r, started_w), (gate_r, gate_w), (trace_r, trace_w) = [os.pipe() for _ in range(3)]
        all_fds = {started_r, started_w, gate_r, gate_w, trace_r, trace_w}
        self.addCleanup(lambda: [os.close(fd) for fd in all_fds])
        child_pid = os.fork()
        if child_pid == 0:
            try:
                report.close()
                owner = Authority.create(ledger_path, disposable=True)
                app = owner.bootstrap_app("a" * 32, uid=os.getuid(), pid=os.getpid())
                owner.negotiate(app, Message("hello", (app.instance, CAPABILITIES)))
                lease = owner.grant_disposable(app).fields[4]
                records = Participants(owner)
                capability = records.register(app, lease, domain="audio", profile="null-v2", uid=os.getuid())
                worker = ParticipantWorker(owner, capability, fixtures.audio_factory(started_w, gate_r, trace_w),
                                           inherited_fds=(started_w, gate_r, trace_w))
                deadline = time.monotonic() + 5
                while not select.select([started_r], [], [], 0)[0] and time.monotonic() < deadline:
                    worker.pump()
                    time.sleep(.002)
                if not select.select([started_r], [], [], 0)[0] or os.read(started_r, 1) != b"L":
                    raise RuntimeError("worker never accepted its blocked load")
                request = owner.quiesce(app, lease)
                owner.local_done(app, Message("local-done", (*request.fields[:4], "none")))
                worker.request_drain()
                # The normal supervisor waits for acquire's receipt. This
                # adversarial trusted coordinator already has a counted cleanup
                # permit and sends its credentialed command before that receipt.
                cleanup = owner.begin_operation(capability.actor, "cleanup")
                if not worker.channel.send(Packet("release", worker.binding, cleanup, {})):
                    raise RuntimeError("credentialed queued release unavailable")
                record = records.snapshot(capability)
                payload = json.dumps({"pid": worker.pid, "lease": lease,
                                      "actor": capability.actor.identifier,
                                      "operation": record["acquire"]}).encode()
                duplicate = array.array("i", [worker.channel.fd])
                child_report.sendmsg([payload], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, duplicate)])
                # The observer kills this coordinator; no finally path closes
                # its worker or fakes a normal authority shutdown.
                while True:
                    signal.pause()
            except BaseException:
                try:
                    child_report.send(b"failure")
                except OSError:
                    pass
                os._exit(41)
        child_report.close()
        for fd in (started_w, gate_r, trace_w):
            os.close(fd)
            all_fds.remove(fd)
        coordinator_pidfd = os.pidfd_open(child_pid, 0)
        worker_pidfd = -1
        worker_pid = -1
        retained = -1
        coordinator_reaped = worker_reaped = False
        def cleanup_processes():
            nonlocal coordinator_reaped, worker_reaped
            for pidfd in (coordinator_pidfd, worker_pidfd):
                if pidfd >= 0:
                    try:
                        signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            # Finite waits through exact pidfds, followed by nonblocking reaps.
            for pid, pidfd, was_reaped in ((child_pid, coordinator_pidfd, coordinator_reaped),
                                          (worker_pid, worker_pidfd, worker_reaped)):
                if pid > 0 and pidfd >= 0 and not was_reaped:
                    self.assertTrue(select.select([pidfd], [], [], 5)[0], "test descendant did not exit")
                    self.assertEqual(os.waitpid(pid, os.WNOHANG)[0], pid)
            for pidfd in (coordinator_pidfd, worker_pidfd):
                if pidfd >= 0:
                    os.close(pidfd)
            if retained >= 0:
                os.close(retained)
        self.addCleanup(cleanup_processes)
        self.assertTrue(select.select([report], [], [], 5)[0], "coordinator did not report worker")
        payload, ancillary, flags, _ = report.recvmsg(2048, socket.CMSG_SPACE(4), socket.MSG_CMSG_CLOEXEC)
        self.assertFalse(flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC))
        metadata = json.loads(payload)
        descriptors = array.array("i")
        for level, kind, data in ancillary:
            self.assertEqual((level, kind), (socket.SOL_SOCKET, socket.SCM_RIGHTS))
            descriptors.frombytes(data)
        self.assertEqual(len(descriptors), 1)
        retained = descriptors[0]
        self.assertFalse(os.get_inheritable(retained))
        worker_pid = metadata["pid"]
        worker_pidfd = os.pidfd_open(worker_pid, 0)
        signal.pidfd_send_signal(coordinator_pidfd, signal.SIGKILL)
        self.assertTrue(select.select([coordinator_pidfd], [], [], 5)[0])
        self.assertEqual(os.waitpid(child_pid, os.WNOHANG)[0], child_pid)
        coordinator_reaped = True
        owner = Authority.open(ledger_path)
        self.addCleanup(owner.close)
        state = owner.snapshot()
        lease = state["leases"][metadata["lease"]]
        self.assertEqual(lease["state"], "QUARANTINED")
        self.assertEqual(lease["operations"][metadata["operation"]]["state"], "UNKNOWN")
        self.assertEqual(state["participants"][metadata["actor"]]["phase"], "unknown")
        successor = owner.bootstrap_app("b" * 32, uid=os.getuid(), pid=os.getpid())
        owner.negotiate(successor, Message("hello", (successor.instance, CAPABILITIES)))
        with self.assertRaises(AuthorityError):
            owner.grant_disposable(successor)
        # The socket endpoint is still held and has not delivered EOF. The
        # original credentialed release is queued, so an EOF-only worker would
        # accept it after the acquire callback and perform an unsafe unload.
        os.fstat(retained)
        self.assertFalse(select.select([worker_pidfd], [], [], 0)[0])
        os.write(gate_w, b"G")
        self.assertTrue(select.select([worker_pidfd], [], [], 5)[0],
                        "worker ignored the registered coordinator's death")
        self.assertEqual(os.waitpid(worker_pid, os.WNOHANG)[0], worker_pid)
        worker_reaped = True
        self.assertEqual(os.read(trace_r, 16), b"A", "old queued cleanup ran after coordinator death")
        os.fstat(retained)  # Keeping it alive was necessary through worker exit.
        self.assertEqual(owner.snapshot()["leases"][metadata["lease"]]["operations"][metadata["operation"]]["state"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
