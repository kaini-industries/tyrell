"""Audio backend callback finality and private Pulse null-server integration."""
from dataclasses import replace
import ctypes as C
import ctypes.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import handoff_audio as audio

TOKEN = "a" * 32


class FakeConnection:
    def __init__(self, _server):
        self.cookie = 73
        self.binding = {"server_uid": os.getuid(), "server_pid": os.getpid(),
                        "socket_dev": 1, "socket_ino": 7}
        self.rows = []
        self.loads, self.unloads = [], []
        self.closed = False
        self.unload_success = True
        self.leave_module = False
        self.index = 7
    def server_cookie(self):
        return self.cookie
    def modules(self):
        return list(self.rows)
    def load(self, name, arguments):
        self.loads.append((name, arguments))
        if self.index != audio.INVALID_INDEX:
            self.rows.append(audio.Module(self.index, name, arguments))
        return self.index
    def unload(self, index):
        self.unloads.append(index)
        if self.unload_success and not self.leave_module:
            self.rows = [row for row in self.rows if row.index != index]
        return self.unload_success
    def close(self):
        self.closed = True


class AudioBackendTests(unittest.TestCase):
    def backend(self, **kwargs):
        self.connection = FakeConnection("unused")
        backend = audio.PulseAudioBackend("unix:/unused", profile="null",
                    connection_factory=lambda _server: self.connection, **kwargs)
        self.addCleanup(backend.close)
        return backend

    def test_exact_load_readback_unload_and_absence(self):
        backend = self.backend()
        lease = backend.acquire(TOKEN)
        self.assertTrue(backend.disposable)
        self.assertEqual(lease.cookie, 73)
        self.assertEqual(json.loads(json.dumps(lease.evidence()))["index"], 7)
        result = backend.release(lease)
        self.assertTrue(result["unload_confirmed"] and result["absence_confirmed"])
        self.assertEqual(self.connection.unloads, [7])
        self.assertEqual(backend.release(lease), result)
        self.assertEqual(self.connection.unloads, [7])

    def test_prepared_load_identity_is_complete_immutable_and_has_no_load(self):
        backend = self.backend()
        prepared = backend.prepared(TOKEN)
        self.assertEqual(set(prepared), {"generation", "cookie", "token", "name", "arguments",
                                         "server_uid", "server_pid", "socket_dev", "socket_ino"})
        self.assertFalse(self.connection.loads)
        self.assertFalse(self.connection.unloads)
        copy = dict(prepared)
        prepared["token"] = "foreign"
        self.assertEqual(backend.prepared(TOKEN), copy)
        with self.assertRaises(audio.AudioError):
            backend.acquire("b" * 32)
        self.assertFalse(self.connection.loads)
        lease = backend.acquire(TOKEN)
        self.assertEqual(lease.evidence(), {**copy, "index": lease.index})
        backend.release(lease)

    def test_invalid_tokens_and_duplicate_acquire_never_load(self):
        backend = self.backend()
        for token in (None, "A" * 32, "a" * 31, TOKEN + " sink_name=foreign", True):
            with self.subTest(token=token), self.assertRaises(audio.AudioError):
                backend.acquire(token)
        self.assertFalse(self.connection.loads)
        backend.acquire(TOKEN)
        with self.assertRaises(audio.AudioError):
            backend.acquire("b" * 32)
        self.assertEqual(len(self.connection.loads), 1)

    def test_fixed_hdmi_scope_has_no_caller_arguments_or_fallback(self):
        connection = FakeConnection("unused")
        backend = audio.PulseAudioBackend("unix:/unused", device="vc4hdmi0",
                                        connection_factory=lambda _server: connection)
        self.addCleanup(backend.close)
        lease = backend.acquire(TOKEN)
        self.assertFalse(backend.disposable)
        self.assertEqual(lease.name, "module-alsa-sink")
        self.assertIn("device=hdmi:CARD=vc4hdmi0,DEV=0", lease.arguments)
        self.assertNotIn("default", lease.arguments)
        for device in (None, "other", "vc4hdmi0 x=foreign", "/dev/snd/pcm"):
            with self.subTest(device=device), self.assertRaises(audio.AudioError):
                audio.PulseAudioBackend("unix:/unused", device=device,
                                        connection_factory=lambda _server: connection)

    def test_foreign_lease_cannot_unload(self):
        backend = self.backend()
        lease = backend.acquire(TOKEN)
        for foreign in (None, replace(lease, generation="b" * 32), replace(lease, index=8),
                        replace(lease, token="b" * 32), replace(lease, arguments="foreign")):
            with self.subTest(lease=foreign), self.assertRaises(audio.AudioError):
                backend.release(foreign)
        self.assertFalse(self.connection.unloads)

    def test_reused_module_index_or_changed_arguments_is_never_unloaded(self):
        for replacement in (audio.Module(7, "module-null-sink", "foreign"),
                            audio.Module(7, "module-other", "foreign")):
            with self.subTest(replacement=replacement):
                backend = self.backend()
                lease = backend.acquire(TOKEN)
                self.connection.rows = [replacement]
                with self.assertRaises(audio.AudioUnknown):
                    backend.release(lease)
                self.assertFalse(self.connection.unloads)
                backend.close()

    def test_missing_module_does_not_certify_an_unknown_old_load(self):
        backend = self.backend()
        lease = backend.acquire(TOKEN)
        self.connection.rows.clear()
        with self.assertRaises(audio.AudioUnknown):
            backend.release(lease)
        self.assertFalse(self.connection.unloads)
        self.connection.rows.append(audio.Module(7, lease.name, lease.arguments))
        with self.assertRaises(audio.AudioUnknown):
            backend.release(lease)

    def test_changed_server_cookie_cannot_reuse_module_index(self):
        backend = self.backend()
        lease = backend.acquire(TOKEN)
        self.connection.cookie += 1
        with self.assertRaises(audio.AudioUnknown):
            backend.release(lease)
        self.assertFalse(self.connection.unloads)

    def test_temporary_identity_loss_permanently_poisoned_until_reconciliation(self):
        backend = self.backend()
        lease = backend.acquire(TOKEN)
        with patch.object(self.connection, "server_cookie", side_effect=audio.AudioUnknown("endpoint replaced")):
            with self.assertRaises(audio.AudioUnknown):
                backend.release(lease)
        with self.assertRaises(audio.AudioUnknown):
            backend.release(lease)
        self.assertFalse(self.connection.unloads)

    def test_failed_unload_or_failed_absence_readback_is_unknown(self):
        for key in ("unload_success", "leave_module"):
            with self.subTest(key=key):
                backend = self.backend()
                lease = backend.acquire(TOKEN)
                setattr(self.connection, key, key == "leave_module")
                with self.assertRaises(audio.AudioUnknown):
                    backend.release(lease)
                with self.assertRaises(audio.AudioUnknown):
                    backend.release(lease)
                self.assertEqual(self.connection.unloads, [7])
                backend.close()

    def test_preexisting_token_and_index_are_not_adopted(self):
        backend = self.backend()
        self.connection.rows.append(audio.Module(99, "module-null-sink", "sink_name=tyrell_v2_" + TOKEN))
        with self.assertRaises(audio.AudioError):
            backend.acquire(TOKEN)
        self.assertFalse(self.connection.loads)
        self.connection.rows = [audio.Module(7, "module-null-sink", "sink_name=foreign")]
        with self.assertRaises(audio.AudioUnknown):
            backend.acquire(TOKEN)
        self.assertFalse(self.connection.unloads)

    def test_positive_load_rejection_is_not_a_created_module(self):
        backend = self.backend()
        self.connection.index = audio.INVALID_INDEX
        with self.assertRaises(audio.AudioError):
            backend.acquire(TOKEN)
        self.assertFalse(self.connection.rows)
        self.assertFalse(self.connection.unloads)


