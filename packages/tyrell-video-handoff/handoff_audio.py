"""Separately versioned managed audio backend; never imported by legacy Video.

Run blocking methods in a registered participant worker. An accepted libpulse
request is retained on its original connection until its callback completes.
There is intentionally no cancellation, reconnect, scan-cleanup or server reset.
"""
from __future__ import annotations

import ctypes as C
import ctypes.util
from dataclasses import dataclass
import os
import re
import secrets
import select
import socket
import stat
import struct
import sys
import threading

HEX = re.compile(r"[0-9a-f]{32}\Z")
DEVICE = re.compile(r"vc4hdmi[A-Za-z0-9_]*\Z")
INVALID_INDEX = 0xffffffff


class AudioError(ValueError):
    """A definite refusal before ownership or a rejected local argument."""


class AudioUnknown(AudioError):
    """Accepted backend work cannot be positively reconciled by this context."""


@dataclass(frozen=True)
class AudioLease:
    generation: str
    cookie: int
    token: str
    index: int
    name: str
    arguments: str
    server_uid: int
    server_pid: int
    socket_dev: int
    socket_ino: int

    def evidence(self):
        return {"generation": self.generation, "cookie": self.cookie,
                "token": self.token, "index": self.index,
                "name": self.name, "arguments": self.arguments,
                "server_uid": self.server_uid, "server_pid": self.server_pid,
                "socket_dev": self.socket_dev, "socket_ino": self.socket_ino}


@dataclass(frozen=True)
class Module:
    index: int
    name: str
    arguments: str


class _SampleSpec(C.Structure):
    _fields_ = [("format", C.c_int), ("rate", C.c_uint32), ("channels", C.c_uint8)]


class _ServerInfo(C.Structure):
    # Read only the ABI-stable prefix through cookie; newer fields may follow.
    _fields_ = [("user_name", C.c_char_p), ("host_name", C.c_char_p),
                ("server_version", C.c_char_p), ("server_name", C.c_char_p),
                ("sample_spec", _SampleSpec), ("default_sink_name", C.c_char_p),
                ("default_source_name", C.c_char_p), ("cookie", C.c_uint32)]


class _ModuleInfo(C.Structure):
    _fields_ = [("index", C.c_uint32), ("name", C.c_char_p), ("argument", C.c_char_p),
                ("n_used", C.c_uint32), ("proplist", C.c_void_p)]


_SERVER_CB = C.CFUNCTYPE(None, C.c_void_p, C.POINTER(_ServerInfo), C.c_void_p)
_MODULE_CB = C.CFUNCTYPE(None, C.c_void_p, C.POINTER(_ModuleInfo), C.c_int, C.c_void_p)
_INDEX_CB = C.CFUNCTYPE(None, C.c_void_p, C.c_uint32, C.c_void_p)
_SUCCESS_CB = C.CFUNCTYPE(None, C.c_void_p, C.c_int, C.c_void_p)


