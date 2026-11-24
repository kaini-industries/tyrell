"""Bounded, in-memory H2 transition rehearsal. There is no hardware backend.

This models ordering and generation checks for synthetic fixtures. It cannot
serialize the real tmpfiles reader, apply a boot policy or certify recovery.
"""
from __future__ import annotations

import copy
import re

import eligibility

GENERATION = re.compile(r"[0-9a-f]{32}\Z")
MAX_EVENTS = 64
EVENTS = {
    "apply": set(), "step": {"readback"}, "invalidate": set(),
    "maintenance": set(), "crash": set(), "restart": set(),
    "recover": {"readback"}, "snapshot": {"snapshot"},
    "control-change": {"path", "value"}, "select": {"preset", "new_generation"},
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_events(events):
    require(isinstance(events, list) and len(events) <= MAX_EVENTS, "at most 64 events are allowed")
    for event in events:
        require(isinstance(event, dict), "event must be an object")
        kind = event.get("type")
        require(isinstance(kind, str) and kind in EVENTS, "unsupported event type")
        required = {"type", "generation"}
        if kind in ("snapshot", "control-change", "select"):
            required |= EVENTS[kind]
        require(required <= set(event) <= {"type", "generation"} | EVENTS[kind], "unexpected or missing event fields")
        require(isinstance(event.get("generation"), str) and GENERATION.fullmatch(event["generation"]), "invalid event generation")
        if kind in ("step", "recover"):
            require(event.get("readback", "ok") in ("ok", "fail"), "unknown readback result")
        elif kind == "snapshot":
            eligibility.validate_snapshot(event["snapshot"])
        elif kind == "control-change":
            require(isinstance(event["path"], str) and 0 < len(event["path"]) <= 512,
                    "invalid simulated control path")
            require(isinstance(event["value"], str) and 0 < len(event["value"]) <= 64 and
                    not any(ord(c) < 32 for c in event["value"]), "invalid simulated control value")
        elif kind == "select":
            require(event["preset"] in ("baseline", "ethernet", "cpu", "combined"), "unknown preset")
            require(isinstance(event["new_generation"], str) and GENERATION.fullmatch(event["new_generation"]),
                    "invalid replacement generation")


class Coordinator:
    """An ordering model whose only mutable objects are private Python copies."""

    def __init__(self, snapshot, preset, generation):
        self.snapshot = copy.deepcopy(eligibility.validate_snapshot(snapshot))
        self.plan = eligibility.plan(self.snapshot, preset, generation)
        self.generation = generation
        self.used_generations = {generation}
        self.original_identity = self.plan["identity_sha256"]
        self.controls = eligibility.control_values(self.snapshot) if self.plan["status"] == "ready" else {}
        self.baseline = copy.deepcopy(self.controls)
        self.phase = "idle" if self.plan["status"] == "ready" else "blocked"
        self.authorized = False
        self.revoked = False
        self.cursor = 0
        self.journal = []
        self.uncertain = set()
        self.external = set()
        self.trace = []

    def identity_ok(self):
        return eligibility.identity_digest(self.snapshot) == self.original_identity

    def baseline_observed(self):
        return self.plan["status"] == "ready" and self.identity_ok() and not self.uncertain and self.controls == self.baseline

    def set_control(self, path, value):
        require(path in self.controls, "event targets a control outside this plan's scope")
        candidate = copy.deepcopy(self.snapshot)
        if path.endswith("/sampling_down_factor"):
            candidate["cpu"]["tunables"]["sampling_down_factor"] = value
        else:
            for node in candidate["usb"]["nodes"]:
                if path == node["path"] + "/power/control":
                    node["control"] = value
                    break
                if path == node["path"] + "/power/autosuspend_delay_ms":
                    node["delay_ms"] = value
                    break
            else:
                raise ValueError("unknown simulated control path")
        eligibility.validate_snapshot(candidate)
        self.snapshot = candidate
        self.controls[path] = value

    def revoke(self):
        # This happens before rollback. A previously prepared reader must check
        # this generation authorization again when it tries to take a step.
        self.authorized = False
        self.revoked = True

    def apply(self, row):
        if self.plan["status"] != "ready":
            row["decision"] = "eligibility-blocked"
        elif self.revoked:
            row["decision"] = "generation-revoked"
        elif self.authorized:
            row["decision"] = "duplicate-coalesced"
        elif not self.baseline_observed():
            self.revoke()
            self.phase = "recovery-required"
            row["decision"] = "baseline-or-identity-changed"
        elif not self.plan["actions"]:
            self.phase = "baseline-observed"
            row["decision"] = "baseline-no-op"
        else:
            self.authorized = True
            self.phase = "prepared"
            row["decision"] = "prepared-not-written"

    def step(self, event, row):
        if not self.authorized or self.revoked or self.phase not in ("prepared", "applying", "active"):
            row["decision"] = "not-authorized"
            return
        if self.cursor >= len(self.plan["actions"]):
            row["decision"] = "duplicate-coalesced"
            return
        action = self.plan["actions"][self.cursor]
        path = action["path"]
        expected = copy.deepcopy(self.baseline)
        expected.update({entry["path"]: entry["after"] for entry in self.journal})
        good = self.identity_ok() and not self.uncertain and not self.external and self.controls == expected and self.controls.get(path) == action["before"]
        row["write_preconditions_satisfied"] = good
        if not good:
            self.revoke()
            self.phase = "recovery-required"
            row["decision"] = "write-precondition-failed"
            return
        # Record intent before changing even the simulated control. A crash
        # cannot erase the baseline value needed for explicit recovery.
        self.journal.append(copy.deepcopy(action))
        self.set_control(path, action["after"])
        row["simulated_writes"].append({"path": path, "before": action["before"], "after": action["after"]})
        self.cursor += 1
        if event.get("readback", "ok") == "fail":
            self.uncertain.add(path)
            self.revoke()
            self.phase = "recovery-required"
            row["decision"] = "readback-failed"
        else:
            self.phase = "active" if self.cursor == len(self.plan["actions"]) else "applying"
            row["decision"] = "simulated-write-read-back"

    def recover(self, event, row):
        if self.authorized or not self.revoked:
            row["decision"] = "revoke-before-recovery"
            return
        if not self.identity_ok():
            self.phase = "recovery-required"
            row["decision"] = "identity-lost-do-not-restore"
            return
        if not self.journal:
            self.phase = "baseline-observed" if self.baseline_observed() else "recovery-required"
            row["decision"] = "model-baseline-observed" if self.phase == "baseline-observed" else "baseline-readback-failed"
            return
        action = self.journal[-1]
        path = action["path"]
        current = self.controls.get(path)
        if path in self.uncertain or (path in self.external and current != action["before"]) or current not in (action["before"], action["after"]):
            self.phase = "recovery-required"
            row["decision"] = "unowned-or-uncertain-control-preserved"
            return
        if current == action["after"]:
            self.set_control(path, action["before"])
            row["simulated_writes"].append({"path": path, "before": current, "after": action["before"]})
            if event.get("readback", "ok") == "fail":
                self.uncertain.add(path)
                self.phase = "recovery-required"
                row["decision"] = "rollback-readback-failed"
                return
        self.journal.pop()
        self.external.discard(path)
        self.phase = "rolling-back" if self.journal else ("baseline-observed" if self.baseline_observed() else "recovery-required")
        row["decision"] = "simulated-restore-read-back"

    def dispatch(self, event):
        row = {"index": len(self.trace), "type": event["type"], "generation": event["generation"],
               "phase_before": self.phase, "authorization_before": self.authorized,
               "simulated_writes": []}
        kind = event["type"]
        if event["generation"] != self.generation:
            row["decision"] = "stale-generation-ignored"
        elif self.plan["status"] != "ready":
            # A script cannot manufacture eligibility by cycling a blocked
            # snapshot through recovery, crash, restart or preset selection.
            row["decision"] = "eligibility-blocked"
        elif kind == "apply":
            self.apply(row)
        elif kind == "step":
            self.step(event, row)
        elif kind in ("invalidate", "maintenance"):
            if self.revoked:
                row["decision"] = "duplicate-coalesced"
            else:
                self.revoke()
                self.phase = "revoked" if self.journal else ("baseline-observed" if self.baseline_observed() else "recovery-required")
                row["decision"] = "authorization-revoked-before-recovery"
        elif kind == "crash":
            self.revoke()
            self.phase = "interrupted"
            row["decision"] = "interrupted-journal-retained"
        elif kind == "restart":
            self.revoke()
            self.phase = "recovery-required"
            row["decision"] = "restart-requires-explicit-recovery"
        elif kind == "recover":
            self.recover(event, row)
        elif kind == "snapshot":
            incoming = copy.deepcopy(event["snapshot"])
            incoming_controls = eligibility.control_values(incoming)
            for path, value in incoming_controls.items():
                if path in self.controls and value != self.controls[path] and path not in self.uncertain:
                    self.external.add(path)
            self.snapshot, self.controls = incoming, incoming_controls
            self.uncertain.clear()
            if not self.identity_ok() or self.external:
                self.revoke()
                self.phase = "recovery-required"
                row["decision"] = "identity-or-control-change-revoked"
            else:
                row["decision"] = "fresh-synthetic-observation"
        elif kind == "control-change":
            self.set_control(event["path"], event["value"])
            self.external.add(event["path"])
            self.uncertain.discard(event["path"])
            self.revoke()
            self.phase = "recovery-required"
            row["decision"] = "external-control-change-revoked"
        elif kind == "select":
            if self.phase != "baseline-observed" or not self.baseline_observed() or event["new_generation"] in self.used_generations:
                row["decision"] = "baseline-and-new-generation-required"
            else:
                new_plan = eligibility.plan(self.snapshot, event["preset"], event["new_generation"])
                if new_plan["status"] != "ready":
                    row["decision"] = "replacement-eligibility-blocked"
                else:
                    self.plan = new_plan
                    self.generation = event["new_generation"]
                    self.used_generations.add(self.generation)
                    self.original_identity = new_plan["identity_sha256"]
                    self.baseline = copy.deepcopy(self.controls)
                    self.phase, self.cursor = "idle", 0
                    self.authorized, self.revoked = False, False
                    self.external.clear()
                    row["decision"] = "new-generation-selected"
                    row["new_generation"] = self.generation
        row.update(phase_after=self.phase, authorization_after=self.authorized,
                   current_generation=self.generation, journal_entries=len(self.journal))
        self.trace.append(row)

    def report(self):
        statuses = {"blocked": "blocked", "active": "simulated-active", "baseline-observed": "simulated-baseline",
                    "recovery-required": "recovery-required", "interrupted": "interrupted"}
        return {"schema": 1, "kind": "tyrell-power-rehearsal", "scope": "offline-model-only",
                "status": statuses.get(self.phase, "incomplete"), "phase": self.phase,
                "generation": self.generation, "plan_sha256": eligibility.digest(self.plan),
                "identity_sha256": self.original_identity, "live_authorized": False,
                "hardware_writes": 0, "baseline_recovery_certified": False,
                "hardware_qualified": False, "battery_qualified": False,
                "simulated_write_count": sum(len(row["simulated_writes"]) for row in self.trace),
                "authorization_in_model": self.authorized, "generation_revoked_in_model": self.revoked,
                "trace": copy.deepcopy(self.trace), "controls": copy.deepcopy(self.controls),
                "pending_recovery": copy.deepcopy(self.journal), "uncertain_controls": sorted(self.uncertain),
                "blockers": copy.deepcopy(self.plan["blockers"]),
                "limitations": ["Only copied synthetic Python objects are changed; no hardware backend exists.",
                                "Ordering here does not serialize real tmpfiles, administrator commands or boot events.",
                                "Simulated baseline observations cannot establish live restoration or hardware acceptance.",
                                "Physical functional/performance, boot/update rollback and paired battery trials remain pending."]}


def rehearse(snapshot, preset, generation, events):
    """Return a bounded trace; inputs and every external system remain untouched."""
    original_snapshot, original_events = copy.deepcopy(snapshot), copy.deepcopy(events)
    validate_events(original_events)
    model = Coordinator(original_snapshot, preset, generation)
    snapshot_sha256 = eligibility.digest(original_snapshot)
    events_sha256 = eligibility.digest(original_events)
    for event in original_events:
        model.dispatch(event)
    result = model.report()
    result.update(input_snapshot_sha256=snapshot_sha256, events_sha256=events_sha256,
                  initial_preset=preset, initial_generation=generation)
    return result
