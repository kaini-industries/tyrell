"""Retained-root inventory guards; no VM, chroot, package install or receipts."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('zero_lock_guest_inventory', REPO / 'builder/zero-lock-guest.py')
guest = importlib.util.module_from_spec(spec);spec.loader.exec_module(guest)
spec = importlib.util.spec_from_file_location('zero_seed_inventory_apt', REPO / 'image/apt-lock.py')
apt = importlib.util.module_from_spec(spec);spec.loader.exec_module(apt)


class CopyBoundary(RuntimeError):
    pass


class RetainedInventoryTests(unittest.TestCase):
    def prepare(self, directory, lock, installed):
        base = Path(directory)
        job = base / 'job';job.mkdir()
        seed = base / 'seed';seed.mkdir()
        retained = base / 'retained';status = retained / 'var/lib/dpkg/status'
        status.parent.mkdir(parents=True)
        status.write_text('\n\n'.join('Package: ' + name + '\nVersion: ' + version +
            '\nArchitecture: ' + architecture + '\nStatus: install ok installed\n'
            for name, version, architecture in installed))
        data = json.dumps(lock)
        (job / 'seed-lock.json').write_text(data);(seed / 'apt-lock.json').write_text(data)
        verify = Mock(return_value=lock)
        api = {'verify_repository': verify, 'inventory': apt.inventory}
        runner = Mock(side_effect=CopyBoundary('stopped before rsync'))
        return job, seed, retained, api, verify, runner

    def test_final_inventory_subset_is_exact_guard_before_copy(self):
        candidates = [['runtime', '1', 'arm64'], ['build-only', '2', 'all']]
        lock = {'stages': {'stage2': candidates}, 'installed_stages': {'stage2': candidates[:1]}}
        for installed, accepted in [(candidates[:1], True), (candidates, False), ([], False),
                                    ([['unexpected', '1', 'arm64']], False)]:
            with self.subTest(installed=installed), tempfile.TemporaryDirectory() as directory:
                job, seed, retained, api, verify, runner = self.prepare(directory, lock, installed)
                with patch.object(guest.runpy, 'run_path', return_value=api), patch.object(guest, 'run', runner):
                    if accepted:
                        with self.assertRaises(CopyBoundary):guest.prepare(job, seed, retained)
                        self.assertEqual(runner.call_args.args[0][0], 'rsync')
                    else:
                        with self.assertRaisesRegex(ValueError, 'Retained stage2 inventory differs'):
                            guest.prepare(job, seed, retained)
                        runner.assert_not_called()
                        self.assertFalse((job / 'rootfs').exists())
                verify.assert_called_once_with(seed)

    def test_explicit_installed_stage_map_requires_stage2(self):
        lock = {'stages': {'stage2': [['runtime', '1', 'arm64']]},
                'installed_stages': {'stage0': [['runtime', '1', 'arm64']]}}
        with tempfile.TemporaryDirectory() as directory:
            job, seed, retained, api, _verify, runner = self.prepare(directory, lock, lock['stages']['stage2'])
            with patch.object(guest.runpy, 'run_path', return_value=api), patch.object(guest, 'run', runner):
                with self.assertRaisesRegex(ValueError, 'explicit stage2 inventory'):
                    guest.prepare(job, seed, retained)
            runner.assert_not_called()

    def test_legacy_lock_still_requires_full_stage2_inventory(self):
        candidates = [['runtime', '1', 'arm64'], ['build-only', '2', 'all']]
        lock = {'stages': {'stage2': candidates}}
        for installed, accepted in [(candidates, True), (candidates[:1], False)]:
            with self.subTest(installed=installed), tempfile.TemporaryDirectory() as directory:
                job, seed, retained, api, _verify, runner = self.prepare(directory, lock, installed)
                with patch.object(guest.runpy, 'run_path', return_value=api), patch.object(guest, 'run', runner):
                    if accepted:
                        with self.assertRaises(CopyBoundary):guest.prepare(job, seed, retained)
                    else:
                        with self.assertRaisesRegex(ValueError, 'Retained stage2 inventory differs'):
                            guest.prepare(job, seed, retained)
                        runner.assert_not_called()

    def test_exported_system_receipt_counts_final_inventory_instead_of_candidates(self):
        # All subprocesses are mocked. The temporary receipt only tests the
        # producer's serialization and is never retained as qualification.
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / 'rootfs/tmp/tyrell-zero-debs').mkdir(parents=True)
            for name, value in {
                    'transaction.json': {'removals': [], 'installs': []}, 'apt-arguments.json': [],
                    'greetd-authentication.json': {}, 'seed-lock.json': {
                        'stages': {'stage2': [['runtime', '1', 'arm64'], ['build-only', '2', 'all']]},
                        'installed_stages': {'stage2': [['runtime', '1', 'arm64']]}}}.items():
                (job / name).write_text(json.dumps(value))
            for name in ('apt-update.txt', 'apt-simulation.txt'):(job / name).write_text('synthetic log\n')
            api = {'inventory': lambda _root: {('runtime', '1', 'arm64')}}
            with patch.object(guest.runpy, 'run_path', return_value=api), \
                    patch.object(guest, 'run'), patch.object(guest.subprocess, 'check_output', return_value=''):
                guest.install(job)
            receipt = json.loads((job / 'system-qualification.json').read_text())
            self.assertEqual(receipt['retained_stage2_installed_count'], 1)
            self.assertEqual(receipt['installed_count'], 1)


if __name__ == '__main__':unittest.main()
