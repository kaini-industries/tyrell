"""Version-2 participant ledger transitions; trusted coordinator API only.

The only admitted profiles are disposable null audio and controlled VT ioctl
fixtures. Production backend classes are implemented separately but no live
manager/service or hardware grant exists in this increment.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import json

PROFILES = {"audio": "null-v2", "vt": "vt-fixture-v2"}
PHASES = {"registered", "bound", "acquired", "cleaned", "fence-acked", "fenced", "unknown"}
FIELDS = {"lease", "generation", "domain", "profile", "uid", "pid", "phase", "launch", "prepared",
          "acquire", "cleanup", "post_actor", "stop_post_done", "drain", "evidence"}


def validate_evidence(record, phase, evidence):
    from handoff_authority import require, identifier, integer
    require(isinstance(evidence, dict) and evidence and len(json.dumps(evidence, ensure_ascii=True)) <= 1800,
            "invalid backend evidence")
    if record["domain"] == "audio":
        fields = {"generation", "cookie", "token", "index", "name", "arguments",
                  "server_uid", "server_pid", "socket_dev", "socket_ino"}
        expected = fields - {"index"} if phase == "prepare" else fields if phase == "acquire" else fields | {"unload_confirmed", "absence_confirmed"}
        require(set(evidence) == expected,
                "invalid audio evidence fields")
        require(identifier(evidence["generation"]) and evidence["token"] == record["generation"] and
                integer(evidence["cookie"], 0, 2**32-1) and (phase == "prepare" or integer(evidence["index"], 0, 2**32-2)) and
                evidence["name"] == "module-null-sink" and evidence["arguments"] ==
                "sink_name=tyrell_v2_" + record["generation"] + " rate=48000 channels=2" and
                integer(evidence["server_uid"], 1, 2**32-1) and evidence["server_uid"] == record["uid"] and
                integer(evidence["server_pid"], 1, 2**31-1) and
                integer(evidence["socket_dev"], 0) and integer(evidence["socket_ino"]), "invalid audio provenance")
        if phase == "acquire":
            require({key:value for key, value in evidence.items() if key != "index"} == record["prepared"],
                    "accepted audio server binding changed")
        if phase == "cleanup":
            require("acquire" in record["evidence"] and evidence["unload_confirmed"] is True and evidence["absence_confirmed"] is True and
                    {key:evidence[key] for key in fields} == record["evidence"]["acquire"], "audio cleanup identity mismatch")
    else:
        require(set(evidence) == {"kind", "version", "backend", "managed_vt", "neutral_vt", "token", "state"} and
                evidence["kind"] == "vt-v2" and type(evidence["version"]) is int and evidence["version"] == 2 and
                identifier(evidence["backend"]) and evidence["managed_vt"] == 2 and type(evidence["managed_vt"]) is int and
                evidence["neutral_vt"] == 1 and type(evidence["neutral_vt"]) is int and
                evidence["token"] == record["generation"] and evidence["state"] ==
                ("NEW" if phase == "prepare" else "OWNED" if phase == "acquire" else "RELEASED"),
                "invalid disposable VT evidence")
        if phase == "acquire":
            require(evidence == {**record["prepared"], "state": "OWNED"}, "accepted VT binding changed")
        if phase == "cleanup":
            require("acquire" in record["evidence"] and evidence == {**record["evidence"]["acquire"], "state": "RELEASED"}, "VT cleanup identity mismatch")


def validate_participants(state):
    from handoff_authority import require, identifier, integer
    records = state["participants"]
    require(isinstance(records, dict) and len(records) <= 64, "invalid participant registry")
    require(not records or state["mode"] == "disposable-v2", "participants require disposable profile")
    for name, record in records.items():
        require(identifier(name) and isinstance(record, dict) and set(record) == FIELDS, "invalid participant record")
        require(record["domain"] in PROFILES and record["profile"] == PROFILES[record["domain"]] and
                identifier(record["generation"]) and integer(record["uid"], 1, 2**32-1) and
                (record["pid"] is None or integer(record["pid"], 1, 2**31-1)) and record["phase"] in PHASES,
                "invalid participant identity/profile")
        lease = state["leases"].get(record["lease"])
        require(lease is not None and name in lease["actors"] and record["post_actor"] in lease["actors"] and
                name != record["post_actor"], "participant actors missing")
        for field, actor in (("launch", name), ("acquire", name), ("cleanup", name)):
            operation = record[field]
            require((field != "launch" and operation is None) or
                    (operation in lease["operations"] and lease["operations"][operation]["actor"] == actor),
                    "participant permit missing")
        require(type(record["stop_post_done"]) is bool and type(record["drain"]) is bool and
                isinstance(record["evidence"], dict) and set(record["evidence"]) <= {"acquire", "cleanup"},
                "invalid participant evidence")
        require(len(json.dumps(record["evidence"], ensure_ascii=True)) <= 4096, "participant evidence too large")
        if record["prepared"] is not None:
            validate_evidence(record, "prepare", record["prepared"])
        require(record["phase"] in ("registered", "unknown") or record["prepared"] is not None, "unprepared participant phase")
        for phase, evidence in record["evidence"].items():
            validate_evidence(record, phase, evidence)
        if record["phase"] == "fenced":
            require(record["cleanup"] is not None and "cleanup" in record["evidence"] and
                    all(lease["operations"][record[key]]["state"] == "DONE" for key in ("launch", "acquire", "cleanup")),
                    "fence without completed backend work")
        if record["stop_post_done"] or lease["state"] == "RELEASED":
            require(record["phase"] == "fenced" and record["stop_post_done"] and
                    lease["actors"][name]["released"] and lease["actors"][record["post_actor"]]["released"],
                    "unfenced participant retirement")


@dataclass(frozen=True)
class Participant:
    actor: object
    post_actor: object
    generation: str


class Participants:
    """Durable permits and stop-post fences shared with the D1 authority."""

    def __init__(self, authority):
        self.owner = authority

    def _record(self, capability):
        from handoff_authority import require
        require(isinstance(capability, Participant), "unregistered participant")
        lease = self.owner._actor(capability.actor)
        self.owner._actor(capability.post_actor)
        record = self.owner._state["participants"].get(capability.actor.identifier)
        require(record is not None and record["generation"] == capability.generation and
                record["post_actor"] == capability.post_actor.identifier, "stale participant generation")
        return lease, record

    def register(self, app, lease_id, *, domain, profile, uid):
        from handoff_authority import Actor, require, fresh_id, integer, MAX_ACTORS, MAX_OPERATIONS
        owner = self.owner
        with owner._lock:
            owner._expire()
            lease = owner._owned(app, lease_id)
            require(owner._state["mode"] == "disposable-v2" and domain in PROFILES and profile == PROFILES[domain],
                    "only explicit disposable participant profiles are admitted")
            require(integer(uid, 1, 2**32-1) and lease["state"] == "OWNED" and
                    len(lease["actors"]) + 2 <= MAX_ACTORS and len(lease["operations"]) < MAX_OPERATIONS and
                    len(owner._state["participants"]) < 64, "sealed/full participant registry or invalid user")
            used_actors = {name for old in owner._state["leases"].values() for name in old["actors"]}
            actor_id = fresh_id(used_actors)
            post_id = fresh_id({*used_actors, actor_id})
            actor = Actor(lease_id, actor_id, app.incarnation, fresh_id(owner._actors))
            post = Actor(lease_id, post_id, app.incarnation, fresh_id({*owner._actors, actor._token}))
            generation = fresh_id({r["generation"] for r in owner._state["participants"].values()})
            operation = fresh_id(lease["operations"])
            state = copy.deepcopy(owner._state)
            target = state["leases"][lease_id]
            for name in (actor_id, post_id):
                target["actors"][name] = {"kind": "inert", "released": False}
            target["operations"][operation] = {"actor": actor_id, "kind": "acquire", "state": "PENDING"}
            state["participants"][actor_id] = {"lease": lease_id, "generation": generation,
                "domain": domain, "profile": profile, "uid": uid, "pid": None, "phase": "registered",
                "launch": operation, "acquire": None, "cleanup": None, "post_actor": post_id,
                "stop_post_done": False, "drain": False, "prepared": None, "evidence": {}}
            owner._commit(state, "v2-participant-before-launch", participant=actor_id)
            owner._actors[actor._token], owner._actors[post._token] = actor, post
            return Participant(actor, post, generation)

    def snapshot(self, capability):
        with self.owner._lock:
            _, record = self._record(capability)
            return copy.deepcopy(record)

    def bind(self, capability, *, pid, uid, prepared):
        from handoff_authority import require, integer, fresh_id, MAX_OPERATIONS
        owner = self.owner
        with owner._lock:
            owner._expire()
            lease, record = self._record(capability)
            require(record["phase"] == "registered" and uid == record["uid"] and integer(pid, 1, 2**31-1), "invalid worker binding")
            validate_evidence(record, "prepare", prepared)
            # Spawn was accepted before seal; binding preserves that obligation.
            require(len(lease["operations"]) < MAX_OPERATIONS, "operation registry full")
            state = copy.deepcopy(owner._state)
            target = state["participants"][capability.actor.identifier]
            target["pid"], target["phase"] = pid, "bound"
            target["prepared"] = copy.deepcopy(prepared)
            operations = state["leases"][record["lease"]]["operations"]
            operations[record["launch"]]["state"] = "DONE"
            operation = fresh_id(operations)
            target["acquire"] = operation
            operations[operation] = {"actor": capability.actor.identifier, "kind": "acquire", "state": "PENDING"}
            owner._commit(state, "v2-worker-bound-acquire-permitted", participant=capability.actor.identifier)
            return operation

    def completed(self, capability, *, phase, evidence):
        from handoff_authority import require
        owner = self.owner
        with owner._lock:
            owner._expire()
            _, record = self._record(capability)
            key, previous, following = ("acquire", "bound", "acquired") if phase == "acquire" else ("cleanup", "acquired", "cleaned")
            require(phase in ("acquire", "cleanup"), "invalid backend phase")
            validate_evidence(record, phase, evidence)
            if key in record["evidence"]:
                require(record["evidence"][key] == evidence, "conflicting backend replay")
                return
            require(record["phase"] == previous and record[key] is not None, "unexpected backend completion")
            state = copy.deepcopy(owner._state)
            target = state["participants"][capability.actor.identifier]
            target["phase"], target["evidence"][key] = following, copy.deepcopy(evidence)
            state["leases"][record["lease"]]["operations"][record[key]]["state"] = "DONE"
            owner._commit(state, "v2-backend-terminal-readback", participant=capability.actor.identifier, phase=phase)

    def drain(self, capability):
        from handoff_authority import require, fresh_id, MAX_OPERATIONS
        owner = self.owner
        with owner._lock:
            owner._expire()
            lease, record = self._record(capability)
            require(lease["state"] in ("DRAINING", "QUARANTINED", "RELEASED"), "seal epoch before drain")
            if record["phase"] in ("fence-acked", "fenced", "unknown"):
                return None
            state = copy.deepcopy(owner._state)
            target = state["participants"][capability.actor.identifier]
            target["drain"] = True
            operation = record["cleanup"]
            if record["phase"] == "acquired" and operation is None:
                require(len(lease["operations"]) < MAX_OPERATIONS, "operation registry full")
                operation = fresh_id(lease["operations"])
                target["cleanup"] = operation
                state["leases"][record["lease"]]["operations"][operation] = {
                    "actor": capability.actor.identifier, "kind": "cleanup", "state": "PENDING"}
            if state != owner._state:
                owner._commit(state, "v2-drain-intent-cleanup-permit", participant=capability.actor.identifier)
            return operation

    def fence_ack(self, capability):
        from handoff_authority import require
        owner = self.owner
        with owner._lock:
            owner._expire()
            _, record = self._record(capability)
            if record["phase"] == "fence-acked":
                return
            require(record["phase"] == "cleaned", "cleanup must precede worker fence")
            state = copy.deepcopy(owner._state)
            state["participants"][capability.actor.identifier]["phase"] = "fence-acked"
            owner._commit(state, "v2-worker-fence-ack", participant=capability.actor.identifier)

    def reaped(self, capability):
        """Trusted supervisor confirms exact worker exit after recorded fence ACK."""
        from handoff_authority import require
        owner = self.owner
        with owner._lock:
            owner._expire()
            _, record = self._record(capability)
            require(record["phase"] == "fence-acked", "worker exit alone is not completion")
            state = copy.deepcopy(owner._state)
            state["participants"][capability.actor.identifier]["phase"] = "fenced"
            state["leases"][record["lease"]]["actors"][capability.actor.identifier]["released"] = True
            owner._commit(state, "v2-worker-fenced-and-reaped", participant=capability.actor.identifier)

    def stop_post(self, capability):
        """No resource operation exists here. Held or durably suppressed only."""
        owner = self.owner
        with owner._lock:
            owner._expire()
            _, record = self._record(capability)
            if record["stop_post_done"]:
                return "suppressed"
            if record["phase"] != "fenced":
                return "held"
            state = copy.deepcopy(owner._state)
            state["participants"][capability.actor.identifier]["stop_post_done"] = True
            state["leases"][record["lease"]]["actors"][record["post_actor"]]["released"] = True
            owner._release_if_complete(state, record["lease"])
            owner._commit(state, "v2-stop-post-durably-suppressed", participant=capability.actor.identifier)
            return "suppressed"

    def lost(self, capability):
        owner = self.owner
        with owner._lock:
            owner._check()
            _, record = self._record(capability)
            if record["phase"] in ("fenced", "unknown"):
                return
            state = copy.deepcopy(owner._state)
            state["participants"][capability.actor.identifier]["phase"] = "unknown"
            lease = state["leases"][record["lease"]]
            owner._quarantine(lease, "unknown")
            for operation in lease["operations"].values():
                if operation["actor"] == capability.actor.identifier and operation["state"] == "PENDING":
                    operation["state"] = "UNKNOWN"
            owner._commit(state, "v2-worker-lost-unknown", participant=capability.actor.identifier)
