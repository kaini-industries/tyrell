"""Byte-preserving packaging and draft-before-publish outcomes with mocked APIs."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import candidate
import github
import promotion
import publish

SPEC = importlib.util.spec_from_file_location("publication_candidate_fixture", Path(__file__).with_name("test_candidate.py"))
fixtures = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(fixtures)
REPO = "kaini-industries/tyrell"


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CandidateTests("test_deterministic_candidate_and_safe_roundtrip")
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.result = self.fixture.assemble()
        self.archive = Path(self.result["archive"]); self.directory = Path(self.result["directory"])
        self.output = self.fixture.root / "publication"
        self.manifest = candidate.verify_directory(self.directory, self.result["candidate_id"])
        self.approval = {"schema": 1, "status": "approved", "repository": REPO,
                         "candidate_sha256": self.result["candidate_id"], "archive_sha256": self.result["archive_sha256"],
                         "source_commit": self.fixture.revision, "tag_name": promotion.tag_name(self.manifest["release_version"]),
                         "level": "development", "prerelease": True, "make_latest": False}
        self.state = {"immutable_releases_enabled": True, "release_exists": False,
                      "tag_exists": False, "package_versions": []}

    def files(self):
        return publish.publication_files(self.archive, self.directory, self.approval, self.output, part_bytes=97)

    def test_multipart_download_reassembles_exact_candidate_and_hashes(self):
        files = self.files()
        index = github.read(self.output / "release-index.json")
        reassembled = bytearray(); offset = 0
        for item in index["parts"]:
            data = (self.output / item["path"]).read_bytes()
            self.assertEqual(item["offset"], offset); self.assertEqual(item["bytes"], len(data))
            self.assertLessEqual(len(data), 97); self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])
            offset += len(data); reassembled.extend(data)
        self.assertEqual(bytes(reassembled), self.archive.read_bytes())
        self.assertEqual(hashlib.sha256(reassembled).hexdigest(), self.approval["archive_sha256"])
        for name, identity in files.items():
            self.assertEqual(github.sha(self.output / name), identity["sha256"])
            self.assertEqual((self.output / name).stat().st_size, identity["bytes"])
        self.assertEqual(json.loads((self.output / "promotion.json").read_text()), self.approval)
        self.assertEqual((self.output / "release-manifest.json").read_bytes(), (self.directory / "release-manifest.json").read_bytes())

    def test_archive_corruption_is_rejected_before_publication_directory_exists(self):
        data = bytearray(self.archive.read_bytes()); data[-1] ^= 1; self.archive.write_bytes(data)
        with self.assertRaises(ValueError): self.files()
        self.assertFalse(self.output.exists())

    def test_changed_archive_during_split_never_yields_publication_assets(self):
        actual_sha = github.sha; changed = False
        def racing_sha(path):
            nonlocal changed
            checksum = actual_sha(path)
            if Path(path) == self.archive and not changed:
                body = bytearray(self.archive.read_bytes()); body[-1] ^= 1
                self.archive.write_bytes(body); changed = True
            return checksum
        with patch.object(publish.github, "sha", side_effect=racing_sha), self.assertRaises(ValueError):
            self.files()

    def test_part_limits_and_existing_outputs_are_preserved(self):
        for limit in (0, publish.PART_BYTES + 1):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                publish.publication_files(self.archive, self.directory, self.approval, self.output, part_bytes=limit)
        self.files(); prior = (self.output / "release-index.json").read_bytes()
        with self.assertRaises(FileExistsError): self.files()
        self.assertEqual((self.output / "release-index.json").read_bytes(), prior)

    def network(self, files, *, upload_digest="correct", listed_digest="correct", enabled=True,
                reserved_tag_sha=None):
        """Provide finite server state; no test invokes a real gh process."""
        events = []; uploads = []
        prefix = f"https://uploads.github.com/repos/{REPO}/releases/700/assets"
        remote = [{"name": name, "digest": "sha256:" + identity["sha256"], "size": identity["bytes"]}
                  for name, identity in files.items()]
        if listed_digest == "wrong": remote[0]["digest"] = "sha256:" + "0" * 64
        if listed_digest == "missing": remote[0].pop("digest")
        if listed_digest == "extra": remote.append({"name": "extra", "digest": "sha256:" + "0" * 64, "size": 1})
        if listed_digest == "absent": remote.pop()
        def api(path, method="GET", payload=None):
            events.append(("api", method, path, payload))
            if path == f"repos/{REPO}/git/refs" and method == "POST":
                return {"ref": "refs/tags/" + self.approval["tag_name"],
                        "object": {"sha": self.fixture.revision, "type": "commit"}}
            if path == f"repos/{REPO}/releases" and method == "POST":
                return {"id": 700, "draft": True, "upload_url": prefix + "{?name,label}"}
            if path == f"repos/{REPO}/immutable-releases": return {"enabled": enabled}
            if path == f"repos/{REPO}/releases/700" and method == "PATCH":
                return {"immutable": True, "draft": False, "prerelease": True, "html_url": "https://example.invalid/release"}
            if path.startswith(f"repos/{REPO}/git/ref/tags/"):
                return {"object": {"sha": reserved_tag_sha or self.fixture.revision, "type": "commit"}}
            raise AssertionError("Unexpected API request: " + path)
        def upload(*args, **kwargs):
            self.assertEqual(args[:3], ("api", "--method", "POST"))
            path = Path(args[args.index("--input") + 1]); name = path.name
            self.assertIn(name, files); uploads.append(name); events.append(("upload", name))
            result = {"digest": "sha256:" + files[name]["sha256"], "size": files[name]["bytes"]}
            if upload_digest == "wrong": result["digest"] = "sha256:" + "0" * 64
            if upload_digest == "missing": result.pop("digest")
            return json.dumps(result)
        def listing(path):
            events.append(("list", path)); return remote
        return events, uploads, api, upload, listing

    def call_publish(self, files, **options):
        events, uploads, api, upload, listing = self.network(files, **options)
        self.events = events
        with patch.object(publish.github, "existing", return_value=self.state), \
                patch.object(publish.github, "api", side_effect=api), \
                patch.object(publish.github, "gh", side_effect=upload), \
                patch.object(publish.github, "pages", side_effect=listing):
            result = publish.publish(REPO, self.approval, files, self.output,
                                     receipt_path=self.fixture.root / "publication-progress.json")
        return result, events, uploads

    def patches(self):
        return [event for event in self.events if event[0] == "api" and event[1] == "PATCH"]

    def test_draft_upload_all_verified_server_assets_before_immutable_prerelease(self):
        files = self.files(); result, events, uploads = self.call_publish(files)
        self.assertEqual(result["status"], "published"); self.assertTrue(result["immutable"])
        self.assertFalse(result["latest_requested"]); self.assertEqual(set(uploads), set(files))
        create = next(event for event in events if event[0] == "api" and event[1] == "POST"
                      and event[2] == f"repos/{REPO}/releases")
        self.assertTrue(create[3]["draft"]); self.assertTrue(create[3]["prerelease"])
        self.assertEqual(create[3]["make_latest"], "false")
        final = self.patches(); self.assertEqual(len(final), 1)
        self.assertEqual(final[0][3], {"draft": False, "prerelease": True, "make_latest": "false"})
        publish_at = events.index(final[0]); last_upload = max(i for i, event in enumerate(events) if event[0] == "upload")
        asset_list = next(i for i, event in enumerate(events) if event[0] == "list")
        self.assertLess(last_upload, asset_list); self.assertLess(asset_list, publish_at)

    def test_wrong_or_missing_upload_digest_preserves_draft_without_publish_patch(self):
        for value in ("wrong", "missing"):
            with self.subTest(value=value):
                files = self.files() if not self.output.exists() else self.prepared_files
                self.prepared_files = files
                with self.assertRaises(ValueError): self.call_publish(files, upload_digest=value)
                self.assertEqual(self.patches(), [])
                progress = github.read(self.fixture.root / "publication-progress.json")
                self.assertEqual(progress["status"], "draft-created")
                self.assertEqual(progress["release_id"], 700)

    def test_remote_missing_extra_or_corrupt_assets_prevent_publish_patch(self):
        files = self.files()
        for value in ("wrong", "missing", "extra", "absent"):
            with self.subTest(value=value), self.assertRaises(ValueError): self.call_publish(files, listed_digest=value)
            self.assertEqual(self.patches(), [])

    def test_immutability_disabled_at_final_boundary_prevents_publish_patch(self):
        files = self.files()
        with self.assertRaisesRegex(ValueError, "no longer enabled"): self.call_publish(files, enabled=False)
        self.assertEqual(self.patches(), [])

    def test_reserved_tag_changed_before_final_boundary_prevents_publish_patch(self):
        files = self.files()
        with self.assertRaisesRegex(ValueError, "Reserved tag changed"):
            self.call_publish(files, reserved_tag_sha="0" * 40)
        self.assertEqual(self.patches(), [])

    def test_existing_tag_release_or_disabled_immutability_blocks_creation(self):
        files = self.files()
        for field, value in (("release_exists", True), ("tag_exists", True), ("immutable_releases_enabled", False)):
            prior = copy.deepcopy(self.state); self.state[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.call_publish(files)
            self.assertEqual(self.events, [])
            self.state = prior

    def test_changed_package_version_bytes_block_creation(self):
        files = self.files(); package = self.manifest["assets"][0]
        registered = {key: package[key] for key in ("package", "version", "architecture")}; registered["sha256"] = "9" * 64
        self.state["package_versions"] = [registered]
        with self.assertRaisesRegex(ValueError, "different bytes"): self.call_publish(files)
        self.assertEqual(self.events, [])

    def test_changed_local_publication_part_never_publishes(self):
        files = self.files(); part = next(name for name in files if ".part-" in name)
        path = self.output / part; data = bytearray(path.read_bytes()); data[0] ^= 1; path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "changed after preparation"): self.call_publish(files)
        self.assertEqual(self.patches(), [])

    def test_stable_latest_or_other_repository_approval_never_creates_release(self):
        files = self.files()
        for key, value in (("prerelease", False), ("make_latest", True), ("repository", "other/repo"), ("status", "prepared")):
            old = self.approval[key]; self.approval[key] = value
            with self.subTest(key=key), patch.object(publish.github, "api") as network, self.assertRaises(ValueError):
                publish.publish(REPO, self.approval, files, self.output)
            network.assert_not_called(); self.approval[key] = old

    def test_local_cli_only_prepares_files_and_never_creates_release(self):
        receipt = self.fixture.root / "approval.json"; receipt.write_text(json.dumps(self.approval))
        report = self.fixture.root / "prepared-report.json"
        args = ["publish.py", "--archive", str(self.archive), "--directory", str(self.directory),
                "--approval", str(receipt), "--output", str(self.output), "--report", str(report),
                "--repository", REPO]
        with patch.object(sys, "argv", args), patch.object(github, "api") as api, patch.object(github, "gh") as gh:
            publish.main()
        api.assert_not_called(); gh.assert_not_called()
        self.assertEqual(github.read(report)["status"], "prepared-only")
        self.assertFalse(github.read(report)["publication_performed"])


if __name__ == "__main__":
    unittest.main()
