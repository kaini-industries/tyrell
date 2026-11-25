"""Separate v2 participant workers for explicitly disposable backend trials.

No service is installed, no environment descriptor is adopted and no live
hardware profile is admitted. Backend calls run outside the authority process;
each call retains its durable permit while it blocks. Stop-post never performs
an independent backend scan, unload or VT restoration.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import time
import threading
import sys

from handoff_authority import AuthorityError, identifier, require
from handoff_channel import Channel
from handoff_participant_state import Participants

_PENDING_REAPS = {}


def reap_pending():
    """Poll retained abandoned workers, including failed launch constructors."""
    for worker in tuple(_PENDING_REAPS.values()):
        worker.poll_exit()
    return len(_PENDING_REAPS)


@dataclass(frozen=True)
class AudioWorkerConfig:
    """Fixed fresh-process null-audio bootstrap, supplied by the coordinator."""
    server: str
    expected_uid: int
    expected_pid: int

    def __post_init__(self):
        require(type(self.server) is str and len(self.server) <= 256 and
                re.fullmatch(r"unix:/[A-Za-z0-9_./-]+", self.server) is not None and
                ".." not in self.server[5:].split("/") and
                type(self.expected_uid) is int and 1 <= self.expected_uid <= 2**32-1 and
                type(self.expected_pid) is int and 1 <= self.expected_pid <= 2**31-1,
                "invalid trusted disposable audio bootstrap")

    def factory(self):
        from handoff_audio import PulseAudioBackend
        return PulseAudioBackend(self.server, profile="null", expected_uid=self.expected_uid,
                                 expected_pid=self.expected_pid)


def _close_other_fds(keep):
    for name in os.listdir("/proc/self/fd"):
        number = int(name)
        if number not in keep:
            try:
                os.close(number)
            except OSError:
                pass


@dataclass(frozen=True)
class Packet:
    verb: str
    binding: tuple[str, str, str, str]
    operation: str
    evidence: dict


class ParticipantCodec:
    """TVP2 is separate from the app's TVH2 grammar and has fixed roles."""
    maximum = 2048
    verbs = {"hello", "acquire", "acquired", "release", "cleaned", "fence", "fenced", "failed"}

    @classmethod
    def encode(cls, packet):
        require(isinstance(packet, Packet) and packet.verb in cls.verbs and
                type(packet.binding) is tuple and len(packet.binding) == 4 and
                all(identifier(item) for item in packet.binding) and
                (identifier(packet.operation) or packet.operation == "none") and
                type(packet.evidence) is dict, "invalid participant packet")
        require((packet.verb in ("hello", "acquired", "cleaned") and bool(packet.evidence)) or
                (packet.verb not in ("hello", "acquired", "cleaned") and not packet.evidence), "invalid evidence role")
        try:
            payload = json.dumps(packet.evidence, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
            # No arbitrary nested structures or unbounded backend data.
            require(all(type(key) is str and len(key) <= 64 and
                        (type(value) in (str, int, bool, type(None))) and
                        (not isinstance(value, str) or len(value) <= 1024)
                        for key, value in packet.evidence.items()), "invalid evidence fields")
            raw = ("\t".join(("TVP2", packet.verb, *packet.binding, packet.operation, payload))).encode("ascii")
        except (ValueError, TypeError, UnicodeError) as error:
            raise AuthorityError("invalid participant encoding") from error
        require(len(raw) <= cls.maximum, "participant packet exceeds bound")
        return raw

    @classmethod
    def parse(cls, raw):
        require(type(raw) is bytes and 0 < len(raw) <= cls.maximum and
                all(byte == 9 or 32 <= byte <= 126 for byte in raw), "invalid participant bytes")
        try:
            fields = raw.decode("ascii").split("\t")
            require(len(fields) == 8 and fields[0] == "TVP2", "invalid participant grammar")
            evidence = json.loads(fields[7])
            packet = Packet(fields[1], tuple(fields[2:6]), fields[6], evidence)
            require(cls.encode(packet) == raw, "noncanonical participant packet")
            return packet
        except (ValueError, TypeError, KeyError) as error:
            raise AuthorityError("malformed participant packet") from error


def _worker(fd, gate, parent_pidfd, inherited, factory, binding, domain, parent_pid, uid):
    """Child owns backend/context; a dead coordinator never means cleanup."""
    channel, backend = None, None
    try:
        os.set_inheritable(parent_pidfd, False)
        keep = {0, 1, 2, fd, gate, parent_pidfd, *inherited}
        # Do not inherit the authority's SQLite connection, flock, other app
        # channels or unrelated participant descriptors into a long-lived call.
        _close_other_fds(keep)
        require(os.read(gate, 1) == b"S", "worker launch not authorized")
        os.close(gate)
        channel = Channel.adopt(fd, expected_uid=uid, expected_pid=parent_pid,
                                expected_creator_uid=uid, codec=ParticipantCodec)
        backend = factory()
        require(getattr(backend, "disposable", False) is True and
                ((domain == "audio" and backend.profile == "null") or
                 (domain == "vt" and backend.profile == "vt-fixture")), "live backend refused by disposable worker")
        prepared = backend.prepared(binding[3]) if domain == "audio" else {**backend.identity, "token": binding[3], "state": "NEW"}
        require(channel.send(Packet("hello", binding, "none", prepared)), "worker hello unavailable")
        phase, owned, cached = "new", None, {}
        while not channel.lost:
            require(not select.select([parent_pidfd], [], [], 0)[0], "registered coordinator exited")
            channel.flush()
            packet = channel.receive()
            if packet is None:
                select.select([channel.fd], [], [], 0.02) if not channel.lost else None
                continue
            require(packet.binding == binding, "stale participant binding")
            require(not select.select([parent_pidfd], [], [], 0)[0], "registered coordinator exited")
            cache_key = (packet.verb, packet.operation)
            if cache_key in cached:
                require(channel.send(cached[cache_key]), "worker replay unavailable")
                continue
            if packet.verb == "acquire" and phase == "new" and identifier(packet.operation):
                owned = backend.acquire(binding[3])
                evidence = owned.evidence() if hasattr(owned, "evidence") else backend.evidence(owned)
                response = Packet("acquired", binding, packet.operation, evidence)
                phase = "owned"
            elif packet.verb == "release" and phase == "owned" and identifier(packet.operation):
                # Live VT cleanup is excluded; D3 must supply real display
                # completion evidence before any managed console integration.
                result = backend.release(owned, display_drained=True) if domain == "vt" else backend.release(owned)
                evidence = result if isinstance(result, dict) else backend.evidence(owned)
                response = Packet("cleaned", binding, packet.operation, evidence)
                phase = "cleaned"
            elif packet.verb == "fence" and phase == "cleaned" and identifier(packet.operation):
                backend.close()
                backend = None
                response = Packet("fenced", binding, packet.operation, {})
                require(channel.send(response) and not channel.pending, "worker fence delivery unknown")
                return
            else:
                raise AuthorityError("participant command/phase mismatch")
            cached[cache_key] = response
            require(channel.send(response), "worker result unavailable")
    except BaseException:
        if channel is not None:
            try:
                channel.send(Packet("failed", binding, "none", {}))
            except BaseException:
                pass
    finally:
        # close() cannot stand in for release; any unfinished server operation
        # remains unknown. There is no fallback cleanup/reconnect in this path.
        if backend is not None:
            try:
                backend.close()
            except BaseException:
                pass
        if channel is not None:
            channel.close()
        os.close(parent_pidfd)


class ParticipantWorker:
    """Normal-user disposable process supervisor; coordinator, not app API."""

    def __init__(self, authority, capability, factory, *, inherited_fds=()):
        reap_pending()
        require(hasattr(os, "pidfd_open") and hasattr(os, "fork") and os.getuid() > 0,
                "Linux normal-user worker required")
        require(threading.active_count() == 1, "disposable workers must launch before coordinator threads")
        self.owner, self.records, self.capability = authority, Participants(authority), capability
        record = self.records.snapshot(capability)
        require(record["phase"] == "registered" and record["uid"] == os.getuid(), "registered same-user disposable worker required")
        require(callable(factory) or (isinstance(factory, AudioWorkerConfig) and record["domain"] == "audio" and
                factory.expected_uid == record["uid"]), "fixed audio config or trusted fixture factory required")
        self.binding = (capability.actor.incarnation, capability.actor.lease, capability.actor.identifier, capability.generation)
        self.pid, self.pidfd, self.channel = -1, -1, None
        self.closed, self.reaped, self.sent_cleanup, self.sent_fence = False, False, False, False
        self.kill_requested = False
        self.started = time.monotonic_ns()
        left = right = None
        gate_read = gate_write = parent_pidfd = -1
        adopt_fd = -1
        try:
            left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            # Enable credentials before fork/start; no credentialless packet
            # can enter queues while receivers finish adopting endpoints.
            for endpoint in (left, right):
                endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            gate_read, gate_write = os.pipe2(os.O_CLOEXEC)
            parent_pidfd = os.pidfd_open(os.getpid(), 0)
            parent_pid = os.getpid()
            self.pid = os.fork()
            if self.pid == 0:
                try:
                    if isinstance(factory, AudioWorkerConfig):
                        # libpulse rejects use after fork once the parent has
                        # touched it. A fixed entry in a fresh interpreter also
                        # prevents inheriting the parent's live audio contexts.
                        keep = {0, 1, 2, right.fileno(), gate_read, parent_pidfd}
                        _close_other_fds(keep)
                        for fd in (right.fileno(), gate_read, parent_pidfd):
                            os.set_inheritable(fd, True)
                        bootstrap = json.dumps({"server":factory.server, "expected_uid":factory.expected_uid,
                                                "expected_pid":factory.expected_pid}, separators=(",", ":"))
                        os.execv(sys.executable, [sys.executable, "-I", str(Path(__file__).with_name("handoff_participant_worker.py")),
                            str(right.fileno()), str(gate_read), str(parent_pidfd), str(parent_pid),
                            json.dumps(self.binding), bootstrap])
                    _worker(right.fileno(), gate_read, parent_pidfd, inherited_fds, factory, self.binding,
                            record["domain"], parent_pid, record["uid"])
                finally:
                    os._exit(0)
            right.close()
            os.close(gate_read)
            gate_read = -1
            self.pidfd = os.pidfd_open(self.pid, 0)
            os.set_inheritable(self.pidfd, False)
            adopt_fd = left.detach()
            self.channel = Channel.adopt(adopt_fd, expected_uid=record["uid"], expected_pid=self.pid,
                                         expected_creator_uid=os.getuid(), codec=ParticipantCodec)
            adopt_fd = -1
            os.write(gate_write, b"S")
        except BaseException:
            try:
                self.records.lost(capability)
            finally:
                self.close()
            raise
        finally:
            for endpoint in (left, right):
                if endpoint is not None:
                    endpoint.close()
            for fd in (gate_read, gate_write, parent_pidfd, adopt_fd):
                if fd >= 0:
                    os.close(fd)

    def _send(self, verb, operation):
        require(self.channel.send(Packet(verb, self.binding, operation, {})), "participant output unknown")

    def request_drain(self):
        operation = self.records.drain(self.capability)
        if operation is not None and not self.sent_cleanup:
            self._send("release", operation)
            self.sent_cleanup = True

    def pump(self):
        reap_pending()
        if self.closed:
            self.poll_exit()
            if self.pid > 0 and not self.reaped:
                _PENDING_REAPS[self.pid] = self
            return
        try:
            self.owner.tick()
            if self.records.snapshot(self.capability)["phase"] == "registered":
                require(time.monotonic_ns() - self.started < 1000000000, "participant negotiation expired")
            # Drain already queued authenticated receipts before reaping this
            # unreaped child; PID cannot be reused while it remains our zombie.
            for _ in range(64):
                packet = self.channel.receive()
                if packet is None:
                    break
                require(packet.binding == self.binding, "stale worker receipt")
                record = self.records.snapshot(self.capability)
                if packet.verb == "hello" and record["phase"] == "registered" and packet.operation == "none":
                    operation = self.records.bind(self.capability, pid=self.pid, uid=record["uid"], prepared=packet.evidence)
                    self._send("acquire", operation)
                elif packet.verb in ("acquired", "cleaned"):
                    phase = "acquire" if packet.verb == "acquired" else "cleanup"
                    require(packet.operation == record[phase], "wrong operation receipt")
                    self.records.completed(self.capability, phase=phase, evidence=packet.evidence)
                elif packet.verb == "fenced" and packet.operation == record["cleanup"]:
                    self.records.fence_ack(self.capability)
                else:
                    raise AuthorityError("unexpected worker result")
            record = self.records.snapshot(self.capability)
            if record["drain"] and record["phase"] == "acquired":
                self.request_drain()
            if record["phase"] == "cleaned" and not self.sent_fence:
                self._send("fence", record["cleanup"])
                self.sent_fence = True
            self.channel.flush()
            result, _status = os.waitpid(self.pid, os.WNOHANG)
            if result:
                self.reaped = True
                if self.records.snapshot(self.capability)["phase"] == "fence-acked":
                    self.records.reaped(self.capability)
                    self.close()
                else:
                    raise AuthorityError("worker exit without durable fence")
            elif self.channel.lost and record["phase"] != "fence-acked":
                raise AuthorityError("participant channel lost")
        except (OSError, ValueError):
            try:
                self.records.lost(self.capability)
            finally:
                self.close()
            raise

    def stop_post(self):
        """Explicit supervisor hook; no independently executable cleanup."""
        result = self.records.stop_post(self.capability)
        if result == "held" and not self.closed:
            self.request_drain()
        return result

    def close(self):
        if self.closed:
            self.poll_exit()
            return
        self.closed = True
        try:
            if self.pid > 0 and not self.reaped:
                try:
                    self.records.lost(self.capability)
                finally:
                    try:
                        if self.pidfd >= 0:
                            signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)
                        else:
                            os.kill(self.pid, signal.SIGKILL)  # Still our unreaped child.
                    except ProcessLookupError:
                        pass
                    self.kill_requested = True
        finally:
            if self.channel is not None:
                self.channel.close()
            self.poll_exit()
            if self.pid > 0 and not self.reaped:
                _PENDING_REAPS[self.pid] = self

    def poll_exit(self):
        """Never wait for a killed/blocked worker; retain its exact pidfd.

        The coordinator must keep this supervisor and pump closed workers
        until reaped. Exit is resource completion only on the earlier durable
        cleanup/fence path; a kill retains UNKNOWN in the ledger.
        """
        if self.pid > 0 and not self.reaped:
            result, _status = os.waitpid(self.pid, os.WNOHANG)
            self.reaped = bool(result)
        if self.reaped and self.pidfd >= 0:
            os.close(self.pidfd)
            self.pidfd = -1
        if self.reaped:
            _PENDING_REAPS.pop(self.pid, None)
        return self.reaped