class FakeLib:
    """Exercise the ctypes callback lifecycle, not a second backend state model."""
    def __init__(self):
        self.queue, self.operations, self.unrefs = [], {}, []
        self.state = 4
        self.closed = False
        self.rows = []
        self.accepted = threading.Event()
        self.finish_load = threading.Event()
        self.fail_load_connection = False
        self.no_load_callback = False
    def pa_mainloop_new(self): return 1
    def pa_mainloop_get_api(self, _loop): return 2
    def pa_context_new(self, _api, _name): return 3
    def pa_context_connect(self, _context, _server, flags, _spawn):
        assert flags == 1
        return 0
    def pa_context_get_state(self, _context): return self.state
    def pa_context_disconnect(self, _context): self.closed = True
    def pa_context_unref(self, _context): pass
    def pa_mainloop_free(self, _loop): pass
    def pa_operation_get_state(self, operation): return self.operations[operation]
    def pa_operation_unref(self, operation): self.unrefs.append(operation)
    def operation(self, callback):
        operation = len(self.operations) + 1
        self.operations[operation] = 0
        def dispatch():
            callback()
            self.operations[operation] = 2 if self.fail_load_connection and self.accepted.is_set() else 1
        self.queue.append(dispatch)
        return operation
    def pa_mainloop_iterate(self, _loop, _block, _retval):
        self.queue.pop(0)()
        return 0
    def pa_context_get_server_info(self, _context, callback, _userdata):
        info = audio._ServerInfo()
        info.cookie = 73
        return self.operation(lambda: callback(3, C.pointer(info), None))
    def pa_context_get_module_info_list(self, _context, callback, _userdata):
        def complete():
            for row in self.rows:
                info = audio._ModuleInfo()
                info.index, info.name, info.argument = row.index, row.name.encode(), row.arguments.encode()
                callback(3, C.pointer(info), 0, None)
            callback(3, None, 1, None)
        return self.operation(complete)
    def pa_context_load_module(self, _context, name, arguments, callback, _userdata):
        def complete():
            self.accepted.set()
            if not self.finish_load.wait(5):
                raise RuntimeError("load gate not released")
            if self.fail_load_connection:
                self.state = 5
                return
            self.rows.append(audio.Module(7, name.decode(), arguments.decode()))
            if not self.no_load_callback:
                callback(3, 7, None)
        return self.operation(complete)
    def pa_context_unload_module(self, _context, index, callback, _userdata):
        def complete():
            self.rows = [row for row in self.rows if row.index != index]
            callback(3, 1, None)
        return self.operation(complete)


