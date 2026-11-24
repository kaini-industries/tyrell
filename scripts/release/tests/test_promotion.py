"""Promotion cannot borrow source-only greens or historical device acceptance."""
import copy
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("release_promotion", Path(__file__).resolve().parents[1] / "promotion.py")
promotion = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(promotion)

SOURCE = "a" * 40
TOOLING = "b" * 40
IMAGE = "1" * 64
PACKAGE = "2" * 64
KNOWN = "3" * 64
RECOVERY = "4" * 64
QUALIFICATION = "5" * 64
EVIDENCE = "6" * 64
ARCHIVE = "f" * 64
REPOSITORY = "kaini-industries/tyrell"


def row(sha, gate, target="tyrell-zero"):
    return {"artifact_sha256": sha, "target": target, "gate": gate,
            "result": "not-tested", "evidence_scope": "not-tested", "evidence": [],
            "limitations": ["Physical acceptance remains pending for these exact bytes."]}


def fixture():
    manifest = {
        "schema": 1, "kind": "tyrell-release-candidate", "source_commit": SOURCE,
        "release_version": "0.1.0-dev.1", "target": "tyrell-zero", "optimized_power": False,
        "assets": [
            {"kind": "image", "path": "payload/system.img.xz", "sha256": IMAGE, "bytes": 100},
            {"kind": "app-deb", "path": "payload/app.deb", "sha256": PACKAGE, "bytes": 50,
             "package": "tyrell-chess", "version": "0.1.4-1", "architecture": "arm64"},
        ],
        "files": {
            "payload/system.img.xz": {"sha256": IMAGE, "bytes": 100, "role": "image"},
            "payload/app.deb": {"sha256": PACKAGE, "bytes": 50, "role": "app-deb"},
            "metadata/known-issues.md": {"sha256": KNOWN, "bytes": 20, "role": "known-issues"},
            "metadata/recovery.md": {"sha256": RECOVERY, "bytes": 20, "role": "recovery"},
            "metadata/qualification.json": {"sha256": QUALIFICATION, "bytes": 200, "role": "qualification"},
            "metadata/device.md": {"sha256": EVIDENCE, "bytes": 100, "role": "evidence"},
        },
        "qualification": {"schema": 1, "records": [row(IMAGE, "z4"), row(PACKAGE, "app-acceptance")]},
    }
    request = {
        "schema": 1, "kind": "tyrell-promotion-request", "candidate_sha256": "",
        "archive_sha256": ARCHIVE, "source_commit": SOURCE, "release_version": "0.1.0-dev.1",
        "target": "tyrell-zero", "level": "development", "optimized_power": False,
        "acknowledge_known_issues": True, "known_issues_sha256": KNOWN, "recovery_sha256": RECOVERY,
        "manual_base_selection": True, "base_image_sha256": IMAGE,
        "qualification_path": "release/qualifications/alpha.json", "qualification_sha256": QUALIFICATION,
    }
    checks = {
        "source_run": {"id": 100, "repository": REPOSITORY, "path": ".github/workflows/checks.yml",
                       "head_sha": SOURCE, "status": "completed", "conclusion": "success",
                       "jobs": [{"name": name, "status": "completed", "conclusion": "success"}
                                for name in sorted(promotion.REQUIRED_SOURCE_JOBS)]},
        "candidate_run": {"id": 200, "repository": REPOSITORY, "path": ".github/workflows/image-candidate.yml",
                          "head_sha": TOOLING, "status": "completed", "conclusion": "success",
                          "event": "workflow_dispatch"},
        "candidate_report": {"schema": 1, "kind": "tyrell-candidate-verification", "status": "passed",
                             "source_commit": SOURCE, "tooling_commit": TOOLING, "run_id": 200,
                             "candidate_sha256": "", "archive_sha256": ARCHIVE, "artifact_name": "exact-candidate"},
        "candidate_artifact": {"id": 300, "name": "exact-candidate", "expired": False,
                               "workflow_run": {"id": 200, "head_sha": TOOLING}},
    }
    existing = {"immutable_releases_enabled": True, "release_exists": False, "tag_exists": False,
                "package_versions": []}
    data = {"manifest": manifest, "request": request, "checks": checks, "existing": existing,
            "repository": REPOSITORY, "approved_qualification_sha256": QUALIFICATION}
    refresh(data)
    return data


