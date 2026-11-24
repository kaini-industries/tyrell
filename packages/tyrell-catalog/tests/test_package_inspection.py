"""Reject unsafe package paths before directory/file inventory normalization."""
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("catalog_package_inspection", PACKAGE / "tools/check_package.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def archive(name, directory=False):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        item = tarfile.TarInfo(name)
        item.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
        item.mode = 0o755 if directory else 0o644
        tar.addfile(item, None if directory else io.BytesIO(b""))
    return out.getvalue()


class PackageInspectionTests(unittest.TestCase):
    def test_unsafe_data_directory_rejected_before_inventory_checks(self):
        fields = (b"Package: tyrell-catalog\nVersion: 0.1.0-1\nArchitecture: arm64\n"
                  b"Depends: python3, openssl, ca-certificates, libc6, libstdc++6, libssl3t64\n")
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "test.deb"; package.write_bytes(b"mock archive")
            for name in ("/", "/usr/", "../", "./usr/../"):
                with self.subTest(name=name), patch.object(checker, "output", side_effect=[
                        fields, archive("./control"), archive(name, True)]), self.assertRaisesRegex(ValueError, "Unsafe archive path"):
                    checker.inspect(package)

    def test_absolute_control_member_is_not_a_relative_control_file(self):
        with self.assertRaisesRegex(ValueError, "Unsafe archive path"):
            checker.archive_name("/control", False)


if __name__ == "__main__":
    unittest.main()