class AudioCallbackTests(unittest.TestCase):
    @staticmethod
    def bind(connection, _uid, _pid):
        connection._binding = {"server_uid": os.getuid(), "server_pid": os.getpid(),
                               "socket_dev": 1, "socket_ino": 7}

    def run_gated(self, lib, cancel):
        result = {}
        def worker():
            with patch.object(audio.PulseConnection, "_bind_server", self.bind), patch.object(audio.PulseConnection, "_server_alive"):
                backend = audio.PulseAudioBackend("unix:/unused", profile="null",
                    connection_factory=lambda server: audio.PulseConnection(server, library=lib))
                result["backend"] = backend
                try:
                    lease = backend.acquire(TOKEN)
                    result["lease"] = lease
                    if cancel.is_set():
                        result["release"] = backend.release(lease)
                except BaseException as error:
                    result["error"] = error
                finally:
                    result["held_before_close"] = len(backend._connection._pending)
                    backend.close()
        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(lib.accepted.wait(5))
        self.assertEqual(lib.unrefs, list(range(1, len(lib.operations))))
        self.assertFalse(lib.closed)
        cancel.set()  # Queued drain intent, never cancellation of pa_operation.
        self.assertNotIn("lease", result)
        self.assertFalse(lib.rows)
        lib.finish_load.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        return result

    def test_cancel_while_load_pending_keeps_callback_then_unloads_created_module(self):
        lib = FakeLib()
        result = self.run_gated(lib, threading.Event())
        self.assertNotIn("error", result)
        self.assertTrue(result["release"]["absence_confirmed"])
        self.assertEqual(result["held_before_close"], 0)
        self.assertFalse(lib.rows)
        self.assertTrue(lib.closed)

    def test_context_loss_during_load_retains_unknown_operation_until_abandonment(self):
        lib = FakeLib()
        lib.fail_load_connection = True
        result = self.run_gated(lib, threading.Event())
        self.assertIsInstance(result["error"], audio.AudioUnknown)
        self.assertNotIn("release", result)
        self.assertEqual(result["held_before_close"], 1)

    def test_terminal_operation_without_load_callback_is_not_completion(self):
        lib = FakeLib()
        lib.no_load_callback = True
        result = self.run_gated(lib, threading.Event())
        self.assertIsInstance(result["error"], audio.AudioUnknown)
        self.assertNotIn("release", result)
        self.assertEqual(result["held_before_close"], 1)
        self.assertEqual(len(lib.rows), 1)

    def test_default_remote_and_control_character_endpoints_rejected(self):
        for endpoint in (None, "", "tcp:localhost", "unix:/tmp/server\n", "unix:/tmp/a b"):
            with self.subTest(endpoint=endpoint), self.assertRaises(audio.AudioError):
                audio.PulseConnection(endpoint, library=FakeLib())

    def test_connection_endpoint_restoration_cannot_clear_known_identity_loss(self):
        with patch.object(audio.PulseConnection, "_bind_server", self.bind), patch.object(audio.PulseConnection, "_server_alive"):
            connection = audio.PulseConnection("unix:/unused", library=FakeLib())
            self.addCleanup(connection.close)
            with patch.object(connection, "_server_alive", side_effect=audio.AudioUnknown("endpoint replaced")):
                with self.assertRaises(audio.AudioUnknown):
                    connection.server_cookie()
            with self.assertRaises(audio.AudioUnknown):
                connection.server_cookie()
            self.assertTrue(connection._poisoned)


