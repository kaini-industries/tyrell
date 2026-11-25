"""Synthetic adversarial filesystem fixtures; never real-image qualification."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPOSITORY = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('zero_verifier', REPOSITORY / 'image/verify-zero.py')
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def elf():
    data = bytearray(64)
    data[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<H', data, 18, 183)
    return bytes(data)


def elf_with_needed(*names, interpreter=None):
    data = bytearray(1024 if interpreter else 640);data[:64] = elf()
    struct.pack_into('<Q', data, 32, 64)
    struct.pack_into('<HH', data, 54, 56, 3 if interpreter else 2)
    strings = b'\x00';offsets = []
    for name in names:
        offsets.append(len(strings));strings += name.encode() + b'\x00'
    data[256:256+len(strings)] = strings
    entries = [(5, 0x1000 + 256), (10, len(strings)), *[(1, offset) for offset in offsets], (0, 0)]
    for index, (tag, value) in enumerate(entries):struct.pack_into('<qQ', data, 480 + index*16, tag, value)
    struct.pack_into('<IIQQQQQQ', data, 64, 1, 0, 0, 0x1000, 0, len(data), len(data), 1)
    struct.pack_into('<IIQQQQQQ', data, 120, 2, 0, 480, 0x1000+480, 0, len(entries)*16, len(entries)*16, 1)
    if interpreter:
        value = interpreter.encode() + b'\x00';data[768:768 + len(value)] = value
        struct.pack_into('<IIQQQQQQ', data, 176, 3, 0, 768, 0x1000 + 768, 0, len(value), len(value), 1)
    return bytes(data)


class Fixture:
    def __init__(self, base):
        self.base = Path(base).resolve()
        self.root = self.base / 'root';self.root.mkdir()
        self.inputs = self.base / 'input';self.locks = self.inputs / 'locks';self.locks.mkdir(parents=True)
        self.profile = json.loads((REPOSITORY / 'image/profiles/tyrell-zero.json').read_text())
        self.put_input('image/profiles/tyrell-zero.json', self.profile)
        self.contracts = {}
        for name, item in self.profile['contracts'].items():
            data = (REPOSITORY / item['path']).read_bytes()
            destination = self.inputs / item['path'];destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data);self.contracts[name] = data
        names = set(sum(self.profile['runtime_roots'].values(), [])) | {
            'tyrell-zero-session', 'tyrell-zero-store', 'tyrell-zero-power-policy', 'tyrell-status',
            'linux-image-' + V.KERNEL, 'raspberrypi-sys-mods'}
        self.packages = {name: {'Version': '1', 'Architecture': 'arm64'} for name in sorted(names)}
        self.packages['greetd']['Version'] = '0.10.3-4'
        self.packages['applaunch']['Version'] = '0.6.111'
        self.packages['linux-image-' + V.KERNEL]['Version'] = self.profile['pins']['kernel_package']
        self.packages['raspberrypi-sys-mods']['Version'] = self.profile['pins']['raspberrypi_sys_mods_version']
        self.write_status()
        self.put('/etc/tyrell-zero/power-policy.json', {'schema': 1, 'preset': 'baseline'})
        self.put(V.UPDATE_POLICY, {'vendor_app_updater': 'masked-unqualified',
                 'launcher_updater': 'masked-incompatible', 'base_update_method': 'reviewed-offline-image',
                 'application_updates': 'qualified-store-catalog', 'ordinary_apt_security_maintenance': 'preserve'})
        self.put(V.APPS_UPDATER_STATUS, 'failed:incompatible\n')
        apt_refresh = '/usr/lib/systemd/system/applaunch-apt-update.service'
        self.put(apt_refresh, '[Unit]\nDescription=APPLaunch trusted package index refresh\n'
                 'After=network-online.target\nWants=network-online.target\n\n[Service]\n'
                 'Type=oneshot\nExecStart=/usr/bin/apt update\nTimeoutStartSec=5min\nNice=10\n')
        self.put('/usr/share/tyrell-zero/power-policy/contract.json', self.contracts['power'])
        self.put('/etc/cardputerzero-dtoverlays.commit', self.profile['pins']['bsp_commit'] + '\n')
        self.first_boot_files = {}
        for name in V.NOV19_FIRST_BOOT_FILES:
            data = ('synthetic first-boot fixture: ' + name + '\n').encode()
            self.put(name, data, 0o755 if name.endswith('randomize-disk-id') else 0o644)
            self.first_boot_files[name] = sha(data)
        self.link('/etc/systemd/system/multi-user.target.wants/cardputerzero-diskid.service',
                  '/usr/lib/systemd/system/cardputerzero-diskid.service')
        self.link('/etc/systemd/system/fstrim.timer', '/dev/null')
        self.put('/usr/lib/systemd/system/rpi-resize.service', '[Unit]\nDescription=First boot chain fixture\n')
        self.link('/etc/systemd/system/sysinit.target.wants/rpi-resize.service',
                  '/usr/lib/systemd/system/rpi-resize.service')
        self.put('/etc/systemd/system/rpi-resize.service.d/10-cardputerzero-no-growfs-fstrim.conf',
                 '[Unit]\nWants=\nWants=first-boot-complete.target\n')
        self.put('/boot/firmware/cmdline.txt', 'root=ROOTDEV quiet\n')

        self.put('/etc/machine-id', '')
        self.put('/etc/passwd', 'root:x:0:0:root:/root:/bin/bash\npi:x:1000:1000:pi:/home/pi:/bin/bash\n'
                 '_greetd:x:110:110:greetd:/var/lib/greetd:/usr/sbin/nologin\n')
        self.put('/etc/group', 'root:x:0:\nvideo:x:44:_greetd\ninput:x:104:_greetd\n')
        self.put('/etc/shadow', 'root:!:1:0:99999:7:::\npi:!:1:0:99999:7:::\n')
        for marker in ('run-oobe', 'run-keyboard-guide'):
            self.put('/var/lib/LaunchWizard/' + marker, '')
        for name in ('start.elf', 'fixup.dat', 'kernel8.img'):
            self.put('/boot/firmware/' + name, b'pinned boot bytes')
        config = '\n'.join('dtoverlay=' + name for name in (
            'cardputerzero-v5-overlay', 'bq27220_v5', 'bmi270_bmm150_overlay')) + '\n'
        self.put('/boot/firmware/config.txt', config)
        for name in ('cardputerzero-v5-overlay', 'bq27220_v5', 'bmi270_bmm150_overlay'):
            self.put('/boot/firmware/overlays/' + name + '.dtbo', b'overlay')
        self.put('/lib/firmware/cardputerzero,st7789v_lcd.bin', b'panel firmware')
        module_names = ('bq27xxx_battery', 'bq27xxx_battery_i2c', 'm5ioe1', 'tca8418_keypad_m5stack',
                        'es8389_m5stack', 'pwm_bl_m5stack', 'st7789v_m5stack', 'cardputerzero-audio',
                        'gpio-forwarder', 'powerfail_suo', 'panel-mipi-dbi-m', 'simple-amplifier')
        for name in module_names:
            self.put('/lib/modules/' + V.KERNEL + '/extra/' + name + '.ko',
                     b'\x00vermagic=' + V.KERNEL.encode() + b' SMP\x00')
        self.assets = {}
        paths = {'launcher': V.MAIN, 'wizard': '/usr/libexec/tyrell-zero/LaunchWizard',
                 'store_dispatcher': V.STORE, 'store_original': V.ORIGINAL,
                 'store_backend': '/usr/libexec/tyrell-zero/appstore-package-helper'}
        for role, path in paths.items():
            data = b'#!/usr/bin/python3\n' if role == 'store_backend' else elf()
            self.put(path, data, 0o755);self.assets[role] = {'path': path, 'sha256': sha(data)}
        runtime = {
            '/usr/lib/tyrell-zero/session/state.py', '/usr/lib/tyrell-zero/session/supervisor.py',
            '/usr/lib/tyrell-zero/session/wizard_complete.py', '/usr/libexec/tyrell-zero-session-state',
            '/usr/libexec/tyrell-zero-session', '/usr/libexec/tyrell-zero-session-recovery',
            '/usr/libexec/tyrell-zero-wizard-complete', '/usr/lib/tyrell-zero/store/guard.py'}
        self.runtime = {}
        for path in runtime:
            data = elf() if path.endswith('-recovery') else b'fixture runtime'
            self.put(path, data, 0o755);self.runtime[path] = sha(data)
        self.put('/usr/share/tyrell-zero/store-policy.json', {'schema': 1, 'dependencies': []})
        self.put('/usr/share/tyrell-zero/store-catalog.json', {'schema': 1, 'packages': [{'package': 'tyrell-status'}]})
        for path in ('/usr/share/tyrell-zero/store-policy.json', '/usr/share/tyrell-zero/store-catalog.json'):
            self.runtime[path] = V.digest(self.root / path.lstrip('/'))
        resource = '/usr/share/APPLaunch/share/font/fixture.ttf'
        self.put(resource, b'fixture font')
        library = '/usr/lib/aarch64-linux-gnu/fixture.so'
        self.put(library, elf())
        self.put('/etc/pam.d/greetd', '@include common-session\n')
        self.put('/etc/pam.d/common-session', 'session optional pam_systemd.so\n')
        for name in ('common-auth', 'common-account', 'common-password'):
            self.put('/etc/pam.d/' + name, '# pinned synthetic PAM fixture\n')
        self.put('/etc/systemd/logind.conf.d/90-tyrell-zero.conf', '[Login]\nNAutoVTs=0\nReserveVT=0\n')
        for path in V.MASKS:self.link(path, '/dev/null')
        self.link('/etc/systemd/system/default.target', '/usr/lib/systemd/system/multi-user.target')
        self.link('/etc/systemd/system/multi-user.target.wants/tyrell-zero.target', '/usr/lib/systemd/system/tyrell-zero.target')
        for name in ('tyrell-zero-boot-state.service', 'tyrell-zero-session.service',
                     'tyrell-zero-wizard.service', 'tyrell-zero.target'):
            self.put('/usr/lib/systemd/system/' + name, (REPOSITORY / 'packages/tyrell-zero-session/system' / name).read_bytes())
        self.put('/var/lib/dpkg/diversions', V.STORE + '\n' + V.ORIGINAL + '\ntyrell-zero-store\n')
        image = V.ImageRoot(self.root, os.getuid())
        self.compat = {'schema': 1, 'profile': V.PROFILE, 'complete': True, 'assets': self.assets,
            'runtime_files': self.runtime, 'resource_files': {resource: sha(b'fixture font')},
            'library_files': {library: {'sha256': sha(elf())}}, 'required_packages': {'greetd': '0.10.3-4'},
            'configuration_files': {'/etc/pam.d/' + name: V.digest(self.root / 'etc/pam.d' / name)
                for name in ('greetd', 'common-session', 'common-auth', 'common-account', 'common-password')},
            'protected_roots': ['/lib/modules', '/lib/firmware', '/boot/firmware/overlays'],
            'protected_files': {}, 'qualification': {'scope': 'disposable-offline-runtime-root',
                'assembled_image_qualified': False, 'hardware_qualified': False}}
        roots = {V.MAIN, V.ORIGINAL, V.STORE, '/usr/libexec/tyrell-zero/LaunchWizard',
                 '/usr/libexec/tyrell-zero-session-recovery', '/usr/bin/tyrell-status'}
        roots.update('/usr/sbin/' + name for name in ('greetd', 'systemd-logind', 'systemd-udevd',
            'dbus-daemon', 'NetworkManager', 'wpa_supplicant', 'sshd', 'bluetoothd', 'polkitd',
            'pkexec', 'udisksd', 'pipewire', 'wireplumber', 'rtkit-daemon'))
        self.compat['elf_roots'] = {}
        self.compat['optional_elf_backends'] = []
        for path in roots:
            self.put(path, elf(), 0o755);self.compat['elf_roots'][path] = sha(elf())
        source = {'status': 'passed', 'scope': 'pre-image-integration',
                  'physical_hardware_qualified': False, 'assets': self.assets}
        self.put('/usr/share/tyrell-zero/source-pre-image-qualification.json', source)
        self.compat['configuration_files'].update(self.first_boot_files)
        self.compat['configuration_files']['/etc/systemd/system/fstrim.timer'] = {'symlink': '/dev/null'}
        self.compat['configuration_files']['/etc/systemd/system/sysinit.target.wants/rpi-resize.service'] = {
            'symlink': '/usr/lib/systemd/system/rpi-resize.service'}
        self.compat['configuration_files']['/etc/systemd/system/multi-user.target.wants/cardputerzero-diskid.service'] = {
            'symlink': '/usr/lib/systemd/system/cardputerzero-diskid.service'}
        for path in ('/usr/lib/systemd/system/rpi-resize.service',
                     '/etc/systemd/system/rpi-resize.service.d/10-cardputerzero-no-growfs-fstrim.conf'):
            self.compat['configuration_files'][path] = V.digest(self.root / path.lstrip('/'))
        self.compat['configuration_files'][apt_refresh] = V.digest(self.root / apt_refresh.lstrip('/'))
        self.compat['source_qualification_sha256'] = V.digest(
            self.root / 'usr/share/tyrell-zero/source-pre-image-qualification.json')
        for path in self.compat['protected_roots']:self.compat['protected_files'].update(image.tree(path))
        for path in ('/lib', '/boot/firmware/config.txt', '/boot/firmware/kernel8.img',
                     '/boot/firmware/start.elf', '/boot/firmware/fixup.dat',
                     '/etc/cardputerzero-dtoverlays.commit'):
            self.compat['protected_files'][path] = image.identity(path)
        custom = set(self.profile['custom_packages'])
        self.apt = {'schema': 1, 'profile': V.PROFILE, 'complete': True, 'kind': 'tyrell-zero-apt',
                    'architecture': 'arm64', 'seed_apt_lock_sha256': self.profile['pins']['seed_apt_lock_sha256'],
                    'stages': dict.fromkeys(V.STAGES, []),
                    'installed': [[name, value['Version'], value['Architecture']] for name, value in self.packages.items()
                                  if name not in custom]}
        self.apps = {'schema': 1, 'profile': V.PROFILE, 'complete': True, 'packages': [
            {'Package': name, 'Version': self.packages[name]['Version'], 'Architecture': 'arm64',
             'sha256': 'a' * 64} for name in sorted(custom)]}
        self.bind_locks()
        self.bind_receipts()

    def put(self, name, value, mode=0o644):
        destination = self.root / name.lstrip('/');destination.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, dict):value = json.dumps(value, sort_keys=True) + '\n'
        if isinstance(value, str):value = value.encode()
        destination.write_bytes(value);destination.chmod(mode)
        return destination

    def link(self, name, target):
        path = self.root / name.lstrip('/');path.parent.mkdir(parents=True, exist_ok=True);path.symlink_to(target)

    def put_input(self, name, value):
        path = self.inputs / name;path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True) + '\n')
        return path

    def write_status(self):
        self.put('/var/lib/dpkg/status', '\n\n'.join('Package: ' + name + '\nStatus: install ok installed\n'
                 + 'Version: ' + value['Version'] + '\nArchitecture: ' + value['Architecture']
                 for name, value in self.packages.items()) + '\n')

    def bind_locks(self):
        self.compat['installed_packages'] = {
            name: {'Version': version, 'Architecture': arch} for name, version, arch in self.apt['installed']}
        self.compat['installed_packages'].update({row['Package']: {
            'Version': row['Version'], 'Architecture': row['Architecture']} for row in self.apps['packages']})
        image = V.ImageRoot(self.root, os.getuid())
        self.compat['elf_root_metadata'] = {
            '/' + str(image.path(path).relative_to(self.root)): image.identity(
                '/' + str(image.path(path).relative_to(self.root))) for path in self.compat['elf_roots']}
        hashes = {}
        for name, value in [('apt', self.apt), ('apps', self.apps), ('native-compat', self.compat)]:
            filename = 'tyrell-zero-' + name + '.json'
            hashes[filename] = V.digest(self.put_input('locks/' + filename, value))
        self.metadata = {'profile_id': V.PROFILE, 'profile': V.PROFILE, 'build_mode': 'zero-native', 'apt_profile': 'zero-frozen',
            'stages': V.STAGES, 'selected_power_preset': 'baseline', 'locks_sha256': hashes,
            'profile_sha256': V.digest(self.inputs / 'image/profiles/tyrell-zero.json'),
            'hardware_contract_sha256': sha(self.contracts['hardware']), 'power_contract_sha256': sha(self.contracts['power'])}
        self.metadata.update(pi_gen_commit=self.profile['pins']['pi_gen_commit'],
            drivers_commit=self.profile['pins']['bsp_commit'], kernel_release=V.KERNEL,
            applaunch_version='0.6.111', output=self.profile['output'],
            image_name=self.profile['output']['basename'], hardware_qualified=False, battery_qualified=False)
        self.put('/etc/tyrell-image.json', self.metadata)
        update_policy = json.loads((self.root / V.UPDATE_POLICY.lstrip('/')).read_text())
        update_policy['native_compat_sha256'] = hashes['tyrell-zero-native-compat.json']
        self.put(V.UPDATE_POLICY, update_policy)

    def bind_receipts(self):
        ordinary = {'tyrell-status'}
        image = V.ImageRoot(self.root, os.getuid())
        baseline_files = {}
        for path in {V.STORE, V.ORIGINAL, '/var/lib/dpkg/diversions',
                     '/usr/lib/tyrell-zero/store/guard.py', '/usr/libexec/tyrell-zero/appstore-package-helper',
                     '/etc/systemd/system/default.target',
                     '/etc/systemd/system/multi-user.target.wants/tyrell-zero.target',
                     '/etc/systemd/logind.conf.d/90-tyrell-zero.conf', '/etc/pam.d/greetd',
                     '/etc/tyrell-zero/power-policy.json', *V.MASKS, *V.RUNTIME}:
            identity = image.identity(path)
            baseline_files[path] = ({'symlink': identity['target']} if identity['type'] == 'symlink'
                                    else identity['sha256'])
        for path in self.compat['configuration_files']:
            if path == '/etc/systemd/system/sysinit.target.wants/rpi-resize.service':continue
            identity = image.identity(path)
            baseline_files[path] = ({'symlink': identity['target']} if identity['type'] == 'symlink'
                                    else identity['sha256'])
        self.put(V.BASELINE, {'schema': 1, 'complete': True, 'packages': {
            name: item['Version'] for name, item in self.packages.items() if name not in ordinary},
            'files': baseline_files, 'triggers': {}})
        runtime = dict(self.runtime);runtime[V.BASELINE] = V.digest(self.root / V.BASELINE.lstrip('/'))
        qualification = {'schema': 1, 'profile': V.PROFILE, 'workflow': 'native-setup-v1', 'status': 'passed',
            'scope': 'pre-image-integration', 'physical_hardware_qualified': False,
            'suites': {'session': 'passed', 'wizard': 'passed', 'store': 'passed'},
            'assets': self.assets, 'runtime_files': runtime}
        self.put(V.QUALIFICATION, qualification)
        self.put(V.INTEGRATION, {'schema': 1, 'profile': V.PROFILE, 'workflow': 'native-setup-v1',
            'complete': True, 'applaunch_version': '0.6.111', 'assets': self.assets, 'runtime_files': runtime,
            'qualification_sha256': V.digest(self.root / V.QUALIFICATION.lstrip('/'))})
        image = V.ImageRoot(self.root, os.getuid())
        names = {V.BASELINE, V.QUALIFICATION, V.INTEGRATION, V.UPDATE_POLICY, V.APPS_UPDATER_STATUS,
                 '/etc/tyrell-zero/power-policy.json',
                 '/etc/pam.d/greetd', *V.MASKS}
        self.activation = {'schema': 1, 'profile_id': V.PROFILE, 'phase': 'assembled-root', 'complete': True,
            'physical_hardware_qualified': False, 'power_preset': 'baseline',
            'profile_sha256': self.metadata['profile_sha256'], 'locks_sha256': self.metadata['locks_sha256'],
            'source_qualification_sha256': self.compat['source_qualification_sha256'],
            'stages': V.STAGES, 'files': {name: image.identity(name) for name in names}}
        self.put(V.ACTIVATION, self.activation)

    def verify(self):
        # Only vendor ELF bytes are synthetic here. No host fixture is promoted
        # to a hardware, ARM64 execution or genuine vendor-content qualification.
        with patch.object(V, 'MAIN_SHA', sha(elf())), patch.object(V, 'STORE_SHA', sha(elf())), \
                patch.object(V, 'NOV19_FIRST_BOOT_FILES', self.first_boot_files):
            return V.verify_root(self.root, self.metadata, self.locks, owner=os.getuid())


class ZeroVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='zero-verifier-fixture-')
        self.addCleanup(self.temporary.cleanup)
        self.fixture = Fixture(self.temporary.name)

    def fails(self, text):
        report = self.fixture.verify()
        self.assertEqual(report['status'], 'failed', report)
        self.assertIn(text, report.get('error', ''))

    def test_synthetic_valid_fixture_reports_offline_only(self):
        report = self.fixture.verify()
        self.assertEqual(report['status'], 'passed', report.get('error'))
        self.assertFalse(report['hardware_qualified']);self.assertFalse(report['battery_qualified'])

    def test_captured_fstrim_mask_does_not_follow_missing_device_endpoint(self):
        f = self.fixture
        self.assertEqual(f.compat['configuration_files']['/etc/systemd/system/fstrim.timer'],
                         {'symlink': '/dev/null'})
        self.assertFalse((f.root / 'dev').exists())
        self.assertEqual(f.verify()['status'], 'passed')

    def test_captured_fstrim_mask_identity_and_parent_must_remain_trusted(self):
        f = self.fixture
        path = '/etc/systemd/system/fstrim.timer'
        f.compat['configuration_files'][path]['extra'] = 'unreviewed identity'
        f.bind_locks();f.bind_receipts()
        self.fails('systemd_mask_' + path)
        del f.compat['configuration_files'][path]['extra']
        f.bind_locks();f.bind_receipts()
        (f.root / 'etc/systemd/system').chmod(0o777)
        self.fails('untrusted parent')

    def test_null_endpoint_exception_does_not_apply_to_other_configuration_links(self):
        f = self.fixture
        path = '/etc/tyrell-zero/unqualified-null-link'
        f.link(path, '/dev/null')
        (f.root / 'dev/null').mkdir(parents=True)
        f.compat['configuration_files'][path] = {'symlink': '/dev/null'}
        f.bind_locks();f.bind_receipts()
        self.fails('untrusted image file: /dev/null')

    def test_november_first_boot_payload_and_fresh_state_are_required(self):
        f = self.fixture
        helper = '/usr/lib/cardputerzero/randomize-disk-id'
        original = (f.root / helper.lstrip('/')).read_bytes()
        f.put(helper, b'changed first-boot disk helper', 0o755)
        self.fails('nov19_first_boot_file_')
        f.put(helper, original, 0o755)
        f.put('/var/lib/cardputerzero/diskid-randomized', 'already executed\n')
        self.fails('nov19_diskid_not_preexecuted')

    def test_partial_diskid_and_preexecuted_rootfs_expansion_cannot_be_exported(self):
        f = self.fixture
        for path in ('/var/lib/cardputerzero/diskid-state', '/var/lib/LaunchWizard/rootfs-expansion-attempted',
                     '/etc/init.d/resize2fs_once', '/etc/rc3.d/S01resize2fs_once'):
            with self.subTest(path=path):
                f.put(path, 'retained first-boot state\n')
                self.fails('no_initial_first_boot_state_' + path)
                (f.root / path.lstrip('/')).unlink()

    def test_november_fixed_size_boot_policy_cannot_regress_to_resize(self):
        f = self.fixture
        f.put('/boot/firmware/cmdline.txt', 'root=ROOTDEV init=/usr/lib/raspberrypi-sys-mods/firstboot quiet\nresize\n')
        self.fails('nov19_no_resize_cmdline')
        f.put('/boot/firmware/cmdline.txt', 'root=ROOTDEV quiet\n')
        f.put('/etc/systemd/system/rpi-resize.service.d/10-cardputerzero-no-growfs-fstrim.conf',
              '[Unit]\nWants=systemd-growfs-root.service\n')
        self.fails('nov19_resize_ordering_without_growth')

    def test_fresh_image_still_requires_captured_resize_chain(self):
        f = self.fixture
        link = '/etc/systemd/system/sysinit.target.wants/rpi-resize.service'
        del f.compat['configuration_files'][link]
        f.bind_locks();f.bind_receipts()
        self.fails('nov19_resize_first_boot_configuration_locked')

    def test_store_baseline_cannot_freeze_the_self_removing_resize_link(self):
        f = self.fixture
        baseline = V.load_json(f.root / V.BASELINE.lstrip('/'))
        baseline['files']['/etc/systemd/system/sysinit.target.wants/rpi-resize.service'] = {
            'symlink': '/usr/lib/systemd/system/rpi-resize.service'}
        f.put(V.BASELINE, baseline)
        self.fails('store_baseline_resize_initial_only')

    def test_wizard_requires_completed_diskid_ordering(self):
        f = self.fixture
        unit = '/usr/lib/systemd/system/tyrell-zero-wizard.service'
        text = (f.root / unit.lstrip('/')).read_text()
        f.put(unit, text.replace('Requires=tyrell-zero-boot-state.service cardputerzero-diskid.service',
                                 'Requires=tyrell-zero-boot-state.service'))
        self.fails('native_unit_contract_tyrell-zero-wizard.service')
        f.put(unit, text.replace(' systemd-user-sessions.service cardputerzero-diskid.service',
                                 ' systemd-user-sessions.service'))
        self.fails('native_unit_contract_tyrell-zero-wizard.service')

    def test_new_application_updater_stays_masked_and_metadata_refresh_stays_metadata_only(self):
        f = self.fixture
        mask = f.root / 'etc/systemd/system/applaunch-apps-updater.service'
        mask.unlink();mask.symlink_to('/usr/lib/systemd/system/applaunch-apps-updater.service')
        self.fails('mask_/etc/systemd/system/applaunch-apps-updater.service')
        mask.unlink();mask.symlink_to('/dev/null')
        unit = '/usr/lib/systemd/system/applaunch-apt-update.service'
        f.put(unit, (f.root / unit.lstrip('/')).read_text() + '\nExecStartPost=/usr/bin/apt upgrade\n')
        f.compat['configuration_files'][unit] = V.digest(f.root / unit.lstrip('/'))
        f.bind_locks();f.bind_receipts()
        self.fails('vendor_apt_refresh_metadata_only')

    def test_changed_lock_and_duplicate_json_fail_closed(self):
        path = self.fixture.locks / 'tyrell-zero-apt.json'
        path.write_text(path.read_text() + ' ')
        self.fails('lock_hash')
        with self.assertRaises(V.VerificationError):V.strict_json('{"a":1,"a":2}')
        with self.assertRaises(V.VerificationError):V.strict_json('{"a":NaN}')

    def test_power_inspector_profile_alias_is_required(self):
        f = self.fixture
        f.metadata.pop('profile');f.put('/etc/tyrell-image.json', f.metadata)
        self.fails('metadata_profile')

    def test_required_elf_interpreter_must_exist_and_be_locked(self):
        f = self.fixture
        path = '/usr/bin/tyrell-status'
        data = elf_with_needed('fixture.so', interpreter='/lib/ld-linux-aarch64.so.1')
        f.put(path, data, 0o755);f.compat['elf_roots'][path] = sha(data)
        loader = '/usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1'
        f.put(loader, elf(), 0o755);f.link('/lib/ld-linux-aarch64.so.1', loader)
        f.compat['library_files'][loader] = {'sha256': sha(elf())}
        f.bind_locks();f.bind_receipts()
        self.assertEqual(f.verify()['status'], 'passed')
        del f.compat['library_files'][loader]
        f.bind_locks();f.bind_receipts();self.fails('required_elf_interpreter')
        f.compat['library_files'][loader] = {'sha256': sha(elf())}
        f.bind_locks();f.bind_receipts();(f.root / loader.lstrip('/')).unlink()
        self.assertEqual(f.verify()['status'], 'failed')
        with self.assertRaises(V.VerificationError):
            V.elf_interpreter(elf_with_needed(interpreter='/lib/../unlocked-loader'))

    def test_nested_host_mount_is_not_an_offline_root(self):
        mountinfo = Path('/proc/self/mountinfo')
        original_is_file, original_read_text = Path.is_file, Path.read_text
        row = '10 1 0:1 / ' + str(self.fixture.root / 'etc') + ' rw - ext4 /dev/mock rw\n'
        with patch.object(Path, 'is_file', lambda path: path == mountinfo or original_is_file(path)), \
             patch.object(Path, 'read_text', lambda path, *a, **kw: row if path == mountinfo
                          else original_read_text(path, *a, **kw)):
            self.fails('detach nested mount: etc')

    def test_extra_removed_and_half_configured_packages_rejected(self):
        f = self.fixture
        f.packages['extra'] = {'Version': '1', 'Architecture': 'arm64'};f.write_status()
        self.fails('exact_installed_closure')
        del f.packages['extra'];del f.packages['greetd'];f.write_status()
        self.fails('exact_installed_closure')
        path = f.root / 'var/lib/dpkg/status'
        path.write_text(path.read_text().replace('install ok installed', 'install ok unpacked', 1))
        self.fails('unclean dpkg')

    def test_intentional_hold_is_installed_and_never_needs_unholding(self):
        path = self.fixture.root / 'var/lib/dpkg/status'
        original = path.read_text()
        path.write_text(original.replace('Status: install ok installed', 'Status: hold ok installed', 1))
        self.assertEqual(self.fixture.verify()['status'], 'passed')
        path.write_text(original.replace('Status: install ok installed', 'Status: hold ok unpacked', 1))
        self.fails('unclean dpkg')

    def test_pruned_and_extra_firmware_rejected(self):
        target = self.fixture.root / 'lib/firmware/cardputerzero,st7789v_lcd.bin'
        target.unlink();self.fails('full_firmware')
        target.write_bytes(b'panel firmware')
        self.fixture.put('/lib/firmware/unexpected.bin', b'other')
        self.fails('full_firmware')

    def test_firmware_alternatives_routes_are_pinned_not_only_leaf_links(self):
        f = self.fixture
        f.put('/lib/firmware/regulatory-real.bin', b'regulatory')
        f.link('/lib/firmware/regulatory.db', '/etc/alternatives/regulatory.db')
        f.link('/etc/alternatives/regulatory.db', '/lib/firmware/regulatory-real.bin')
        image = V.ImageRoot(f.root, os.getuid())
        f.compat['protected_files'].update(image.tree('/lib/firmware'))
        f.bind_locks();f.bind_receipts();self.fails('firmware_route_pinned')
        f.compat['protected_files']['/etc/alternatives/regulatory.db'] = image.identity('/etc/alternatives/regulatory.db')
        f.bind_locks();f.bind_receipts();self.assertEqual(f.verify()['status'], 'passed')
        route = f.root / 'etc/alternatives/regulatory.db';route.unlink()
        route.symlink_to('/lib/firmware/cardputerzero,st7789v_lcd.bin')
        self.fails('full_firmware')

    def test_module_abi_checked_even_if_input_snapshot_rebound(self):
        f = self.fixture
        target = '/lib/modules/' + V.KERNEL + '/extra/m5ioe1.ko'
        f.put(target, b'\x00vermagic=wrong-kernel SMP\x00')
        f.compat['protected_files'][target] = V.ImageRoot(f.root, os.getuid()).identity(target)
        f.bind_locks();f.bind_receipts();self.fails('module_abi_m5ioe1')

    def test_unsafe_links_and_world_writable_runtime_rejected(self):
        f = self.fixture
        target = f.root / 'usr/lib/tyrell-zero/session/state.py'
        target.chmod(0o666);self.fails('untrusted image file')
        target.chmod(0o644);target.unlink();target.symlink_to('/etc/passwd')
        self.fails('untrusted image file')

    def test_raw_parent_cannot_be_hidden_by_an_alias(self):
        f = self.fixture
        original = f.root / 'usr/lib/tyrell-zero/session'
        trusted = f.root / 'usr/lib/trusted/session';trusted.parent.mkdir()
        original.rename(trusted);original.symlink_to('/usr/lib/trusted/session')
        parent = original.parent;parent.chmod(0o777)
        self.fails('untrusted parent')
        parent.chmod(0o755)
        self.fails('native parent alias')
        # General image libraries may use safe aliases, including usrmerge.
        image = V.ImageRoot(f.root, os.getuid())
        image.secure('/usr/lib/tyrell-zero/session/state.py', parent_aliases=True)
        parent.chmod(0o777)
        with self.assertRaisesRegex(V.VerificationError, 'untrusted parent'):
            image.secure('/usr/lib/tyrell-zero/session/state.py', parent_aliases=True)
        parent.chmod(0o755)
        library = f.root / 'lib';merged = f.root / 'usr/lib'
        for child in list(library.iterdir()):child.rename(merged / child.name)
        library.rmdir();library.symlink_to('usr/lib')
        image.secure('/lib/firmware/cardputerzero,st7789v_lcd.bin')

    def test_image_symlinks_cannot_escape_into_host(self):
        f = self.fixture
        f.link('/unsafe', '../../etc/passwd')
        with self.assertRaises(V.VerificationError):V.ImageRoot(f.root, os.getuid()).path('/unsafe')
        with self.assertRaises(V.VerificationError):V.ImageRoot(f.root, os.getuid()).path('/a/../b')

    def test_stale_store_baseline_and_diversion_rejected(self):
        f = self.fixture
        baseline = V.load_json(f.root / V.BASELINE.lstrip('/'));baseline['packages']['greetd'] = 'old'
        f.put(V.BASELINE, baseline);self.fails('store_baseline_exact_packages')
        f.bind_receipts();f.put('/var/lib/dpkg/diversions', '')
        self.fails('store_diversion')

    def test_sparse_hardware_snapshot_cannot_claim_complete(self):
        f = self.fixture
        f.compat['protected_roots'].remove('/lib/firmware')
        f.bind_locks();f.bind_receipts();self.fails('protected_hardware_roots')

    def test_desktop_and_unlisted_resources_rejected(self):
        f = self.fixture
        f.put('/usr/share/APPLaunch/share/unlisted.png', b'unreviewed')
        self.fails('exact_resource_path_set')
        (f.root / 'usr/share/APPLaunch/share/unlisted.png').unlink()
        f.packages['lightdm'] = {'Version': '1', 'Architecture': 'arm64'}
        f.apt['installed'].append(['lightdm', '1', 'arm64']);f.write_status()
        f.bind_locks();f.bind_receipts();self.fails('no_desktop_activation_packages')

    def test_extra_unit_command_and_duplicate_exec_rejected(self):
        f = self.fixture
        unit = '/usr/lib/systemd/system/tyrell-zero-session.service'
        original = f.root.joinpath(unit.lstrip('/')).read_text()
        f.put(unit, original + '\nExecStartPost=/bin/sh -c unsafe\n')
        self.fails('native_unit_contract')
        f.put(unit, original + '\nExecStart=/bin/sh\n')
        self.assertEqual(f.verify()['status'], 'failed')

    def test_pam_comments_do_not_supply_a_real_session(self):
        f = self.fixture
        f.put('/etc/pam.d/common-session', '# session optional pam_systemd.so\n')
        f.compat['configuration_files']['/etc/pam.d/common-session'] = V.digest(f.root / 'etc/pam.d/common-session')
        f.bind_locks();f.bind_receipts();self.fails('pam_real_session')

    def test_pam_locked_recursive_login_chain_is_a_real_session(self):
        f = self.fixture
        f.put('/etc/pam.d/greetd', '#%PAM-1.0\n@include login\n')
        f.put('/etc/pam.d/login', 'session required pam_loginuid.so\n@include common-session\n')
        for name in ('greetd', 'login'):
            f.compat['configuration_files']['/etc/pam.d/' + name] = V.digest(f.root / 'etc/pam.d' / name)
        f.bind_locks();f.bind_receipts();self.assertEqual(f.verify()['status'], 'passed')
        f.put('/etc/pam.d/login', 'session include common-session\n')
        f.compat['configuration_files']['/etc/pam.d/login'] = V.digest(f.root / 'etc/pam.d/login')
        f.bind_locks();f.bind_receipts();self.assertEqual(f.verify()['status'], 'passed')
        f.put('/etc/pam.d/login', 'auth include common-session\n')
        f.compat['configuration_files']['/etc/pam.d/login'] = V.digest(f.root / 'etc/pam.d/login')
        f.bind_locks();f.bind_receipts();self.fails('pam_real_session')

    def test_pam_unlocked_missing_and_cyclic_includes_fail_closed(self):
        f = self.fixture
        f.put('/etc/pam.d/greetd', '@include login\n')
        f.compat['configuration_files']['/etc/pam.d/greetd'] = V.digest(f.root / 'etc/pam.d/greetd')
        f.put('/etc/pam.d/login', '@include common-session\n')
        f.bind_locks();f.bind_receipts();self.fails('pam_include_bound_login')
        f.compat['configuration_files']['/etc/pam.d/login'] = V.digest(f.root / 'etc/pam.d/login')
        f.bind_locks();f.bind_receipts();(f.root / 'etc/pam.d/login').unlink()
        self.assertEqual(f.verify()['status'], 'failed')
        f.put('/etc/pam.d/login', '@include greetd\n')
        f.compat['configuration_files']['/etc/pam.d/login'] = V.digest(f.root / 'etc/pam.d/login')
        f.bind_locks();f.bind_receipts();self.fails('cyclic/deep PAM include')

    def test_unlisted_systemd_dropin_cannot_change_native_service(self):
        self.fixture.put('/etc/systemd/system/tyrell-zero-session.service.d/override.conf',
                         '[Service]\nExecStartPost=/bin/sh\n')
        self.fails('no_native_unit_dropin')

    def test_legacy_userconfig_cannot_compete_with_native_setup(self):
        f = self.fixture
        mask = f.root / 'etc/systemd/system/userconfig.service'
        mask.unlink();mask.symlink_to('/usr/lib/systemd/system/userconfig.service')
        self.fails('mask_/etc/systemd/system/userconfig.service')
        mask.unlink();mask.symlink_to('/dev/null')
        f.link('/etc/systemd/system/multi-user.target.wants/userconfig.service',
               '/usr/lib/systemd/system/userconfig.service')
        self.fails('no_desktop_wants_etc/systemd/system/multi-user.target.wants/userconfig.service')

    def test_unlocked_root_password_is_rejected_until_locked(self):
        f = self.fixture
        f.put('/etc/shadow', 'root:$6$synthetic-password:1:0:99999:7:::\npi:!:1:0:99999:7:::\n')
        self.fails('no_bootstrap_password_root')
        f.put('/etc/shadow', 'root:!$6$synthetic-password:1:0:99999:7:::\npi:!:1:0:99999:7:::\n')
        self.assertEqual(f.verify()['status'], 'passed')

    def test_fat_projection_only_ignores_unstored_posix_metadata(self):
        expected = {'type': 'file', 'mode': 0o644, 'uid': 0, 'gid': 0, 'sha256': 'a' * 64}
        actual = dict(expected, mode=0o755)
        image = SimpleNamespace(fat_boot=True)
        self.assertTrue(V.same_identity(image, '/boot/firmware/kernel8.img', actual, expected))
        self.assertFalse(V.same_identity(image, '/lib/modules/kernel.ko', actual, expected))
        actual['sha256'] = 'b' * 64
        self.assertFalse(V.same_identity(image, '/boot/firmware/kernel8.img', actual, expected))

    def test_live_root_and_device_input_are_refused(self):
        with self.assertRaises(V.VerificationError):V.ImageRoot('/')
        with self.assertRaisesRegex(V.VerificationError, 'regular raw image'):
            V.verify_image('/dev/null', self.fixture.metadata, self.fixture.locks, self.fixture.base)

    def test_export_rewrites_are_semantically_checked_without_weakening_hardware(self):
        f = self.fixture
        f.put('/etc/fstab', 'ROOTDEV / ext4 defaults 0 1\nBOOTDEV /boot/firmware vfat defaults 0 2\n')
        f.put('/boot/firmware/cmdline.txt', 'root=ROOTDEV quiet\n')
        self.assertEqual(f.verify()['status'], 'passed')
        ids = {'root': '1234abcd-02', 'boot': '1234abcd-01'}
        command = 'root=PARTUUID=1234abcd-02 quiet\n'
        f.put('/boot/firmware/cmdline.txt', command);f.put('/boot/cmdline.txt', command)
        f.put('/etc/fstab', 'PARTUUID=1234abcd-02 / ext4 defaults 0 1\n'
                           'PARTUUID=1234abcd-01 /boot/firmware vfat defaults 0 2\n')
        issue = 'Generated using pi-gen, ' + f.metadata['pi_gen_commit'] + ', stage2\n'
        f.put('/etc/rpi-issue', issue);f.put('/boot/firmware/issue.txt', issue)
        f.link('/boot/issue.txt', 'firmware/issue.txt');f.put('/etc/machine-id', 'uninitialized\n')
        self.assertEqual(f.verify()['status'], 'passed')
        checks = []
        def check(name, condition, detail=None):
            checks.append(name)
            V.require(condition, name)
        V.verify_exported_boot(V.ImageRoot(f.root, os.getuid()), ids, f.metadata, check)
        self.assertIn('export_fstab_root', checks)
        # UUIDs hidden in comments and duplicate/incorrect root arguments fail.
        f.put('/etc/fstab', '# PARTUUID=1234abcd-02 PARTUUID=1234abcd-01\n/dev/wrong / ext4 defaults 0 1\n')
        with self.assertRaisesRegex(V.VerificationError, 'export_fstab_root'):
            V.verify_exported_boot(V.ImageRoot(f.root, os.getuid()), ids, f.metadata, check)

    def test_required_elf_transitive_dependency_cannot_be_exempted(self):
        f = self.fixture
        program = elf_with_needed('libRequired.so.1');library = elf_with_needed('libMissing.so.1')
        f.put('/usr/bin/tyrell-status', program, 0o755)
        f.compat['elf_roots']['/usr/bin/tyrell-status'] = sha(program)
        path = '/usr/lib/aarch64-linux-gnu/libRequired.so.1';f.put(path, library)
        f.compat['library_files'][path] = {'sha256': sha(library)}
        f.compat['optional_elf_backends'] = [{'path': path, 'sha256': sha(library),
            'reason': 'This attempted exemption must not override reachability',
            'runtime_eligible': False, 'missing_sonames': ['libMissing.so.1']}]
        f.bind_locks();f.bind_receipts();self.fails('required_elf_dependencies')

    def test_optional_backend_requires_exact_noneligible_explanation(self):
        f = self.fixture
        path = '/usr/lib/aarch64-linux-gnu/caca/libgl_plugin.so.0.0.0'
        data = elf_with_needed('libGLU.so.1', 'libglut.so.3');f.put(path, data)
        f.compat['library_files'][path] = {'sha256': sha(data)}
        record = {'path': path, 'sha256': sha(data), 'runtime_eligible': False,
            'reason': 'Optional terminal OpenGL backend is outside the native runtime selection',
            'missing_sonames': ['libGLU.so.1', 'libglut.so.3']}
        f.compat['optional_elf_backends'] = [record]
        f.bind_locks();f.bind_receipts();self.assertEqual(f.verify()['status'], 'passed')
        f.compat['optional_elf_backends'] = [];f.bind_locks();f.bind_receipts()
        self.fails('noneligible_backend_explained')

    def test_debian_soname_versions_allow_tilde_but_never_a_path(self):
        name = 'libgallium-26.2.6-1~bpo13+0~rpt2.so'
        self.assertEqual(V.elf_needed(elf_with_needed(name)), [name])
        with self.assertRaises(V.VerificationError):V.elf_needed(elf_with_needed('../libbad.so'))

    def test_pinned_base_setuid_elf_is_read_without_allowing_setuid_native_assets(self):
        f = self.fixture
        original = Path.lstat
        def setuid_mode(target):
            # The desktop sandbox strips set-ID bits. Model only that stat bit;
            # actual root-owned base mode qualification happens in Linux later.
            def inspect(path, *args, **kwargs):
                value = original(path, *args, **kwargs)
                return os.stat_result((value.st_mode | stat.S_ISUID, *tuple(value)[1:])) if path == target else value
            return inspect
        with patch.object(Path, 'lstat', setuid_mode(f.root / 'usr/sbin/pkexec')):
            f.bind_locks();f.bind_receipts();self.assertEqual(f.verify()['status'], 'passed')
        self.fails('elf_root_metadata')
        f.bind_locks();f.bind_receipts()
        with patch.object(Path, 'lstat', setuid_mode(f.root / V.MAIN.lstrip('/'))):
            self.fails('untrusted image file')

    def test_activation_files_and_power_selection_bound(self):
        f = self.fixture
        f.activation['files']['/etc/tyrell-zero/power-policy.json']['sha256'] = '0' * 64
        f.put(V.ACTIVATION, f.activation)
        self.fails('activation_file')
        f.put('/etc/tyrell-zero/power-policy.json', {'schema': 1, 'preset': 'combined'})
        f.bind_receipts();self.fails('power_selection')

    def test_credentials_and_ready_state_cannot_leak(self):
        f = self.fixture
        f.put('/etc/shadow', 'root:!:1:0:99999:7:::\npi:$6$secret:1:0:99999:7:::\n')
        self.fails('no_bootstrap_password_pi')
        f.put('/etc/shadow', 'root:!:1:0:99999:7:::\npi:!:1:0:99999:7:::\n')
        f.put('/var/lib/tyrell-zero/readiness.v1.json', {})
        self.fails('no_configured_setup')

    def test_missing_resource_and_claimed_hardware_qualification_rejected(self):
        f = self.fixture
        f.compat['qualification']['hardware_qualified'] = True
        f.bind_locks();f.bind_receipts();self.fails('native_qualification_scope')
        f.compat['qualification']['hardware_qualified'] = False
        f.bind_locks();f.bind_receipts()
        (f.root / 'usr/share/APPLaunch/share/font/fixture.ttf').unlink()
        self.fails('No such file')

    def test_elf_header_and_bounds_checked(self):
        self.assertEqual(V.elf_needed(elf()), [])
        for data in (b'not-ELF', elf()[:20]):
            with self.assertRaises(V.VerificationError):V.elf_needed(data)
        data = bytearray(elf());struct.pack_into('<Q', data, 32, 1024);struct.pack_into('<HH', data, 54, 56, 1)
        with self.assertRaises(V.VerificationError):V.elf_needed(bytes(data))


if __name__ == '__main__':unittest.main()
