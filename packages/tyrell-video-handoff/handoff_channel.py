"""Linux sequenced-packet handoff channel with trusted-bootstrap peer binding.

The caller supplies expected PID/UID from its trusted registration, not from
wire fields or an environment variable. No device or helper operation exists.
"""
from __future__ import annotations

import array
from collections import deque
import fcntl
import os
import socket
import struct
import sys

from handoff_protocol import Message, MAX_PACKET_BYTES, ProtocolError, encode_packet, parse_packet

MAX_QUEUED_PACKETS = 16
MAX_SENDS_PER_FLUSH = 64
_CREDENTIAL = struct.Struct("iII")


class ChannelError(ValueError):
    pass


class ChannelUnsupported(ChannelError):
    pass


def _supported():
    return sys.platform.startswith("linux") and all(hasattr(socket, name) for name in
        ("SOCK_SEQPACKET", "SO_PEERCRED", "SO_PASSCRED", "SCM_CREDENTIALS", "MSG_NOSIGNAL", "MSG_CMSG_CLOEXEC"))


class Channel:
    """Own one validated FD. Queue acceptance is never peer delivery evidence."""

    def __init__(self, endpoint, expected_uid, expected_pid, *, codec=None):
        self._socket = endpoint
        self._expected_uid = expected_uid
        self._expected_pid = expected_pid
        self._queue = deque()
        self._lost = False
        self._error = None
        self._encode = encode_packet if codec is None else codec.encode
        self._parse = parse_packet if codec is None else codec.parse
        self._maximum = MAX_PACKET_BYTES if codec is None else codec.maximum

    @classmethod
    def adopt(cls, fd: int, *, expected_uid: int, expected_pid: int,
              expected_creator_uid=None, codec=None):
        """Transfer an exclusively owned FD only after validation.

        Socketpair SO_PEERCRED identifies its creator, possibly not the child
        inheriting the endpoint. Actual sender PID is checked on every packet.
        Adopt receivers before allowing senders to start: queued packets lacking
        credentials are deliberately rejected rather than retroactively trusted.
        """
        if expected_creator_uid is None:
            expected_creator_uid = expected_uid
        if (type(expected_creator_uid) is not int or not 0 <= expected_creator_uid <= 0xffffffff or
                (codec is not None and (not callable(codec.encode) or not callable(codec.parse) or
                 type(codec.maximum) is not int or not 1 <= codec.maximum <= 4096))):
            raise ChannelError("invalid trusted creator or codec")
        if not _supported():
            raise ChannelUnsupported("TVH2 channels require Linux per-message credentials")
        if socket.getdefaulttimeout() is not None:
            # Constructing a Python wrapper under a global default timeout can
            # change the shared descriptor's O_NONBLOCK flag before validation.
            raise ChannelError("global socket default timeouts are unsupported")
        if (type(fd) is not int or fd <= 2 or type(expected_uid) is not int or
                not 0 <= expected_uid <= 0xffffffff or type(expected_pid) is not int or
                not 1 <= expected_pid <= 0x7fffffff):
            raise ChannelError("invalid descriptor or trusted peer identity")
        duplicate = None
        probe = None
        try:
            duplicate = os.dup(fd)
            probe = socket.socket(fileno=duplicate)
            duplicate = None
            if probe.family != socket.AF_UNIX or probe.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_SEQPACKET:
                raise ChannelError("expected an AF_UNIX SOCK_SEQPACKET endpoint")
            probe.getpeername()
            credentials = probe.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _CREDENTIAL.size)
            pid, uid, _gid = _CREDENTIAL.unpack(credentials)
            if pid <= 0 or uid != expected_creator_uid:
                raise ChannelError("socket creator credentials do not match the trusted user")
            old_passcred = probe.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED)
            old_fdflags = fcntl.fcntl(fd, fcntl.F_GETFD)
        except (OSError, struct.error) as error:
            raise ChannelError("descriptor is not a connected handoff endpoint") from error
        finally:
            if probe is not None:
                probe.close()
            if duplicate is not None:
                os.close(duplicate)
        endpoint = socket.socket(fileno=fd)
        try:
            endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            fcntl.fcntl(fd, fcntl.F_SETFD, old_fdflags | fcntl.FD_CLOEXEC)
        except OSError as error:
            # All identity/type checks precede mutation. Preserve ownership on
            # an OS-level setup failure as well; exclusive FD ownership is a
            # caller prerequisite, so concurrent closes/reuse are unsupported.
            try:
                endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, old_passcred)
                fcntl.fcntl(fd, fcntl.F_SETFD, old_fdflags)
            except OSError:
                pass
            finally:
                endpoint.detach()
            raise ChannelError("cannot configure the validated handoff endpoint") from error
        return cls(endpoint, expected_uid, expected_pid, codec=codec)

    @property
    def fd(self):
        return -1 if self._socket is None else self._socket.fileno()

    @property
    def pending(self):
        return len(self._queue)

    @property
    def expected_uid(self):
        return self._expected_uid

    @property
    def expected_pid(self):
        return self._expected_pid

    @property
    def lost(self):
        return self._lost

    @property
    def last_error(self):
        return self._error

    def close(self):
        self._lost = True
        self._queue.clear()
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def _fail(self, reason):
        self._error = reason
        self.close()

    def send(self, message: Message) -> bool:
        if self._lost:
            return False
        packet = self._encode(message)
        if len(self._queue) >= MAX_QUEUED_PACKETS:
            return False
        self._queue.append(packet)
        self.flush()
        return not self._lost

    def flush(self) -> int:
        sent = 0
        while self._queue and not self._lost and sent < MAX_SENDS_PER_FLUSH:
            try:
                count = self._socket.send(self._queue[0], socket.MSG_DONTWAIT | socket.MSG_NOSIGNAL)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                self._fail("send-failed")
                break
            if count != len(self._queue[0]):
                self._fail("partial-sequenced-packet-send")
                break
            self._queue.popleft()
            sent += 1
        return sent

    def receive(self) -> Message | None:
        if self._lost:
            return None
        ancillary_size = socket.CMSG_SPACE(_CREDENTIAL.size) + socket.CMSG_SPACE(16 * array.array("i").itemsize)
        try:
            data, ancillary, flags, _address = self._socket.recvmsg(
                self._maximum, ancillary_size,
                socket.MSG_DONTWAIT | socket.MSG_CMSG_CLOEXEC)
        except (BlockingIOError, InterruptedError):
            return None
        except OSError:
            self._fail("receive-failed")
            return None
        credentials, bad_ancillary = [], False
        for level, kind, value in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                # Reject descriptor payloads and close every FD actually
                # delivered, even if another cmsg or the packet is malformed.
                delivered = array.array("i")
                delivered.frombytes(value[:len(value) - len(value) % delivered.itemsize])
                for received in delivered:
                    try:
                        os.close(received)
                    except OSError:
                        pass
                bad_ancillary = True
            elif level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS and len(value) == _CREDENTIAL.size:
                credentials.append(_CREDENTIAL.unpack(value))
            else:
                bad_ancillary = True
        if (not data or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or bad_ancillary or
                len(credentials) != 1):
            self._fail("missing-or-malformed-packet-credentials")
            return None
        pid, uid, _gid = credentials[0]
        if pid != self._expected_pid or uid != self._expected_uid:
            self._fail("sender-identity-mismatch")
            return None
        try:
            return self._parse(data)
        except ValueError:
            self._fail("malformed-control-packet")
            return None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def __del__(self):
        endpoint = getattr(self, "_socket", None)
        if endpoint is not None:
            endpoint.close()
