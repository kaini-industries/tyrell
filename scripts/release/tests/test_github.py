"""Live-metadata adapter regressions, with all GitHub and download I/O mocked."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import github

REPO = "kaini-industries/tyrell"
SOURCE = "a" * 40
TOOLING = "b" * 40


def canonical(data):
    return (json.dumps(data, sort_keys=True, indent=2) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run_metadata():
    return {"id": 101, "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
            "path": ".github/workflows/checks.yml", "head_sha": SOURCE,
            "event": "push", "status": "completed", "conclusion": "success"}


def job_metadata():
    return [{"name": name, "status": "completed", "conclusion": "success"}
            for name in sorted(github.JOBS)]


class GithubTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_authenticates_complete_nonfork_source_workflow(self):
        with patch.object(github, "api", return_value=run_metadata()), \
                patch.object(github, "pages", return_value=job_metadata()):
            result = github.run(REPO, 101, ".github/workflows/checks.yml", SOURCE)
        self.assertEqual(result["head_sha"], SOURCE)
        self.assertEqual({job["name"] for job in result["jobs"]}, github.JOBS)

    def test_wrong_repository_fork_workflow_source_or_failed_run_rejected(self):
        mutations = ({"repository": {"full_name": "other/repo"}},
                     {"head_repository": {"full_name": "fork/tyrell"}},
                     {"head_sha": TOOLING}, {"path": ".github/workflows/fake.yml"},
                     {"conclusion": "failure"}, {"status": "in_progress"},
                     {"id": 102}, {"event": "pull_request_target"})
        for mutation in mutations:
            data = run_metadata(); data.update(mutation)
            with self.subTest(mutation=mutation), patch.object(github, "api", return_value=data), \
                    patch.object(github, "pages", return_value=job_metadata()), self.assertRaises(ValueError):
                github.run(REPO, 101, ".github/workflows/checks.yml", SOURCE)

    def test_source_gate_requires_all_suites_and_successful_unique_jobs(self):
        for mutation in ("missing", "skipped", "failed", "duplicate", "unexpected"):
            jobs = job_metadata()
            if mutation == "missing": jobs.pop()
            if mutation == "skipped": jobs[0]["conclusion"] = "skipped"
            if mutation == "failed": jobs[0]["conclusion"] = "failure"
            if mutation == "duplicate": jobs.append(copy.deepcopy(jobs[0]))
            if mutation == "unexpected": jobs.append({"name": "untrusted", "status": "completed", "conclusion": "success"})
            with self.subTest(mutation=mutation), patch.object(github, "api", return_value=run_metadata()), \
                    patch.object(github, "pages", return_value=jobs), self.assertRaises(ValueError):
                github.run(REPO, 101, ".github/workflows/checks.yml", SOURCE)

    def test_retained_artifact_must_be_unique_live_and_from_selected_run_head(self):
        item = {"id": 303, "name": "selected", "expired": False,
                "workflow_run": {"id": 101, "head_sha": SOURCE}}
        with patch.object(github, "pages", return_value=[item]):
            self.assertEqual(github.artifact(REPO, 101, "selected", SOURCE)["id"], 303)
        mutations = ([dict(item, expired=True)], [dict(item, workflow_run={"id": 102, "head_sha": SOURCE})],
                     [dict(item, workflow_run={"id": 101, "head_sha": TOOLING})], [item, copy.deepcopy(item)], [])
        for items in mutations:
            with self.subTest(items=items), patch.object(github, "pages", return_value=items), self.assertRaises(ValueError):
                github.artifact(REPO, 101, "selected", SOURCE)

    def selection_fixture(self):
        inputs = self.root / "inputs"; inputs.mkdir()
        hosted = {}
        checks = []
        for suite in github.SUITES:
            data = {"schema": 1, "suite": suite, "status": "passed", "scope": "source-checks-only",
                    "source": {"commit": SOURCE, "dirty": False, "worktree_sha256": "1" * 64}}
            hosted[suite] = canonical(data)
            name = "checks/" + suite + ".json"
            path = inputs / name; path.parent.mkdir(exist_ok=True); path.write_bytes(hosted[suite])
            checks.append({"path": name, "sha256": digest(hosted[suite])})
        selection = {"source_commit": SOURCE, "checks": checks}
        self.write_selection(inputs, selection)
        return inputs, hosted, selection

    def write_selection(self, inputs, selection):
        path = inputs / "release-selection.json"; path.write_bytes(canonical(selection))
        return digest(path.read_bytes())

    def authenticate(self, inputs, hosted, selection):
        def download(*args, **kwargs):
            self.assertEqual(args[:2], ("run", "download"))
            name = args[args.index("--name") + 1]
            suite = next(suite for suite in github.SUITES if name == f"checks-{suite}-{SOURCE}")
            destination = Path(args[args.index("--dir") + 1]); destination.mkdir(parents=True)
            (destination / (suite + ".json")).write_bytes(hosted[suite])
            return ""
        with patch.object(github, "run", return_value={"id": 101, "head_sha": SOURCE}), \
                patch.object(github, "artifact", return_value={"id": 303}), \
                patch.object(github, "gh", side_effect=download):
            return github.authenticate_selection(REPO, 101, SOURCE, inputs,
                self.write_selection(inputs, selection), download_root=self.root / "download")

    def test_selection_authenticates_all_exact_hosted_report_bytes(self):
        inputs, hosted, selection = self.selection_fixture()
        result = self.authenticate(inputs, hosted, selection)
        self.assertEqual(set(result["report_sha256"]), set(github.SUITES))
        self.assertEqual(result["report_sha256"], {suite: digest(body) for suite, body in hosted.items()})

    def test_self_authored_report_with_green_status_is_not_hosted_report(self):
        inputs, hosted, selection = self.selection_fixture()
        path = inputs / selection["checks"][0]["path"]
        counterfeit = json.loads(path.read_bytes()); counterfeit["source"]["worktree_sha256"] = "9" * 64
        path.write_bytes(canonical(counterfeit)); selection["checks"][0]["sha256"] = digest(path.read_bytes())
        with self.assertRaisesRegex(ValueError, "authenticated hosted report"):
            self.authenticate(inputs, hosted, selection)

    def test_hosted_reports_must_describe_one_clean_exact_source_snapshot(self):
        for mutation in ({"commit": TOOLING}, {"dirty": True}, {"worktree_sha256": "9" * 64},
                         {"worktree_sha256": None}):
            case = self.root / str(len(list(self.root.iterdir()))); case.mkdir()
            old_root = self.root; self.root = case
            try:
                inputs, hosted, selection = self.selection_fixture()
                suite = github.SUITES[-1]; report = json.loads(hosted[suite]); report["source"].update(mutation)
                hosted[suite] = canonical(report)
                (inputs / selection["checks"][-1]["path"]).write_bytes(hosted[suite])
                selection["checks"][-1]["sha256"] = digest(hosted[suite])
                with self.subTest(mutation=mutation), self.assertRaises(ValueError): self.authenticate(inputs, hosted, selection)
            finally:
                self.root = old_root

    def test_selected_report_inventory_must_include_every_suite_once(self):
        for mutation in ("missing", "duplicate"):
            case = self.root / mutation; case.mkdir(); old_root = self.root; self.root = case
            try:
                inputs, hosted, selection = self.selection_fixture()
                if mutation == "missing": selection["checks"].pop()
                else: selection["checks"].append(copy.deepcopy(selection["checks"][0]))
                with self.subTest(mutation=mutation), self.assertRaises(ValueError): self.authenticate(inputs, hosted, selection)
            finally:
                self.root = old_root

    def test_reviewed_local_data_rejects_duplicate_keys_traversal_and_symlinks(self):
        (self.root / "duplicate.json").write_text('{"source":"reviewed","source":"changed"}')
        with self.assertRaisesRegex(ValueError, "Duplicate JSON"):
            github.read(self.root / "duplicate.json")
        (self.root / "receipt.json").write_text("{}")
        (self.root / "redirect.json").symlink_to(self.root / "receipt.json")
        for name in ("../receipt.json", "/receipt.json", "redirect.json", "a//b"):
            with self.subTest(name=name), self.assertRaises(ValueError): github.local(self.root, name)

    def test_pagination_keeps_all_registry_pages_and_rejects_malformed_list(self):
        with patch.object(github, "api", side_effect=[[{"id": i} for i in range(100)], [{"id": 100}]]):
            self.assertEqual(len(github.pages("repos/repo/releases")), 101)
        with patch.object(github, "api", return_value={"not": "a listing"}), self.assertRaises(ValueError):
            github.pages("repos/repo/releases")

    def test_existing_registry_combines_retained_and_published_package_bytes(self):
        retained = self.root / "dist/app/1.0/tyrell-fixture_1.0-1_arm64.deb"
        retained.parent.mkdir(parents=True); retained.write_bytes(b"retained")
        releases = [{"tag_name": "candidate-first", "draft": False, "immutable": True,
                     "assets": [{"name": "release-manifest.json", "size": 200, "id": 500},
                                {"name": "other-app_2.0-1_arm64.deb", "digest": "sha256:" + "2" * 64}]}]
        manifest = {"assets": [{"kind": "app-deb", "package": "new-app", "version": "3.0-1",
                                 "architecture": "arm64", "sha256": "3" * 64}]}
        def listing(path):
            return [{"name": "candidate-first"}] if path.endswith("/tags") else releases
        with patch.object(github, "api", return_value={"enabled": True}), \
                patch.object(github, "pages", side_effect=listing), \
                patch.object(github, "gh", return_value=json.dumps(manifest)):
            result = github.existing(REPO, "candidate-first", self.root)
        self.assertTrue(result["release_exists"]); self.assertTrue(result["tag_exists"])
        self.assertEqual({item["package"] for item in result["package_versions"]}, {"tyrell-fixture", "other-app", "new-app"})
        self.assertEqual(next(item["sha256"] for item in result["package_versions"] if item["package"] == "tyrell-fixture"), digest(b"retained"))

    def test_existing_unprotected_releases_or_ambiguous_legacy_debs_block_promotion(self):
        cases = [{"tag_name": "candidate-old", "draft": False, "immutable": False, "assets": []},
                 {"tag_name": "old", "assets": [{"name": "app_1_arm64.deb", "digest": None}]}]
        for release in cases:
            def listing(path): return [] if path.endswith("/tags") else [release]
            with self.subTest(release=release), patch.object(github, "api", return_value={"enabled": True}), \
                    patch.object(github, "pages", side_effect=listing), self.assertRaises(ValueError):
                github.existing(REPO, "candidate-new", self.root)

    def test_prepare_rejects_unreviewed_request_and_receipt_before_downloading(self):
        request_path = self.root / "promotion.json"
        request = {"candidate_sha256": "1" * 64, "archive_sha256": "2" * 64,
                   "qualification_path": "receipt.json", "qualification_sha256": "3" * 64}
        request_path.write_bytes(canonical(request))
        with patch.object(github, "gh") as network, self.assertRaisesRegex(ValueError, "request checksum"):
            github.prepare(REPO, 101, "1" * 64, "2" * 64, "promotion.json", "0" * 64, self.root / "out", root=self.root)
        network.assert_not_called()
        (self.root / "receipt.json").write_text("{}")
        with patch.object(github, "gh") as network, self.assertRaisesRegex(ValueError, "receipt checksum"):
            github.prepare(REPO, 101, "1" * 64, "2" * 64, "promotion.json", digest(request_path.read_bytes()), self.root / "out", root=self.root)
        network.assert_not_called()

    def prepare_fixture(self):
        import candidate
        import promotion
        spec = importlib.util.spec_from_file_location("github_candidate_fixture", Path(__file__).with_name("test_candidate.py"))
        fixtures = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixtures)
        fixture = fixtures.CandidateTests("test_deterministic_candidate_and_safe_roundtrip")
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        assembly = fixture.assemble(); manifest = candidate.verify_directory(assembly["directory"], assembly["candidate_id"])
        docs = {item["role"]: item["sha256"] for item in manifest["files"].values()}
        qual = next(path for path, item in manifest["files"].items() if item["role"] == "qualification")
        reviewed = self.root / "receipts/reviewed.json"; reviewed.parent.mkdir()
        reviewed.write_bytes((Path(assembly["directory"]) / qual).read_bytes())
        request = {"schema": 1, "kind": "tyrell-promotion-request", "candidate_sha256": assembly["candidate_id"],
                   "archive_sha256": assembly["archive_sha256"], "source_commit": fixture.revision,
                   "release_version": manifest["release_version"], "target": manifest["target"],
                   "level": "development", "optimized_power": False, "acknowledge_known_issues": True,
                   "known_issues_sha256": docs["known-issues"], "recovery_sha256": docs["recovery"],
                   "qualification_path": "receipts/reviewed.json", "qualification_sha256": docs["qualification"],
                   "manual_base_selection": False, "base_image_sha256": None}
        request_path = self.root / "request.json"; request_path.write_bytes(canonical(request))
        report = {"schema": 1, "kind": "tyrell-candidate-verification", "status": "passed",
                  "candidate_sha256": assembly["candidate_id"], "archive_sha256": assembly["archive_sha256"],
                  "source_commit": fixture.revision, "tooling_commit": TOOLING,
                  "run_id": 202, "source_run_id": 101, "artifact_name": "release-candidate-202"}
        artifact = {"id": 303, "name": "release-candidate-202", "expired": False,
                    "workflow_run": {"id": 202, "head_sha": TOOLING}}
        def runs(repo, run_id, path, source=None):
            self.assertEqual(repo, REPO)
            if path == ".github/workflows/image-candidate.yml":
                self.assertEqual(run_id, 202)
                return {"id": 202, "repository": REPO, "path": path, "head_sha": TOOLING,
                        "status": "completed", "conclusion": "success", "event": "workflow_dispatch"}
            self.assertEqual(run_id, 101); self.assertEqual(source, fixture.revision)
            return {"id": 101, "repository": REPO, "path": path, "head_sha": fixture.revision,
                    "status": "completed", "conclusion": "success", "jobs": job_metadata()}
        def download(*args, **kwargs):
            self.assertEqual(args[:2], ("run", "download"))
            destination = Path(args[args.index("--dir") + 1]); destination.mkdir(parents=True)
            shutil.copyfile(assembly["archive"], destination / Path(assembly["archive"]).name)
            (destination / "candidate-verification.json").write_bytes(canonical(report))
        state = {"immutable_releases_enabled": True, "release_exists": False, "tag_exists": False, "package_versions": []}
        return fixture, assembly, request_path, runs, download, artifact, state

    def test_prepare_uses_reviewed_receipt_and_both_trusted_ancestors_before_approval(self):
        fixture, assembly, request, runs, download, retained, state = self.prepare_fixture()
        with patch.object(github, "api", return_value={"default_branch": "main"}), \
                patch.object(github, "run", side_effect=runs), patch.object(github, "ancestor") as ancestry, \
                patch.object(github, "artifact", return_value=retained), patch.object(github, "gh", side_effect=download), \
                patch.object(github, "existing", return_value=state), \
                patch.object(github.subprocess, "check_output", return_value=TOOLING + "\n"):
            result = github.prepare(REPO, 202, assembly["candidate_id"], assembly["archive_sha256"],
                                    "request.json", digest(request.read_bytes()), self.root / "prepared", root=self.root)
        self.assertEqual(result["approval"]["status"], "approved")
        self.assertFalse(result["approval"]["hardware_supported"])
        self.assertEqual(result["approval"]["request_sha256"], digest(request.read_bytes()))
        self.assertEqual([call.args[1:] for call in ancestry.call_args_list],
                         [(TOOLING, "refs/remotes/origin/main"), (fixture.revision, "refs/remotes/origin/main")])

    def test_untrusted_candidate_tooling_or_source_ancestor_blocks_download(self):
        fixture, assembly, request, runs, download, retained, state = self.prepare_fixture()
        with patch.object(github, "api", return_value={"default_branch": "main"}), \
                patch.object(github, "run", side_effect=runs), \
                patch.object(github, "ancestor", side_effect=ValueError("Untrusted ancestor")), \
                patch.object(github, "gh") as network, self.assertRaisesRegex(ValueError, "Untrusted ancestor"):
            github.prepare(REPO, 202, assembly["candidate_id"], assembly["archive_sha256"],
                           "request.json", digest(request.read_bytes()), self.root / "prepared", root=self.root)
        network.assert_not_called()

    def test_publication_cli_always_reauthenticates_reviewed_promotion(self):
        import publish
        events = []; report = self.root / "result.json"
        args = ["github.py", "publish-reviewed", "--repository", REPO, "--run-id", "202",
                "--candidate-id", "1" * 64, "--archive-sha256", "2" * 64,
                "--request", "request.json", "--request-sha256", "3" * 64,
                "--output", str(self.root / "prepare"), "--publication-output", str(self.root / "assets"),
                "--report", str(report)]
        def prepare(*args, **kwargs):
            events.append("live-prepare")
            return {"approval": {"status": "approved"}, "directory": "verified", "archive": "verified.tar"}
        def prepared(*args, **kwargs): events.append("split-verified"); return {"asset": {"sha256": "4" * 64}}
        def published(*args, **kwargs): events.append("publish"); return {"status": "published"}
        with patch.object(sys, "argv", args), patch.object(github, "prepare", side_effect=prepare) as authentication, \
                patch.object(publish, "publication_files", side_effect=prepared), patch.object(publish, "publish", side_effect=published):
            github.main()
        self.assertEqual(events, ["live-prepare", "split-verified", "publish"])
        self.assertEqual(authentication.call_args.args[4:6], ("request.json", "3" * 64))
        self.assertEqual(github.read(report)["status"], "published")


if __name__ == "__main__":
    unittest.main()
