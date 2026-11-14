"""Stock-image jobs retain their selected inputs and fail before guest writes."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location('stock_lifecycle_builder', Path(__file__).resolve().parents[1] / 'build-image.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class StockLifecycleTests(unittest.TestCase):
    def prepare(self, root, selected_in_tree=False):
        project = root / 'project'
        (project / 'image').mkdir(parents=True)
        (project / 'builder').mkdir()
        (project / 'scripts').mkdir()
        (project / 'artifacts').mkdir()
        (project / 'image/example.py').write_text('captured recipe')
        (project / 'image/stock-image.json').write_text('default lock must be replaced')
        for name in ('build-stock-image.sh', 'ensure-image-deps.sh', 'image-dependencies.txt'):
            (project / 'builder' / name).write_text('script')
        (project / 'scripts/builder').write_text('builder')
        (project / 'Makefile').write_text('make')
        package_name = 'tyrell-status_0.1.0-1_arm64.deb'
        (project / 'artifacts' / package_name).write_bytes(b'package')
        (project / 'artifacts/manifest.json').write_text(json.dumps({
            'sha256': {package_name: digest(b'package')}, 'source': {'source_revision': 'test'}}))
        lock = {'image': {'name': 'stock.img.xz', 'url': 'https://example.invalid/stock.img.xz',
                          'sha256': digest(b'stock base'), 'bytes': len(b'stock base')},
                'pi_gen_commit': 'a' * 40, 'drivers_commit': 'b' * 40,
                'kernel_release': 'test-kernel', 'applaunch_version': 'test-version',
                'upstream': {'tag': 'selected'}}
        selected = project / 'image/candidate.json' if selected_in_tree else root / 'selected.json'
        selected.write_text(json.dumps(lock))
        target = root / 'storage'
        job = target / 'jobs/example'
        job.mkdir(parents=True)
        tools = {'load_lock': lambda path: json.loads(path.read_text()), 'validate_lock': lambda data: data}

        def fetch(spec, destination):
            path = destination / spec['name']
            path.write_bytes(b'stock base')
            return path

        with patch.object(builder, 'ROOT', project), patch.object(builder, 'stock_tools', return_value=tools), \
                patch.object(builder, 'fetch', side_effect=fetch), \
                patch.object(builder, 'preflight_stock_host'), \
                patch.object(builder, 'stock_image_size', return_value=8 * 1024**3), \
                patch.object(builder.subprocess, 'check_output', return_value='test-project-commit\n'):
            bundle = builder.prepare_stock(target, job, selected)
        return bundle, tools, selected

    def rewrite(self, path, name, value):
        with tarfile.open(path) as archive:
            members = [(member.name, archive.extractfile(member).read()) for member in archive]
        with tarfile.open(path, 'w') as archive:
            for member_name, data in members:
                if member_name == name:
                    data = value
                member = tarfile.TarInfo(member_name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))

    def test_selected_external_lock_is_captured_and_stock_profile_does_not_need_apt(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle, tools, selected = self.prepare(Path(directory))
            with patch.object(builder, 'stock_tools', return_value=tools):
                metadata = builder.verify_bundle(bundle)
            self.assertEqual(metadata['apt_profile'], 'stock-image')
            self.assertEqual(metadata['stock_image_lock_sha256'], digest(selected.read_bytes()))
            with tarfile.open(bundle) as archive:
                self.assertEqual(archive.extractfile('image/stock-image.json').read(), selected.read_bytes())
                self.assertFalse(any(name.startswith(('vendor/', 'apt-repository/')) for name in archive.getnames()))

    def test_stock_base_corruption_after_selection_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle, tools, _ = self.prepare(Path(directory))
            self.rewrite(bundle, 'assets/stock.img.xz', b'tampered!!')
            with patch.object(builder, 'stock_tools', return_value=tools):
                with self.assertRaisesRegex(RuntimeError, 'Stock image changed'):
                    builder.verify_bundle(bundle)

    def test_in_tree_selected_lock_is_captured_as_regular_contents_at_canonical_path(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle, tools, selected = self.prepare(Path(directory), selected_in_tree=True)
            with patch.object(builder, 'stock_tools', return_value=tools):
                builder.verify_bundle(bundle)
            with tarfile.open(bundle) as archive:
                canonical = archive.getmember('image/stock-image.json')
                self.assertTrue(canonical.isfile())
                self.assertFalse(canonical.islnk())
                self.assertEqual(archive.extractfile(canonical).read(), selected.read_bytes())

    def test_link_cannot_replace_an_application_package(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle, tools, _ = self.prepare(Path(directory))
            with tarfile.open(bundle) as archive:
                members = [(member, archive.extractfile(member).read()) for member in archive]
            with tarfile.open(bundle, 'w') as archive:
                for member, data in members:
                    if member.name == 'assets/tyrell-status_0.1.0-1_arm64.deb':
                        member.type = tarfile.LNKTYPE
                        member.linkname = member.name
                        member.size = 0
                        archive.addfile(member)
                    else:
                        archive.addfile(member, io.BytesIO(data))
            with patch.object(builder, 'stock_tools', return_value=tools):
                with self.assertRaisesRegex(RuntimeError, 'Unsafe captured application package'):
                    builder.verify_bundle(bundle)

    def test_package_manifest_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle, tools, _ = self.prepare(Path(directory))
            self.rewrite(bundle, 'package-manifest.json', b'{}')
            with patch.object(builder, 'stock_tools', return_value=tools):
                with self.assertRaisesRegex(RuntimeError, 'Package manifest changed'):
                    builder.verify_bundle(bundle)

    def test_cached_image_symlink_cannot_enter_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / 'original'
            original.write_bytes(b'base')
            (root / 'stock.img.xz').symlink_to(original)
            with self.assertRaisesRegex(RuntimeError, 'regular file'):
                builder.fetch({'name': 'stock.img.xz', 'sha256': digest(b'base'), 'bytes': 4}, root)

    def test_guest_capacity_checks_actual_guest_space_before_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle, _, _ = self.prepare(Path(directory))
            resources = {'check_stock_guest_space': lambda free, bundle, unpacked, raw:
                         self.assertEqual((free, raw), (23 * 1024**3, 8 * 1024**3)) or
                         {'required_bytes': bundle + unpacked + 2 * raw + 1024**3, 'free_bytes': free}}
            result = subprocess.CompletedProcess([], 0, str(23 * 1024**3))
            with patch.object(builder, 'B', {'guest': lambda *args, **kwargs: result}):
                report = builder.preflight_stock_guest(bundle, resources)
            self.assertGreater(report['required_bytes'], 16 * 1024**3)

    def test_host_capacity_counts_the_cache_and_archive_before_download(self):
        preflight = Mock()
        with patch.object(builder.runpy, 'run_path', return_value={'preflight': preflight}):
            builder.preflight_stock_host(Path('/existing/storage'), 2 * 1024**3)
        self.assertEqual(preflight.call_args.kwargs,
                         {'operation': 'prepare', 'input_bytes': 4 * 1024**3})

    def test_low_guest_space_stops_stock_job_before_transfer(self):
        args = builder.parse_args(['--stock-lock', 'lock.json'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / 'input.tar'
            bundle.write_bytes(b'captured input')
            guest = Mock()
            resources = {'preflight': lambda *a, **k: None}
            with patch.object(builder, 'B', dict(builder.B, up=lambda: None, guest=guest)), \
                    patch.object(builder.runpy, 'run_path', return_value=resources), \
                    patch.object(builder, 'assert_idle', return_value={}), \
                    patch.object(builder, 'new_job', return_value=root), \
                    patch.object(builder, 'prepare_stock', return_value=bundle), \
                    patch.object(builder, 'preflight_stock_guest', side_effect=RuntimeError('insufficient guest space')):
                with self.assertRaisesRegex(RuntimeError, 'insufficient guest space'):
                    builder.start_job(root, args)
            guest.assert_not_called()

    def test_stock_mode_rejects_source_specific_and_observational_options(self):
        for args in (['--stock-lock', 'lock.json', '--discovery'],
                     ['--stock-lock', 'lock.json', '--resume-from', 'stage2'],
                     ['--stock-lock', 'lock.json', '--apt-repository', 'old-closure'],
                     ['status', '--stock-lock', 'lock.json']):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                builder.parse_args(args)


if __name__ == '__main__':
    unittest.main()
