"""Zero transport/inspection boundaries; no Lima, package install, or device."""
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


zero = load('zero_host_boundary_tests', ROOT / 'builder/zero.py')
builder = load('zero_builder_dispatch_tests', ROOT / 'scripts/builder')


def write(root, name, content='source'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def source_fixture(root):
    write(root, 'locks/tyrell-zero-build-inputs.json', json.dumps({
        'schema_version': 1, 'source_date_epoch': 123,
        'packages': {'tyrell-zero-power-policy': '0.1.0-1'},
    }))
    for name in ['builder/zero.py', 'builder/zero-guest.py',
                 'builder/build-zero-packages.sh',
                 'image/contracts/tyrell-zero-power.json',
                 'packages/tyrell-zero-power-policy/build-package.py',
                 'packages/tyrell-zero-power-policy/policy.py']:
        write(root, name)
    job = root / 'build/job'
    job.mkdir(parents=True)
    return job


def archive(path, rows):
    """Rows are (name, bytes, type[, link target]); use raw names deliberately."""
    with tarfile.open(path, 'w') as tar:
        for row in rows:
            name, data, kind, *link = row
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.REGTYPE:
                info.size = len(data)
            if link:
                info.linkname = link[0]
            tar.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)


def sums(name, content):
    return hashlib.sha256(content).hexdigest().encode() + b'  ' + name.encode() + b'\n'


class BundleBoundaryTests(unittest.TestCase):
    def test_only_selected_source_and_contracts_are_transferred(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = source_fixture(root)
            write(root, '.env', 'fake private connection data')
            write(root, 'packages/tyrell-chess/private.txt', 'unrelated work')
            write(root, 'packages/tyrell-zero-power-policy/__pycache__/policy.pyc', 'cache')
            with patch.object(zero, 'ROOT', root):
                path, manifest = zero.bundle(job, ['tyrell-zero-power-policy'])
            with tarfile.open(path) as tar:
                names = set(tar.getnames())
            self.assertIn('packages/tyrell-zero-power-policy/policy.py', names)
            self.assertIn('image/contracts/tyrell-zero-power.json', names)
            self.assertNotIn('.env', names)
            self.assertFalse(any('tyrell-chess' in name or '__pycache__' in name for name in names))
            self.assertEqual(names, set(manifest['source_files']) | {'source-manifest.json'})
            self.assertFalse(manifest['device_access'])
            self.assertFalse(manifest['image_build'])

    def test_credential_file_inside_package_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = source_fixture(root)
            write(root, 'packages/tyrell-zero-power-policy/.env.local', 'fake credential')
            with patch.object(zero, 'ROOT', root), self.assertRaisesRegex(RuntimeError, 'Credentials'):
                zero.bundle(job, ['tyrell-zero-power-policy'])
            self.assertFalse((job / 'input.tar').exists())

    def test_package_symlink_cannot_include_an_outside_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = source_fixture(root)
            victim = write(root, 'outside.txt', 'private unrelated input')
            (root / 'packages/tyrell-zero-power-policy/link').symlink_to(victim)
            with patch.object(zero, 'ROOT', root), self.assertRaisesRegex(RuntimeError, 'Symlink'):
                zero.bundle(job, ['tyrell-zero-power-policy'])
            self.assertEqual(victim.read_text(), 'private unrelated input')

    def test_symlinked_contract_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = source_fixture(root)
            contract = root / 'image/contracts/tyrell-zero-power.json'
            contract.unlink()
            contract.parent.rmdir()
            outside = root / 'elsewhere'
            outside.mkdir()
            write(outside, 'tyrell-zero-power.json')
            (root / 'image/contracts').symlink_to(outside, target_is_directory=True)
            with patch.object(zero, 'ROOT', root), self.assertRaisesRegex(RuntimeError, 'Symlink'):
                zero.bundle(job, ['tyrell-zero-power-policy'])

    def test_generated_job_ancestor_cannot_redirect_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'build').mkdir()
            outside = root / 'outside'
            outside.mkdir()
            (root / 'build/zero-packages').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, 'Symlink'):
                zero.generated_path(root / 'build/zero-packages/jobs/new', root=root)
            self.assertEqual(list(outside.iterdir()), [])

    def test_generated_path_rejects_parent_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(RuntimeError):
                zero.generated_path(root / 'build/../../outside', root=root)


