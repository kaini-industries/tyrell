"""Trusted-launcher draft-2 app session, with no default endpoint or devices.

Linux pidfds pin the registered process lifetime. Per-packet credentials are
checked independently by Channel. App IDs and wire receipts are correlation,
not substitutes for this bootstrap. Only inert grants exist in D1.
"""
from __future__ import annotations

import os
import select
import time

from handoff_authority import AuthorityError
from handoff_channel import Channel, ChannelError, ChannelUnsupported

MAX_PACKETS_PER_PUMP = 64
NEGOTIATION_NS = 1000000000


class Session:
    def __init__(self, authority, fd, *, app_instance, expected_uid, expected_pid, clock=time.monotonic_ns):
        if not hasattr(os, "pidfd_open"):
            raise ChannelUnsupported("D1 bootstrap requires Linux pidfds")
        self.authority, self.clock = authority, clock
        self.channel, self.app, self.pidfd = None, None, -1
        self.closed = False
        self.negotiated = False
        self.last_error = None
        self.started_ns = clock()
        if type(self.started_ns) is not int or self.started_ns < 0:
            raise AuthorityError("invalid session clock")
        try:
            if type(expected_pid) is not int or expected_pid <= 0:
                raise ChannelError("invalid trusted process")
            self.pidfd = os.pidfd_open(expected_pid, 0)
            os.set_inheritable(self.pidfd, False)
            if self._exited():
                raise ChannelError("registered process has exited")
            self.channel = Channel.adopt(fd, expected_uid=expected_uid, expected_pid=expected_pid)
            self.app = authority.bootstrap_app(app_instance, uid=expected_uid, pid=expected_pid)
        except BaseException:
            if self.channel is not None:
                self.channel.close()
            if self.pidfd >= 0:
                os.close(self.pidfd)
                self.pidfd = -1
            raise

    def _exited(self):
        poller = select.poll()
        poller.register(self.pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
        return bool(poller.poll(0))

    def _stop(self, reason):
        self.last_error = reason
        self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.app is not None:
                # This persists uncertainty before discarding the channel.
                self.authority.disconnect(self.app)
        finally:
            if self.channel is not None:
                self.channel.close()
            if self.pidfd >= 0:
                os.close(self.pidfd)
                self.pidfd = -1

    def pump(self):
        """At most 64 receives and bounded sends; durable IO may still block.

        The owner schedules pump/tick against its monotonic deadline. Socket
        operations never wait for a peer; this is not a hard real-time promise
        about SQLite, kernel scheduling or a future device operation.
        """
        if self.closed:
            return 0
        count = 0
        try:
            self.authority.tick()
            now = self.clock()
            if type(now) is not int or now < self.started_ns:
                raise AuthorityError("invalid or backwards session clock")
            if not self.negotiated and now - self.started_ns >= NEGOTIATION_NS:
                self._stop("negotiation-expired")
                return 0
            if self._exited():
                self._stop("registered-process-exited")
                return 0
            self.channel.flush()
            for _ in range(MAX_PACKETS_PER_PUMP):
                message = self.channel.receive()
                if message is None:
                    break
                # A reused PID cannot inherit the dead registration's rights.
                if self._exited():
                    self._stop("registered-process-exited")
                    return count
                responses = self.authority.receive_app(self.app, message)
                for response in responses:
                    if not self.channel.send(response):
                        self._stop("bounded-output-unavailable")
                        return count
                if message.verb == "hello":
                    self.negotiated = True
                count += 1
            self.channel.flush()
            if self.channel.lost:
                self._stop(self.channel.last_error or "peer-lost")
        except (OSError, ValueError):
            # No fallback or reconstructed empty authority on ledger failure.
            try:
                self._stop("session-or-authority-failed")
            except (OSError, ValueError):
                raise
        return count

    def grant_inert(self):
        if self.closed or not self.negotiated or self._exited():
            raise AuthorityError("active negotiated bootstrap required")
        grant = self.authority.grant_inert(self.app)
        if not self.channel.send(grant):
            self._stop("grant-output-unavailable")
            raise AuthorityError("grant delivery is unknown")
        return grant

    def quiesce(self, lease, *, budget_ms=10000):
        if self.closed:
            raise AuthorityError("closed session")
        packet = self.authority.quiesce(self.app, lease, budget_ms=budget_ms)
        if not self.channel.send(packet):
            self._stop("quiesce-output-unavailable")
            raise AuthorityError("quiesce delivery is unknown")
        return packet

    def cancel(self, lease):
        if self.closed:
            raise AuthorityError("closed session")
        packet = self.authority.cancel(self.app, lease)
        if not self.channel.send(packet):
            self._stop("cancel-output-unavailable")
            raise AuthorityError("cancel delivery is unknown")
        return packet

    def commit_hidden(self, lease, receipt):
        if self.closed:
            raise AuthorityError("closed session")
        packet = self.authority.commit_hidden(self.app, lease, receipt)
        if not self.channel.send(packet):
            self._stop("commit-output-unavailable")
            raise AuthorityError("commit delivery is unknown")
        return packet
