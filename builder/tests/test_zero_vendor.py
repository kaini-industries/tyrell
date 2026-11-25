"""Adversarial archive replay checks; no compiler, VM, device or network needed."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("zero_vendor", Path(__file__).parents[1] / "zero-vendor.py")
vendor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vendor)


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def archive(self, members):
        archive = self.root / "test.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            for name, content, kind in members:
                info = tarfile.TarInfo(name)
                info.type = kind
                info.size = len(content) if kind == tarfile.REGTYPE else 0
                info.linkname = "../../escape" if kind == tarfile.SYMTYPE else ""
                info.mode = 0o4755
                stream.addfile(info, io.BytesIO(content))
        return archive, {
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "bytes": archive.stat().st_size, "members": len(members),
            "unpacked_bytes": sum(len(c) for _, c, k in members if k == tarfile.REGTYPE),
            "archive_root": "repo-commit",
        }

    def test_regular_replay_strips_root_and_setid(self):
        archive, entry = self.archive([("repo-commit/src/run", b"ok", tarfile.REGTYPE)])
        target = self.root / "out"
        vendor.safe_extract(archive, target, entry)
        self.assertEqual((target / "src/run").read_bytes(), b"ok")
        self.assertEqual((target / "src/run").stat().st_mode & 0o7777, 0o755)

    def test_traversal_absolute_and_noncanonical_paths_rejected_before_writes(self):
        for name in ("repo-commit/../escape", "/repo-commit/escape", "repo-commit/a//b", "repo-commit/a\\b"):
            with self.subTest(name=name):
                archive, entry = self.archive([("repo-commit/valid", b"ok", tarfile.REGTYPE),
                                               (name, b"bad", tarfile.REGTYPE)])
                target = self.root / "out"
                with self.assertRaises(ValueError):
                    vendor.safe_extract(archive, target, entry)
                self.assertFalse(target.exists())

    def test_link_special_and_duplicate_members_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE):
            with self.subTest(kind=kind):
                archive, entry = self.archive([("repo-commit/link", b"", kind)])
                with self.assertRaises(ValueError):
                    vendor.safe_extract(archive, self.root / "out", entry)
        archive, entry = self.archive([("repo-commit/a", b"a", tarfile.REGTYPE),
                                       ("repo-commit/a", b"b", tarfile.REGTYPE)])
        with self.assertRaises(ValueError):
            vendor.safe_extract(archive, self.root / "out", entry)

    def test_hash_expansion_and_ancestor_collisions_rejected(self):
        archive, entry = self.archive([("repo-commit/a", b"a", tarfile.REGTYPE)])
        for change in ({"sha256": "0" * 64}, {"members": 8}, {"unpacked_bytes": 9}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                vendor.safe_extract(archive, self.root / "out", dict(entry, **change))
        archive, entry = self.archive([("repo-commit/a", b"a", tarfile.REGTYPE),
                                       ("repo-commit/a/b", b"b", tarfile.REGTYPE)])
        with self.assertRaises(ValueError):
            vendor.safe_extract(archive, self.root / "out", entry)

    def test_existing_destination_and_symlink_ancestor_rejected(self):
        archive, entry = self.archive([("repo-commit/a", b"a", tarfile.REGTYPE)])
        target = self.root / "existing"
        target.mkdir()
        (target / "keep").write_text("keep")
        with self.assertRaises(ValueError):
            vendor.safe_extract(archive, target, entry)
        (self.root / "alias").symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            vendor.safe_extract(archive, self.root / "alias/subdir", entry)
        self.assertEqual((target / "keep").read_text(), "keep")

    def test_offline_guard_allows_only_locked_identity_and_disables_downloaders(self):
        root = self.root / "github_source"
        (root / "eventpp").mkdir(parents=True)
        identity = {"commit": "exact", "repository": "https://github.com/example/eventpp.git",
                    "destination": "SDK/github_source/eventpp"}
        namespace = {"env": {"GIT_REPO_LISTS": {"eventpp": {
            "path": str(root / "eventpp"), "url": identity["repository"], "commit": "exact"}}}}
        exec(vendor.offline_guard({"archives": [identity]}), namespace)
        with patch.dict(os.environ, {"GIT_REPO_PATH": str(root)}):
            self.assertEqual(namespace["check_component"]("eventpp"), str(root / "eventpp"))
            namespace["env"]["GIT_REPO_LISTS"]["eventpp"]["commit"] = "changed"
            with self.assertRaises(RuntimeError):
                namespace["check_component"]("eventpp")
        for function in ("sample_wget", "wget_tar_xz", "wget_tar_gz", "wget_zip", "wget_github",
                         "wget_github_commit", "check_wget_down", "Export_conanenv", "ensure_static_lib_version"):
            with self.subTest(function=function), self.assertRaises(RuntimeError):
                namespace[function]("https://example.invalid/input")

    def test_complete_locked_sdk_helper_executes_in_clean_component_globals(self):
        lock = vendor.load_lock()
        fixture = Path(__file__).with_name("fixtures") / "zero-sdk-SConstruct_tool.json"
        upstream = json.loads(fixture.read_text())["source"]
        self.assertEqual(hashlib.sha256(upstream.encode()).hexdigest(), lock["sdk_offline_patch"]["upstream_sha256"])
        # SCons component globals do not promise pathlib.Path. Reproduce the
        # actual upstream annotation failure before testing the full adapter.
        with self.assertRaisesRegex(NameError, "Path"):
            clean = {"env": {}}
            exec(compile(upstream, str(fixture), "exec"), clean)
            # Python 3.14 defers annotations; ARM64's Python 3.13 evaluates
            # them while defining the function. Exercise both host versions.
            clean["ensure_static_lib_version"].__annotations__
        dependency = next(item for item in lock["archives"]
                          if item["destination"] == "SDK/github_source/eventpp")
        root = self.root / "github_source"
        (root / "eventpp").mkdir(parents=True)
        namespace = {"env": {"GIT_REPO_LISTS": {"eventpp": {
            "path": str(root / "eventpp"), "url": dependency["repository"],
            "commit": dependency["commit"]}}}}
        self.assertNotIn("Path", namespace)
        exec(compile(vendor.offline_helper(upstream, lock), str(fixture), "exec"), namespace)
        with patch.dict(os.environ, {"GIT_REPO_PATH": str(root)}):
            self.assertEqual(namespace["check_component"]("eventpp"), str(root / "eventpp"))
        for function in ("sample_wget", "wget_tar_xz", "wget_tar_gz", "wget_zip", "wget_github",
                         "wget_github_commit", "check_wget_down", "Export_conanenv", "ensure_static_lib_version"):
            with self.subTest(function=function), self.assertRaisesRegex(RuntimeError, "forbids downloads/Conan"):
                namespace[function]("https://example.invalid/input")


if __name__ == "__main__":
    unittest.main()