class ExportBoundaryTests(unittest.TestCase):
    def check_archive(self, rows, message=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'export.tar'
            destination = root / 'destination'
            destination.mkdir()
            archive(path, rows)
            if message is None:
                zero.extract_export(path, destination)
                return (destination / 'payload').read_bytes()
            with self.assertRaisesRegex(RuntimeError, message):
                zero.extract_export(path, destination)
            self.assertFalse((root / 'outside').exists())

    def test_complete_checksummed_export_is_accepted(self):
        value = b'foundation package bytes'
        self.assertEqual(self.check_archive([
            ('./', b'', tarfile.DIRTYPE),
            ('./payload', value, tarfile.REGTYPE),
            ('./SHA256SUMS', sums('payload', value), tarfile.REGTYPE),
        ]), value)

    def test_parent_and_absolute_escape_are_rejected(self):
        for name in ['../outside', '/outside']:
            with self.subTest(name=name):
                self.check_archive([(name, b'unsafe', tarfile.REGTYPE)], 'Unsafe')

    def test_link_members_are_rejected(self):
        for kind in [tarfile.SYMTYPE, tarfile.LNKTYPE]:
            with self.subTest(kind=kind):
                self.check_archive([('payload', b'', kind, '../outside')], 'Unsafe')

    def test_canonical_duplicate_member_names_are_rejected(self):
        self.check_archive([
            ('payload', b'first', tarfile.REGTYPE),
            ('./payload', b'second', tarfile.REGTYPE),
        ], 'duplicate')

    def test_canonical_duplicate_checksum_names_are_rejected(self):
        value = b'same'
        self.check_archive([
            ('payload', value, tarfile.REGTYPE),
            ('SHA256SUMS', sums('payload', value) + sums('./payload', value), tarfile.REGTYPE),
        ], 'Unsafe checksum')

    def test_unlisted_export_file_is_rejected(self):
        value = b'listed'
        self.check_archive([
            ('payload', value, tarfile.REGTYPE),
            ('unlisted', b'extra', tarfile.REGTYPE),
            ('SHA256SUMS', sums('payload', value), tarfile.REGTYPE),
        ], 'incomplete')

    def test_bad_payload_checksum_is_rejected(self):
        self.check_archive([
            ('payload', b'changed', tarfile.REGTYPE),
            ('SHA256SUMS', sums('payload', b'original'), tarfile.REGTYPE),
        ], 'checksum mismatch')

    def test_oversized_export_is_rejected_before_extraction(self):
        with patch.object(zero, 'MAX_EXPORT', 2):
            self.check_archive([('payload', b'larger', tarfile.REGTYPE)], 'large')


class DispatchTests(unittest.TestCase):
    def test_zero_forwards_full_command_without_lima_requirement(self):
        self.assertEqual(builder.parse_args(['zero', 'build-packages', '--package', 'power']).command,
                         ['build-packages', '--package', 'power'])
        with patch.object(builder.sys, 'argv', ['builder', 'zero', 'check']), \
                patch.object(builder.shutil, 'which', return_value=None), \
                patch.object(builder, 'run') as run:
            builder.main()
        self.assertEqual(run.call_args.args[0][-1], 'check')
        self.assertTrue(run.call_args.args[0][-2].endswith('builder/zero.py'))

    def test_profile_inspection_never_calls_package_builder_or_lima(self):
        report = {'structural_valid': True, 'build_ready': False}
        inspected = []

        def inspect(path, root):
            inspected.append((path, root))
            return report

        out = io.StringIO()
        with patch.object(zero.runpy, 'run_path', return_value={'inspect_profile': inspect}), \
                patch.object(zero, 'build_packages') as build, \
                patch.object(zero.shutil, 'which', side_effect=AssertionError('No VM dependency for inspection')), \
                contextlib.redirect_stdout(out):
            zero.main(['check'])
        self.assertEqual(json.loads(out.getvalue()), report)
        self.assertEqual(len(inspected), 1)
        build.assert_not_called()


if __name__ == '__main__':
    unittest.main()