def _library():
    if not sys.platform.startswith("linux"):
        raise AudioError("managed audio requires Linux libpulse")
    try:
        library = C.CDLL(ctypes.util.find_library("pulse") or "libpulse.so.0")
        signatures = {
            "pa_mainloop_new": (C.c_void_p, []),
            "pa_mainloop_get_api": (C.c_void_p, [C.c_void_p]),
            "pa_mainloop_iterate": (C.c_int, [C.c_void_p, C.c_int, C.POINTER(C.c_int)]),
            "pa_mainloop_free": (None, [C.c_void_p]),
            "pa_context_new": (C.c_void_p, [C.c_void_p, C.c_char_p]),
            "pa_context_connect": (C.c_int, [C.c_void_p, C.c_char_p, C.c_int, C.c_void_p]),
            "pa_context_get_state": (C.c_int, [C.c_void_p]),
            "pa_context_disconnect": (None, [C.c_void_p]),
            "pa_context_unref": (None, [C.c_void_p]),
            "pa_operation_get_state": (C.c_int, [C.c_void_p]),
            "pa_operation_unref": (None, [C.c_void_p]),
            "pa_context_get_server_info": (C.c_void_p, [C.c_void_p, _SERVER_CB, C.c_void_p]),
            "pa_context_get_module_info_list": (C.c_void_p, [C.c_void_p, _MODULE_CB, C.c_void_p]),
            "pa_context_load_module": (C.c_void_p, [C.c_void_p, C.c_char_p, C.c_char_p, _INDEX_CB, C.c_void_p]),
            "pa_context_unload_module": (C.c_void_p, [C.c_void_p, C.c_uint32, _SUCCESS_CB, C.c_void_p]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(library, name)
            function.restype, function.argtypes = result, arguments
        return library
    except (OSError, AttributeError) as error:
        raise AudioError("required libpulse API unavailable") from error


class PulseConnection:
    """One original asynchronous libpulse context, owned by one worker thread.

    The server cookie detects a changed instance on this still-connected context;
    its 32 bits are correlation, not authentication. Trusted user-session endpoint
    qualification is required before deployment. No reconnect exists, so an old
    module index is never used against a replacement server connection.
    The peer-credential probe and libpulse context are separate connections:
    qualified private endpoint ownership and exclusion of conflicting writers
    remain required; this is not isolation against same-user/admin replacement.
    """
    def __init__(self, server, *, library=None, expected_uid=None, expected_pid=None):
        if not isinstance(server, str) or not server.startswith("unix:/") or any(c.isspace() or ord(c) < 33 for c in server):
            raise AudioError("explicit trusted Unix Pulse endpoint required")
        self._lib = library or _library()
        self._owner = (os.getpid(), threading.get_ident())
        self._loop = self._context = None
        self._server_pidfd = -1
        self._server_path = server[5:]
        self._server_identity = None
        self._binding = None
        self._pending = []
        self._poisoned = self._closed = False
        try:
            self._bind_server(os.getuid() if expected_uid is None else expected_uid, expected_pid)
            self._loop = self._lib.pa_mainloop_new()
            if not self._loop:
                raise AudioError("cannot allocate audio mainloop")
            self._context = self._lib.pa_context_new(self._lib.pa_mainloop_get_api(self._loop), b"Tyrell managed audio v2")
            if not self._context:
                raise AudioError("cannot allocate audio context")
            # PA_CONTEXT_NOAUTOSPAWN; never use the default server/environment.
            if self._lib.pa_context_connect(self._context, server.encode("utf-8"), 1, None) < 0:
                raise AudioError("audio connection refused")
            while self._lib.pa_context_get_state(self._context) != 4:
                if self._lib.pa_context_get_state(self._context) in (5, 6):
                    raise AudioError("audio connection unavailable")
                self._iterate()
            self.cookie = self.server_cookie()
        except BaseException:
            self.close()
            raise

    def _bind_server(self, expected_uid, expected_pid):
        if not hasattr(os, "pidfd_open") or not hasattr(socket, "SO_PEERCRED"):
            raise AudioError("managed audio requires Linux server identity binding")
        if type(expected_uid) is not int or expected_uid < 0 or (expected_pid is not None and (type(expected_pid) is not int or expected_pid <= 0)):
            raise AudioError("invalid trusted audio server identity")
        info = os.stat(self._server_path, follow_symlinks=False)
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != expected_uid:
            raise AudioError("audio endpoint is not owned by the registered user")
        self._server_identity = (info.st_dev, info.st_ino, info.st_uid)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            probe.connect(self._server_path)
            pid, uid, _gid = struct.unpack("iII", probe.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if pid <= 0 or uid != expected_uid or (expected_pid is not None and pid != expected_pid):
                raise AudioError("audio server peer does not match trusted registration")
            self._server_pidfd = os.pidfd_open(pid, 0)
            os.set_inheritable(self._server_pidfd, False)
            self._binding = {"server_uid": uid, "server_pid": pid,
                             "socket_dev": info.st_dev, "socket_ino": info.st_ino}
        self._server_alive()

    @property
    def binding(self):
        if self._binding is None:
            raise AudioUnknown("audio server binding unavailable")
        return dict(self._binding)

    def _server_alive(self):
        try:
            info = os.stat(self._server_path, follow_symlinks=False)
            if (info.st_dev, info.st_ino, info.st_uid) != self._server_identity or not stat.S_ISSOCK(info.st_mode):
                raise AudioUnknown("audio endpoint identity changed")
            poller = select.poll()
            poller.register(self._server_pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
            if poller.poll(0):
                raise AudioUnknown("registered audio server exited")
        except OSError as error:
            raise AudioUnknown("audio endpoint identity unavailable") from error

    def _check(self):
        if self._owner != (os.getpid(), threading.get_ident()):
            raise AudioError("audio context belongs to its original worker")
        if self._closed or self._poisoned:
            raise AudioUnknown("audio context unavailable; reconciliation required")
        try:
            self._server_alive()
            if self._lib.pa_context_get_state(self._context) != 4:
                raise AudioUnknown("original audio connection lost")
        except AudioUnknown:
            self._poisoned = True
            raise

    def _iterate(self):
        result = C.c_int()
        if self._lib.pa_mainloop_iterate(self._loop, 1, C.byref(result)) < 0:
            self._poisoned = True
            raise AudioUnknown("audio mainloop failed")

    def _operation(self, start, callback, result):
        self._check()
        operation = start(callback)
        if not operation:
            self._poisoned = True
            raise AudioUnknown("audio request was not positively accepted")
        # Keep Python callback + result storage alive with the operation too.
        held = (operation, callback, result)
        self._pending.append(held)
        try:
            while not result["done"] or self._lib.pa_operation_get_state(operation) == 0:
                self._check()
                if self._lib.pa_operation_get_state(operation) != 0 and not result["done"]:
                    raise AudioUnknown("audio operation ended without its completion callback")
                self._iterate()
            self._check()
            if self._lib.pa_operation_get_state(operation) != 1:
                raise AudioUnknown("audio operation was canceled without backend finality")
            if result.get("invalid"):
                raise AudioUnknown("invalid audio callback data")
        except BaseException:
            self._poisoned = True
            # No unref/cancel/disconnect here: uncertainty retains its context
            # and operation until the supervisor deliberately abandons worker.
            raise
        self._lib.pa_operation_unref(operation)
        self._pending.remove(held)
        return result

    def server_cookie(self):
        result = {"done": False}
        @_SERVER_CB
        def callback(_context, info, _userdata):
            if not info:
                result["invalid"] = True
            else:
                result["cookie"] = int(info.contents.cookie)
            result["done"] = True
        return self._operation(lambda cb: self._lib.pa_context_get_server_info(self._context, cb, None), callback, result)["cookie"]

    def modules(self):
        result = {"done": False, "modules": []}
        @_MODULE_CB
        def callback(_context, info, end, _userdata):
            if end:
                result["done"] = True
                result["invalid"] = result.get("invalid", False) or end < 0
            elif info:
                try:
                    row = info.contents
                    name = (row.name or b"").decode("utf-8", "strict")
                    arguments = (row.argument or b"").decode("utf-8", "strict")
                    if not name or len(name) > 256 or len(arguments) > 16384 or len(result["modules"]) >= 4096:
                        raise ValueError("unbounded module result")
                    result["modules"].append(Module(int(row.index), name, arguments))
                except (UnicodeError, ValueError):
                    result["invalid"] = True
            else:
                result["invalid"] = True
        return self._operation(lambda cb: self._lib.pa_context_get_module_info_list(self._context, cb, None), callback, result)["modules"]

    def load(self, name, arguments):
        result = {"done": False}
        @_INDEX_CB
        def callback(_context, index, _userdata):
            result["index"], result["done"] = int(index), True
        return self._operation(lambda cb: self._lib.pa_context_load_module(self._context, name.encode(), arguments.encode(), cb, None), callback, result)["index"]

    def unload(self, index):
        result = {"done": False}
        @_SUCCESS_CB
        def callback(_context, success, _userdata):
            result["success"], result["done"] = success > 0, True
        return self._operation(lambda cb: self._lib.pa_context_unload_module(self._context, index, cb, None), callback, result)["success"]

    def close(self):
        if self._closed:
            return
        if self._owner != (os.getpid(), threading.get_ident()):
            raise AudioError("audio context belongs to its original worker")
        self._closed = True
        # Abandoning a lost worker never manufactures a release receipt. Its
        # durable operation must remain UNKNOWN in the authority.
        if self._context:
            self._lib.pa_context_disconnect(self._context)
        for operation, _callback, _result in self._pending:
            self._lib.pa_operation_unref(operation)
        self._pending.clear()
        if self._context:
            self._lib.pa_context_unref(self._context)
            self._context = None
        if self._loop:
            self._lib.pa_mainloop_free(self._loop)
            self._loop = None
        if self._server_pidfd >= 0:
            os.close(self._server_pidfd)
            self._server_pidfd = -1


class PulseAudioBackend:
    """Single-lease audio participant backend with fixed production module scope.

    Configuration is trusted coordinator policy, never packet fields. The null
    profile is explicit disposable-test policy, not a fallback when HDMI fails.
    Context calls are serialized in a registered worker; queued cancellation is
    handled by completing acquire and then release on this same connection.
    """
    def __init__(self, server, *, device=None, profile="hdmi", expected_uid=None,
                 expected_pid=None, connection_factory=PulseConnection):
        if profile not in ("hdmi", "null") or (profile == "hdmi" and (not isinstance(device, str) or not DEVICE.fullmatch(device))):
            raise AudioError("fixed VC4 HDMI profile required")
        if profile == "null" and device is not None:
            raise AudioError("null test profile does not accept device arguments")
        if expected_uid is not None or expected_pid is not None:
            self._connection = connection_factory(server, expected_uid=expected_uid, expected_pid=expected_pid)
        else:
            self._connection = connection_factory(server)
        self._owner = (os.getpid(), threading.get_ident())
        self._generation = secrets.token_hex(16)
        self._cookie = self._connection.cookie
        self._device, self._profile = device, profile
        self.profile, self.disposable = profile, profile == "null"
        self._lease = None
        self._released = None
        self._prepared = None
        self._poisoned = self._closed = False

    def _check(self):
        if self._owner != (os.getpid(), threading.get_ident()):
            raise AudioError("audio backend belongs to its original worker")
        if self._closed or self._poisoned:
            raise AudioUnknown("audio backend requires controlled reconciliation")
        try:
            if self._connection.server_cookie() != self._cookie:
                raise AudioUnknown("audio server incarnation changed")
        except AudioUnknown:
            self._poisoned = True
            raise

    def _arguments(self, token):
        sink = "tyrell_v2_" + token
        if self._profile == "null":
            return "module-null-sink", "sink_name=" + sink + " rate=48000 channels=2"
        return "module-alsa-sink", ("device=hdmi:CARD=" + self._device + ",DEV=0 sink_name=" + sink +
            " namereg_fail=true channels=2 channel_map=front-left,front-right rate=48000 format=s32le mmap=false"
            " sink_properties=\"device.description='Tyrell HDMI' priority.session=-1000 priority.driver=-1000 node.autoconnect=false\"")

    def prepared(self, token):
        """Bind immutable load intent for the coordinator to journal first.

        This reads server identity but does not submit a module-load request.
        A worker cannot switch token/profile after advertising this intent.
        """
        if not isinstance(token, str) or not HEX.fullmatch(token):
            raise AudioError("invalid managed audio lease token")
        self._check()
        name, arguments = self._arguments(token)
        result = {"generation": self._generation, "cookie": self._cookie,
                  "token": token, "name": name, "arguments": arguments,
                  **self._connection.binding}
        if self._prepared is not None and self._prepared != result:
            raise AudioError("prepared audio load identity cannot change")
        self._prepared = result
        return dict(result)

    def acquire(self, token):
        prepared = self.prepared(token)
        if self._lease is not None or self._released is not None:
            raise AudioError("audio backend is single-lease; do not repeat acquisition")
        name, arguments = prepared["name"], prepared["arguments"]
        try:
            before = self._connection.modules()
            # Refuse accidental token reuse before sending any load request.
            if any("tyrell_v2_" + token in row.arguments for row in before):
                raise AudioError("managed sink token already exists")
            index = self._connection.load(name, arguments)
            if index == INVALID_INDEX:
                raise AudioError("audio server rejected module creation")
            if any(row.index == index for row in before):
                raise AudioUnknown("load result reused a pre-existing module identity")
            self._check()
            matches = [row for row in self._connection.modules() if row.index == index]
            expected = Module(index, name, arguments)
            if matches != [expected]:
                raise AudioUnknown("created module identity cannot be confirmed")
            self._lease = AudioLease(self._generation, self._cookie, token, index, name,
                                     arguments, **self._connection.binding)
            return self._lease
        except AudioUnknown:
            self._poisoned = True
            raise

    def release(self, lease):
        if not isinstance(lease, AudioLease) or lease != self._lease:
            raise AudioError("foreign or unconfirmed audio ownership")
        self._check()
        if self._released is not None:
            return dict(self._released)
        try:
            rows = [row for row in self._connection.modules() if row.index == lease.index]
            if rows != [Module(lease.index, lease.name, lease.arguments)]:
                raise AudioUnknown("owned audio module changed; refusing unload")
            if not self._connection.unload(lease.index):
                raise AudioUnknown("audio unload callback did not confirm success")
            self._check()
            if any(row.index == lease.index or ("tyrell_v2_" + lease.token) in row.arguments
                   for row in self._connection.modules()):
                raise AudioUnknown("audio absence readback did not confirm release")
            self._released = {**lease.evidence(), "unload_confirmed": True, "absence_confirmed": True}
            return dict(self._released)
        except AudioUnknown:
            self._poisoned = True
            raise

    def close(self):
        if self._closed:
            return
        if self._owner != (os.getpid(), threading.get_ident()):
            raise AudioError("audio backend belongs to its original worker")
        self._closed = True
        self._connection.close()
