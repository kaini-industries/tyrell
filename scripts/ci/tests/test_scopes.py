"""Scope selection must not hide package inputs or changes across renames."""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("ci_scopes", Path(__file__).parents[1] / "scopes.py")
scopes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scopes)


class ScopesTests(unittest.TestCase):
    def test_documentation_only_and_empty_diff_still_have_one_gate(self):
        self.assertEqual(scopes.select(["README.md", "docs/a.md", "research/power-audit.json"]), ["docs"])
        self.assertEqual(scopes.select([]), ["docs"])

    def test_package_docs_and_assets_enter_the_native_package(self):
        self.assertEqual(scopes.select(["packages/tyrell-video/README.md"]), ["docs", "python", "video"])
        self.assertEqual(scopes.select(["packages/tyrell-status/assets/icon.png"]), ["docs", "python", "status"])

    def test_shared_game_covers_chess_and_both_multiplayer_consumers(self):
        selected = scopes.select(["packages/tyrell-game/include/tyrell/game/runtime.hpp"])
        self.assertEqual(selected, ["docs", "python", "game", "chess", "chess-native"])
        self.assertEqual(scopes.select(["locks/game-build-inputs.json"]), selected)
        self.assertEqual(scopes.select(["scripts/game"]), selected)

    def test_app_specific_change_does_not_select_every_native_suite(self):
        self.assertEqual(scopes.select(["packages/tyrell-chess/src/main.cpp"]), ["docs", "python", "chess", "chess-native"])
        self.assertEqual(scopes.select(["packages/tyrell-tanks/src/network.cpp"]), ["docs", "python", "game"])

    def test_ci_ledger_and_common_capture_inputs_force_full_coverage(self):
        for path in (".github/workflows/checks.yml", "scripts/ci/scopes.py", "scripts/release/candidate.py", "research/compatibility-ledger.json",
                     "builder/write-manifest.py", "image/overlay.py", "locks/tyrell-zero-apt.json",
                     "packages/tyrell-app-common/include/new.hpp", "Makefile"):
            with self.subTest(path=path):
                self.assertEqual(scopes.select([path]), list(scopes.SUITES))

    def test_prefix_lookalike_and_new_inputs_fail_closed(self):
        for path in ("packages/tyrell-game-new/source.cpp", "scripts/new-build-helper", "toolchain/new.cmake"):
            with self.subTest(path=path):
                self.assertEqual(scopes.select([path]), list(scopes.SUITES))

    def test_lvgl_lock_covers_both_apps_and_base_integration(self):
        self.assertEqual(scopes.select(["locks/build-inputs.json"]),
                         ["docs", "python", "status", "video", "zero-native"])

    def test_retained_package_changes_exercise_guard(self):
        self.assertEqual(scopes.select(["dist/tyrell-video/0.1.7/package.deb"]),
                         ["docs", "python", "zero-native"])

    def test_catalog_changes_keep_native_frontend_and_guard_coverage(self):
        self.assertEqual(scopes.select(["packages/tyrell-catalog/catalog_cache.py"]),
                         ["docs", "python", "zero-native"])

    def test_handoff_changes_cover_native_managed_display_consumers(self):
        for path in ("packages/tyrell-video-handoff/handoff_channel.py",
                     "packages/tyrell-video-handoff/tests/test_session.py",
                     "packages/tyrell-video-handoff/README.md"):
            with self.subTest(path=path):
                self.assertEqual(scopes.select([path]), ["docs", "python", "video"])
        self.assertEqual(scopes.select(["packages/tyrell-video-handoff-new/actor.py"]),
                         list(scopes.SUITES))

    def test_observation_tools_require_portable_checks_without_device_jobs(self):
        self.assertEqual(scopes.select(["scripts/observe/collect.py"]), ["docs", "python"])
        self.assertEqual(scopes.select(["scripts/power/transaction.py"]), ["docs", "python"])

    def test_panel_code_and_audited_metadata_require_parsing_checks(self):
        for name in ("scripts/panel/counters.py", "research/panel-source-audit.json",
                     "research/panel-experiment-template.json"):
            with self.subTest(name=name):
                self.assertEqual(scopes.select([name]), ["docs", "python"])

    def test_git_parser_keeps_deleted_and_both_renamed_paths(self):
        data = b"D\0packages/tyrell-video/src/removed.cpp\0R100\0packages/tyrell-chess/old.cpp\0docs/new name\n.md\0"
        paths = scopes.parse_name_status(data)
        self.assertEqual(paths, ["packages/tyrell-video/src/removed.cpp", "packages/tyrell-chess/old.cpp", "docs/new name\n.md"])
        self.assertEqual(scopes.select(paths), ["docs", "python", "chess", "chess-native", "video"])

    def test_parser_rejects_incomplete_data_and_unsafe_paths(self):
        for data in (b"M\0not-terminated", b"R100\0old\0", b"?\0new\0"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                scopes.parse_name_status(data)
        for path in ("/tmp/code.cpp", "../code.cpp", "docs/../hidden", ""):
            with self.subTest(path=path), self.assertRaises(ValueError):
                scopes.select([path])

    def test_commit_planning_is_bound_to_sha_and_includes_rename_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
            git("init", "-q")
            git("config", "user.email", "ci-test@example.invalid")
            git("config", "user.name", "CI test")
            original = root / "packages/tyrell-video/source.cpp"
            original.parent.mkdir(parents=True)
            original.write_text("unchanged source\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (root / "docs").mkdir()
            git("mv", str(original.relative_to(root)), "docs/source.md")
            git("commit", "-qm", "rename")
            head = git("rev-parse", "HEAD")
            result = scopes.plan(base, head, root=root)
            self.assertEqual(result["source_sha"], head)
            self.assertEqual(result["base_sha"], base)
            self.assertTrue(result["video"])
            self.assertFalse(result["status"])
            self.assertEqual(result["native_matrix"], {"include": [{"suite": "video"}]})
            missing = scopes.plan("a" * 40, head, root=root)
            self.assertEqual(missing["suites"], list(scopes.SUITES))
            with self.assertRaises(ValueError):
                scopes.plan(base, "--help", root=root)

    def test_rewritten_history_includes_inputs_removed_from_previous_head(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
            git("init", "-q")
            git("config", "user.email", "ci-test@example.invalid")
            git("config", "user.name", "CI test")
            (root / "README.md").write_text("base\n")
            git("add", ".")
            git("commit", "-qm", "common base")
            common = git("rev-parse", "HEAD")
            (root / "packages/tyrell-status").mkdir(parents=True)
            (root / "packages/tyrell-status/new.cpp").write_text("new code\n")
            git("add", ".")
            git("commit", "-qm", "previous push")
            previous = git("rev-parse", "HEAD")
            git("checkout", "-q", "--detach", common)
            (root / "README.md").write_text("replacement branch\n")
            git("add", ".")
            git("commit", "-qm", "replacement head")
            result = scopes.plan(previous, git("rev-parse", "HEAD"), root=root)
            self.assertTrue(result["status"], "Deleted status input must still trigger its suite")


if __name__ == "__main__":
    unittest.main()
