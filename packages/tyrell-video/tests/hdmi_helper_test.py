#!/usr/bin/env python3
"""Exercise VT ownership and recovery without touching a real console."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import socket
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
    def __init__(self, payload=b''):
        self.sent = []
        self.payload = payload

    def sendall(self, payload):
        self.sent.append(payload)

    def shutdown(self, how):
        pass

    def recv(self, count):
        return self.payload

    def getsockopt(self, level, option, length):
        return struct.pack('3i', 1234, 1000, 1000)


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

    def test_authentication_rejects_another_executable(self):
        status = 'Uid:\t1000\t1000\t1000\t1000\nGroups:\t44 1000\n'
        with patch.object(helper.Path, 'read_text', return_value=status), \
                patch.object(helper.grp, 'getgrnam', return_value=types.SimpleNamespace(gr_gid=44)), \
                patch.object(helper.os, 'readlink', return_value='/usr/bin/python3'):
            with self.assertRaisesRegex(helper.LeaseError, 'installed Tyrell Video'):
                helper.authenticate(Peer())

    def test_authentication_rejects_missing_video_group(self):
        status = 'Uid:\t1000\t1000\t1000\t1000\nGroups:\t1000\n'
        with patch.object(helper.Path, 'read_text', return_value=status), \
                patch.object(helper.grp, 'getgrnam', return_value=types.SimpleNamespace(gr_gid=44)):
            with self.assertRaisesRegex(helper.LeaseError, 'video group'):
                helper.authenticate(Peer())

    def test_invalid_recovery_marker_never_selects_console(self):
        self.marker().write_text('{"original":"7", "takeover":1}')
        with self.assertRaisesRegex(helper.LeaseError, 'Invalid HDMI recovery record'):
            helper.restore(self.console)
        self.assertEqual(self.console.switched, [])
        self.assertTrue(self.marker().exists())


if __name__ == '__main__':
    unittest.main()
