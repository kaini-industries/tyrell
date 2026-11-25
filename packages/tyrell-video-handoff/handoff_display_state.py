"""Durable D3 display borrowers and explicit display-to-VT dependencies.

Only native controlled-fixture facts are admitted. A driver fence, fsync or
local app completion cannot replace an independent borrower/finality result.
No live display profile or recovery-clear operation is provided here.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import re

DISPLAY_DOMAINS = {"kms", "lcd"}
SCOPES = {"kms": "connector=11;crtc=12;planes=14,15", "lcd": "320x170:RGB565"}
IDENTITY = {"kind", "domain", "epoch", "resource", "generation", "scope", "backend_pid", "backend_uid", "native_identity_sha256"}
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def registration_display_metadata(state, lease, domain):
    from handoff_authority import fresh_id
    if domain not in DISPLAY_DOMAINS:
        return None
    resources = {record["display"]["resource"] for record in state["participants"].values() if record["display"] is not None}
    return {"resource": fresh_id(resources), "epoch": state["leases"][lease]["epoch"], "borrowers": {}}


def required_display_ids(state, lease, domain, values):
    from handoff_authority import require, identifier
    require(type(values) in (tuple, list) and all(identifier(value) for value in values), "invalid display dependency IDs")
    if domain != "vt-display":
        require(not values, "this profile accepts no display dependency override")
        return []
    require(len(values) == 2 and len(set(values)) == 2, "VT requires explicit KMS and LCD participants")
    records = [state["participants"].get(value) for value in values]
    require(all(record is not None and record["lease"] == lease and record["display"] is not None for record in records) and
            {record["domain"] for record in records} == DISPLAY_DOMAINS, "missing, foreign or duplicate display domain")
    return sorted(values)


def validate_display_evidence(record, phase, evidence):
    from handoff_authority import require, identifier, integer
    display = record["display"]
    extra = set() if phase == "prepare" else {"operation", "admitted"} if phase == "acquire" else {
        "operation", "sealed", "borrowers", "queued", "inflight", "driver_finality", "callbacks_suppressed", "native_receipt", "native_sha256"}
    require(display is not None and set(evidence) == IDENTITY | extra, "invalid display evidence fields")
    require(evidence["kind"] == "display-fixture-v3" and evidence["domain"] == record["domain"] and
            integer(evidence["epoch"]) and evidence["epoch"] == display["epoch"] and
            evidence["resource"] == display["resource"] and evidence["generation"] == record["generation"] and
            evidence["scope"] == SCOPES[record["domain"]] and
            integer(evidence["backend_pid"], 1, 2**31-1) and integer(evidence["backend_uid"], 1, 2**32-1) and
            evidence["backend_uid"] == record["uid"] and type(evidence["native_identity_sha256"]) is str and
            SHA256.fullmatch(evidence["native_identity_sha256"]) is not None, "stale display identity or unsupported scope")
    if record["pid"] is not None:
        require(evidence["backend_pid"] == record["pid"], "display worker identity changed")
    if phase != "prepare":
        require({key:evidence[key] for key in IDENTITY} == record["prepared"], "display backend binding changed")
        require(evidence["operation"] == record[phase] and identifier(evidence["operation"]), "unpermitted display result")
    if phase == "acquire":
        require(evidence["admitted"] is True, "display acquisition not confirmed")
    if phase == "cleanup":
        require(evidence["sealed"] is True and evidence["driver_finality"] is True and evidence["callbacks_suppressed"] is True and
                all(type(evidence[key]) is int and evidence[key] == 0 for key in ("borrowers", "queued", "inflight")) and
                identifier(evidence["native_receipt"]) and isinstance(evidence["native_sha256"],str) and
                SHA256.fullmatch(evidence["native_sha256"]) is not None and
                all(borrower["state"] == "DONE" for borrower in display["borrowers"].values()), "display cleanup barrier is not empty")


def cleanup_dependencies(state, record):
    from handoff_authority import require
    display = record["display"]
    if display is not None:
        require(all(borrower["state"] == "DONE" for borrower in display["borrowers"].values()), "display still has graph/flush borrowers")
    if record["domain"] != "vt-display":
        return {}
    required_display_ids(state, record["lease"], record["domain"], record["requires"])
    proofs = {}
    for name in record["requires"]:
        other = state["participants"][name]
        require(other["phase"] == "fenced" and other["stop_post_done"] and "cleanup" in other["evidence"],
                "display backend/future cleanup is not fenced")
        proofs[name] = other["evidence"]["cleanup"]["native_receipt"]
    return proofs


def validate_display_record(state, name, record):
    from handoff_authority import require, identifier, integer
    display = record["display"]
    require(type(record["requires"]) is list and type(record["display_proofs"]) is dict, "invalid display dependency state")
    required_display_ids(state, record["lease"], record["domain"], record["requires"])
    if display is None:
        require(record["domain"] not in DISPLAY_DOMAINS, "missing display metadata")
    else:
        lease = state["leases"][record["lease"]]
        require(record["domain"] in DISPLAY_DOMAINS and type(display) is dict and set(display) == {"resource", "epoch", "borrowers"} and
                identifier(display["resource"]) and integer(display["epoch"]) and display["epoch"] == lease["epoch"] and
                type(display["borrowers"]) is dict and len(display["borrowers"]) <= 8, "invalid display metadata")
        for actor, borrower in display["borrowers"].items():
            require(identifier(actor) and actor in lease["actors"] and actor not in (name,record["post_actor"],lease["app"]) and
                    type(borrower) is dict and set(borrower) == {"kind","operation","state","proof"} and
                    borrower["kind"] in ("graph","flush") and borrower["state"] in ("PENDING","UNKNOWN","DONE"), "invalid display borrower")
            operation = lease["operations"].get(borrower["operation"])
            require(operation is not None and operation["actor"] == actor and operation["kind"] == "use" and operation["state"] == borrower["state"],
                    "display borrower permit mismatch")
            if borrower["state"] == "DONE":
                require(lease["actors"][actor]["released"] and borrower["proof"] is not None, "unverified borrower completion")
                validate_borrower_proof(record, actor, borrower, borrower["proof"])
            else:
                require(not lease["actors"][actor]["released"] and borrower["proof"] is None, "unverified borrower retired")
    if record["cleanup"] is not None:
        require(record["display_proofs"] == cleanup_dependencies(state,record), "missing durable display-to-VT dependency")
    else:
        require(not record["display_proofs"], "display dependency before cleanup permit")


def validate_borrower_proof(record, actor, borrower, proof):
    from handoff_authority import require, identifier
    display = record["display"]
    require(type(proof) is dict and set(proof) == {"epoch","resource","generation","actor","operation","disposition","receipt"} and
            type(proof["epoch"]) is int and proof["epoch"] == display["epoch"] and proof["resource"] == display["resource"] and
            proof["generation"] == record["generation"] and proof["actor"] == actor and proof["operation"] == borrower["operation"] and
            proof["disposition"] in ("drained","suppressed-before-start") and identifier(proof["receipt"]) and
            (proof["disposition"] != "suppressed-before-start" or borrower["kind"] == "flush"), "invalid independent borrower proof")


@dataclass(frozen=True)
class DisplayBorrower:
    participant: str
    generation: str
    actor: object
    operation: str


class Displays:
    """Trusted producer registration/completion, never an app packet handler."""
    def __init__(self, authority):
        from handoff_participant_state import Participants
        self.owner, self.participants = authority, Participants(authority)

    def register_borrower(self, capability, *, kind):
        from handoff_authority import Actor, require, fresh_id, MAX_ACTORS, MAX_OPERATIONS
        owner = self.owner
        with owner._lock:
            owner._expire()
            lease, record = self.participants._record(capability)
            require(record["display"] is not None and kind in ("graph","flush") and lease["state"] == "OWNED" and
                    record["phase"] in ("registered","bound","acquired") and len(lease["actors"]) < MAX_ACTORS and
                    len(lease["operations"]) < MAX_OPERATIONS and len(record["display"]["borrowers"]) < 8,
                    "sealed/full display borrower registry")
            used = {actor for old in owner._state["leases"].values() for actor in old["actors"]}
            actor = Actor(capability.actor.lease, fresh_id(used), capability.actor.incarnation, fresh_id(owner._actors))
            operation = fresh_id(lease["operations"])
            state = copy.deepcopy(owner._state)
            target = state["leases"][record["lease"]]
            target["actors"][actor.identifier] = {"kind":"inert","released":False}
            target["operations"][operation] = {"actor":actor.identifier,"kind":"use","state":"PENDING"}
            state["participants"][capability.actor.identifier]["display"]["borrowers"][actor.identifier] = {
                "kind":kind,"operation":operation,"state":"PENDING","proof":None}
            owner._commit(state,"display-borrower-before-submission",participant=capability.actor.identifier,actor=actor.identifier)
            owner._actors[actor._token] = actor
            return DisplayBorrower(capability.actor.identifier,capability.generation,actor,operation)

    def borrower_done(self, capability, borrower, proof):
        from handoff_authority import require
        owner = self.owner
        with owner._lock:
            owner._expire()
            _, record = self.participants._record(capability)
            require(isinstance(borrower,DisplayBorrower) and borrower.participant == capability.actor.identifier and
                    borrower.generation == capability.generation, "foreign display borrower")
            owner._actor(borrower.actor)
            target = record["display"]["borrowers"].get(borrower.actor.identifier)
            require(target is not None and target["operation"] == borrower.operation, "unknown borrower operation")
            validate_borrower_proof(record,borrower.actor.identifier,target,proof)
            if target["state"] == "DONE":
                require(target["proof"] == proof,"conflicting borrower completion")
                return
            state = copy.deepcopy(owner._state)
            entry = state["participants"][capability.actor.identifier]["display"]["borrowers"][borrower.actor.identifier]
            entry["state"],entry["proof"] = "DONE",copy.deepcopy(proof)
            lease = state["leases"][record["lease"]]
            lease["operations"][borrower.operation]["state"] = "DONE"
            lease["actors"][borrower.actor.identifier]["released"] = True
            owner._commit(state,"display-borrower-terminal",participant=capability.actor.identifier,actor=borrower.actor.identifier)

    def borrower_lost(self, capability, borrower):
        from handoff_authority import require
        owner = self.owner
        with owner._lock:
            owner._check()
            _, record = self.participants._record(capability)
            require(isinstance(borrower,DisplayBorrower) and borrower.participant == capability.actor.identifier and
                    borrower.generation == capability.generation, "foreign display borrower")
            owner._actor(borrower.actor)
            entry = record["display"]["borrowers"].get(borrower.actor.identifier)
            require(entry is not None and entry["operation"] == borrower.operation,"unknown display borrower")
            if entry["state"] in ("DONE","UNKNOWN"):
                return
            state = copy.deepcopy(owner._state)
            state["participants"][capability.actor.identifier]["display"]["borrowers"][borrower.actor.identifier]["state"] = "UNKNOWN"
            lease = state["leases"][record["lease"]]
            lease["operations"][borrower.operation]["state"] = "UNKNOWN"
            owner._quarantine(lease,"unknown")
            owner._commit(state,"display-borrower-lost-unknown",participant=capability.actor.identifier,actor=borrower.actor.identifier)
