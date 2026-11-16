from pathlib import Path
import runpy
import tempfile
import unittest

WIZARD = Path(__file__).resolve().parents[1]
API = runpy.run_path(str(WIZARD / "native_config.py"))


class NativeConfig(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "projects/LaunchWizard"
        self.project.mkdir(parents=True)
        for name in API["INPUT_SHA256"]:
            path = self.project / name
            path.write_bytes((WIZARD / "tests/fixtures" / name).read_bytes())
            path.chmod(0o644)

    def test_exact_vendor_runtime_choices_survive_native_derivation(self):
        result = API["generate"](self.root)
        target = self.project / result["config"]
        data = target.read_bytes()
        for choice in API["REQUIRED"]:
            self.assertIn(choice, data)
        self.assertNotIn(b"CONFIG_TOOLCHAIN_", data)
        self.assertNotIn("cross", target.name)
        self.assertEqual(result["sha256"],
                         "1df551d93c93925eeaa1050fb1bb71c7e82813464e0a27fe6be3dd201075344b")
        self.assertEqual(API["generate"](self.root), result)

    def test_changed_vendor_defaults_cannot_silently_remove_hardware_backend(self):
        original = self.project / API["CARDPUTER"]
        original.write_bytes(original.read_bytes().replace(b"CONFIG_V9_5_LV_USE_LINUX_FBDEV=y", b""))
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            API["generate"](self.root)
        self.assertFalse((self.project / API["OUTPUT"]).exists())

    def test_different_existing_output_is_preserved(self):
        target = self.project / API["OUTPUT"]
        target.write_text("different config\n")
        with self.assertRaisesRegex(ValueError, "refusing to replace"):
            API["generate"](self.root)
        self.assertEqual(target.read_text(), "different config\n")

    def test_input_symlink_or_group_writable_input_is_rejected(self):
        original = self.project / API["DEFAULTS"]
        original.chmod(0o664)
        with self.assertRaisesRegex(ValueError, "nonwritable regular"):
            API["generate"](self.root)
        original.chmod(0o644)
        other = self.root / "other"
        original.rename(other)
        original.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "symlink"):
            API["generate"](self.root)

    def test_output_symlink_is_never_followed(self):
        outside = self.root / "outside"
        outside.write_text("keep\n")
        (self.project / API["OUTPUT"]).symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            API["generate"](self.root)
        self.assertEqual(outside.read_text(), "keep\n")


if __name__ == "__main__":
    unittest.main()
