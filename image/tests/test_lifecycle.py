import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'build-image.py'
spec = importlib.util.spec_from_file_location('image_builder', MODULE)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def archive(path, recipe=b'recipe', expected=None, job_id='20261113T123456123456Z-abcdef12'):
    metadata = {'job_id': job_id, 'recipe_sha256': {'image/example.py': digest(recipe) if expected is None else expected},
                'package_filename': 'example.deb', 'package_sha256': digest(b'package')}
    with tarfile.open(path, 'w') as tar:
        for name, data in [('image-metadata.json', json.dumps(metadata).encode()),
                           ('image/example.py', recipe), ('assets/example.deb', b'package')]:
            member = tarfile.TarInfo(name); member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    return metadata


class LifecycleTests(unittest.TestCase):
    def test_real_host_lock_excludes_second_operation_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with builder.LOCK(root):
                with self.assertRaisesRegex(RuntimeError, 'Another operation'):
                    with builder.LOCK(root):
                        self.fail('Concurrent operation entered')
            with builder.LOCK(root):
                pass

    def test_build_checks_active_unit_before_any_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            args = builder.parse_args(['--discovery'])
            active = {'LoadState': 'loaded', 'ActiveState': 'active', 'SubState': 'running'}
            resources = {'preflight': lambda *args, **kwargs: None}
            with patch.object(builder, 'B', {'up': lambda: None}), \
                    patch.object(builder, 'unit_state', return_value=active), \
                    patch.object(builder.runpy, 'run_path', return_value=resources), \
                    patch.object(builder, 'prepare') as prepare, patch.object(builder, 'new_job') as new_job:
                with self.assertRaisesRegex(RuntimeError, 'already running'):
                    builder.start_job(Path(directory), args)
            prepare.assert_not_called()
            new_job.assert_not_called()

    def test_archive_detects_source_edit_between_hash_and_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.tar'
            archive(path, expected=digest(b'before edit'))
            with self.assertRaisesRegex(RuntimeError, 'changed while preparing'):
                builder.verify_bundle(path)

    def test_tampered_bundle_is_rejected_before_status_or_export(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            job_id = '20261113T123456123456Z-abcdef12'
            jobdir = target / 'jobs' / job_id; jobdir.mkdir(parents=True)
            archive(jobdir / 'image-input.tar')
            job = {'job_id': job_id, 'guest_input': '/var/tmp/tyrell-image-jobs/' + job_id + '/input',
                   'bundle_sha256': builder.sha(jobdir / 'image-input.tar')}
            (jobdir / 'job.json').write_text(json.dumps(job))
            self.assertEqual(builder.load_job(target, job_id), job)
            with (jobdir / 'image-input.tar').open('ab') as stream: stream.write(b'tamper')
            with self.assertRaisesRegex(RuntimeError, 'missing or changed'):
                builder.load_job(target, job_id)

    def test_durable_result_after_transient_unit_disappears(self):
        job = {'job_id': 'selected', 'guest_input': '/var/tmp/example/input', 'bundle_sha256': 'abc'}
        result = subprocess.CompletedProcess([], 0, json.dumps({'job_id': 'selected', 'exit_status': 0, 'bundle_sha256': 'abc'}))
        with patch.object(builder, 'unit_state', return_value={'LoadState': 'not-found'}), \
                patch.object(builder, 'B', {'guest': lambda *a, **kw: result}):
            state = builder.result_state(job)
            self.assertEqual(state['state'], 'succeeded')
            self.assertEqual(state['guest_input'], job['guest_input'])

    def test_result_for_another_bundle_cannot_authorize_export(self):
        job = {'job_id': 'selected', 'guest_input': '/var/tmp/example/input', 'bundle_sha256': 'expected'}
        result = subprocess.CompletedProcess([], 0, json.dumps({'job_id': 'selected', 'exit_status': 0, 'bundle_sha256': 'wrong'}))
        with patch.object(builder, 'unit_state', return_value={}), \
                patch.object(builder, 'B', {'guest': lambda *a, **kw: result}):
            with self.assertRaisesRegex(RuntimeError, 'different image inputs'):
                builder.result_state(job)

    def test_stale_completed_unit_without_result_fails_closed(self):
        job = {'job_id': 'selected', 'guest_input': '/var/tmp/example/input', 'bundle_sha256': 'expected'}
        result = subprocess.CompletedProcess([], 0, 'null')
        with patch.object(builder, 'unit_state', return_value={'ActiveState': 'active', 'SubState': 'exited'}), \
                patch.object(builder, 'B', {'guest': lambda *a, **kw: result}):
            with self.assertRaisesRegex(RuntimeError, 'refusing stale'):
                builder.result_state(job)

    def test_status_does_not_start_builder_or_prepare_inputs(self):
        with patch.object(builder, 'storage', return_value=Path('/some/storage')), \
                patch.object(builder, 'load_job', return_value={}), \
                patch.object(builder, 'result_state', return_value={'state': 'running'}), \
                patch.object(builder, 'start_job') as start, \
                patch.object(builder, 'LOCK') as lock:
            builder.main(['status'])
        start.assert_not_called(); lock.assert_not_called()

    def test_unlisted_export_file_fails_checksum_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'listed').write_bytes(b'yes')
            (path / 'unlisted').write_bytes(b'no')
            (path / 'SHA256SUMS').write_text(digest(b'yes') + '  listed\n')
            with self.assertRaisesRegex(RuntimeError, 'every artifact'):
                builder.verify_checksums(path)

    def fake_export(self, target, corrupt=False, interrupted=False):
        job_id = '20261113T123456123456Z-abcdef12'
        directory = target / 'jobs' / job_id
        directory.mkdir(parents=True)
        metadata = archive(directory / 'image-input.tar')
        job = {'job_id': job_id, 'bundle_sha256': builder.sha(directory / 'image-input.tar'),
               'guest_input': '/var/tmp/tyrell-image-jobs/' + job_id + '/input'}
        payloads = {'build-inputs.json': json.dumps(dict(metadata, job_id='wrong') if corrupt else metadata).encode(),
                    'image.img.xz': b'fake image'}
        manifest = {'inputs': metadata, 'verification': {'status': 'passed'},
                    'sha256': {name: digest(data) for name, data in payloads.items()}}
        payloads['image-manifest.json'] = json.dumps(manifest).encode()
        payloads['SHA256SUMS'] = ''.join(digest(data) + '  ' + name + '\n' for name, data in payloads.items()).encode()
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w') as tar:
            for name, data in payloads.items():
                member = tarfile.TarInfo(name); member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
        def guest(*args, **kwargs):
            if 'python3' in args:
                return subprocess.CompletedProcess([], 0, '10240')
            kwargs['stdout'].write(output.getvalue())
            if interrupted:
                raise subprocess.CalledProcessError(1, 'interrupted SSH')
            return subprocess.CompletedProcess([], 0)
        return job, guest

    def test_export_retains_verified_input_archive_and_prior_image(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prior = target / 'images/prior/image.img.xz'
            prior.parent.mkdir(parents=True); prior.write_bytes(b'prior image')
            job, guest = self.fake_export(target)
            root = target / 'project'; root.mkdir()
            with patch.object(builder, 'ROOT', root), patch.object(builder, 'assert_idle'), \
                    patch.object(builder, 'result_state', return_value={'state': 'succeeded'}), \
                    patch.object(builder.runpy, 'run_path', return_value={'preflight': lambda *a, **kw: None}), \
                    patch.object(builder, 'B', dict(builder.B, guest=guest)):
                destination = builder.export_job(target, job)
            self.assertEqual(builder.sha(destination / 'image-input.tar'), job['bundle_sha256'])
            self.assertEqual(prior.read_bytes(), b'prior image')
            self.assertEqual((root / 'image-artifacts').resolve(), destination.resolve())
            builder.verify_checksums(destination)

    def test_interrupted_export_publishes_nothing_and_can_be_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            job, guest = self.fake_export(target, interrupted=True)
            with patch.object(builder, 'ROOT', target), patch.object(builder, 'assert_idle'), \
                    patch.object(builder, 'result_state', return_value={'state': 'succeeded'}), \
                    patch.object(builder.runpy, 'run_path', return_value={'preflight': lambda *a, **kw: None}), \
                    patch.object(builder, 'B', dict(builder.B, guest=guest)):
                with self.assertRaises(subprocess.CalledProcessError): builder.export_job(target, job)
            self.assertFalse((target / 'image-artifacts').exists())
            self.assertEqual(list((target / 'images').iterdir()), [])

    def test_export_rejects_output_from_different_job(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            job, guest = self.fake_export(target, corrupt=True)
            with patch.object(builder, 'ROOT', target), patch.object(builder, 'assert_idle'), \
                    patch.object(builder, 'result_state', return_value={'state': 'succeeded'}), \
                    patch.object(builder.runpy, 'run_path', return_value={'preflight': lambda *a, **kw: None}), \
                    patch.object(builder, 'B', dict(builder.B, guest=guest)):
                with self.assertRaisesRegex(RuntimeError, 'does not match'): builder.export_job(target, job)
            self.assertFalse((target / 'image-artifacts').exists())
            self.assertEqual(list((target / 'images').iterdir()), [])

    def test_publication_retry_recovers_complete_export_and_stale_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            job, guest = self.fake_export(target)
            root = target / 'project'; root.mkdir()
            (root / 'image-artifacts').mkdir()
            with patch.object(builder, 'ROOT', root), patch.object(builder, 'assert_idle'), \
                    patch.object(builder, 'result_state', return_value={'state': 'succeeded'}), \
                    patch.object(builder.runpy, 'run_path', return_value={'preflight': lambda *a, **kw: None}), \
                    patch.object(builder, 'B', dict(builder.B, guest=guest)):
                with self.assertRaisesRegex(RuntimeError, 'Preserve or rename image-artifacts'):
                    builder.export_job(target, job)
                destination = target / 'images' / job['job_id']
                self.assertTrue(destination.is_dir())
                (root / 'image-artifacts').rename(root / 'preserved-directory')
                stale = root / ('image-artifacts-' + job['job_id'] + '.partial')
                stale.symlink_to(root / 'missing-old-target')
                # An existing complete export must publish without another SSH
                # output transfer or an additional large free-space budget.
                with patch.object(builder, 'B', dict(builder.B, guest=lambda *a, **kw: self.fail('unexpected transfer'))):
                    builder.export_job(target, job)
            self.assertEqual((root / 'image-artifacts').resolve(), destination.resolve())
            self.assertFalse(stale.is_symlink())
            self.assertTrue((root / 'preserved-directory').is_dir())

    def frozen_archive(self, path, changed=None, extra=False):
        package = b'frozen package'
        index = b'frozen index'
        files = {'pool/package.deb': package, 'dists/stage0/Packages': index}
        lock = {'schema': 1, 'architecture': 'arm64',
                'files': {name: {'bytes': len(data), 'sha256': digest(data)} for name, data in files.items()}}
        lock_bytes = json.dumps(lock).encode()
        metadata = {'job_id': 'example', 'apt_profile': 'frozen', 'apt_lock_sha256': digest(lock_bytes),
                    'recipe_sha256': {'image/example.py': digest(b'recipe'), 'locks/image-apt.json': digest(lock_bytes)},
                    'package_filename': 'example.deb', 'package_sha256': digest(b'package')}
        entries = {'image-metadata.json': json.dumps(metadata).encode(), 'image/example.py': b'recipe',
                   'assets/example.deb': b'package', 'locks/image-apt.json': lock_bytes,
                   'apt-repository/apt-lock.json': lock_bytes,
                   **{'apt-repository/' + name: data for name, data in files.items()}}
        if changed:
            entries['apt-repository/' + changed] += b'changed after host verification'
        if extra:
            entries['apt-repository/unlisted.deb'] = b'new file during bundling'
        with tarfile.open(path, 'w') as tar:
            for name, data in entries.items():
                member = tarfile.TarInfo(name); member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
        return metadata

    def test_captured_frozen_repository_bytes_are_verified_without_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bundle.tar'
            metadata = self.frozen_archive(path)
            self.assertEqual(builder.verify_bundle(path), metadata)
            for changed in ['apt-lock.json', 'pool/package.deb', 'dists/stage0/Packages']:
                with self.subTest(changed=changed):
                    self.frozen_archive(path, changed=changed)
                    with self.assertRaisesRegex(RuntimeError, 'changed while preparing'):
                        builder.verify_bundle(path)
            self.frozen_archive(path, extra=True)
            with self.assertRaisesRegex(RuntimeError, 'file allowlist'):
                builder.verify_bundle(path)

    def test_unsafe_and_build_only_options_are_rejected(self):
        for arguments in [['status', '--resume-from', 'stage2'], ['wait', '--discovery'], ['build', '--job', 'old']]:
            with self.assertRaises(SystemExit): builder.parse_args(arguments)


if __name__ == '__main__':
    unittest.main()