def refresh(data):
    digest = promotion.manifest_sha256(data["manifest"])
    data["candidate_sha256"] = digest
    data["request"]["candidate_sha256"] = digest
    data["checks"]["candidate_report"]["candidate_sha256"] = digest


def hardware(data):
    data["request"]["level"] = "hardware-alpha"
    for item in data["manifest"]["qualification"]["records"]:
        item.update(result="passed", evidence_scope="device-tested",
                    evidence=[{"path": "metadata/device.md", "sha256": EVIDENCE}])
    refresh(data)


class PromotionTests(unittest.TestCase):
    def test_file_validated_development_keeps_missing_hardware_visible(self):
        result = promotion.evaluate(**fixture())
        self.assertEqual(result["status"], "approved")
        self.assertEqual(len(result["missing_hardware_gates"]), 2)
        self.assertFalse(result["hardware_supported"])
        self.assertFalse(result["optimized_power_qualified"])
        self.assertTrue(result["prerelease"])
        self.assertFalse(result["make_latest"])
        self.assertFalse(result["device_write"])
        self.assertEqual(result["source_run_id"], 100)
        self.assertEqual(result["tooling_commit"], TOOLING)

    def test_alpha_requires_exact_device_acceptance(self):
        data = fixture(); data["request"]["level"] = "hardware-alpha"
        with self.assertRaisesRegex(ValueError, "device acceptance"):
            promotion.evaluate(**data)
        hardware(data)
        result = promotion.evaluate(**data)
        self.assertTrue(result["hardware_supported"])
        self.assertEqual(result["missing_hardware_gates"], [])

    def test_source_only_or_historical_other_bytes_cannot_qualify_alpha(self):
        for scope, sha in (("automated-tested", IMAGE), ("source-inspected", IMAGE),
                           ("device-tested", "9" * 64)):
            data = fixture(); hardware(data)
            data["manifest"]["qualification"]["records"][0].update(evidence_scope=scope, artifact_sha256=sha)
            refresh(data)
            with self.subTest(scope=scope, sha=sha), self.assertRaises(ValueError):
                promotion.evaluate(**data)

    def test_hidden_missing_qualification_rows_are_rejected_even_for_development(self):
        data = fixture(); data["manifest"]["qualification"]["records"] = []; refresh(data)
        with self.assertRaisesRegex(ValueError, "hides a required"):
            promotion.evaluate(**data)

    def test_failed_and_not_tested_results_cannot_pass_alpha(self):
        for value in ("failed", "not-tested"):
            data = fixture(); hardware(data)
            item = data["manifest"]["qualification"]["records"][0]
            item["result"] = value
            if value == "not-tested": item.update(evidence_scope="not-tested", evidence=[])
            refresh(data)
            with self.subTest(value=value), self.assertRaises(ValueError):
                promotion.evaluate(**data)

    def test_gh0st_app_needs_original_m5_regression_too(self):
        data = fixture(); hardware(data)
        data["manifest"]["target"] = data["request"]["target"] = "gh0st"
        for item in data["manifest"]["qualification"]["records"]: item["target"] = "gh0st"
        refresh(data)
        with self.assertRaisesRegex(ValueError, "original-m5"):
            promotion.evaluate(**data)
        additional = row(PACKAGE, "app-acceptance", "original-m5")
        data["manifest"]["qualification"]["records"].append(additional); hardware(data)
        self.assertTrue(promotion.evaluate(**data)["hardware_supported"])

    def test_optimized_power_needs_alpha_image_and_z6_acceptance(self):
        data = fixture(); data["manifest"]["optimized_power"] = data["request"]["optimized_power"] = True; refresh(data)
        with self.assertRaisesRegex(ValueError, "hardware-qualified image alpha"):
            promotion.evaluate(**data)
        hardware(data)
        with self.assertRaisesRegex(ValueError, "z6"):
            promotion.evaluate(**data)
        data["manifest"]["qualification"]["records"].append(row(IMAGE, "z6")); refresh(data)
        with self.assertRaisesRegex(ValueError, "device acceptance"):
            promotion.evaluate(**data)
        hardware(data)
        self.assertTrue(promotion.evaluate(**data)["optimized_power_qualified"])

    def test_manifest_digest_must_match_verified_bytes(self):
        data = fixture(); data["manifest"]["release_version"] = "changed"
        with self.assertRaisesRegex(ValueError, "digest differs"):
            promotion.evaluate(**data)

    def test_request_identity_and_acknowledged_documents_cannot_drift(self):
        changes = {"candidate_sha256": "0" * 64, "source_commit": "0" * 40,
                   "release_version": "another", "target": "gh0st", "optimized_power": True,
                   "known_issues_sha256": "0" * 64, "recovery_sha256": "0" * 64,
                   "qualification_sha256": "0" * 64, "acknowledge_known_issues": False}
        for key, value in changes.items():
            data = fixture(); data["request"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): promotion.evaluate(**data)

    def test_unknown_request_claims_and_boolean_schema_rejected(self):
        for mutation in ({"schema": True}, {"unreviewed_claim": True}, {"level": "stable"}):
            data = fixture(); data["request"].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): promotion.evaluate(**data)

    def test_manual_base_selection_must_bind_exact_image(self):
        for field, value in (("manual_base_selection", False), ("base_image_sha256", "0" * 64)):
            data = fixture(); data["request"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "manual selection"):
                promotion.evaluate(**data)

    def test_app_only_candidate_cannot_imply_base_selection_or_power_claim(self):
        data = fixture(); data["manifest"]["assets"] = [data["manifest"]["assets"][1]]
        data["request"].update(manual_base_selection=False, base_image_sha256=None); refresh(data)
        self.assertEqual(len(promotion.evaluate(**data)["missing_hardware_gates"]), 1)
        data["manifest"]["optimized_power"] = data["request"]["optimized_power"] = True; hardware(data)
        with self.assertRaisesRegex(ValueError, "image alpha"):
            promotion.evaluate(**data)

    def test_qualification_requires_independent_reviewed_digest(self):
        data = fixture(); data["approved_qualification_sha256"] = None
        with self.assertRaisesRegex(ValueError, "reviewed checkout"):
            promotion.evaluate(**data)

    def test_reviewed_qualification_path_cannot_escape_checkout(self):
        for path in ("../outside", "/tmp/receipt", "review/../receipt", "review\\receipt", "review//receipt"):
            data = fixture(); data["request"]["qualification_path"] = path
            with self.subTest(path=path), self.assertRaises(ValueError): promotion.evaluate(**data)

    def test_qualification_evidence_must_be_bound_in_candidate(self):
        data = fixture(); hardware(data)
        data["manifest"]["qualification"]["records"][0]["evidence"][0]["sha256"] = "0" * 64; refresh(data)
        with self.assertRaisesRegex(ValueError, "not bound"):
            promotion.evaluate(**data)

    def test_ambiguous_or_unsupported_qualification_is_rejected(self):
        for mutation in ("duplicate", "no-evidence", "no-limits", "unknown-gate", "not-tested-device"):
            data = fixture(); hardware(data)
            rows = data["manifest"]["qualification"]["records"]
            if mutation == "duplicate": rows.append(copy.deepcopy(rows[0]))
            if mutation == "no-evidence": rows[0]["evidence"] = []
            if mutation == "no-limits": rows[0]["limitations"] = []
            if mutation == "unknown-gate": rows[0]["gate"] = "source-inspected"
            if mutation == "not-tested-device": rows[0]["result"] = "not-tested"
            refresh(data)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): promotion.evaluate(**data)

    def test_exact_source_workflow_repository_path_and_success_are_mandatory(self):
        changes = {"repository": "elsewhere/repo", "path": ".github/workflows/untrusted.yml",
                   "head_sha": "0" * 40, "status": "in_progress", "conclusion": "failure", "id": True}
        for key, value in changes.items():
            data = fixture(); data["checks"]["source_run"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): promotion.evaluate(**data)

    def test_full_source_suite_inventory_and_actual_execution_are_mandatory(self):
        for mutation in ("missing", "skipped", "failure", "duplicate"):
            data = fixture(); jobs = data["checks"]["source_run"]["jobs"]
            if mutation == "missing": jobs.pop()
            if mutation == "skipped": jobs[0]["conclusion"] = "skipped"
            if mutation == "failure": jobs[0]["conclusion"] = "failure"
            if mutation == "duplicate": jobs.append(copy.deepcopy(jobs[0]))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): promotion.evaluate(**data)

    def test_source_green_alone_or_input_preflight_is_not_candidate_verification(self):
        for mutation in (None, {"schema": 1, "status": "passed", "promotable": False,
                                "kind": "captured-input-preflight-only"}):
            data = fixture(); data["checks"]["candidate_report"] = mutation
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): promotion.evaluate(**data)

    def test_exact_candidate_report_source_archive_tooling_and_run_are_required(self):
        changes = {"candidate_sha256": "0" * 64, "archive_sha256": "0" * 64,
                   "source_commit": "0" * 40, "tooling_commit": "0" * 40, "run_id": True,
                   "artifact_name": "another", "status": "failed", "schema": True}
        for key, value in changes.items():
            data = fixture(); data["checks"]["candidate_report"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): promotion.evaluate(**data)

    def test_candidate_tooling_may_differ_from_package_source(self):
        data = fixture()
        self.assertNotEqual(SOURCE, TOOLING)
        self.assertEqual(promotion.evaluate(**data)["tooling_commit"], TOOLING)
        data["checks"]["candidate_run"]["event"] = "push"
        with self.assertRaisesRegex(ValueError, "explicitly dispatched"):
            promotion.evaluate(**data)

    def test_candidate_artifact_must_be_live_and_from_verified_run(self):
        mutations = ({"id": True}, {"expired": True}, {"name": "other"},
                     {"workflow_run": {"id": 201, "head_sha": TOOLING}},
                     {"workflow_run": {"id": 200, "head_sha": SOURCE}})
        for mutation in mutations:
            data = fixture(); data["checks"]["candidate_artifact"].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): promotion.evaluate(**data)

    def test_repository_immutability_and_new_tag_are_publication_prerequisites(self):
        for key, value in (("immutable_releases_enabled", False), ("immutable_releases_enabled", None),
                           ("release_exists", True), ("tag_exists", True)):
            data = fixture(); data["existing"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): promotion.evaluate(**data)

    def test_package_version_cannot_be_reused_for_changed_bytes(self):
        data = fixture()
        item = {"package": "tyrell-chess", "version": "0.1.4-1", "architecture": "arm64", "sha256": "9" * 64}
        data["existing"]["package_versions"] = [item]
        with self.assertRaisesRegex(ValueError, "already accepted with different bytes"):
            promotion.evaluate(**data)
        item["sha256"] = PACKAGE
        self.assertEqual(promotion.evaluate(**data)["status"], "approved")

    def test_conflicting_registry_or_duplicate_candidate_packages_rejected(self):
        data = fixture()
        package = copy.deepcopy(data["manifest"]["assets"][1]); package["sha256"] = PACKAGE
        registered = {key: package[key] for key in ("package", "version", "architecture", "sha256")}
        other = copy.deepcopy(registered); other["sha256"] = "9" * 64
        data["existing"]["package_versions"] = [registered, other]
        with self.assertRaisesRegex(ValueError, "conflicting package bytes"):
            promotion.evaluate(**data)
        data["existing"]["package_versions"] = []
        data["manifest"]["assets"].append(package); refresh(data)
        with self.assertRaisesRegex(ValueError, "ambiguous candidate package"):
            promotion.evaluate(**data)

    def test_moving_aliases_and_unsafe_tags_rejected(self):
        for version in ("latest", "Stable", "main", "../release", "v1/asset", "a" * 97, ""):
            with self.subTest(version=version), self.assertRaises(ValueError): promotion.tag_name(version)
        self.assertEqual(promotion.tag_name("0.2.0-alpha.1"), "candidate-0.2.0-alpha.1")


if __name__ == "__main__":
    unittest.main()
