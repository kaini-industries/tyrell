"""Pure fixtures test guards without reading proc/sys or performing a write."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixtures import fixture_snapshot
import eligibility as policy

GENERATION = "a" * 32


class EligibilityTests(unittest.TestCase):
    def test_only_explicit_synthetic_snapshot_can_be_ready(self):
        snapshot = fixture_snapshot()
        self.assertEqual(policy.plan(snapshot, "combined", GENERATION)["status"], "ready")
        snapshot["scope"] = "unverified-observation"
        result = policy.plan(snapshot, "combined", GENERATION)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["actions"], [])
        self.assertIn("No qualified live observer", result["blockers"][0])

    def test_template_is_bounded_structural_data_and_always_blocked(self):
        template = policy.prepare_snapshot()
        self.assertIs(policy.validate_snapshot(template), template)
        for preset in policy.PRESETS:
            result = policy.plan(template, preset, GENERATION)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["actions"], [])
            self.assertFalse(result["live_authorized"])

    def test_four_presets_only_propose_owned_changes(self):
        counts = {"baseline": 0, "ethernet": 1, "cpu": 1, "combined": 2}
        for preset, count in counts.items():
            snapshot = fixture_snapshot()
            before = copy.deepcopy(snapshot)
            result = policy.plan(snapshot, preset, GENERATION)
            self.assertEqual(snapshot, before)
            self.assertEqual(result["status"], "ready")
            self.assertEqual(len(result["actions"]), count)
            self.assertFalse(result["hardware_qualified"])
            self.assertFalse(result["battery_qualified"])
            self.assertFalse(result["baseline_recovery_certified"])
            self.assertEqual(result["scope"], "offline-model-only")
            self.assertEqual(result, policy.plan(snapshot, preset, GENERATION))
            self.assertTrue(all(row["before"] != row["after"] for row in result["actions"]))
        result = policy.plan(fixture_snapshot(), "combined", GENERATION)
        self.assertEqual([row["control_id"] for row in result["actions"]], ["ethernet-control", "cpu-factor"])

    def test_kernel_image_contract_and_vendor_drift_block_before_any_intent(self):
        mutations = [lambda s: s["image"].update(kernel_release="new"),
                     lambda s: s["image"].update(raw_sha256="0" * 64),
                     lambda s: s["image"].update(contract_sha256="0" * 64),
                     lambda s: s["image"].update(bsp_commit="0" * 40),
                     lambda s: s["policy"]["vendor_hashes"].update({next(iter(policy.VENDOR_HASHES)): "0" * 64})]
        for mutation in mutations:
            snapshot = fixture_snapshot()
            mutation(snapshot)
            result = policy.plan(snapshot, "combined", GENERATION)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["actions"], [])

    def test_all_cpu_baseline_controls_and_policy_membership_required(self):
        mutations = [lambda s: s["cpu"].update(governor="schedutil"),
                     lambda s: s["cpu"].update(driver="unknown"),
                     lambda s: s["cpu"].update(min_khz=500000),
                     lambda s: s["cpu"].update(max_khz=900000),
                     lambda s: s["cpu"].update(related_cpus=[0, 1])]
        mutations += [lambda s, key=key: s["cpu"]["tunables"].update({key: "1"})
                      for key in policy.BASELINE_TUNABLES]
        for mutation in mutations:
            snapshot = fixture_snapshot()
            mutation(snapshot)
            self.assertEqual(policy.plan(snapshot, "cpu", GENERATION)["status"], "blocked")

    def test_existing_or_unknown_overrides_block(self):
        for key in ("administrator_override", "generated_override"):
            for state in ("present", "unknown"):
                snapshot = fixture_snapshot()
                snapshot["policy"][key] = state
                self.assertEqual(policy.plan(snapshot, "combined", GENERATION)["status"], "blocked")

    def test_board_unknown_controller_and_overlay_drift_block(self):
        for key, value in (("family", "generic-pi"), ("hardware_id", "unknown"), ("overlay", "cardputerzero-v6"),
                           ("controller_firmware", "unknown"), ("compatible", ["raspberrypi,4-model-b"])):
            snapshot = fixture_snapshot()
            snapshot["board"][key] = value
            self.assertEqual(policy.plan(snapshot, "ethernet", GENERATION)["status"], "blocked")

    def test_ids_alone_wrong_ports_drivers_and_mode_cannot_select_ethernet(self):
        mutations = [lambda s: s["usb"].update(mode="gadget"),
                     lambda s: s["usb"].update(controller_driver="xhci-hcd"),
                     lambda s: s["usb"]["nodes"][2].update(devpath="1.2"),
                     lambda s: s["usb"]["nodes"][2].update(driver="r8152"),
                     lambda s: s["usb"]["nodes"][3].update(driver="unknown"),
                     lambda s: s["usb"]["nodes"][1].update(control="on"),
                     lambda s: s["usb"]["nodes"][2].update(control="auto"),
                     lambda s: s["usb"]["nodes"][2].update(delay_ms="5000"),
                     lambda s: s["usb"]["nodes"][1].update(parent=s["usb"]["controller_path"])]
        for mutation in mutations:
            snapshot = fixture_snapshot()
            mutation(snapshot)
            self.assertEqual(policy.plan(snapshot, "ethernet", GENERATION)["status"], "blocked")

    def test_multiple_matching_adapters_and_missing_leaf_are_ambiguous(self):
        snapshot = fixture_snapshot()
        duplicate = dict(snapshot["usb"]["nodes"][2])
        duplicate["path"] += "-external"
        snapshot["usb"]["nodes"].append(duplicate)
        self.assertEqual(policy.plan(snapshot, "ethernet", GENERATION)["status"], "blocked")
        snapshot["usb"]["nodes"] = []
        self.assertEqual(policy.plan(snapshot, "ethernet", GENERATION)["status"], "blocked")

    def test_canonical_controller_ancestry_rejects_external_paths(self):
        snapshot = fixture_snapshot()
        snapshot["usb"]["nodes"][2]["path"] = "/sys/devices/platform/external/eth"
        self.assertEqual(policy.plan(snapshot, "ethernet", GENERATION)["status"], "blocked")

    def test_identity_excludes_only_owned_controls_and_not_parent_policies(self):
        before = fixture_snapshot()
        after = copy.deepcopy(before)
        after["cpu"]["tunables"]["sampling_down_factor"] = "1"
        after["usb"]["nodes"][2].update(control="auto", delay_ms="3000")
        self.assertEqual(policy.identity_digest(before), policy.identity_digest(after))
        self.assertNotEqual(policy.digest(before), policy.digest(after))
        values = policy.control_values(after)
        self.assertEqual(values[policy.CPU_FACTOR], "1")
        self.assertEqual(len(values), 3)
        after["usb"]["nodes"][1]["control"] = "on"
        self.assertNotEqual(policy.identity_digest(before), policy.identity_digest(after))

    def test_boot_administrator_and_topology_changes_invalidate_identity(self):
        for mutation in (lambda s: s.update(boot_id="c" * 32),
                         lambda s: s["policy"].update(administrator_override="present"),
                         lambda s: s["usb"]["nodes"][2].update(devpath="1.2")):
            before = fixture_snapshot()
            after = copy.deepcopy(before)
            mutation(after)
            self.assertNotEqual(policy.identity_digest(before), policy.identity_digest(after))

    def test_bound_types_unknown_fields_controls_and_paths_rejected(self):
        mutations = [lambda s: s.update(extra=True), lambda s: s.update(schema=True),
                     lambda s: s["cpu"].update(min_khz=True), lambda s: s["cpu"].update(related_cpus=[False]),
                     lambda s: s["board"].update(model="bad\x00value"),
                     lambda s: s["cpu"]["tunables"].update(sampling_down_factor=True),
                     lambda s: s["usb"]["nodes"][0].update(path="/sys/devices/../escape"),
                     lambda s: s["usb"]["nodes"][0].update(path="/sys/devices//escape"),
                     lambda s: s["usb"]["nodes"][0].update(path="/dev/mem"),
                     lambda s: s["usb"].update(nodes=s["usb"]["nodes"] * 5)]
        for mutation in mutations:
            snapshot = fixture_snapshot()
            mutation(snapshot)
            with self.assertRaises(ValueError):
                policy.validate_snapshot(snapshot)

    def test_invalid_presets_and_generations_never_emit_plans(self):
        for preset in ("schedutil", "../cpu", True, [], None):
            with self.assertRaises(ValueError):
                policy.plan(fixture_snapshot(), preset, GENERATION)
        for generation in ("b" * 31, "z" * 32, True):
            with self.assertRaises(ValueError):
                policy.plan(fixture_snapshot(), "cpu", generation)

    def test_pins_match_current_contract_and_foundation(self):
        root = Path(__file__).resolve().parents[3]
        contract = root / "image/contracts/tyrell-zero-power.json"
        self.assertEqual(hashlib.sha256(contract.read_bytes()).hexdigest(), policy.CONTRACT_SHA256)


if __name__ == "__main__":
    unittest.main()
