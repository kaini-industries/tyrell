"""Trusted draft-2 shell controller for explicit non-device integration profiles.

The shell owns transition intent, never backend finality. App messages still
pass through Session's role-restricted dispatcher and Linux PID/UID binding.
These APIs are coordinator calls, not a wire command server or live launcher.
"""
from __future__ import annotations

import os

from handoff_authority import AuthorityError, require
from handoff_protocol import Message
from handoff_session import Session


class ShellSession:
    """One registered app and serial asynchronous shell transitions.

    The caller provides an exclusively owned inherited descriptor and trusted
    registration. No environment variable, executable name or app packet can
    select a profile or gain a helper capability. The coordinator must schedule
    pump outside the UI owner: SQLite durability may block even though channel
    work is bounded and no graph/device call is performed here.
    """
    PROFILES = frozenset(("inert", "disposable-v2"))
    STOP_REASONS = frozenset(("shell-exit", "sigterm", "sigint", "peer-loss"))
    live_hardware_admission = False

    def __init__(self, authority, fd, *, profile, app_instance,
                 expected_uid, expected_pid, **session_options):
        # All profile checks precede descriptor adoption and app registration.
        require(type(profile) is str and profile in self.PROFILES and authority.live_hardware_admission is False,
                "no qualified live shell profile exists")
        require(authority.snapshot()["mode"] == profile, "shell/authority profile mismatch")
        require(type(expected_uid) is int and expected_uid == os.getuid(),
                "shell and app must share the trusted normal-user session")
        require(profile != "disposable-v2" or expected_uid > 0,
                "disposable shell integration requires a normal user")
        self.authority, self.profile = authority, profile
        self.session = Session(authority, fd, app_instance=app_instance,
                               expected_uid=expected_uid, expected_pid=expected_pid,
                               **session_options)
        self.lease = None
        self.commit_queued = False
        self._failure_sent = None
        self._stop_reason = None

    @property
    def app(self):
        return self.session.app

    @property
    def closed(self):
        return self.session.closed

    @property
    def negotiated(self):
        return self.session.negotiated

    @property
    def last_error(self):
        return self._stop_reason or self.session.last_error

    def stop(self, reason="shell-exit"):
        """Revoke new work; never signal, freeze, kill or report completion.

        A surrounding process may call this for SIGTERM/SIGINT. Disconnect is
        journaled before channels close; storage failure propagates and the
        Session still closes its owned descriptors without inventing success.
        Independent participant supervisors retain their existing obligations.
        """
        require(reason in self.STOP_REASONS, "unknown fixed shell stop reason")
        if self._stop_reason is None:
            self._stop_reason = reason
        self.session.close()

    close = stop

    def _deliver(self, packet, reason):
        if not self.session.channel.send(packet):
            self._stop_reason = reason
            self.session.close()
            raise AuthorityError("shell control delivery is unknown")
        return packet

    def pump(self):
        """Run Session's bounded credential-checked pump and terminal notices.

        Expiry records failure and leaves the operation barrier quarantined.
        Sending the fixed failure packet does not stop a worker or acknowledge
        display/audio cutoff. A terminal notice is queued at most once.
        """
        count = self.session.pump()
        if self.closed or self.lease is None:
            return count
        lease = self.authority.snapshot()["leases"][self.lease]
        request = lease["quiesce"]
        if request is not None and request["status"] in ("expired", "failed", "unknown"):
            key = (self.lease, request["request"], request["status"])
            if self._failure_sent != key:
                code = {"expired": "expired", "failed": "backend-failed",
                        "unknown": "completion-unknown"}[request["status"]]
                self._deliver(Message("failed", (lease["incarnation"], lease["app"],
                    str(lease["epoch"]), str(request["request"]), code)), "failure-output-unavailable")
                self._failure_sent = key
        return count

    def _active(self):
        self.pump()
        require(not self.closed and self.negotiated, "active negotiated shell bootstrap required")

    def grant(self):
        """Journal a fresh fixed-profile grant before any backend dispatch.

        A prior lease must have a positive composite receipt and its commit
        queued in order. Wait until that queue drains before a successor grant;
        queue acceptance alone does not establish peer delivery or hidden UI.
        The existing authority independently refuses every outstanding barrier.
        """
        self._active()
        require(self.lease is None or self.commit_queued, "commit prior composite release first")
        require(self.session.channel.pending == 0, "prior shell packets remain queued")
        grant = (self.authority.grant_inert(self.app) if self.profile == "inert"
                 else self.authority.grant_disposable(self.app))
        self.lease, self.commit_queued, self._failure_sent = grant.fields[4], False, None
        return self._deliver(grant, "grant-output-unavailable")

    def quiesce(self, *, budget_ms=10000):
        self._active()
        require(self.lease is not None and not self.commit_queued, "no foreground shell lease")
        return self.session.quiesce(self.lease, budget_ms=budget_ms)

    def cancel(self):
        self._active()
        require(self.lease is not None and not self.commit_queued, "no draining shell lease")
        return self.session.cancel(self.lease)

    def release_receipt(self):
        """Read the authority's durable composite result, never app assertion."""
        self._active()
        require(self.lease is not None, "no shell lease")
        return self.authority.release_receipt(self.app, self.lease)

    def commit_hidden(self, receipt):
        self._active()
        require(self.lease is not None, "no shell lease")
        # Session validates this exact receipt through Authority before send.
        packet = self.session.commit_hidden(self.lease, receipt)
        self.commit_queued = True
        return packet

    def status(self):
        """Bounded diagnostic facts, with no invented hidden/privacy result."""
        if not self.closed:
            self.pump()
        state = self.authority.snapshot()
        lease = None if self.lease is None else state["leases"][self.lease]
        request = None if lease is None else lease["quiesce"]
        operations = () if lease is None else lease["operations"].values()
        return {"profile": self.profile, "live_hardware_admission": False,
                "connected": not self.closed, "negotiated": self.negotiated,
                "lease": self.lease, "epoch": None if lease is None else lease["epoch"],
                "state": None if lease is None else lease["state"],
                "request": None if request is None else request["request"],
                "request_status": None if request is None else request["status"],
                "checkpoint": None if lease is None else lease["checkpoint"],
                "cancelled": False if lease is None else lease["cancelled"],
                "pending_operations": sum(op["state"] != "DONE" for op in operations),
                "unknown_operations": sum(op["state"] == "UNKNOWN" for op in operations),
                "pending_actors": 0 if lease is None else sum(not actor["released"] for actor in lease["actors"].values()),
                "receipt_available": request is not None and request["status"] == "released",
                "commit_queued": self.commit_queued, "pending_packets": self.session.channel.pending,
                "last_error": self.last_error}
