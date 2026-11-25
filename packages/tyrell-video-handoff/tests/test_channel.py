"""Real Linux Unix sockets/processes; no display, systemd or audio backend."""
import array
import fcntl
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import handoff_channel as transport
from handoff_protocol import CAPABILITIES, Message, encode_packet, parse_packet

HELLO = Message("hello", ("a" * 32, CAPABILITIES))


@unittest.skipUnless(transport._supported(), "Linux SCM_CREDENTIALS and SOCK_SEQPACKET required")
class ChannelTests(unittest.TestCase):
    def pair(self, expected_pid=None):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(right.close)
        channel = transport.Channel.adopt(left.detach(), expected_uid=os.getuid(),
                                          expected_pid=os.getpid() if expected_pid is None else expected_pid)
        self.addCleanup(channel.close)
        return channel, right

    def test_round_trip_is_one_packet_and_never_implies_delivery_completion(self):
        channel, peer = self.pair()
        self.assertIsNone(channel.receive())
        self.assertFalse(channel.lost)
        self.assertTrue(channel.send(HELLO))
        self.assertEqual(channel.pending, 0)
        self.assertEqual(parse_packet(peer.recv(512)), HELLO)
        peer.send(encode_packet(HELLO))
        self.assertEqual(channel.receive(), HELLO)
        self.assertIsNone(channel.receive())
        self.assertEqual(channel.expected_pid, os.getpid())
        self.assertEqual(channel.expected_uid, os.getuid())

    def test_invalid_descriptors_and_socket_types_remain_open_and_unchanged(self):
        stderr_flags = fcntl.fcntl(2, fcntl.F_GETFD)
        for fd in (-1, 0, 1, 2, True, "3"):
            with self.subTest(fd=fd), self.assertRaises(transport.ChannelError):
                transport.Channel.adopt(fd, expected_uid=os.getuid(), expected_pid=os.getpid())
        self.assertEqual(fcntl.fcntl(2, fcntl.F_GETFD), stderr_flags)
        with tempfile.TemporaryFile() as regular:
            before = fcntl.fcntl(regular.fileno(), fcntl.F_GETFD)
            with self.assertRaises(transport.ChannelError):
                transport.Channel.adopt(regular.fileno(), expected_uid=os.getuid(), expected_pid=os.getpid())
            self.assertEqual(fcntl.fcntl(regular.fileno(), fcntl.F_GETFD), before)
        endpoints = [socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)]
        stream, stream_peer = socket.socketpair()
        endpoints.append(stream)
        self.addCleanup(stream_peer.close)
        for endpoint in endpoints:
            self.addCleanup(endpoint.close)
            fd = endpoint.fileno()
            before = fcntl.fcntl(fd, fcntl.F_GETFD), fcntl.fcntl(fd, fcntl.F_GETFL)
            with self.assertRaises(transport.ChannelError):
                transport.Channel.adopt(fd, expected_uid=os.getuid(), expected_pid=os.getpid())
            self.assertEqual((fcntl.fcntl(fd, fcntl.F_GETFD), fcntl.fcntl(fd, fcntl.F_GETFL)), before)

    def test_untrusted_creator_uid_is_refused_without_mutating_socket(self):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(left.close); self.addCleanup(right.close)
        before = left.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED)
        with self.assertRaises(transport.ChannelError):
            transport.Channel.adopt(left.fileno(), expected_uid=os.getuid() + 1, expected_pid=os.getpid())
        self.assertEqual(left.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED), before)
        left.send(b"still-open")
        self.assertEqual(right.recv(32), b"still-open")

    def test_pre_adoption_credentialless_packet_cannot_be_retroactively_trusted(self):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(right.close)
        right.send(encode_packet(HELLO))
        channel = transport.Channel.adopt(left.detach(), expected_uid=os.getuid(), expected_pid=os.getpid())
        self.addCleanup(channel.close)
        self.assertIsNone(channel.receive())
        self.assertTrue(channel.lost)

    def test_global_default_timeout_cannot_mutate_an_unadopted_fd(self):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(left.close); self.addCleanup(right.close)
        before = fcntl.fcntl(left.fileno(), fcntl.F_GETFL)
        previous = socket.getdefaulttimeout()
        socket.setdefaulttimeout(0.5)
        try:
            with self.assertRaises(transport.ChannelError):
                transport.Channel.adopt(left.fileno(), expected_uid=os.getuid(), expected_pid=os.getpid())
        finally:
            socket.setdefaulttimeout(previous)
        self.assertEqual(fcntl.fcntl(left.fileno(), fcntl.F_GETFL), before)

    def test_shared_nonblocking_flags_are_preserved_and_exec_cannot_inherit_fd(self):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(right.close)
        left.setblocking(False)
        fd = left.detach()
        os.set_inheritable(fd, True)
        before = fcntl.fcntl(fd, fcntl.F_GETFL)
        channel = transport.Channel.adopt(fd, expected_uid=os.getuid(), expected_pid=os.getpid())
        self.addCleanup(channel.close)
        self.assertEqual(fcntl.fcntl(fd, fcntl.F_GETFL), before)
        self.assertFalse(os.get_inheritable(fd))
        code = "import os,sys;\ntry: print(os.fstat(int(sys.argv[1])).st_ino)\nexcept OSError: print('closed')"
        child = subprocess.check_output([sys.executable, "-c", code, str(fd)], close_fds=False, text=True).strip()
        self.assertNotEqual(child, str(os.fstat(fd).st_ino))

    def test_backpressure_has_a_bounded_queue_and_preserves_packet_order(self):
        channel, peer = self.pair()
        duplicate = socket.socket(fileno=os.dup(channel.fd))
        duplicate.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
        duplicate.close()
        accepted = []
        refused = False
        for index in range(1, 101):
            message = Message("grant", ("a" * 32, "b" * 32, "1", str(index), "c" * 32))
            if not channel.send(message):
                refused = True
                break
            accepted.append(message)
        self.assertTrue(refused)
        self.assertFalse(channel.lost)
        self.assertLessEqual(channel.pending, transport.MAX_QUEUED_PACKETS)
        peer.setblocking(False)
        received = []
        for _ in range(100):
            while True:
                try:
                    received.append(parse_packet(peer.recv(512)))
                except BlockingIOError:
                    break
            count = channel.flush()
            self.assertLessEqual(count, 64)
            if len(received) == len(accepted):
                break
        self.assertEqual(received, accepted)
        self.assertEqual(channel.pending, 0)

    def test_truncated_malformed_and_empty_packets_close_the_channel(self):
        for packet in (b"x" * 513, b"TVH2\tunknown", b"hello\t1", b"", encode_packet(HELLO) + b"\n"):
            channel, peer = self.pair()
            peer.send(packet)
            with self.subTest(packet=packet):
                self.assertIsNone(channel.receive())
                self.assertTrue(channel.lost)
                self.assertEqual(channel.fd, -1)
                self.assertIsNotNone(channel.last_error)

    def test_eof_and_send_failure_are_loss_not_release(self):
        channel, peer = self.pair()
        peer.close()
        self.assertIsNone(channel.receive())
        self.assertTrue(channel.lost)
        self.assertFalse(channel.send(HELLO))
        channel, peer = self.pair()
        peer.close()
        self.assertFalse(channel.send(HELLO))
        self.assertTrue(channel.lost)

    def test_received_rights_are_rejected_and_every_delivered_fd_is_closed(self):
        channel, peer = self.pair()
        with tempfile.TemporaryFile() as original:
            inode = os.fstat(original.fileno()).st_ino
            rights = array.array("i", [original.fileno()] * 32)
            peer.sendmsg([encode_packet(HELLO)], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, rights)])
            closed = []
            close = os.close
            def tracked(fd):
                closed.append((fd, os.fstat(fd).st_ino, os.get_inheritable(fd)))
                close(fd)
            with patch.object(transport.os, "close", side_effect=tracked):
                self.assertIsNone(channel.receive())
            self.assertTrue(channel.lost)
            self.assertTrue(closed)
            self.assertTrue(all(item[1] == inode for item in closed))
            if hasattr(socket, "MSG_CMSG_CLOEXEC"):
                self.assertTrue(all(not item[2] for item in closed))
            for fd, _, _ in closed:
                with self.assertRaises(OSError):
                    os.fstat(fd)
            self.assertEqual(os.fstat(original.fileno()).st_ino, inode)

    def test_missing_duplicate_and_truncated_ancillary_credentials_fail_closed(self):
        credential = (socket.SOL_SOCKET, socket.SCM_CREDENTIALS, struct.pack("iII", os.getpid(), os.getuid(), os.getgid()))
        cases = (([], 0), ([credential, credential], 0), ([credential], socket.MSG_CTRUNC),
                 ([(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, b"bad")], 0))
        for ancillary, flags in cases:
            channel, _peer = self.pair()
            real = channel._socket
            class Endpoint:
                def recvmsg(self, *_args):
                    return encode_packet(HELLO), ancillary, flags, None
                def close(self):
                    real.close()
            channel._socket = Endpoint()
            self.assertIsNone(channel.receive())
            self.assertTrue(channel.lost)

    def fork_sender(self, expect_child):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        signal_read, signal_write = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                left.close(); os.close(signal_write)
                os.read(signal_read, 1)
                right.send(encode_packet(HELLO))
                os._exit(0)
            except BaseException:
                os._exit(1)
        right.close(); os.close(signal_read)
        channel = None
        try:
            channel = transport.Channel.adopt(left.detach(), expected_uid=os.getuid(),
                                              expected_pid=pid if expect_child else os.getpid())
            self.addCleanup(channel.close)
            creator = struct.unpack("iII", channel._socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[0]
            self.assertEqual(creator, os.getpid())
            os.write(signal_write, b"1")
            deadline = time.monotonic() + 2
            message = None
            while message is None and not channel.lost and time.monotonic() < deadline:
                message = channel.receive()
                if message is None:
                    time.sleep(0.001)
            return message, channel
        finally:
            os.close(signal_write)
            deadline = time.monotonic() + 2
            while True:
                waited, status = os.waitpid(pid, os.WNOHANG)
                if waited:
                    self.assertTrue(os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0)
                    break
                if time.monotonic() > deadline:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                    self.fail("fixture child did not exit")
                time.sleep(0.001)

    def test_inherited_socketpair_checks_actual_sender_not_creator_pid(self):
        message, channel = self.fork_sender(True)
        self.assertEqual(message, HELLO)
        self.assertFalse(channel.lost)

    def test_wrong_registered_sender_pid_is_rejected_even_with_same_uid(self):
        message, channel = self.fork_sender(False)
        self.assertIsNone(message)
        self.assertTrue(channel.lost)
        self.assertEqual(channel.last_error, "sender-identity-mismatch")


class UnsupportedChannelTests(unittest.TestCase):
    def test_unsupported_platform_does_not_touch_the_descriptor(self):
        with tempfile.TemporaryFile() as source:
            fd = source.fileno()
            before = fcntl.fcntl(fd, fcntl.F_GETFD)
            with patch.object(transport.sys, "platform", "darwin"), self.assertRaises(transport.ChannelUnsupported):
                transport.Channel.adopt(fd, expected_uid=os.getuid(), expected_pid=os.getpid())
            self.assertEqual(fcntl.fcntl(fd, fcntl.F_GETFD), before)


if __name__ == "__main__":
    unittest.main()
