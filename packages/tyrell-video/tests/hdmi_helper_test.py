#!/usr/bin/env python3
"""Exercise VT ownership and recovery without touching a real console."""
import errno
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import struct
import tempfile
import types
import unittest
from unittest.mock import patch

source = Path(__file__).resolve().parents[1] / 'system/tyrell-video-hdmi-session'
loader = importlib.machinery.SourceFileLoader('hdmi_helper', str(source))
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)


class Console:
    def __init__(self):
        self.current = 7
        self.switched = []
        self.disallocated = []
        self.closed = False

    def active(self):
        return self.current

    def unused(self):
        return 1

    def activate(self, number):
        self.current = number
        self.switched.append(number)

    def wait_active(self, number):
        if self.current != number:
            raise helper.LeaseError('Console activation failed')

    def disallocate(self, number):
        self.disallocated.append(number)

    def close(self):
        self.closed = True


class Peer:
    def __init__(self, payload=b'', *, pid=1234, uid=1000, gid=1000, groups=(44, 1000)):
        self.sent = []
        self.payload = payload
        self.credentials = struct.pack('3i', pid, uid, gid)
        self.groups = struct.pack(f'{len(groups)}I', *groups)
        self.group_error = None
        self.queries = []

    def sendall(self, payload):
        self.sent.append(payload)

    def shutdown(self, how):
        pass

    def recv(self, count):
        return self.payload

    def getsockopt(self, level, option, length):
        self.queries.append((level, option, length))
        if option == socket.SO_PEERCRED:
            return self.credentials
        if option == helper.SO_PEERGROUPS:
            if self.group_error:
                raise self.group_error
            return self.groups
        raise AssertionError('Unexpected peer socket option')


class HelperTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.runtime = Path(self.directory.name)
        self.runtime_patch = patch.object(helper, 'RUNTIME', self.runtime)
        self.runtime_patch.start()
        self.credentials_patch = patch.object(helper.socket, 'SO_PEERCRED', 17, create=True)
        self.credentials_patch.start()
        self.console = Console()

    def tearDown(self):
        self.credentials_patch.stop()
        self.runtime_patch.stop()
        self.directory.cleanup()

    def marker(self):
        return self.runtime / 'restore.json'

    def lease(self, peer, select_result=None, drm=None):
        fd = os.open(self.runtime / 'lock', os.O_RDWR | os.O_CREAT, 0o600)
        if select_result is None:
            select_result = lambda *args: ([peer], [], [])
        try:
            with patch.object(helper, 'authenticate'), patch.object(helper, 'open_lock', return_value=fd), \
                    patch.object(helper, 'Console', return_value=self.console), \
                    patch.object(helper, 'wait_drm_release', side_effect=drm), \
                    patch.object(helper.select, 'select', side_effect=select_result):
                helper.run_lease(peer)
        finally:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_disconnect_restores_original_vt_and_frees_console(self):
        peer = Peer()
        self.lease(peer)
        self.assertEqual(peer.sent, [b'READY\n'])
        self.assertEqual(self.console.switched, [1, 7])
        self.assertEqual(self.console.disallocated, [1])
        self.assertTrue(self.console.closed)
        self.assertFalse(self.marker().exists())

    def test_marker_exists_before_first_console_change(self):
        original_activate = self.console.activate
        def activate(number):
            self.assertTrue(self.marker().exists())
            saved = json.loads(self.marker().read_text())
            self.assertEqual((saved['original'], saved['takeover']), (7, 1))
            original_activate(number)
        self.console.activate = activate
        self.lease(Peer())

    def test_acquisition_failure_rolls_back(self):
        with self.assertRaises(helper.DrmBusy):
            self.lease(Peer(), drm=[helper.DrmBusy('not released'), None])
        self.assertEqual(self.console.switched, [1, 7])
        self.assertFalse(self.marker().exists())

    def test_unexpected_data_is_not_a_command(self):
        with self.assertRaises(helper.LeaseError):
            self.lease(Peer(b'systemctl stop lightdm'))
        self.assertEqual(self.console.current, 7)
        self.assertFalse(self.marker().exists())

    def test_explicit_console_switch_is_respected(self):
        def changed(*args):
            self.console.current = 3
            return [], [], []
        with self.assertRaisesRegex(helper.LeaseError, 'Foreground console changed'):
            self.lease(Peer(), select_result=changed)
        self.assertEqual(self.console.current, 3)
        self.assertEqual(self.console.switched, [1])
        self.assertFalse(self.marker().exists())

    def test_failed_console_recovery_retains_marker(self):
        helper.save_marker(7, 1)
        self.console.current = 1
        with patch.object(self.console, 'activate', side_effect=OSError('console failed')):
            with self.assertRaises(OSError):
                helper.restore(self.console)
        self.assertTrue(self.marker().exists())
        self.assertEqual(self.console.current, 1)

    def test_busy_drm_never_strands_blank_console(self):
        helper.save_marker(7, 1, granted=True)
        self.console.current = 1
        fd = os.open(self.runtime / 'lock', os.O_RDWR | os.O_CREAT, 0o600)
        with patch.object(helper, 'open_lock', return_value=fd), \
                patch.object(helper, 'Console', return_value=self.console), \
                patch.object(helper, 'wait_drm_release', side_effect=helper.DrmBusy('wait')):
            helper.recovery()
        self.assertEqual(self.console.current, 7)
        self.assertFalse(self.marker().exists())

    def test_recovery_does_not_touch_another_live_lease(self):
        helper.save_marker(7, 1)
        with patch.object(helper, 'open_lock', side_effect=helper.LeaseBusy('leased')), \
                patch.object(helper, 'Console') as constructor:
            helper.recovery()
        constructor.assert_not_called()
        self.assertTrue(self.marker().exists())

    def authenticate(self, peer):
        with patch.object(helper.grp, 'getgrnam', return_value=types.SimpleNamespace(gr_gid=44)):
            helper.authenticate(peer)

    def test_authentication_accepts_root_without_supplementary_lookup(self):
        peer = Peer(uid=0, gid=0, groups=())
        self.authenticate(peer)
        self.assertEqual(len(peer.queries), 1)

    def test_authentication_accepts_primary_video_group(self):
        peer = Peer(gid=44, groups=())
        self.authenticate(peer)
        self.assertEqual(len(peer.queries), 1)

    def test_authentication_accepts_captured_supplementary_video_group(self):
        peer = Peer(groups=(5, 44, 1000))
        with patch.object(helper.Path, 'read_text', side_effect=AssertionError('No proc lookup')), \
                patch.object(helper.os, 'readlink', side_effect=AssertionError('No executable lookup')):
            self.authenticate(peer)
        self.assertEqual(peer.queries[-1], (socket.SOL_SOCKET, helper.SO_PEERGROUPS, 1024))

    def test_authentication_rejects_missing_video_group(self):
        with self.assertRaisesRegex(helper.LeaseError, 'video group'):
            self.authenticate(Peer(groups=(1000,)))

    def test_authentication_rejects_empty_supplementary_groups(self):
        with self.assertRaisesRegex(helper.LeaseError, 'video group'):
            self.authenticate(Peer(groups=()))

    def test_authentication_rejects_invalid_peer_identifiers(self):
        for identifiers in ({'pid': 0}, {'pid': -1}, {'uid': -1}, {'gid': -1}):
            with self.subTest(**identifiers), self.assertRaisesRegex(helper.LeaseError, 'credentials'):
                self.authenticate(Peer(**identifiers))

    def test_authentication_rejects_malformed_credentials(self):
        peer = Peer()
        for credentials in (b'', b'x', peer.credentials[:-1], peer.credentials + b'x'):
            peer.credentials = credentials
            with self.subTest(length=len(credentials)), self.assertRaises(helper.LeaseError):
                self.authenticate(peer)

    def test_authentication_fails_closed_when_peer_credentials_unavailable(self):
        peer = Peer()
        with patch.object(peer, 'getsockopt', side_effect=OSError(errno.ENOTCONN, 'disconnected')):
            with self.assertRaisesRegex(helper.LeaseError, 'verify HDMI peer credentials'):
                self.authenticate(peer)

    def test_authentication_rejects_malformed_groups(self):
        peer = Peer()
        peer.groups = b'\x2c'
        with self.assertRaisesRegex(helper.LeaseError, 'peer groups'):
            self.authenticate(peer)

    def test_authentication_fails_closed_when_groups_cannot_be_verified(self):
        for number in (errno.ERANGE, errno.ENOPROTOOPT, errno.EPERM):
            peer = Peer()
            peer.group_error = OSError(number, 'groups unavailable')
            with self.subTest(errno=number), self.assertRaisesRegex(helper.LeaseError, 'verify'):
                self.authenticate(peer)

    def test_denied_client_cannot_open_console_or_lock(self):
        with patch.object(helper.grp, 'getgrnam', return_value=types.SimpleNamespace(gr_gid=44)), \
                patch.object(helper, 'Console') as console, patch.object(helper, 'open_lock') as lock:
            with self.assertRaises(helper.LeaseError):
                helper.run_lease(Peer(groups=()))
        console.assert_not_called()
        lock.assert_not_called()

    def test_main_rejects_root_or_unrelated_service_identity(self):
        for uid in (0, 1000):
            with self.subTest(uid=uid), patch.object(helper.os, 'geteuid', return_value=uid), \
                    patch.object(helper.pwd, 'getpwnam', return_value=types.SimpleNamespace(pw_uid=321)), \
                    patch.object(helper.socket, 'socket') as peer:
                with self.assertRaisesRegex(helper.LeaseError, 'requires its system service'):
                    helper.main()
            peer.assert_not_called()

    def test_main_rejects_caller_arguments(self):
        with patch.object(helper.os, 'geteuid', return_value=321), \
                patch.object(helper.pwd, 'getpwnam', return_value=types.SimpleNamespace(pw_uid=321)), \
                patch.object(helper.sys, 'argv', ['tyrell-video-hdmi-session', '--command', 'anything']), \
                patch.object(helper.socket, 'socket') as peer:
            with self.assertRaisesRegex(helper.LeaseError, 'Unexpected HDMI helper argument'):
                helper.main()
        peer.assert_not_called()

    def test_private_runtime_lock_excludes_second_connection(self):
        fd = helper.open_lock()
        try:
            with self.assertRaises(helper.LeaseBusy):
                helper.open_lock()
            self.assertFalse(os.get_inheritable(fd))
        finally:
            os.close(fd)
        fd = helper.open_lock()
        os.close(fd)

    def test_runtime_rejects_wrong_owner(self):
        with patch.object(helper.os, 'geteuid', return_value=os.geteuid() + 1):
            with self.assertRaisesRegex(helper.LeaseError, 'Unsafe HDMI recovery directory'):
                helper.open_lock()

    def test_runtime_rejects_group_or_world_access(self):
        for mode in (0o750, 0o705, 0o770, 0o777):
            self.runtime.chmod(mode)
            with self.subTest(mode=mode), self.assertRaises(helper.LeaseError):
                helper.open_lock()
        self.runtime.chmod(0o700)

    def test_runtime_rejects_symlink_directory(self):
        link = self.runtime / 'redirect'
        link.symlink_to(self.runtime, target_is_directory=True)
        with patch.object(helper, 'RUNTIME', link):
            with self.assertRaisesRegex(helper.LeaseError, 'Unsafe HDMI recovery directory'):
                helper.open_lock()

    def test_runtime_lock_rejects_symlink(self):
        target = self.runtime / 'unrelated'
        target.write_text('unchanged')
        (self.runtime / 'lease.lock').symlink_to(target)
        with self.assertRaises(OSError):
            helper.open_lock()
        self.assertEqual(target.read_text(), 'unchanged')

    def test_console_uses_named_descriptor_with_close_on_exec(self):
        environment = {'LISTEN_PID': '123', 'LISTEN_FDS': '2', 'LISTEN_FDNAMES': 'connection:console'}
        info = types.SimpleNamespace(st_mode=stat.S_IFCHR | 0o600, st_rdev=os.makedev(4, 0))
        with patch.dict(helper.os.environ, environment, clear=True), \
                patch.object(helper.os, 'getpid', return_value=123), \
                patch.object(helper.os, 'fstat', return_value=info) as fstat, \
                patch.object(helper.os, 'open') as opened, \
                patch.object(helper.fcntl, 'fcntl', return_value=9) as duplicate:
            console = helper.Console()
        self.assertEqual(console.fd, 9)
        fstat.assert_called_once_with(4)
        duplicate.assert_called_once_with(4, helper.fcntl.F_DUPFD_CLOEXEC, 3)
        opened.assert_not_called()
        with patch.object(helper.os, 'close') as close:
            console.close()
        close.assert_called_once_with(9)

    def test_console_accepts_systemd257_stdin_socket_descriptor_layout(self):
        # On systemd 257 the accepted socket is fd 0 and omitted from the
        # descriptor count, but its connection name remains before console.
        environment = {'LISTEN_PID': '123', 'LISTEN_FDS': '1', 'LISTEN_FDNAMES': 'connection:console'}
        info = types.SimpleNamespace(st_mode=stat.S_IFCHR | 0o600, st_rdev=os.makedev(4, 0))
        with patch.dict(helper.os.environ, environment, clear=True), \
                patch.object(helper.os, 'getpid', return_value=123), \
                patch.object(helper.os, 'fstat', return_value=info) as fstat, \
                patch.object(helper.fcntl, 'fcntl', return_value=9) as duplicate:
            console = helper.Console()
        self.assertEqual(console.fd, 9)
        fstat.assert_called_once_with(3)
        duplicate.assert_called_once_with(3, helper.fcntl.F_DUPFD_CLOEXEC, 3)

    def test_console_rejects_missing_or_invalid_activation_environment(self):
        environments = [
            {},
            {'LISTEN_PID': '456', 'LISTEN_FDS': '1', 'LISTEN_FDNAMES': 'console'},
            {'LISTEN_PID': '123', 'LISTEN_FDS': '0', 'LISTEN_FDNAMES': ''},
            {'LISTEN_PID': '123', 'LISTEN_FDS': '17', 'LISTEN_FDNAMES': ':'.join(['console'] * 17)},
            {'LISTEN_PID': '123', 'LISTEN_FDS': '2', 'LISTEN_FDNAMES': 'console:console'},
            {'LISTEN_PID': '123', 'LISTEN_FDS': '2', 'LISTEN_FDNAMES': 'console'},
            {'LISTEN_PID': '123', 'LISTEN_FDS': '1', 'LISTEN_FDNAMES': 'connection'},
            {'LISTEN_PID': '123', 'LISTEN_FDS': '1', 'LISTEN_FDNAMES': 'unexpected:console'},
            {'LISTEN_PID': '123', 'LISTEN_FDS': '1', 'LISTEN_FDNAMES': 'console:connection'},
            {'LISTEN_PID': '123', 'LISTEN_FDS': '1', 'LISTEN_FDNAMES': 'connection:console:extra'},
            {'LISTEN_PID': '123', 'LISTEN_FDS': 'invalid', 'LISTEN_FDNAMES': 'console'},
        ]
        for environment in environments:
            with self.subTest(environment=environment), patch.dict(helper.os.environ, environment, clear=True), \
                    patch.object(helper.os, 'getpid', return_value=123), \
                    patch.object(helper.os, 'fstat') as fstat, patch.object(helper.os, 'open') as opened:
                with self.assertRaises((helper.LeaseError, ValueError)):
                    helper.Console()
            fstat.assert_not_called()
            opened.assert_not_called()

    def test_console_rejects_regular_file_or_other_character_device(self):
        environment = {'LISTEN_PID': '123', 'LISTEN_FDS': '1', 'LISTEN_FDNAMES': 'console'}
        invalid = [
            types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_rdev=0),
            types.SimpleNamespace(st_mode=stat.S_IFCHR | 0o600, st_rdev=os.makedev(4, 1)),
            types.SimpleNamespace(st_mode=stat.S_IFCHR | 0o600, st_rdev=os.makedev(1, 3)),
        ]
        for info in invalid:
            with self.subTest(info=info), patch.dict(helper.os.environ, environment, clear=True), \
                    patch.object(helper.os, 'getpid', return_value=123), \
                    patch.object(helper.os, 'fstat', return_value=info), \
                    patch.object(helper.fcntl, 'fcntl') as duplicate:
                with self.assertRaisesRegex(helper.LeaseError, 'Invalid HDMI console descriptor'):
                    helper.Console()
            duplicate.assert_not_called()

    def test_invalid_recovery_marker_never_selects_console(self):
        self.marker().write_text('{"original":"7", "takeover":1}')
        with self.assertRaisesRegex(helper.LeaseError, 'Invalid HDMI recovery record'):
            helper.restore(self.console)
        self.assertEqual(self.console.switched, [])
        self.assertTrue(self.marker().exists())


if __name__ == '__main__':
    unittest.main()
