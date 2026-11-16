"""Native build transport/receipt boundaries; never invokes Lima or real sudo."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('zero_integration_boundaries', ROOT / 'builder/zero-integration.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
spec = importlib.util.spec_from_file_location('zero_integration_safety', ROOT / 'builder/zero.py')
safe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safe)
BASELINE = '/Volumes/garage/tyrell-build/downloads/applaunch_0.6.105-m5stack1_arm64.deb'
BASELINE_SHA = 'a38cffde9cb4d60f4ff2d6c95323c64f66cc705fdf315b0550e123ca7b5a9544'
CONFIG_SHA = '1df551d93c93925eeaa1050fb1bb71c7e82813464e0a27fe6be3dd201075344b'


def write(root, name, data='fixture'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    return path


class ReceiptBoundary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wizard = self.root / 'packages/tyrell-zero-session/wizard'
        self.config_inputs = {'generic.mk': '1' * 64, 'cardputer.mk': '2' * 64}
        self.patch_identity = {'schema': 1, 'patch_sha256': '3' * 64}
        for name in ['builder/zero-vendor.py', 'builder/zero-native-guest.py', 'builder/build-zero-native.sh',
                     'locks/tyrell-zero-vendor-inputs.json', 'build/zero-vendor/downloads/input.tar.gz',
                     'packages/tyrell-zero-session/wizard/apply-patches.py']:
            write(self.root, name)
        write(self.root, 'packages/tyrell-zero-session/wizard/patches/identity.json', json.dumps(self.patch_identity))
        write(self.root, 'packages/tyrell-zero-session/wizard/native_config.py')
        self.baseline = write(self.root, 'cached-vendor.deb')
        self.artifacts = self.root / 'artifacts/native/job'
        write(self.artifacts, 'LaunchWizard', 'native-elf-fixture')
        self.artifact_manifest = self.artifacts / 'manifest.json'

        def digest(path):
            if Path(path) == self.baseline:
                return BASELINE_SHA
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()
        self.digest = digest

        def api(path):
            path = str(path)
            if path.endswith('/scripts/builder'):
                return {'digest': digest}
            if path.endswith('/builder/zero.py'):
                return {'regular': safe.regular, 'generated_path': lambda p: safe.generated_path(p, self.root)}
            if path.endswith('/builder/zero-vendor.py'):
                return {'load_lock': lambda: {'archives': [{'filename': 'input.tar.gz'}]},
                        'verify_inputs': lambda *_: None}
            if path.endswith('/wizard/native_config.py'):
                return {'INPUT_SHA256': self.config_inputs, 'OUTPUT': 'tyrell_native_config.mk'}
            raise AssertionError('Unexpected API: ' + path)
        def mocked(*args, **kwargs):
            handle = patch.object(*args, **kwargs)
            self.addCleanup(handle.stop)
            return handle.start()
        mocked(native, 'ROOT', self.root)
        mocked(native.runpy, 'run_path', side_effect=api)
        self.execute = mocked(native, 'execute')
        files = {name: digest(path) for name, path in sorted(native.wizard_inputs().items())}
        self.receipt = {'schema': 1, 'status': 'passed', 'architecture': 'arm64',
                        'network_disabled': True, 'device_access': False, 'milestone': 'native-wizard-build',
                        'source_files': files,
                        'source_sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
                        'patch_identity': self.patch_identity,
                        'native_config': {'inputs': self.config_inputs, 'sha256': CONFIG_SHA,
                                          'config': 'tyrell_native_config.mk', 'cross_toolchain': False},
                        'wizard_sha256': digest(self.artifacts / 'LaunchWizard')}

    def save_receipt(self):
        self.artifact_manifest.write_text(json.dumps(self.receipt))

    def reject(self, message):
        self.save_receipt()
        with self.assertRaisesRegex(RuntimeError, message):
            native.package_build(self.artifacts, self.baseline)
        self.execute.assert_not_called()

    def test_empty_or_subset_inventory_is_not_a_native_build_receipt(self):
        original = self.receipt['source_files']
        for files in [{}, dict(list(original.items())[1:])]:
            with self.subTest(count=len(files)):
                self.receipt['source_files'] = files
                self.reject('inventory changed')

    def test_aggregate_source_identity_is_recomputed(self):
        self.receipt['source_sha256'] = '0' * 64
        self.reject('inventory changed')

    def test_changed_current_patch_input_invalidates_old_build(self):
        (self.wizard / 'apply-patches.py').write_text('new patch transform')
        self.reject('inventory changed')

    def test_receipt_patch_and_backend_config_cannot_be_substituted(self):
        cases = [('patch_identity', {'schema': 1}),
                 ('native_config', dict(self.receipt['native_config'], cross_toolchain=True)),
                 ('native_config', dict(self.receipt['native_config'], sha256='0' * 64))]
        original = dict(self.receipt)
        for key, value in cases:
            with self.subTest(field=key):
                self.receipt = dict(original, **{key: value})
                self.reject('patch/configuration identity')

    def test_changed_native_elf_is_rejected(self):
        (self.artifacts / 'LaunchWizard').write_text('different ELF')
        self.reject('artifact changed')

    def test_credentials_and_links_in_wizard_tree_are_rejected(self):
        secret = write(self.wizard, '.env.local', 'fake connection credential')
        with self.assertRaisesRegex(RuntimeError, 'Unsafe native Wizard source'):
            native.wizard_inputs()
        secret.unlink()
        outside = write(self.root, 'unrelated-private', 'not an input')
        (self.wizard / 'private-link').symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, 'Unsafe native Wizard source'):
            native.wizard_inputs()

    def test_valid_receipt_preserves_explicit_transfer_boundary(self):
        for name in ['builder/zero-integration-guest.py', 'builder/zero-store-lifecycle.py',
                     'locks/tyrell-zero-build-inputs.json',
                     'dist/tyrell-status/0.1.0/tyrell-status_0.1.0-1_arm64.deb',
                     'dist/tyrell-video/0.1.6/tyrell-video_0.1.6-1_arm64.deb',
                     'dist/tyrell-video/0.1.7/tyrell-video_0.1.7-1_arm64.deb',
                     'build/zero-integration/vendor/M5CardputerZero-APPLaunch',
                     'build/zero-integration/vendor/M5CardputerZero-AppStore']:
            write(self.root, name)
        write(self.root, '.env', 'fake handheld credentials')
        write(self.root, 'packages/unrelated-app/private', 'unrelated work')
        self.save_receipt()
        vendor = {'vendor/applaunch.deb':self.baseline}
        for binary in ['M5CardputerZero-APPLaunch', 'M5CardputerZero-AppStore']:
            vendor['vendor/' + binary] = self.root / 'build/zero-integration/vendor' / binary
        with patch.object(native, 'baseline_assets', return_value=vendor) as baseline:
            native.package_build(self.artifacts, self.baseline)
            baseline.assert_called_once_with(self.baseline)
        paths, kind = self.execute.call_args.args
        self.assertEqual(kind, 'integration')
        self.assertFalse(any('.env' in name or 'unrelated-app' in name for name in paths))
        self.assertEqual(paths['wizard/LaunchWizard'], self.artifacts / 'LaunchWizard')
        self.assertEqual(paths['vendor/applaunch.deb'], self.baseline)

    def test_vendor_baseline_rejects_wrong_bytes_and_symlink_before_ar(self):
        bad = write(self.root, 'wrong-vendor.deb', 'unqualified')
        link = self.root / 'vendor-link';link.symlink_to(self.baseline)
        with patch.object(native.subprocess, 'check_output') as execute:
            for value in [bad, link]:
                with self.assertRaisesRegex(RuntimeError, 'Exact cached vendor'):
                    native.baseline_assets(value)
            execute.assert_not_called()


class GuestLockBoundary(unittest.TestCase):
    def test_shell_holds_lock_while_sudo_closes_inherited_descriptors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = root / 'commands'; commands.mkdir()
            lock_dir = root / 'locks'
            ready, release = root / 'ready', root / 'release'
            python = os.sys.executable
            flock = write(commands, 'flock', '#!' + python + '\nimport fcntl,sys\nfcntl.flock(int(sys.argv[-1]),fcntl.LOCK_EX|fcntl.LOCK_NB)\n')
            sudo = write(commands, 'sudo', '#!' + python + '\nimport os,pathlib,time\nos.closerange(3,256)\npathlib.Path(' + repr(str(ready)) + ').touch()\nend=time.monotonic()+10\nwhile not pathlib.Path(' + repr(str(release)) + ').exists() and time.monotonic()<end:time.sleep(0.01)\n')
            flock.chmod(0o755); sudo.chmod(0o755)
            wrapper = root / 'build.sh'
            wrapper.write_text((ROOT / 'builder/build-zero-native.sh').read_text().replace('/var/tmp/tyrell-locks', str(lock_dir)))
            child = subprocess.Popen(['bash', str(wrapper), str(root / 'input'), str(root / 'output'), 'wizard'],
                                     env=dict(os.environ, PATH=str(commands) + ':' + os.environ['PATH']),
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                end = time.monotonic() + 5
                while not ready.exists() and child.poll() is None and time.monotonic() < end:
                    time.sleep(0.01)
                self.assertTrue(ready.exists(), 'wrapper did not reach isolated sudo stand-in')
                with (lock_dir / 'builder.lock').open('a+') as stream:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                release.touch()
                out, error = child.communicate(timeout=12)
            self.assertEqual(child.returncode, 0, error.decode())


if __name__ == '__main__':
    unittest.main()
