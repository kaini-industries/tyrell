"""Host-only resource checks; no Lima instance, network, or device required."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('tyrell_resources', Path(__file__).parents[1] / 'resources.py')
resources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resources)


class ResourceTests(unittest.TestCase):
    def test_same_volume_reservations_are_added(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch.object(resources.shutil, 'disk_usage', return_value=SimpleNamespace(free=40 * resources.GIB)):
                with self.assertRaisesRegex(RuntimeError, '50.00 GiB'):
                    resources.check_budgets([(path, 35 * resources.GIB, 'disk'),
                                             (path, 15 * resources.GIB, 'export')])

    def test_different_volumes_are_checked_separately(self):
        first = SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=1))
        second = SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=2))
        with patch.object(resources, 'existing_parent', side_effect=[first, second]), \
                patch.object(resources.shutil, 'disk_usage', return_value=SimpleNamespace(free=40 * resources.GIB)):
            result = resources.check_budgets([('a', 35 * resources.GIB, 'disk'),
                                              ('b', 15 * resources.GIB, 'export')])
        self.assertEqual(len(result), 2)

    def test_missing_external_backing_disk_fails_before_lima(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            disk = root / 'build/lima/tyrell-build/disk'
            disk.parent.mkdir(parents=True)
            disk.symlink_to(root / 'unmounted-drive/tyrell.raw')
            with self.assertRaisesRegex(RuntimeError, 'dangling symlink'):
                resources.preflight(root, root)

    def test_existing_vm_missing_regular_disk_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'build/lima/tyrell-build/lima.yaml'
            config.parent.mkdir(parents=True)
            config.write_text('memory: 4GiB\n')
            with self.assertRaisesRegex(RuntimeError, 'disk is unavailable'):
                resources.host_preflight(root)

    def test_missing_image_storage_is_not_recreated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, 'Image storage is unavailable'):
                resources.preflight(root, root / 'missing')
            self.assertFalse((root / 'missing').exists())

    def test_internal_reserve_applies_with_external_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(resources, 'check_budgets', return_value=[]) as check:
                resources.preflight(root, root, operation='prepare', input_bytes=3 * resources.GIB)
            requests = check.call_args.args[0]
            self.assertEqual(requests[0][1], 4 * resources.GIB)
            self.assertEqual(requests[2][1], 18 * resources.GIB)

    def test_export_budget_uses_measured_size(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(resources, 'check_budgets', return_value=[]) as check:
                resources.preflight(root, root, operation='export', export_bytes=4 * resources.GIB,
                                    input_bytes=2 * resources.GIB)
            self.assertEqual(check.call_args.args[0][2][1], 11 * resources.GIB)

    def test_explicit_memory_units_and_limits(self):
        self.assertEqual(resources.memory_mib('4GiB'), 4096)
        self.assertEqual(resources.memory_mib('4352MiB'), 4352)
        for value in ['4', '4GB', '0GiB', '1GiB', '33GiB', '4097MiB', '-4GiB', '4GiB; touch bad']:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                resources.memory_mib(value)

    def test_memory_capacity_retains_host_ram(self):
        with patch.object(resources.sys, 'platform', 'darwin'), \
                patch.object(resources.subprocess, 'check_output', return_value=str(8 * resources.GIB)):
            resources.check_memory_capacity(4096)
            with self.assertRaisesRegex(RuntimeError, 'physical RAM'):
                resources.check_memory_capacity(8192)


if __name__ == '__main__':
    unittest.main()
