"""Adversarial byte-lock and private legacy replay tests; no VM/network access."""
import base64
import copy
import contextlib
import gzip
import hashlib
import io
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
import tempfile
import tarfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
API = runpy.run_path(str(ROOT / 'image/zero-lock.py'))


def checksum(data):
    return hashlib.sha256(data).hexdigest()


class ZeroLocks(unittest.TestCase):
    def repository(self, root):
        data = b'test fixture package bytes; not an ARM64 acceptance claim'
        sha = checksum(data); key = ('greetd', '0.10.3-4', 'arm64')
        record = dict(Package=key[0], Version=key[1], Architecture=key[2],
                      file='pool/' + sha + '.deb', sha256=sha, bytes=len(data),
                      source='https://deb.debian.org/debian/pool/greetd.deb')
        target = root / record['file']; target.parent.mkdir(); target.write_bytes(data)
        selected = {stage: {key} for stage in API['STAGES']}
        api = API['legacy_api'](); api['write_indices'](root, selected, {key: record}, {key: dict(Package=key[0], Version=key[1], Architecture=key[2])})
        lock = dict(schema=1, profile='tyrell-zero', kind='tyrell-zero-apt', complete=True,
                    architecture='arm64', packages=[record], stages={stage: [list(key)] for stage in selected},
                    installed=[list(key)], solver_evidence=dict(apt_check='passed', dpkg_audit='clean', receipt_sha256='1' * 64),
                    greetd_authentication=dict(package_sha256_verified=True, inrelease_sha256='2' * 64,
                                               index_sha256='3' * 64, keyring_sha256='4' * 64),
                    files={str(p.relative_to(root)): dict(bytes=p.stat().st_size, sha256=checksum(p.read_bytes())) for p in root.rglob('*') if p.is_file()},
                    repository_metadata={str(p.relative_to(root)): base64.b64encode(gzip.compress(p.read_bytes(), mtime=0)).decode() for p in (root / 'dists').rglob('*') if p.is_file()})
        (root / 'apt-lock.json').write_text(json.dumps(lock))
        return lock

    def test_three_stage_verification_does_not_mutate_five_stage_api(self):
        old = runpy.run_path(str(ROOT / 'image/apt-lock.py'))
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); self.repository(root)
            self.assertEqual(len(API['verify_repository'](root)['stages']), 3)
            self.assertEqual(old['verify_repository'].__globals__['STAGES'], tuple('stage' + str(n) for n in range(5)))
            with self.assertRaisesRegex(ValueError, 'five'):
                old['verify_repository'](root)

    def test_changed_package_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); lock = self.repository(root)
            (root / lock['packages'][0]['file']).write_bytes(b'tamper')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                API['verify_repository'](root)

    def test_extra_file_and_symlink_are_rejected(self):
        for symlink in [False, True]:
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as name:
                root = Path(name); self.repository(root)
                if symlink:
                    (root / 'unexpected').symlink_to('/etc/passwd')
                else:
                    (root / 'unexpected').write_bytes(b'not locked')
                with self.assertRaises(ValueError):
                    API['verify_repository'](root)

    def test_numeric_schema_and_completion_aliases_are_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            lock = self.repository(Path(name))
            for key, value in [('schema', True), ('complete', 1)]:
                changed = copy.deepcopy(lock); changed[key] = value
                with self.assertRaises(ValueError):
                    API['validate'](changed)

    def test_missing_solver_or_signature_proof_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            lock = self.repository(Path(name))
            for key in ['solver_evidence', 'greetd_authentication']:
                changed = copy.deepcopy(lock); changed[key] = {}
                with self.assertRaises(ValueError):
                    API['validate'](changed)

    def test_custom_packages_and_desktop_server_do_not_enter_base_repo(self):
        with tempfile.TemporaryDirectory() as name:
            lock = self.repository(Path(name))
            for package in ['tyrell-status', 'tyrell-zero-session', 'lightdm', 'labwc']:
                changed = copy.deepcopy(lock); changed['packages'][0]['Package'] = package
                with self.assertRaises(ValueError):
                    API['validate'](changed)

    def test_installed_inventory_must_be_exact_unique_subset(self):
        with tempfile.TemporaryDirectory() as name:
            lock = self.repository(Path(name))
            for rows in [[], [['greetd', 'bad', 'arm64']], lock['installed'] * 2, [[True]]]:
                changed = copy.deepcopy(lock); changed['installed'] = rows
                with self.assertRaises(ValueError):
                    API['validate'](changed)

    def test_materialize_missing_local_bytes_is_offline_and_atomic(self):
        with tempfile.TemporaryDirectory() as name:
            parent = Path(name); source = parent / 'source'; source.mkdir()
            self.repository(source); output = parent / 'output'
            with self.assertRaisesRegex(ValueError, 'locally cached'):
                API['materialize'](source / 'apt-lock.json', output)
            self.assertFalse(output.exists())
            self.assertFalse(list(parent.glob('.zero-apt-*')))

    def test_materialize_local_bytes_and_reject_overwrite(self):
        with tempfile.TemporaryDirectory() as name:
            parent = Path(name); source = parent / 'source'; source.mkdir()
            self.repository(source); output = parent / 'output'
            API['materialize'](source / 'apt-lock.json', output, [source])
            API['verify_repository'](output)
            before = (output / 'apt-lock.json').read_bytes()
            with self.assertRaisesRegex(ValueError, 'Preserving'):
                API['materialize'](source / 'apt-lock.json', output, [source])
            self.assertEqual((output / 'apt-lock.json').read_bytes(), before)

    def test_stage_cannot_expose_two_versions_of_one_package(self):
        with tempfile.TemporaryDirectory() as name:
            lock = self.repository(Path(name))
            other = dict(lock['packages'][0], Version='0.10.3-5')
            lock['packages'].append(other)
            lock['stages']['stage2'].append(['greetd', '0.10.3-5', 'arm64'])
            with self.assertRaisesRegex(ValueError, 'stage selection'):
                API['validate'](lock)

    def test_materialize_bounds_expansion_before_metadata_write(self):
        with tempfile.TemporaryDirectory() as name:
            parent = Path(name); source = parent / 'source'; source.mkdir()
            lock = self.repository(source)
            key = 'dists/stage0/main/binary-arm64/Packages'
            lock['files'][key]['bytes'] = 1
            (source / 'apt-lock.json').write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError, 'size/digest'):
                API['materialize'](source / 'apt-lock.json', parent / 'output', [source])
            self.assertFalse((parent / 'output').exists())

    def test_canonical_root_metadata_has_both_profile_aliases_and_contracts(self):
        host = runpy.run_path(str(ROOT / 'builder/zero-lock.py'))
        metadata = host['canonical_metadata']()
        self.assertEqual(metadata['profile'], metadata['profile_id'])
        self.assertEqual(metadata['profile'], 'tyrell-zero')
        self.assertEqual(metadata['stages'], ['stage0', 'stage1', 'stage2'])
        self.assertEqual(set(metadata['locks_sha256']), {
            'tyrell-zero-apt.json', 'tyrell-zero-apps.json', 'tyrell-zero-native-compat.json'})
        for key in ['hardware_contract_sha256', 'power_contract_sha256', 'profile_sha256']:
            self.assertEqual(len(metadata[key]), 64)

    def test_overridden_power_bytes_fail_provenance_before_guest_install(self):
        host = runpy.run_path(str(ROOT / 'builder/zero-lock.py'))
        function = host['verify_custom_inputs']
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); function.__globals__['ROOT'] = root
            native = root / 'native'; native.mkdir()
            (native / 'manifest.json').write_text(json.dumps(dict(milestone='native-wizard-store-integration', status='passed', architecture='arm64', packages={})))
            provenance = root / 'dist/tyrell-status/0.1.0/provenance.json'; provenance.parent.mkdir(parents=True)
            status = root / 'status.deb'; status.write_bytes(b'status-fixture')
            provenance.write_text(json.dumps(dict(debian_version='0.1.0-1', package_sha256=checksum(status.read_bytes()))))
            power = root / 'power/power.deb'; power.parent.mkdir(); power.write_bytes(b'unreviewed-power-bytes')
            (power.parent / 'manifest.json').write_text(json.dumps(dict(package='tyrell-zero-power-policy', architecture='arm64', version='0.1.0-1', package_sha256='0' * 64)))
            control = io.BytesIO()
            with tarfile.open(fileobj=control, mode='w') as archive:
                info = tarfile.TarInfo('control'); info.size = 0; archive.addfile(info, io.BytesIO())
            def inspect(args, **kwargs):
                if '--field' in args: return 'Package: tyrell-status\nVersion: 0.1.0-1\nArchitecture: arm64\n'
                return control.getvalue()
            with patch.object(function.__globals__['subprocess'], 'check_output', inspect):
                with self.assertRaisesRegex(ValueError, 'retained provenance: tyrell-zero-power-policy'):
                    function(native, status, power)

    def test_fresh_root_is_locked_before_activation_but_read_only_recheck_is_not(self):
        for verify_only in (False, True):
            with self.subTest(verify_only=verify_only), tempfile.TemporaryDirectory() as name:
                host = runpy.run_path(str(ROOT / 'builder/zero-lock.py'))
                function = host['qualify_root']; namespace = function.__globals__
                root = Path(name); namespace['ROOT'] = root
                files = ['image/zero_activation.py', 'image/verify-zero.py', 'image/profiles/tyrell-zero.json',
                         'image/contracts/tyrell-zero-hardware.json', 'image/contracts/tyrell-zero-power.json',
                         'packages/tyrell-zero-session/src/state.py', 'packages/tyrell-zero-store/activate-image.py',
                         'packages/tyrell-zero-store/guard.py', 'locks/tyrell-zero-apt.json',
                         'locks/tyrell-zero-apps.json', 'locks/tyrell-zero-native-compat.json']
                for item in files:
                    path = root / item; path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes((ROOT / item).read_bytes())
                apps_path = root / 'locks/tyrell-zero-apps.json'
                apps = json.loads(apps_path.read_text()); apps['qualification']['input_path'] = 'qualification.json'
                apps_path.write_text(json.dumps(apps)); (root / 'qualification.json').write_text('{}')
                vendor = root / 'vendor'
                for item in ['debian.sources', 'raspi.sources']:
                    path = vendor / 'stage0/00-configure-apt/files' / item
                    path.parent.mkdir(parents=True, exist_ok=True); path.write_text('Suites: RELEASE\n')
                metadata = root / 'metadata.json'; metadata.write_text(json.dumps(host['canonical_metadata']()))
                calls = []
                api = dict(shared_lock=contextlib.nullcontext, instance_state=lambda: ('Running', 0),
                           up=lambda: None, assert_guest_idle=lambda: None,
                           guest=lambda *args, **kwargs: calls.append(args), lima=lambda *args: None)
                namespace['export'] = lambda *args: None
                original_run_path = runpy.run_path
                def load(path):
                    if str(path).endswith('scripts/builder'): return api
                    if str(path).endswith('image/zero-lock.py'): return {'validate_inputs': lambda _: None}
                    return original_run_path(path)
                args = SimpleNamespace(action='verify-root' if verify_only else 'qualify-root',
                                       guest_root='/var/tmp/tyrell-zero-lock-fixture/rootfs',
                                       vendor_source=vendor, metadata=metadata)
                commit = json.loads((root / 'image/profiles/tyrell-zero.json').read_text())['pins']['pi_gen_commit']
                with patch.object(runpy, 'run_path', load), patch.object(namespace['subprocess'], 'check_output', return_value=commit):
                    with contextlib.redirect_stdout(io.StringIO()): function(args)
                lock_calls = [i for i, call in enumerate(calls) if 'passwd' in call]
                if verify_only:
                    self.assertEqual(lock_calls, [])
                    self.assertFalse(any('--prepare-runtime' in call for call in calls))
                else:
                    self.assertEqual(len(lock_calls), 1)
                    self.assertEqual(calls[lock_calls[0]], ('sudo', '-n', 'chroot', args.guest_root, 'passwd', '-l', 'root'))
                    activation = next(i for i, call in enumerate(calls) if '--qualification' in call)
                    self.assertLess(lock_calls[0], activation)


if __name__ == '__main__':
    unittest.main()
