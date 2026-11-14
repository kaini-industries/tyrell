import copy
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


VERIFY_PATH = Path(__file__).resolve().parents[1] / 'verify-image.py'
verify = runpy.run_path(str(VERIFY_PATH))
VerificationError = verify['VerificationError']


def checksum(data):
    return hashlib.sha256(data).hexdigest()


class StockVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.lock_sha = 'a' * 64
        self.package_sha = 'b' * 64
        self.lock = {
            'schema_version': 1, 'kind': 'cardputerzero-stock-image', 'board': 'v5',
            'pi_gen_commit': '1' * 40, 'drivers_commit': '2' * 40,
            'kernel_release': '6.20.1+rpt-rpi-v8',
            'kernel_package_version': '1:6.20.1-1+rpt1',
            'applaunch_version': '0.7.0',
            'image': {'name': 'stock.img.xz', 'url': 'https://example.test/stock.img.xz',
                      'bytes': 100, 'sha256': 'c' * 64},
        }
        directories = ('boot/firmware', 'lib/modules', 'lib/firmware',
                       'etc/alternatives',
                       'lib/modules/' + self.lock['kernel_release'],
                       'lib/modules/' + self.lock['kernel_release'] + '/extra',
                       'usr/share/APPLaunch', 'usr/share/APPLaunch/bin',
                       'usr/share/APPLaunch/applications', 'usr/share/APPLaunch/share',
                       'usr/share/APPLaunch/share/images')
        files = {
            'boot/firmware/config.txt': b'dtoverlay=cardputerzero-v5-overlay\n',
            'boot/firmware/start.elf': b'vendor firmware',
            'boot/firmware/fixup.dat': b'vendor fixup',
            'lib/modules/' + self.lock['kernel_release'] + '/extra/keyboard.ko': b'vendor module',
            'lib/firmware/panel.bin': b'vendor panel',
            'usr/share/APPLaunch/bin/M5CardputerZero-APPLaunch': b'vendor launcher',
            'etc/cardputerzero-dtoverlays.commit': (self.lock['drivers_commit'] + '\n').encode(),
        }
        protected = {path: {'type': 'directory'} for path in directories}
        for path in directories:
            (self.root / path).mkdir(parents=True, exist_ok=True)
        for path, content in files.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_bytes(content)
            protected[path] = {'type': 'file', 'sha256': checksum(content)}
        link = 'lib/modules/' + self.lock['kernel_release'] + '/build'
        (self.root / link).symlink_to('/usr/src/vendor-headers')
        protected[link] = {'type': 'symlink', 'target': '/usr/src/vendor-headers'}
        for path, identity in protected.items():
            details = (self.root / path).lstat()
            identity.update(mode=details.st_mode & 0o7777, uid=details.st_uid, gid=details.st_gid)
        self.evidence = {
            'schema_version': 1, 'stock_lock_sha256': self.lock_sha,
            'stock_base': {'filename': self.lock['image']['name'], 'bytes': 100,
                           'sha256': self.lock['image']['sha256'],
                           'uncompressed_bytes': 2048,
                           'uncompressed_sha256': 'd' * 64},
            'raw_image_sha256': 'e' * 64, 'package_sha256': self.package_sha,
            'identity': verify['expected_identity'](self.lock),
            'packages_before': {
                'applaunch': {'Version': self.lock['applaunch_version'],
                              'Architecture': 'arm64', 'Status': 'install ok installed'},
                'linux-image-rpi-v8': {'Version': self.lock['kernel_package_version'],
                                       'Architecture': 'arm64', 'Status': 'hold ok installed'},
            },
            'protected_files': protected,
            # This claimed verdict is deliberately not used by the verifier.
            'protected_unchanged': True,
        }
        for path in verify['STOCK_ADDITIONS']:
            (self.root / path).write_bytes(b'Tyrell app addition')

    def stock_context(self):
        return {'lock': self.lock, 'lock_sha256': self.lock_sha, 'evidence': self.evidence}

    def test_source_mode_retains_original_expected_versions(self):
        identity = verify['expected_identity']()
        self.assertEqual(identity, {
            'pi_gen_commit': verify['PI_GEN_COMMIT'], 'drivers_commit': verify['DRIVERS_COMMIT'],
            'kernel_release': verify['KERNEL'], 'kernel_package_version': verify['KERNEL_PACKAGE'],
            'applaunch_version': verify['LAUNCHER_VERSION'],
        })
        metadata = {**identity, 'package_version': verify['PACKAGE_VERSION'],
                    'package_sha256': self.package_sha}
        verify['validate_expected_metadata'](metadata, identity, self.package_sha)
        self.assertIsNone(verify['load_stock_inputs']())

    def test_stock_mode_accepts_changed_expected_versions_without_old_constants(self):
        identity = verify['expected_identity'](self.lock)
        self.assertEqual(identity['kernel_release'], self.lock['kernel_release'])
        self.assertNotEqual(identity['kernel_release'], verify['KERNEL'])
        metadata = {**identity, 'package_version': verify['PACKAGE_VERSION'],
                    'package_sha256': self.package_sha,
                    'build_mode': 'stock-image', 'apt_profile': 'stock-image',
                    'stock_image_lock_sha256': self.lock_sha,
                    'stock_uncompressed_bytes': self.evidence['stock_base']['uncompressed_bytes'],
                    'stock_base_sha256': self.lock['image']['sha256']}
        verify['validate_expected_metadata'](metadata, identity, self.package_sha, self.stock_context())
        metadata['applaunch_version'] = verify['LAUNCHER_VERSION']
        with self.assertRaisesRegex(VerificationError, 'applaunch_version'):
            verify['validate_expected_metadata'](metadata, identity, self.package_sha, self.stock_context())

    def test_stock_evidence_must_bind_same_lock_base_and_identity(self):
        verify['validate_stock_evidence'](self.lock, self.evidence, self.lock_sha)
        mutations = (
            ('stock_lock_sha256', 'f' * 64),
            ('stock_base', {**self.evidence['stock_base'], 'sha256': 'f' * 64}),
            ('identity', {**self.evidence['identity'], 'drivers_commit': '3' * 40}),
        )
        for key, value in mutations:
            with self.subTest(field=key):
                evidence = {**self.evidence, key: value}
                with self.assertRaises(VerificationError):
                    verify['validate_stock_evidence'](self.lock, evidence, self.lock_sha)

    def test_uncompressed_size_is_required_and_bound_before_mounting(self):
        for size in (None, True, 0, -1, '2048'):
            with self.subTest(size=size):
                evidence = copy.deepcopy(self.evidence)
                evidence['stock_base']['uncompressed_bytes'] = size
                with self.assertRaisesRegex(VerificationError, 'uncompressed stock image size'):
                    verify['validate_stock_evidence'](self.lock, evidence, self.lock_sha)
        image = self.root / 'wrong-size.img'
        image.write_bytes(b'wrong size')
        args = SimpleNamespace(image=image, output_dir=self.root / 'reports')
        stock = self.stock_context()
        report = {'checks': []}
        with mock.patch.dict(verify['verify'].__globals__, {'load_stock_inputs': lambda *args: stock}), \
                mock.patch.object(verify['os'], 'geteuid', return_value=0), \
                mock.patch.object(verify['os'], 'uname', return_value=SimpleNamespace(machine='aarch64')), \
                mock.patch.object(verify['shutil'], 'which', return_value='/usr/bin/tool'), \
                mock.patch.dict(verify['verify'].__globals__, {'command': mock.Mock()}) as patched:
            with self.assertRaisesRegex(VerificationError, 'stock_evidence_final_image_size'):
                verify['verify'](args, report)
            patched['command'].assert_not_called()

    def test_stock_password_policy_requires_locked_accounts(self):
        for password in ('!', '*', '!*'):
            self.assertTrue(verify['unprovisioned_password']('pi', password, stock_mode=True))
        self.assertFalse(verify['unprovisioned_password']('pi', '', stock_mode=True))
        self.assertFalse(verify['unprovisioned_password']('pi', '$6$preset-hash', stock_mode=True))
        self.assertTrue(verify['unprovisioned_password']('pi', '', stock_mode=False))
        self.assertFalse(verify['unprovisioned_password']('root', '', stock_mode=False))

    def test_loaded_evidence_is_bound_to_actual_lock_file_bytes(self):
        lock_path, evidence_path = self.root / 'stock-lock.json', self.root / 'stock-evidence.json'
        lock_path.write_text(json.dumps(self.lock))
        evidence = {**self.evidence, 'stock_lock_sha256': checksum(lock_path.read_bytes())}
        evidence_path.write_text(json.dumps(evidence))
        with mock.patch.object(verify['runpy'], 'run_path', return_value={'load_lock': lambda path: self.lock}):
            result = verify['load_stock_inputs'](lock_path, evidence_path)
            self.assertEqual(result['lock_sha256'], checksum(lock_path.read_bytes()))
            self.assertEqual(result['evidence_sha256'], checksum(evidence_path.read_bytes()))
            lock_path.write_text(json.dumps(self.lock, indent=2))
            with self.assertRaisesRegex(VerificationError, 'different lock'):
                verify['load_stock_inputs'](lock_path, evidence_path)

    def test_snapshot_must_cover_critical_vendor_paths_and_exclude_tyrell(self):
        for path in ('boot/firmware/config.txt', 'lib/modules',
                     'usr/share/APPLaunch/bin/M5CardputerZero-APPLaunch'):
            with self.subTest(path=path):
                evidence = copy.deepcopy(self.evidence)
                del evidence['protected_files'][path]
                with self.assertRaisesRegex(VerificationError, 'omits required'):
                    verify['validate_stock_evidence'](self.lock, evidence, self.lock_sha)
        evidence = copy.deepcopy(self.evidence)
        evidence['protected_files'][next(iter(verify['STOCK_ADDITIONS']))] = {'type': 'file', 'sha256': 'f' * 64}
        with self.assertRaisesRegex(VerificationError, 'already contains Tyrell'):
            verify['validate_stock_evidence'](self.lock, evidence, self.lock_sha)

    def test_complete_stock_payload_and_absolute_header_symlink_are_preserved(self):
        self.assertEqual(verify['compare_stock_protected'](self.root, self.evidence),
                         len(self.evidence['protected_files']))

    def test_overlay_snapshot_identity_and_inventory_match_verifier_contract(self):
        overlay = runpy.run_path(str(VERIFY_PATH.with_name('overlay-stock.py')))
        packages = copy.deepcopy(self.evidence['packages_before'])
        packages['linux-headers-rpi-v8'] = {
            'Version': self.lock['kernel_package_version'], 'Architecture': 'arm64',
            'Status': 'hold ok installed'}
        status = self.root / 'var/lib/dpkg/status'
        status.parent.mkdir(parents=True)
        status.write_text('\n\n'.join('Package: ' + name + '\n' +
                          '\n'.join(key + ': ' + value for key, value in fields.items())
                          for name, fields in packages.items()))
        (self.root / 'etc/rpi-issue').write_text('Generated using pi-gen ' + self.lock['pi_gen_commit'])
        (self.root / 'etc/os-release').write_text('VERSION_CODENAME=trixie\n')
        (self.root / 'boot/firmware/config.txt').write_text('\n'.join(
            'dtoverlay=' + name for name in ('cardputerzero-v5-overlay', 'bq27220_v5',
                                            'bmi270_bmm150_overlay')) + '\n')
        for path in verify['STOCK_ADDITIONS']:
            (self.root / path).unlink()
        evidence = {**self.evidence, 'identity': overlay['stock_identity'](self.root, self.lock),
                    'packages_before': overlay['package_inventory'](self.root),
                    'protected_files': overlay['snapshot'](self.root)}
        verify['validate_stock_evidence'](self.lock, evidence, self.lock_sha)
        for path in verify['STOCK_ADDITIONS']:
            (self.root / path).write_bytes(b'Tyrell app addition')
        verify['compare_stock_protected'](self.root, evidence)
        packages['tyrell-status'] = {'Version': verify['PACKAGE_VERSION'], 'Architecture': 'arm64',
                                     'Status': 'install ok installed'}
        verify['compare_stock_packages'](packages, evidence)

    def test_claimed_success_does_not_hide_protected_content_tampering(self):
        (self.root / 'boot/firmware/start.elf').write_bytes(b'changed boot firmware')
        with self.assertRaisesRegex(VerificationError, 'content differs: boot/firmware/start.elf'):
            verify['compare_stock_protected'](self.root, self.evidence)

    def test_added_deleted_or_redirected_vendor_paths_are_rejected(self):
        added = self.root / 'usr/share/APPLaunch/bin/unexpected'
        added.write_bytes(b'unexpected')
        with self.assertRaisesRegex(VerificationError, 'path set differs'):
            verify['compare_stock_protected'](self.root, self.evidence)
        added.unlink()
        panel = self.root / 'lib/firmware/panel.bin'
        panel.unlink()
        with self.assertRaisesRegex(VerificationError, 'path set differs'):
            verify['compare_stock_protected'](self.root, self.evidence)
        panel.symlink_to('/tmp/host-file')
        with self.assertRaisesRegex(VerificationError, 'content differs: lib/firmware/panel.bin'):
            verify['compare_stock_protected'](self.root, self.evidence)

    def test_protected_file_mode_changes_are_detected(self):
        path = self.root / 'usr/share/APPLaunch/bin/M5CardputerZero-APPLaunch'
        self.evidence['protected_files']['usr/share/APPLaunch/bin/M5CardputerZero-APPLaunch']['mode'] = 0o755
        path.chmod(0o644)
        with self.assertRaisesRegex(VerificationError, 'content differs'):
            verify['compare_stock_protected'](self.root, self.evidence)

    def test_schema_one_requires_exact_identity_fields_including_permissions(self):
        for name in ('boot/firmware', 'boot/firmware/start.elf',
                     'lib/modules/' + self.lock['kernel_release'] + '/build'):
            for field in ('mode', 'uid', 'gid'):
                with self.subTest(path=name, field=field):
                    evidence = copy.deepcopy(self.evidence)
                    del evidence['protected_files'][name][field]
                    with self.assertRaisesRegex(VerificationError, 'Incomplete or unsupported'):
                        verify['validate_stock_evidence'](self.lock, evidence, self.lock_sha)
        evidence = copy.deepcopy(self.evidence)
        evidence['protected_files']['boot/firmware/start.elf']['unexpected'] = True
        with self.assertRaisesRegex(VerificationError, 'Incomplete or unsupported'):
            verify['validate_stock_evidence'](self.lock, evidence, self.lock_sha)

    def test_mode_change_cannot_pass_an_accepted_schema_one_snapshot(self):
        verify['validate_stock_evidence'](self.lock, self.evidence, self.lock_sha)
        (self.root / 'usr/share/APPLaunch/bin/M5CardputerZero-APPLaunch').chmod(0o700)
        with self.assertRaisesRegex(VerificationError, 'content differs'):
            verify['compare_stock_protected'](self.root, self.evidence)

    def test_active_firmware_alternative_retargeting_is_rejected(self):
        name = 'etc/alternatives/regulatory.db'
        path = self.root / name
        path.symlink_to('/lib/firmware/regulatory.db.vendor')
        details = path.lstat()
        self.evidence['protected_files'][name] = {
            'type': 'symlink', 'target': '/lib/firmware/regulatory.db.vendor',
            'mode': details.st_mode & 0o7777, 'uid': details.st_uid, 'gid': details.st_gid}
        verify['validate_stock_evidence'](self.lock, self.evidence, self.lock_sha)
        path.unlink()
        path.symlink_to('/lib/firmware/regulatory.db.changed')
        with self.assertRaisesRegex(VerificationError, 'content differs: etc/alternatives/regulatory.db'):
            verify['compare_stock_protected'](self.root, self.evidence)

    def test_stock_owner_changes_and_invalid_owner_ids_are_rejected(self):
        name = 'usr/share/APPLaunch/bin/M5CardputerZero-APPLaunch'
        for field in ('uid', 'gid'):
            with self.subTest(field=field):
                evidence = copy.deepcopy(self.evidence)
                evidence['protected_files'][name][field] += 1
                verify['validate_stock_evidence'](self.lock, evidence, self.lock_sha)
                with self.assertRaisesRegex(VerificationError, 'content differs'):
                    verify['compare_stock_protected'](self.root, evidence)
            for invalid in (True, -1, 0x100000000):
                with self.subTest(field=field, invalid=invalid):
                    evidence = copy.deepcopy(self.evidence)
                    evidence['protected_files'][name][field] = invalid
                    with self.assertRaisesRegex(VerificationError, 'Invalid protected file ' + field):
                        verify['validate_stock_evidence'](self.lock, evidence, self.lock_sha)

    def test_symlinked_protected_root_cannot_hide_firmware_changes(self):
        (self.root / 'lib/firmware').rename(self.root / 'vendor-firmware')
        (self.root / 'lib/firmware').symlink_to('../vendor-firmware', target_is_directory=True)
        with self.assertRaisesRegex(VerificationError, 'Unsupported protected stock root type: lib/firmware'):
            verify['protected_snapshot'](self.root)
        evidence = copy.deepcopy(self.evidence)
        details = (self.root / 'lib/firmware').lstat()
        evidence['protected_files']['lib/firmware'] = {
            'type': 'symlink', 'mode': details.st_mode & 0o7777,
            'uid': details.st_uid, 'gid': details.st_gid,
            'target': '../vendor-firmware'}
        del evidence['protected_files']['lib/firmware/panel.bin']
        with self.assertRaisesRegex(VerificationError, 'Unsupported protected stock root type: lib/firmware'):
            verify['validate_stock_evidence'](self.lock, evidence, self.lock_sha)

    def test_package_inventory_preserves_vendor_versions_architecture_and_holds(self):
        final = {**copy.deepcopy(self.evidence['packages_before']),
                 'tyrell-status': {'Version': verify['PACKAGE_VERSION'], 'Architecture': 'arm64',
                                   'Status': 'install ok installed'}}
        self.assertEqual(verify['compare_stock_packages'](final, self.evidence), 2)
        mutations = (
            ('Version', '0.8.0'), ('Architecture', 'armhf'), ('Status', 'deinstall ok config-files'),
        )
        for key, value in mutations:
            with self.subTest(field=key):
                changed = copy.deepcopy(final)
                changed['applaunch'][key] = value
                with self.assertRaisesRegex(VerificationError, 'inventory differs: applaunch'):
                    verify['compare_stock_packages'](changed, self.evidence)
        final['linux-image-rpi-v8']['Status'] = 'install ok installed'
        with self.assertRaisesRegex(VerificationError, 'linux-image-rpi-v8'):
            verify['compare_stock_packages'](final, self.evidence)

    def test_package_inventory_rejects_unrelated_addition_deletion_or_wrong_tyrell(self):
        final = {**copy.deepcopy(self.evidence['packages_before']),
                 'tyrell-status': {'Version': verify['PACKAGE_VERSION'], 'Architecture': 'arm64',
                                   'Status': 'install ok installed'}}
        changed = {**final, 'unexpected': {'Version': '1', 'Architecture': 'all', 'Status': 'install ok installed'}}
        with self.assertRaisesRegex(VerificationError, 'path set differs'):
            verify['compare_stock_packages'](changed, self.evidence)
        changed = {name: fields for name, fields in final.items() if name != 'applaunch'}
        with self.assertRaisesRegex(VerificationError, 'path set differs'):
            verify['compare_stock_packages'](changed, self.evidence)
        final['tyrell-status']['Version'] = 'unexpected'
        with self.assertRaisesRegex(VerificationError, 'Unexpected Tyrell'):
            verify['compare_stock_packages'](final, self.evidence)

    def test_stock_metadata_requires_both_source_digests(self):
        identity = verify['expected_identity'](self.lock)
        metadata = {**identity, 'package_version': verify['PACKAGE_VERSION'],
                    'package_sha256': self.package_sha,
                    'build_mode': 'stock-image', 'apt_profile': 'stock-image',
                    'stock_image_lock_sha256': self.lock_sha,
                    'stock_uncompressed_bytes': self.evidence['stock_base']['uncompressed_bytes'],
                    'stock_base_sha256': self.lock['image']['sha256']}
        for name in ('stock_image_lock_sha256', 'stock_base_sha256', 'stock_uncompressed_bytes',
                     'build_mode', 'apt_profile'):
            with self.subTest(field=name):
                changed = {**metadata, name: 'f' * 64}
                with self.assertRaisesRegex(VerificationError, name):
                    verify['validate_expected_metadata'](changed, identity, self.package_sha, self.stock_context())

    def test_unpaired_stock_cli_options_fail_before_output_creation(self):
        for option in ('--stock-lock', '--stock-evidence'):
            with self.subTest(option=option):
                output = self.root / 'not-created'
                result = subprocess.run([sys.executable, str(VERIFY_PATH), str(self.root / 'missing.img'),
                                         '--output-dir', str(output), option, str(self.root / 'missing.json')],
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn('must be supplied together', result.stderr)
                self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
