"""Separately versioned VT participant backend; never activated by import.

Only a trusted coordinator may construct this backend with a manager-opened
tty0 descriptor and a qualified, fixed VT pair. Calls are synchronous worker
calls: the authority must hold their operation permit until they return. In
particular VT_WAITACTIVE may block. No timeout, close or destructor restores an
old foreground console or proves completion of an outstanding kernel call.
"""
from __future__ import annotations

import array
from dataclasses import dataclass, field
import fcntl
import os
import re
import secrets
import stat
import sys
import threading

VT_GETSTATE = 0x5603
VT_ACTIVATE = 0x5606
VT_WAITACTIVE = 0x5607
VERSION = 2
_TOKEN = re.compile(r"[0-9a-f]{32}\Z")


class VtError(ValueError):
    """Rejected request or uncertain backend state; never a release receipt."""


@dataclass(frozen=True)
class OwnedVt:
    """In-process worker result, not a wire credential or authority receipt."""
    token: str
    managed_vt: int
    neutral_vt: int
    backend: str = field(repr=False)


class VtBackend:
    """Fixed-scope console adapter, with no environment or fallback endpoint."""
    disposable = False

    @classmethod
    def adopt(cls, console_fd, *, managed_vt, neutral_vt, ioctl=fcntl.ioctl):
        """Own a CLOEXEC duplicate of an exclusively manager-provided tty0 FD.

        The caller keeps its original descriptor. Validation failures neither
        close nor change it. The trusted launcher supplies the configured VT
        pair; app messages cannot select consoles or paths.
        """
        if (type(console_fd) is not int or console_fd <= 2 or
                type(managed_vt) is not int or type(neutral_vt) is not int or
                not 1 <= managed_vt <= 63 or not 1 <= neutral_vt <= 63 or
                managed_vt == neutral_vt):
            raise VtError("invalid fixed console scope")
        if not callable(ioctl) or (ioctl is fcntl.ioctl and not sys.platform.startswith("linux")):
            raise VtError("VT backend requires Linux console ioctls")
        try:
            info = os.fstat(console_fd)
            if not stat.S_ISCHR(info.st_mode) or info.st_rdev != os.makedev(4, 0):
                raise VtError("manager descriptor is not tty0")
            duplicate = fcntl.fcntl(console_fd, fcntl.F_DUPFD_CLOEXEC, 3)
        except OSError as error:
            raise VtError("cannot validate manager console descriptor") from error
        try:
            return cls(duplicate, managed_vt, neutral_vt, ioctl)
        except BaseException:
            os.close(duplicate)
            raise

    def __init__(self, fd, managed_vt, neutral_vt, ioctl):
        # Use adopt(), never a filename or environment-derived descriptor.
        self._fd, self._managed, self._neutral, self._ioctl = fd, managed_vt, neutral_vt, ioctl
        self._identifier = secrets.token_hex(16)
        self._worker = threading.Lock()
        self._state_lock = threading.Lock()
        self._state = "NEW"
        self._owned = None
        self._error = None

    @property
    def identity(self):
        return {"kind": "vt-v2", "version": VERSION, "backend": self._identifier,
                "managed_vt": self._managed, "neutral_vt": self._neutral}

    def snapshot(self):
        """Status remains callable while the worker is blocked in an ioctl."""
        with self._state_lock:
            return {**self.identity, "state": self._state, "error": self._error,
                    "token": None if self._owned is None else self._owned.token}

    def evidence(self, owned):
        """Fixed worker evidence after positively completed acquire or release."""
        with self._state_lock:
            if owned is not self._owned or self._state not in ("OWNED", "RELEASED"):
                raise VtError("no completed VT operation evidence")
            return {**self.identity, "token": owned.token, "state": self._state}

    def _set(self, state, error=None):
        with self._state_lock:
            self._state, self._error = state, error

    def _active(self):
        state = array.array("H", [0, 0, 0])
        self._ioctl(self._fd, VT_GETSTATE, state, True)
        if not 1 <= state[0] <= 63:
            raise VtError("invalid effective console readback")
        return state[0]

    def _transition(self, expected, target):
        if self._active() != expected:
            raise VtError("effective console changed outside this epoch")
        self._ioctl(self._fd, VT_ACTIVATE, target)
        # VT_ACTIVATE can merely queue a switch. A completed WAITACTIVE and a
        # fresh readback are required; neither has a universal time bound.
        self._ioctl(self._fd, VT_WAITACTIVE, target)
        if self._active() != target:
            raise VtError("console transition was not confirmed")

    def acquire(self, token):
        if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
            raise VtError("invalid authority lease token")
        if not self._worker.acquire(blocking=False):
            raise VtError("console operation is outstanding")
        try:
            if self._state != "NEW":
                raise VtError("console backend is not available for acquisition")
            self._owned = OwnedVt(token, self._managed, self._neutral, self._identifier)
            self._set("ACQUIRING")
            try:
                self._transition(self._neutral, self._managed)
            except BaseException as error:
                self._set("UNKNOWN", "acquire-completion-unknown")
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                raise VtError("VT acquisition requires reconciliation") from error
            self._set("OWNED")
            return self._owned
        finally:
            self._worker.release()

    def release(self, owned, *, display_drained=False):
        """Transition only to the configured neutral console after display drain.

        display_drained is an explicit trusted-coordinator prerequisite, not an
        app assertion. D3 must bind it to its own completed display barriers.
        """
        if display_drained is not True:
            raise VtError("verified display drain is required before VT release")
        if not self._worker.acquire(blocking=False):
            raise VtError("console operation is outstanding")
        try:
            if owned is not self._owned or self._state not in ("OWNED", "RELEASED"):
                raise VtError("unrecognized or non-releasable console lease")
            if self._state == "RELEASED":
                return  # Replay never starts a second old-epoch transition.
            self._set("RELEASING")
            try:
                self._transition(self._managed, self._neutral)
            except BaseException as error:
                self._set("UNKNOWN", "release-completion-unknown")
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                raise VtError("VT release requires reconciliation") from error
            self._set("RELEASED")
        finally:
            self._worker.release()

    def close(self):
        """Close an idle FD without device changes; outstanding calls must drain."""
        if not self._worker.acquire(blocking=False):
            raise VtError("cannot close an outstanding console operation")
        try:
            if self._fd >= 0:
                os.close(self._fd)
                self._fd = -1
            self._set("CLOSED")
        finally:
            self._worker.release()
