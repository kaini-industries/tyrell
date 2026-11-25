"""Durable draft-2 authority for inert and explicitly disposable trials only.

Bootstrap and participant methods are trusted coordinator APIs, not peer verbs.
The app channel can report only its own local completion. No device, process
signal, service manager, audio server or existing Video helper is accessed.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import re
import secrets
import threading
import time

from handoff_ledger import Ledger
from handoff_protocol import Message, CAPABILITIES, encode_packet

HEX = re.compile(r"[0-9a-f]{32}\Z")
U64 = 2**64 - 1
MAX_APPS, MAX_LEASES, MAX_ACTORS, MAX_OPERATIONS = 8, 64, 16, 128
STATES = {"OWNED", "DRAINING", "RELEASED", "QUARANTINED"}
OP_STATES = {"PENDING", "DONE", "FAILED", "UNKNOWN"}


class AuthorityError(ValueError):
    pass


def require(value, message):
    if not value:
        raise AuthorityError(message)


def identifier(value):
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def integer(value, minimum=1, maximum=U64):
    return type(value) is int and minimum <= value <= maximum


def fresh_id(used):
    for _ in range(16):
        value = secrets.token_hex(16)
        if identifier(value) and value not in used:
            return value
    raise AuthorityError("unique identifier allocation failed")


@dataclass(frozen=True)
class App:
    instance: str
    uid: int
    pid: int
    incarnation: str
    _token: str = field(repr=False)


@dataclass(frozen=True)
class Actor:
    lease: str
    identifier: str
    incarnation: str
    _token: str = field(repr=False)


def validate_state(state):
    """Reject corrupt/unsupported snapshots rather than inventing empty state."""
    try:
        require(isinstance(state, dict) and set(state) == {
            "schema", "kind", "mode", "incarnation", "next_epoch", "next_request", "apps", "leases", "active_lease", "participants"},
            "invalid authority state fields")
        require(type(state["schema"]) is int and state["schema"] == 2 and
                state["kind"] == "tyrell-handoff-authority" and state["mode"] in ("inert", "disposable-v2"), "unsupported authority state")
        require(identifier(state["incarnation"]), "invalid authority incarnation")
        require(integer(state["next_epoch"]) and integer(state["next_request"]), "exhausted or invalid authority counters")
        require(isinstance(state["apps"], dict) and len(state["apps"]) <= MAX_APPS, "invalid app registry")
        for name, app in state["apps"].items():
            require(identifier(name) and isinstance(app, dict) and set(app) == {"uid", "pid", "negotiated", "connected"}, "invalid app")
            require(integer(app["uid"], 0, 2**32 - 1) and integer(app["pid"], 1, 2**31 - 1) and
                    type(app["negotiated"]) is bool and type(app["connected"]) is bool, "invalid app identity")
        require(isinstance(state["leases"], dict) and len(state["leases"]) <= MAX_LEASES, "invalid lease registry")
        for name, lease in state["leases"].items():
            require(identifier(name) and isinstance(lease, dict) and set(lease) == {
                "incarnation", "app", "epoch", "grant_request", "state", "actors", "operations",
                "local_done", "checkpoint", "quiesce", "cancelled"}, "invalid lease")
            require(identifier(lease["incarnation"]) and identifier(lease["app"]) and integer(lease["epoch"]) and
                    integer(lease["grant_request"]) and lease["state"] in STATES, "invalid lease identity/state")
            require(type(lease["local_done"]) is bool and type(lease["cancelled"]) is bool and
                    (lease["checkpoint"] is None or lease["checkpoint"] == "none" or identifier(lease["checkpoint"])), "invalid local completion")
            require(isinstance(lease["actors"], dict) and 1 <= len(lease["actors"]) <= MAX_ACTORS, "invalid actors")
            require(lease["app"] in lease["actors"] and lease["actors"][lease["app"]]["kind"] == "app", "missing app actor")
            for actor, record in lease["actors"].items():
                require(identifier(actor) and isinstance(record, dict) and set(record) == {"kind", "released"} and
                        record["kind"] in ("app", "inert") and type(record["released"]) is bool, "invalid actor")
            require(isinstance(lease["operations"], dict) and len(lease["operations"]) <= MAX_OPERATIONS, "invalid operation registry")
            for operation, record in lease["operations"].items():
                require(identifier(operation) and isinstance(record, dict) and set(record) == {"actor", "kind", "state"} and
                        record["actor"] in lease["actors"] and record["kind"] in ("acquire", "use", "cleanup") and
                        record["state"] in OP_STATES, "invalid operation")
            request = lease["quiesce"]
            if request is not None:
                require(isinstance(request, dict) and set(request) == {"request", "deadline_ns", "status", "receipt"}, "invalid quiesce request")
                require(integer(request["request"]) and integer(request["deadline_ns"], 0) and
                        request["status"] in ("pending", "released", "expired", "failed", "unknown") and
                        (request["receipt"] is None or identifier(request["receipt"])), "invalid request state")
            if lease["state"] == "RELEASED":
                require(request is not None and request["status"] == "released" and identifier(request["receipt"]) and
                        lease["local_done"] and all(a["released"] for a in lease["actors"].values()) and
                        all(op["state"] == "DONE" for op in lease["operations"].values()), "release has an outstanding barrier")
            if request is not None and request["status"] == "released":
                require(lease["state"] == "RELEASED", "receipt without terminal release")
        active = state["active_lease"]
        require(active is None or (active in state["leases"] and state["leases"][active]["state"] != "RELEASED"), "invalid active lease")
        require(sum(lease["state"] != "RELEASED" for lease in state["leases"].values()) <= 1, "overlapping composite owners")
        require(all(name == active or lease["state"] == "RELEASED" for name, lease in state["leases"].items()), "untracked outstanding lease")
        from handoff_participant_state import validate_participants
        validate_participants(state)
    except (KeyError, TypeError) as error:
        raise AuthorityError("malformed authority state") from error
    return state


class Authority:
    """Serialized durable decisions; no live hardware profile is admitted."""
    live_hardware_admission = False

    def __init__(self, ledger, clock=time.monotonic_ns):
        self.ledger, self.clock = ledger, clock
        self._lock = threading.RLock()
        self._apps, self._actors = {}, {}
        self._last_time = -1
        self._state = validate_state(ledger.read())
        self._revision = ledger.revision
        self._poisoned = False

    @classmethod
    def create(cls, directory, *, clock=time.monotonic_ns, disposable=False):
        require(type(disposable) is bool, "explicit disposable profile required")
        initial = {"schema": 2, "kind": "tyrell-handoff-authority", "mode": "disposable-v2" if disposable else "inert",
                   "incarnation": fresh_id(set()), "next_epoch": 1, "next_request": 1,
                   "apps": {}, "leases": {}, "active_lease": None, "participants": {}}
        return cls(Ledger.create(directory, initial), clock)

    @classmethod
    def open(cls, directory, *, clock=time.monotonic_ns):
        ledger = Ledger.open(directory)
        try:
            authority = cls(ledger, clock)
            state = copy.deepcopy(authority._state)
            previous = {state["incarnation"], *(lease["incarnation"] for lease in state["leases"].values())}
            state["incarnation"], state["apps"] = fresh_id(previous), {}
            for participant in state["participants"].values():
                if participant["phase"] != "fenced":
                    participant["phase"] = "unknown"
            for lease in state["leases"].values():
                if lease["state"] == "RELEASED":
                    continue
                lease["state"] = "QUARANTINED"
                for operation in lease["operations"].values():
                    if operation["state"] == "PENDING":
                        operation["state"] = "UNKNOWN"
                if lease["quiesce"] is not None and lease["quiesce"]["status"] == "pending":
                    lease["quiesce"]["status"] = "unknown"
            authority._commit(state, "restart-unknown-barriers")
            return authority
        except BaseException:
            ledger.close()
            raise

    def close(self):
        with self._lock:
            self._poisoned = True
            self.ledger.close()

    def _check(self):
        require(not self._poisoned, "authority is closed or requires recovery")
        try:
            require(self.ledger.read() == self._state and self.ledger.revision == self._revision,
                    "durable authority state changed")
        except BaseException:
            self._poisoned = True
            raise

    def _commit(self, state, event, **details):
        self._check()
        validate_state(state)
        try:
            revision = self.ledger.commit(state, {"type": event, **details}, expected_revision=self._revision)
        except BaseException:
            self._poisoned = True
            raise
        self._state, self._revision = state, revision

    def _now(self):
        now = self.clock()
        require(integer(now, 0) and now >= self._last_time, "invalid or backwards authority clock")
        self._last_time = now
        return now

    def _expire(self):
        self._check()
        now = self._now()
        active = self._state["active_lease"]
        if active is not None:
            lease = self._state["leases"][active]
            request = lease["quiesce"]
            if request is not None and request["status"] == "pending" and now >= request["deadline_ns"]:
                state = copy.deepcopy(self._state)
                state["leases"][active]["state"] = "QUARANTINED"
                state["leases"][active]["quiesce"]["status"] = "expired"
                self._commit(state, "request-expired", lease=active)
        return now

    def tick(self):
        with self._lock:
            self._expire()

    def snapshot(self):
        with self._lock:
            self._check()
            return copy.deepcopy(self._state)

    @staticmethod
    def _allocate(state, name):
        value = state[name]
        require(value < U64, "authority counter exhausted; no wrapping")
        state[name] += 1
        return value

    def _app(self, app):
        self._check()
        require(isinstance(app, App) and app.incarnation == self._state["incarnation"] and
                self._apps.get(app._token) == app, "unregistered app capability")
        record = self._state["apps"].get(app.instance)
        require(record is not None and record["connected"], "app connection is not active")
        return record

    def bootstrap_app(self, instance, *, uid, pid):
        """Trusted launcher API. The wire cannot register or choose peer identity."""
        with self._lock:
            self._expire()
            require(identifier(instance) and integer(uid, 0, 2**32 - 1) and integer(pid, 1, 2**31 - 1), "invalid bootstrap identity")
            require(instance not in self._state["apps"] and len(self._state["apps"]) < MAX_APPS, "app registry is duplicate or full")
            state = copy.deepcopy(self._state)
            state["apps"][instance] = {"uid": uid, "pid": pid, "connected": True, "negotiated": False}
            app = App(instance, uid, pid, state["incarnation"], fresh_id(self._apps))
            self._commit(state, "app-bootstrap", app=instance)
            self._apps[app._token] = app
            return app

    def negotiate(self, app, message):
        with self._lock:
            self._expire()
            record = self._app(app)
            require(message == Message("hello", (app.instance, CAPABILITIES)), "incorrect app hello")
            if not record["negotiated"]:
                state = copy.deepcopy(self._state)
                state["apps"][app.instance]["negotiated"] = True
                self._commit(state, "protocol-negotiated", app=app.instance)
            return Message("welcome", (app.incarnation, app.instance, CAPABILITIES))

    def grant_inert(self, app):
        """Issue a control-plane test lease; this cannot authorize hardware."""
        return self._grant(app, "inert")

    def grant_disposable(self, app):
        """Explicit D2 disposable backend profile, never live hardware."""
        return self._grant(app, "disposable-v2")

    def _grant(self, app, mode):
        with self._lock:
            self._expire()
            require(self._state["mode"] == mode, "grant/profile mismatch")
            record = self._app(app)
            require(record["negotiated"], "negotiate before any grant")
            require(self._state["active_lease"] is None, "ownership barrier is not empty")
            require(len(self._state["leases"]) < MAX_LEASES, "lease history limit reached")
            state = copy.deepcopy(self._state)
            epoch = self._allocate(state, "next_epoch")
            request = self._allocate(state, "next_request")
            lease_id = fresh_id(state["leases"])
            state["leases"][lease_id] = {"incarnation": app.incarnation, "app": app.instance,
                "epoch": epoch, "grant_request": request, "state": "OWNED",
                "actors": {app.instance: {"kind": "app", "released": False}}, "operations": {},
                "local_done": False, "checkpoint": None, "quiesce": None, "cancelled": False}
            state["active_lease"] = lease_id
            self._commit(state, "inert-grant" if mode == "inert" else "disposable-v2-grant", lease=lease_id)
            return Message("grant", (app.incarnation, app.instance, str(epoch), str(request), lease_id))

    def _owned(self, app, lease_id):
        self._app(app)
        lease = self._state["leases"].get(lease_id)
        require(lease is not None and lease["incarnation"] == app.incarnation and lease["app"] == app.instance, "stale or foreign lease")
        return lease

    def app_actor(self, app, lease_id):
        with self._lock:
            self._expire()
            lease = self._owned(app, lease_id)
            require(lease["state"] == "OWNED", "actor registry is sealed")
            key = (lease_id, app.instance)
            for actor in self._actors.values():
                if (actor.lease, actor.identifier) == key:
                    return actor
            actor = Actor(lease_id, app.instance, app.incarnation, fresh_id(self._actors))
            self._actors[actor._token] = actor
            return actor

    def register_inert_actor(self, app, lease_id):
        """Trusted adapter bootstrap; no peer verb can create a helper actor."""
        with self._lock:
            self._expire()
            lease = self._owned(app, lease_id)
            require(lease["state"] == "OWNED" and len(lease["actors"]) < MAX_ACTORS, "actor registry is sealed or full")
            state = copy.deepcopy(self._state)
            actor = Actor(lease_id, fresh_id(lease["actors"]), app.incarnation, fresh_id(self._actors))
            state["leases"][lease_id]["actors"][actor.identifier] = {"kind": "inert", "released": False}
            self._commit(state, "inert-actor-registered", lease=lease_id, actor=actor.identifier)
            self._actors[actor._token] = actor
            return actor

    def _actor(self, actor):
        self._check()
        require(isinstance(actor, Actor) and actor.incarnation == self._state["incarnation"] and
                self._actors.get(actor._token) == actor, "unregistered actor capability")
        lease = self._state["leases"].get(actor.lease)
        require(lease is not None and actor.identifier in lease["actors"], "unknown actor lease")
        return lease

    def begin_operation(self, actor, kind):
        with self._lock:
            self._expire()
            lease = self._actor(actor)
            require(kind in ("acquire", "use", "cleanup"), "unknown fixed operation kind")
            require(not lease["actors"][actor.identifier]["released"], "actor is already terminal")
            require(lease["state"] == "OWNED" or (lease["state"] == "DRAINING" and kind == "cleanup"), "new operation is not authorized")
            require(len(lease["operations"]) < MAX_OPERATIONS, "operation registry limit reached")
            operation = fresh_id(lease["operations"])
            state = copy.deepcopy(self._state)
            state["leases"][actor.lease]["operations"][operation] = {"actor": actor.identifier, "kind": kind, "state": "PENDING"}
            self._commit(state, "operation-permit", lease=actor.lease, operation=operation)
            return operation

    def complete_operation(self, actor, operation, *, success=True):
        with self._lock:
            self._expire()
            lease = self._actor(actor)
            record = lease["operations"].get(operation)
            require(record is not None and record["actor"] == actor.identifier and type(success) is bool, "foreign operation or invalid completion")
            terminal = "DONE" if success else "FAILED"
            if record["state"] == terminal:
                return
            require(record["state"] in ("PENDING", "UNKNOWN"), "conflicting terminal operation result")
            state = copy.deepcopy(self._state)
            state["leases"][actor.lease]["operations"][operation]["state"] = terminal
            if not success:
                self._quarantine(state["leases"][actor.lease], "failed")
            self._release_if_complete(state, actor.lease)
            self._commit(state, "operation-terminal", lease=actor.lease, operation=operation, result=terminal)

    def actor_released(self, actor):
        with self._lock:
            self._expire()
            lease = self._actor(actor)
            require(lease["actors"][actor.identifier]["kind"] == "inert", "app completion must use local-done")
            require(lease["state"] in ("DRAINING", "QUARANTINED", "RELEASED"), "seal before actor release")
            require(all(op["state"] == "DONE" for op in lease["operations"].values() if op["actor"] == actor.identifier), "actor has unresolved work")
            if lease["actors"][actor.identifier]["released"]:
                return
            state = copy.deepcopy(self._state)
            state["leases"][actor.lease]["actors"][actor.identifier]["released"] = True
            self._release_if_complete(state, actor.lease)
            self._commit(state, "inert-actor-released", lease=actor.lease, actor=actor.identifier)

    def actor_lost(self, actor):
        """Trusted supervisor reports loss, never inferred successful completion."""
        with self._lock:
            self._expire()
            lease = self._actor(actor)
            if lease["state"] == "RELEASED" or lease["actors"][actor.identifier]["released"]:
                return
            state = copy.deepcopy(self._state)
            target = state["leases"][actor.lease]
            self._quarantine(target, "failed")
            for operation in target["operations"].values():
                if operation["actor"] == actor.identifier and operation["state"] == "PENDING":
                    operation["state"] = "UNKNOWN"
            if state != self._state:
                self._commit(state, "actor-lost-unknown", lease=actor.lease, actor=actor.identifier)

    def quiesce(self, app, lease_id, *, budget_ms=10000):
        with self._lock:
            now = self._expire()
            lease = self._owned(app, lease_id)
            require(integer(budget_ms, 1, 30000), "invalid handoff budget")
            if lease["quiesce"] is not None:
                return self._quiesce_message(lease)
            require(lease["state"] == "OWNED", "lease cannot be quiesced")
            state = copy.deepcopy(self._state)
            target = state["leases"][lease_id]
            target["state"] = "DRAINING"  # Registry sealing and admission close are one durable transaction.
            target["quiesce"] = {"request": self._allocate(state, "next_request"),
                                  "deadline_ns": now + budget_ms * 1000000, "status": "pending", "receipt": None}
            self._commit(state, "epoch-sealed", lease=lease_id)
            return self._quiesce_message(target)

    @staticmethod
    def _tuple(lease):
        return (lease["incarnation"], lease["app"], str(lease["epoch"]), str(lease["quiesce"]["request"]))

    def _quiesce_message(self, lease):
        request = lease["quiesce"]
        require(request["status"] == "pending", "request is already terminal")
        remaining = max(1, min(30000, (request["deadline_ns"] - self._last_time + 999999) // 1000000))
        return Message("quiesce", (*self._tuple(lease), str(remaining)))

    @staticmethod
    def _quarantine(lease, status):
        if lease["state"] != "RELEASED":
            lease["state"] = "QUARANTINED"
            if lease["quiesce"] is not None and lease["quiesce"]["status"] == "pending":
                lease["quiesce"]["status"] = status

    @staticmethod
    def _release_if_complete(state, lease_id):
        lease = state["leases"][lease_id]
        if (lease["state"] == "DRAINING" and lease["quiesce"]["status"] == "pending" and lease["local_done"] and
                all(actor["released"] for actor in lease["actors"].values()) and
                all(op["state"] == "DONE" for op in lease["operations"].values())):
            receipts = {other["quiesce"]["receipt"] for other in state["leases"].values()
                        if other["quiesce"] is not None and other["quiesce"]["receipt"] is not None}
            lease["quiesce"]["receipt"] = fresh_id(receipts)
            lease["quiesce"]["status"], lease["state"] = "released", "RELEASED"
            state["active_lease"] = None

    def _binding(self, app, fields):
        self._app(app)
        for lease_id, lease in self._state["leases"].items():
            if lease["incarnation"] == app.incarnation and lease["app"] == app.instance and str(lease["epoch"]) == fields[2]:
                require(lease["quiesce"] is not None and tuple(fields[:4]) == self._tuple(lease), "stale transaction binding")
                return lease_id, lease
        raise AuthorityError("stale or foreign ownership epoch")

    def local_done(self, app, message):
        with self._lock:
            self._expire()
            encode_packet(message)
            require(message.verb == "local-done", "wrong local completion verb")
            lease_id, lease = self._binding(app, message.fields)
            require(lease["state"] in ("DRAINING", "QUARANTINED", "RELEASED"), "request has not been sealed")
            if lease["local_done"]:
                require(lease["checkpoint"] == message.fields[4], "conflicting duplicate checkpoint")
                return
            require(all(op["state"] == "DONE" for op in lease["operations"].values() if op["actor"] == app.instance), "app still has outstanding work")
            state = copy.deepcopy(self._state)
            target = state["leases"][lease_id]
            target["local_done"], target["checkpoint"] = True, message.fields[4]
            target["actors"][app.instance]["released"] = True
            self._release_if_complete(state, lease_id)
            self._commit(state, "app-local-done", lease=lease_id)

    def cancel(self, app, lease_id):
        with self._lock:
            self._expire()
            lease = self._owned(app, lease_id)
            require(lease["quiesce"] is not None, "no transaction to cancel")
            if lease["quiesce"]["status"] == "pending" and not lease["cancelled"]:
                state = copy.deepcopy(self._state)
                state["leases"][lease_id]["cancelled"] = True
                self._commit(state, "cancel-intent-drain-retained", lease=lease_id)
            return Message("cancel", self._tuple(lease))

    def release_receipt(self, app, lease_id):
        """Trusted shell lookup; no app packet can mint this result."""
        with self._lock:
            self._expire()
            lease = self._owned(app, lease_id)
            require(lease["state"] == "RELEASED", "composite release is not complete")
            return Message("released", (*self._tuple(lease), lease["quiesce"]["receipt"]))

    def commit_hidden(self, app, lease_id, receipt):
        with self._lock:
            released = self.release_receipt(app, lease_id)
            require(receipt == released.fields[4], "unknown release receipt")
            return Message("commit", released.fields)

    def disconnect(self, app):
        with self._lock:
            self._check()
            if self._apps.get(getattr(app, "_token", None)) != app:
                return
            record = self._state["apps"].get(app.instance)
            if record is None or not record["connected"]:
                return
            state = copy.deepcopy(self._state)
            state["apps"][app.instance]["connected"] = False
            for lease in state["leases"].values():
                if lease["app"] == app.instance and lease["incarnation"] == app.incarnation:
                    self._quarantine(lease, "failed")
                    for operation in lease["operations"].values():
                        if operation["actor"] == app.instance and operation["state"] == "PENDING":
                            operation["state"] = "UNKNOWN"
            self._commit(state, "app-disconnected-unknown", app=app.instance)

    def receive_app(self, app, message):
        """Role-restricted dispatcher. Completion of helpers is never a peer verb."""
        encode_packet(message)
        with self._lock:
            self._expire()
            self._app(app)
            if message.verb == "hello":
                return [self.negotiate(app, message)]
            require(self._state["apps"][app.instance]["negotiated"], "hello required first")
            if message.verb == "local-done":
                self.local_done(app, message)
                return []
            if message.verb == "ready":
                lease = self._owned(app, message.fields[4])
                require(lease["state"] == "OWNED" and tuple(message.fields[:4]) == (
                    app.incarnation, app.instance, str(lease["epoch"]), str(lease["grant_request"])), "stale grant ready")
                return []
            if message.verb == "quiescing":
                _, lease = self._binding(app, message.fields)
                require(lease["state"] == "DRAINING", "progress cannot revive a terminal request")
                return []  # Progress deliberately does not change the durable deadline.
            if message.verb == "failed":
                lease_id, lease = self._binding(app, message.fields)
                if lease["quiesce"]["status"] != "pending":
                    return []  # Recorded terminal outcome wins; no repeated durable work.
                state = copy.deepcopy(self._state)
                self._quarantine(state["leases"][lease_id], "failed")
                self._commit(state, "app-reported-failure", lease=lease_id, code=message.fields[4])
                return []
            raise AuthorityError("app is not allowed to send this authority/shell verb")