REAL_PULSE = sys.platform.startswith("linux") and ctypes.util.find_library("pulse") and shutil.which("pulseaudio") and os.getuid() != 0


@unittest.skipUnless(REAL_PULSE, "Linux nonroot libpulse and disposable pulseaudio required")
class RealPulseAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        runtime = self.directory / "runtime"
        runtime.mkdir(mode=0o700)
        self.server = self.directory / "native"
        config = self.directory / "default.pa"
        config.write_text("load-module module-native-protocol-unix socket=" + str(self.server) + " auth-anonymous=1\n")
        environment = dict(os.environ, HOME=str(self.directory), XDG_RUNTIME_DIR=str(runtime), PULSE_RUNTIME_PATH=str(runtime))
        cookie = self.directory / "cookie"
        cookie.write_bytes(os.urandom(256))
        environment_patch = patch.dict(os.environ, {"PULSE_COOKIE": str(cookie)})
        environment_patch.start()
        self.addCleanup(environment_patch.stop)
        self.process = subprocess.Popen(["pulseaudio", "-n", "-F", str(config), "--daemonize=no", "--exit-idle-time=-1", "--use-pid-file=no", "--disable-shm=yes", "--log-target=stderr"],
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_server)
        deadline = time.monotonic() + 5
        while not self.server.exists() and time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.fail("disposable audio server failed: " + self.process.communicate()[1])
            time.sleep(.01)
        self.assertTrue(self.server.exists(), "private audio socket not ready")

    def stop_server(self):
        if self.process.poll() is None:
            self.process.terminate()
        self.process.communicate(timeout=5)

    def backend(self):
        backend = audio.PulseAudioBackend("unix:" + str(self.server), profile="null",
                                          expected_uid=os.getuid(), expected_pid=self.process.pid)
        self.addCleanup(backend.close)
        return backend

    def test_real_null_sink_load_unload_readback_preserves_foreign_sink(self):
        foreign = self.backend()
        foreign_lease = foreign.acquire("b" * 32)
        backend = self.backend()
        lease = backend.acquire(TOKEN)
        self.assertEqual(lease.name, "module-null-sink")
        self.assertEqual(lease.server_pid, self.process.pid)
        self.assertEqual(lease.server_uid, os.getuid())
        self.assertEqual(lease.socket_ino, self.server.stat().st_ino)
        self.assertFalse(os.get_inheritable(backend._connection._server_pidfd))
        result = backend.release(lease)
        self.assertTrue(result["unload_confirmed"] and result["absence_confirmed"])
        self.assertIn(foreign_lease.index, [row.index for row in foreign._connection.modules()])
        self.assertTrue(foreign.release(foreign_lease)["absence_confirmed"])

    def test_real_server_death_cannot_turn_lost_context_into_release(self):
        backend = self.backend()
        lease = backend.acquire(TOKEN)
        self.process.kill()
        self.process.communicate(timeout=5)
        with self.assertRaises(audio.AudioUnknown):
            backend.release(lease)
        self.assertIsNone(backend._released)

    def test_real_server_pid_registration_is_checked(self):
        with self.assertRaises(audio.AudioError):
            audio.PulseConnection("unix:" + str(self.server), expected_pid=os.getpid())


if __name__ == "__main__":
    unittest.main()
