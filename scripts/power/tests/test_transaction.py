"""Synthetic transition evidence is never a live power or recovery result."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import eligibility
import transaction
from fixtures import fixture_snapshot

G = "0123456789abcdef0123456789abcdef"
NEW = "a" * 32
STALE = "b" * 32


def event(kind, generation=G, **extra):
    return {"type": kind, "generation": generation, **extra}


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = fixture_snapshot()
        self.plan = eligibility.plan(self.snapshot, "combined", G)
        self.assertEqual(self.plan["status"], "ready", self.plan)
        self.count = len(self.plan["actions"])
        self.baseline = eligibility.control_values(self.snapshot)

    def run_model(self, events, preset="combined"):
        return transaction.rehearse(self.snapshot, preset, G, events)

    def applied(self):
        return [event("apply"), *[event("step") for _ in range(self.count)]]

    def recovered(self):
        return [*self.applied(), event("invalidate"), *[event("recover") for _ in range(self.count + 1)]]

    def test_no_io_backend_and_inputs_unchanged(self):
        before = copy.deepcopy(self.snapshot)
        events = self.recovered()
        expected_events = copy.deepcopy(events)
        with patch("builtins.open", side_effect=AssertionError("no filesystem backend")), \
                patch.object(Path, "open", side_effect=AssertionError("no filesystem backend")):
            result = self.run_model(events)
        self.assertEqual(self.snapshot, before)
        self.assertEqual(events, expected_events)
        self.assertEqual(result["input_snapshot_sha256"], eligibility.digest(before))
        self.assertEqual(result["events_sha256"], eligibility.digest(expected_events))
        self.assertEqual(result["plan_sha256"], eligibility.digest(self.plan))
        self.assertEqual(result["scope"], "offline-model-only")
        self.assertEqual(result["hardware_writes"], 0)
        for name in ("live_authorized", "baseline_recovery_certified", "hardware_qualified", "battery_qualified"):
            self.assertIs(result[name], False)

    def test_every_preset_is_explicit_and_baseline_is_a_noop(self):
        for preset in ("baseline", "ethernet", "cpu", "combined"):
            plan = eligibility.plan(self.snapshot, preset, G)
            actions = len(plan["actions"])
            result = self.run_model([event("apply"), *[event("step") for _ in range(actions + 2)]], preset)
            with self.subTest(preset=preset):
                self.assertEqual(result["simulated_write_count"], actions)
                self.assertEqual(result["phase"], "baseline-observed" if preset == "baseline" else "active")
                self.assertFalse(result["baseline_recovery_certified"])

    def test_apply_prepares_and_steps_are_individually_bounded(self):
        prepared = self.run_model([event("apply")])
        self.assertEqual(prepared["phase"], "prepared")
        self.assertEqual(prepared["simulated_write_count"], 0)
        complete = self.run_model(self.applied())
        self.assertEqual(complete["phase"], "active")
        self.assertEqual(complete["simulated_write_count"], self.count)
        self.assertTrue(all(len(row["simulated_writes"]) <= 1 for row in complete["trace"]))

    def test_step_without_authorization_never_writes(self):
        result = self.run_model([event("step"), event("recover")])
        self.assertEqual(result["simulated_write_count"], 0)
        self.assertEqual(result["controls"], self.baseline)

    def test_duplicate_events_are_coalesced(self):
        result = self.run_model([event("apply"), event("apply"), *[event("step") for _ in range(self.count + 2)],
                                 event("invalidate"), event("invalidate")])
        self.assertEqual(result["simulated_write_count"], self.count)
        self.assertEqual(result["trace"][1]["decision"], "duplicate-coalesced")
        self.assertEqual(result["trace"][-1]["decision"], "duplicate-coalesced")
        self.assertTrue(result["generation_revoked_in_model"])

    def test_invalidation_at_every_apply_boundary_stops_held_reader_before_recovery(self):
        for prefix in range(self.count + 1):
            result = self.run_model([event("apply"), *[event("step") for _ in range(prefix)],
                                     event("invalidate"), event("step"),
                                     *[event("recover") for _ in range(prefix + 1)]])
            with self.subTest(prefix=prefix):
                revoked = result["trace"][prefix + 1]
                self.assertFalse(revoked["authorization_after"])
                self.assertEqual(revoked["simulated_writes"], [])
                self.assertEqual(result["trace"][prefix + 2]["decision"], "not-authorized")
                self.assertEqual(result["controls"], self.baseline)
                self.assertEqual(result["phase"], "baseline-observed")
                self.assertFalse(result["authorization_in_model"])

    def test_maintenance_revokes_before_any_restore(self):
        result = self.run_model([*self.applied(), event("maintenance"), event("step"), event("recover")])
        row = result["trace"][self.count + 1]
        self.assertEqual(row["decision"], "authorization-revoked-before-recovery")
        self.assertEqual(row["simulated_writes"], [])
        self.assertEqual(result["trace"][self.count + 2]["simulated_writes"], [])

    def test_stale_generation_cannot_revoke_or_write_current(self):
        result = self.run_model([event("apply"), event("invalidate", STALE), event("step", STALE), event("step")])
        self.assertEqual(result["simulated_write_count"], 1)
        self.assertTrue(result["authorization_in_model"])
        self.assertTrue(all(row["decision"] == "stale-generation-ignored" for row in result["trace"][1:3]))

    def test_crash_restart_retains_journal_and_cannot_resume_optimization(self):
        result = self.run_model([event("apply"), event("step"), event("crash"), event("restart"),
                                 event("apply"), event("step")])
        self.assertEqual(result["phase"], "recovery-required")
        self.assertEqual(result["simulated_write_count"], 1)
        self.assertEqual(len(result["pending_recovery"]), 1)
        recovered = self.run_model([event("apply"), event("step"), event("crash"), event("restart"), event("recover")])
        self.assertEqual(recovered["phase"], "baseline-observed")
        self.assertEqual(recovered["controls"], self.baseline)

    def test_readback_failure_needs_fresh_observation_before_restoring(self):
        model = transaction.Coordinator(self.snapshot, "combined", G)
        model.dispatch(event("apply"))
        model.dispatch(event("step", readback="fail"))
        self.assertEqual(model.phase, "recovery-required")
        model.dispatch(event("recover"))
        self.assertEqual(model.trace[-1]["decision"], "unowned-or-uncertain-control-preserved")
        model.dispatch(event("snapshot", snapshot=copy.deepcopy(model.snapshot)))
        model.dispatch(event("recover"))
        self.assertEqual(model.phase, "baseline-observed")
        self.assertEqual(model.controls, self.baseline)

    def test_failed_rollback_readback_is_not_model_recovery(self):
        model = transaction.Coordinator(self.snapshot, "combined", G)
        for item in [event("apply"), event("step"), event("invalidate"), event("recover", readback="fail")]:
            model.dispatch(item)
        self.assertEqual(model.phase, "recovery-required")
        self.assertTrue(model.journal)
        self.assertTrue(model.uncertain)
        model.dispatch(event("snapshot", snapshot=copy.deepcopy(model.snapshot)))
        model.dispatch(event("recover"))
        self.assertEqual(model.phase, "baseline-observed")

    def test_identity_loss_never_restores_a_stale_path(self):
        changed = copy.deepcopy(self.snapshot)
        changed["boot_id"] = "c" * 32
        result = self.run_model([event("apply"), event("step"), event("snapshot", snapshot=changed), event("recover")])
        self.assertEqual(result["phase"], "recovery-required")
        self.assertEqual(result["trace"][-1]["decision"], "identity-lost-do-not-restore")
        self.assertEqual(result["trace"][-1]["simulated_writes"], [])

    def test_external_control_is_preserved_even_when_it_equals_last_write(self):
        action = self.plan["actions"][0]
        result = self.run_model([event("apply"), event("step"),
                                 event("control-change", path=action["path"], value=action["after"]), event("recover")])
        self.assertEqual(result["phase"], "recovery-required")
        self.assertEqual(result["trace"][-1]["decision"], "unowned-or-uncertain-control-preserved")
        self.assertEqual(result["trace"][-1]["simulated_writes"], [])

    def test_external_change_before_pending_write_revokes_the_whole_generation(self):
        action = self.plan["actions"][-1]
        result = self.run_model([event("apply"), event("control-change", path=action["path"], value=action["after"]), event("step")])
        self.assertEqual(result["simulated_write_count"], 0)
        self.assertFalse(result["authorization_in_model"])

    def test_new_preset_requires_baseline_and_a_never_used_generation(self):
        rejected = self.run_model([*self.applied(), event("select", preset="cpu", new_generation=NEW)])
        self.assertEqual(rejected["generation"], G)
        result = self.run_model([*self.recovered(), event("select", preset="cpu", new_generation=NEW),
                                 event("invalidate", G), event("apply", NEW), event("step", NEW)])
        self.assertEqual(result["generation"], NEW)
        self.assertEqual(result["initial_generation"], G)
        self.assertEqual(result["initial_preset"], "combined")
        self.assertEqual(result["phase"], "active")
        self.assertEqual(result["trace"][-3]["decision"], "stale-generation-ignored")
        reused = self.run_model([*self.recovered(), event("select", preset="cpu", new_generation=G)])
        self.assertEqual(reused["trace"][-1]["decision"], "baseline-and-new-generation-required")

    def test_unverified_observations_never_start_even_with_plausible_values(self):
        unverified = copy.deepcopy(self.snapshot)
        unverified["scope"] = "unverified-observation"
        result = transaction.rehearse(unverified, "combined", G,
                                     [event("apply"), event("step"), event("invalidate"), event("recover"),
                                      event("restart"), event("crash"), event("maintenance"), event("recover"),
                                      event("select", preset="baseline", new_generation=NEW)])
        self.assertEqual(result["phase"], "blocked")
        self.assertEqual(result["simulated_write_count"], 0)
        self.assertFalse(result["authorization_in_model"])
        self.assertEqual(result["generation"], G)

    def test_drift_in_earlier_written_control_cannot_finish_later_actions(self):
        action = self.plan["actions"][0]
        result = self.run_model([event("apply"), event("step"),
                                 event("control-change", path=action["path"], value=action["before"]),
                                 *[event("step") for _ in range(self.count)]])
        self.assertEqual(result["simulated_write_count"], 1)
        self.assertEqual(result["phase"], "recovery-required")
        self.assertFalse(result["authorization_in_model"])

    def test_event_bounds_shapes_and_paths_fail_closed(self):
        bad = ([event("apply")] * 65, [{"type": "shell", "generation": G}],
               [event("step", readback=True)], [event("apply", extra="ignored")],
               [event("select", preset="cpu", new_generation="no")],
               [event("control-change", path="/sys/arbitrary", value="1")])
        for events in bad:
            with self.subTest(events=events[:2]), self.assertRaises(ValueError):
                self.run_model(events)

    def test_event_provenance_distinguishes_stale_scripts_with_the_same_control_result(self):
        first = self.run_model([event("apply")])
        second = self.run_model([event("apply"), event("invalidate", STALE)])
        self.assertEqual(first["phase"], second["phase"])
        self.assertEqual(first["controls"], second["controls"])
        self.assertEqual(first["input_snapshot_sha256"], second["input_snapshot_sha256"])
        self.assertNotEqual(first["events_sha256"], second["events_sha256"])


if __name__ == "__main__":
    unittest.main()
