"""Host-side guard checks for the isolated lifecycle harness; no mount/dpkg calls."""
from pathlib import Path
import runpy
import tempfile
import unittest

API = runpy.run_path(str(Path(__file__).resolve().parents[1] / "zero-store-lifecycle.py"))


class LifecycleGuards(unittest.TestCase):
    def test_fixture_identity_does_not_require_or_change_host_uid1000(self):
        lifecycle = API["Lifecycle"](Path("/source"), Path("/output"))
        self.assertEqual((lifecycle.user.pw_name, lifecycle.user.pw_uid,
                          lifecycle.user.pw_gid, lifecycle.user.pw_dir),
                         ("native", 1000, 1000, "/home/native"))

    def test_outer_writes_reject_symlinks_leading_out_of_disposable_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            lifecycle = API["Lifecycle"](base / "input", base / "output")
            lifecycle.root.mkdir(parents=True)
            outside = base / "outside"; outside.mkdir()
            (lifecycle.root / "usr").symlink_to(outside)
            with self.assertRaisesRegex(RuntimeError, "external symlink"):
                lifecycle.at("usr/sbin/policy-rc.d")
            self.assertFalse((outside / "sbin").exists())

    def test_usrmerge_relative_links_remain_inside_disposable_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            lifecycle = API["Lifecycle"](base / "input", base / "output")
            (lifecycle.root / "usr/bin").mkdir(parents=True)
            (lifecycle.root / "bin").symlink_to("usr/bin")
            self.assertEqual(lifecycle.at("/bin/tool"), lifecycle.root / "bin/tool")
            with self.assertRaisesRegex(RuntimeError, "escapes root"):
                lifecycle.at("usr/../../etc/shadow")

    def test_overlay_option_separators_are_rejected(self):
        for path in ["/tmp/a,b", "/tmp/a:b", "/tmp/a\nb"]:
            with self.assertRaisesRegex(RuntimeError, "overlay separators"):
                API["canonical"](path)


if __name__ == "__main__":
    unittest.main()
