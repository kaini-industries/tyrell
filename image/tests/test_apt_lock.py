import base64
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('tyrell_apt_lock', Path(__file__).parents[1] / 'apt-lock.py')
apt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(apt)


class FrozenAptTests(unittest.TestCase):
    def rehash_metadata(self, root, lock, names, refresh_release=True):
        """Model a coherent changed lock, beyond a trivial checksum failure."""
        for name in names:
            path = root / name
            lock['files'][name] = {'bytes': path.stat().st_size, 'sha256': apt.digest(path)}
            lock['repository_metadata'][name] = base64.b64encode(gzip.compress(path.read_bytes(), mtime=0)).decode()
        if refresh_release:
            for suite in {name.split('/')[1] for name in names}:
                name = f'dists/{suite}/Release'
                path = root / name
                header = path.read_text().split('SHA256:', 1)[0]
                prefix = f'dists/{suite}/'
                entries = [(key[len(prefix):], value) for key, value in lock['files'].items()
                           if key.startswith(prefix) and key != name]
                path.write_text(header + 'SHA256:\n' + ''.join(
                    f' {spec["sha256"]} {spec["bytes"]} {relative}\n' for relative, spec in sorted(entries)))
                self.rehash_metadata(root, lock, [name], refresh_release=False)
        (root / 'apt-lock.json').write_text(json.dumps(lock))

    def repository(self, root):
        root.mkdir()
        (root / 'pool').mkdir()
        package = root / 'pool/package.deb'
        package.write_bytes(b'fixed package bytes')
        key = ('example', '1.0', 'arm64')
        record = {**dict(zip(apt.IDENTITY, key)), 'file': 'pool/package.deb',
                  'sha256': apt.digest(package), 'bytes': package.stat().st_size,
                  'source': 'local-build-input'}
        stages = {stage: {key} for stage in apt.STAGES}
        apt.write_indices(root, stages, {key: record}, {key: dict(zip(apt.IDENTITY, key))})
        lock = {'schema': 1, 'architecture': 'arm64', 'stages': {k: list(v) for k, v in stages.items()},
                'packages': [record],
                'repository_metadata': {str(p.relative_to(root)): base64.b64encode(gzip.compress(p.read_bytes(), mtime=0)).decode()
                                        for p in (root / 'dists').rglob('*') if p.is_file()},
                'files': {str(p.relative_to(root)): {'bytes': p.stat().st_size, 'sha256': apt.digest(p)}
                          for p in root.rglob('*') if p.is_file()}}
        (root / 'apt-lock.json').write_text(json.dumps(lock))
        return lock

    def test_valid_complete_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            self.repository(root)
            self.assertEqual(len(apt.verify_repository(root)['packages']), 1)

    def test_custom_application_is_not_frozen_in_os_dependency_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            lock = self.repository(root)
            lock['packages'][0]['Package'] = 'tyrell-status'
            (root / 'apt-lock.json').write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError, 'separate per-job package'):
                apt.verify_repository(root)

    def test_foreign_architecture_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            lock = self.repository(root)
            lock['packages'][0]['Architecture'] = 'armhf'
            (root / 'apt-lock.json').write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError, 'only supports ARM64/all'):
                apt.verify_repository(root)

    def test_package_corruption_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            self.repository(root)
            (root / 'pool/package.deb').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                apt.verify_repository(root)

    def test_extra_file_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            self.repository(root)
            (root / 'unexpected').write_text('unlocked')
            with self.assertRaisesRegex(ValueError, 'allowlist'):
                apt.verify_repository(root)
            (root / 'unexpected').unlink()
            (root / 'unexpected').symlink_to('/etc/passwd')
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                apt.verify_repository(root)

    def test_missing_stage_and_ambiguous_versions_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            lock = self.repository(root)
            del lock['stages']['stage4']
            (root / 'apt-lock.json').write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError, 'all five'):
                apt.verify_repository(root)
            lock['stages']['stage4'] = [['example', '1.0', 'arm64'], ['example', '2.0', 'arm64']]
            (root / 'apt-lock.json').write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError, 'Ambiguous or missing'):
                apt.verify_repository(root)

    def test_materialize_reuses_local_bytes_and_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'repo'
            lock = self.repository(source)
            destination = root / 'restored'
            apt.materialize(source / 'apt-lock.json', destination, [source / 'pool'])
            self.assertEqual(apt.verify_repository(destination), json.loads(json.dumps(lock)))
            with self.assertRaisesRegex(ValueError, 'Preserving existing'):
                apt.materialize(source / 'apt-lock.json', destination)

    def test_missing_local_package_cannot_fall_back_to_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'repo'
            self.repository(source)
            with self.assertRaisesRegex(ValueError, 'exact local package bytes'):
                apt.materialize(source / 'apt-lock.json', root / 'missing')
            self.assertFalse((root / 'missing').exists())

    def test_stage_index_must_match_locked_identity_even_with_updated_file_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            lock = self.repository(root)
            name = 'dists/stage3/main/binary-arm64/Packages'
            path = root / name
            path.write_text(path.read_text().replace('Version: 1.0', 'Version: 2.0'))
            lock['files'][name] = {'bytes': path.stat().st_size, 'sha256': apt.digest(path)}
            (root / 'apt-lock.json').write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError, 'index differs from exact selection'):
                apt.verify_repository(root)

    def test_compressed_candidate_cannot_disagree_even_with_coherent_checksums(self):
        for suite in ['stage2', 'trixie']:
            with self.subTest(suite=suite), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'repo'
                lock = self.repository(root)
                name = f'dists/{suite}/main/binary-arm64/Packages.gz'
                path = root / name
                altered = gzip.decompress(path.read_bytes()).replace(b'Version: 1.0', b'Version: 999.0')
                path.write_bytes(gzip.compress(altered, mtime=0))
                self.rehash_metadata(root, lock, [name])
                with self.assertRaisesRegex(ValueError, 'compressed index differs from plain'):
                    apt.verify_repository(root)

    def test_foreign_architecture_and_component_indices_must_remain_empty(self):
        for relative in ['main/binary-armhf/Packages', 'contrib/binary-arm64/Packages',
                         'non-free/binary-arm64/Packages']:
            with self.subTest(index=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'repo'
                lock = self.repository(root)
                name = 'dists/stage1/' + relative
                plain = (root / 'dists/stage1/main/binary-arm64/Packages').read_bytes()
                (root / name).write_bytes(plain)
                (root / (name + '.gz')).write_bytes(gzip.compress(plain, mtime=0))
                self.rehash_metadata(root, lock, [name, name + '.gz'])
                with self.assertRaisesRegex(ValueError, 'unsupported component/architecture'):
                    apt.verify_repository(root)

    def test_release_cannot_omit_or_duplicate_index_coverage(self):
        for mutation in ['omit', 'duplicate']:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'repo'
                lock = self.repository(root)
                name = 'dists/stage4/Release'
                path = root / name
                lines = path.read_text().splitlines(keepends=True)
                entry = next(line for line in lines if line.startswith(' ') and 'Packages.gz' in line)
                text = ''.join(lines).replace(entry, '' if mutation == 'omit' else entry + entry, 1)
                path.write_text(text)
                self.rehash_metadata(root, lock, [name], refresh_release=False)
                with self.assertRaisesRegex(ValueError, 'Release'):
                    apt.verify_repository(root)

    def test_alternate_index_format_is_rejected_even_when_locked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            lock = self.repository(root)
            name = 'dists/stage0/main/binary-arm64/Packages.xz'
            (root / name).write_bytes(b'coherently locked alternate candidate index')
            self.rehash_metadata(root, lock, [name])
            with self.assertRaisesRegex(ValueError, 'index layout'):
                apt.verify_repository(root)

    def test_frozen_build_sources_restore_usable_runtime_repositories_last(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / 'repo'
            self.repository(repository)
            vendor = root / 'vendor'
            fixtures = {
                'scripts/common': 'BOOTSTRAP_ARGS+=(--arch arm64)\n',
                'stage0/prerun.sh': 'bootstrap trixie root http://deb.debian.org/debian/\n',
                'stage0/00-configure-apt/00-run.sh': '#!/bin/bash\n',
                'stage0/00-configure-apt/files/debian.sources': 'Suites: RELEASE\n',
                'stage0/00-configure-apt/files/raspi.sources': 'Suites: RELEASE\n',
                'stage0/02-firmware/01-run.sh': 'curl "https://archive.raspberrypi.com/debian/pool/main/l/linux/${filename}"\n',
                'export-image/02-set-sources/01-run.sh': 'apt-get update\napt-get dist-upgrade\n',
                'tyrell-prepared.json': '{"prepared_files_sha256":{}}',
            }
            fixtures.update({stage + '/prerun.sh': '#!/bin/bash\ncopy_previous\n' for stage in apt.STAGES[1:]})
            for name, content in fixtures.items():
                path = vendor / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            apt.configure_vendor(vendor, repository)
            export = (vendor / 'export-image/02-set-sources/01-run.sh').read_text()
            self.assertLess(export.index('apt-get dist-upgrade'), export.index('tyrell-runtime-apt/debian.sources'))
            self.assertIn('file:/usr/local/share/tyrell/camera-repo ./', export)
            self.assertIn('--no-check-gpg', (vendor / 'scripts/common').read_text())
            self.assertNotIn('deb.debian.org', (vendor / 'stage0/prerun.sh').read_text())
            configure = (vendor / 'stage0/00-configure-apt/00-run.sh').read_text()
            self.assertIn('raspberrypi-archive-keyring.pgp', configure)
            self.assertIn('dpkg --add-architecture armhf', configure)
            report = json.loads((vendor / 'tyrell-prepared.json').read_text())
            self.assertEqual(report['apt_profile'], 'frozen')
            self.assertIn('export-image/02-set-sources/01-run.sh', report['prepared_files_sha256'])


if __name__ == '__main__':
    unittest.main()
