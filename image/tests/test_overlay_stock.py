"""Host-only stock overlay guards; no mounts, VM, network, or devices."""
import importlib.util
import json
import lzma
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('overlay_stock', Path(__file__).parents[1] / 'overlay-stock.py')
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)


class OverlayStockTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, content='stock\n'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def table(self):
        return {'partitiontable': {'label': 'dos', 'sectorsize': 512, 'partitions': [
            {'start': 8192, 'size': 1048576, 'type': 'c'},
            {'start': 1056768, 'size': 1048576, 'type': '83'}]}}

    def protected(self):
        for name in ('boot/firmware/config.txt', 'lib/modules/test/extra/lcd.ko',
                     'lib/firmware/panel.bin', 'usr/share/APPLaunch/bin/launcher',
                     'etc/cardputerzero-dtoverlays.commit'):
            self.write(name)
        self.write('usr/share/APPLaunch/applications/vendor.desktop')
        self.write('usr/share/APPLaunch/share/images/vendor.png')
        (self.root / 'etc/alternatives').mkdir(parents=True, exist_ok=True)
        (self.root / 'etc/alternatives/regulatory.db').symlink_to('/lib/firmware/panel.bin')

    def additions(self):
        for name in overlay.TYRELL_ADDITIONS:
            self.write(name, 'Tyrell\n')

    def test_partition_layout_rejects_overlap_bounds_wrong_types_and_devices(self):
        overlay.validate_partitions(self.table(), 2 * 1024**3)
        mutations = [lambda t: t.update(label='gpt'),
                     lambda t: t.update(sectorsize=4096),
                     lambda t: t['partitions'][1].update(start=100),
                     lambda t: t['partitions'][1].update(size=10**12),
                     lambda t: t['partitions'][1].update(type='82'),
                     lambda t: t['partitions'][0].update(start=True),
                     lambda t: t['partitions'][0].update(size=-1)]
        for change in mutations:
            with self.subTest(change=change):
                data = self.table()
                change(data['partitiontable'])
                with self.assertRaises(ValueError):
                    overlay.validate_partitions(data, 2 * 1024**3)

    def test_decompression_never_replaces_source_or_existing_output(self):
        source = self.root / 'stock.img.xz'
        source.write_bytes(lzma.compress(b'original image bytes'))
        expected = source.read_bytes()
        target = self.root / 'copy.img'
        overlay.decompress(source, target, len(b'original image bytes'))
        self.assertEqual(target.read_bytes(), b'original image bytes')
        self.assertEqual(source.read_bytes(), expected)
        with self.assertRaisesRegex(ValueError, 'Preserving'):
            overlay.decompress(source, target, len(b'original image bytes'))
        target.unlink()
        target.symlink_to(source)
        with self.assertRaisesRegex(ValueError, 'Preserving'):
            overlay.decompress(source, target, len(b'original image bytes'))
        target.unlink()
        link = self.root / 'download-link'
        link.symlink_to(source)
        with self.assertRaisesRegex(ValueError, 'regular file'):
            overlay.decompress(link, target, len(b'original image bytes'))

    def test_failed_decompression_removes_only_partial_copy(self):
        source = self.write('broken.xz', 'not xz')
        target = self.root / 'partial.img'
        with self.assertRaises(lzma.LZMAError):
            overlay.decompress(source, target, 32)
        self.assertFalse(target.exists())
        self.assertEqual(source.read_text(), 'not xz')

    def test_exclusive_create_race_preserves_the_other_file(self):
        source = self.root / 'stock.xz'
        source.write_bytes(lzma.compress(b'image'))
        target = self.root / 'copy.img'
        original = Path.open
        def racing_open(path, mode='r', *args, **kwargs):
            if path == target and mode == 'xb':
                with original(path, 'wb') as stream:
                    stream.write(b'created by another caller')
                raise FileExistsError('simulated exclusive create race')
            return original(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', racing_open):
            with self.assertRaises(FileExistsError):
                overlay.decompress(source, target, len(b'image'))
        self.assertEqual(target.read_bytes(), b'created by another caller')

    def test_only_tyrell_package_may_be_added(self):
        before = {'vendor': {'Version': '1', 'Architecture': 'arm64', 'Status': 'hold ok installed'}}
        after = {**before, 'tyrell-status': {'Version': '0.1.0-1', 'Architecture': 'arm64', 'Status': 'install ok installed'}}
        overlay.compare_packages(before, after, '0.1.0-1')
        with self.assertRaisesRegex(ValueError, 'stock package set'):
            overlay.compare_packages(before, {**after, 'dependency': before['vendor']}, '0.1.0-1')
        for field, changed in [('Version', '2'), ('Architecture', 'all'), ('Status', 'install ok installed')]:
            with self.subTest(field=field):
                altered = {**after, 'vendor': {**before['vendor'], field: changed}}
                with self.assertRaisesRegex(ValueError, 'stock package: vendor'):
                    overlay.compare_packages(before, altered, '0.1.0-1')

    def test_protected_snapshot_tracks_bytes_modes_symlinks_and_exact_additions(self):
        self.protected()
        link = self.root / 'lib/modules/test/build'
        link.symlink_to('/usr/src/test')
        before = overlay.snapshot(self.root)
        self.assertEqual(before['lib/modules/test/build']['target'], '/usr/src/test')
        self.assertEqual(before['lib/modules/test']['type'], 'directory')
        self.assertEqual(before['lib/modules/test']['uid'], (self.root / 'lib/modules/test').stat().st_uid)
        self.assertEqual(before['lib/modules/test']['gid'], (self.root / 'lib/modules/test').stat().st_gid)
        self.additions()
        overlay.compare_protected(before, overlay.snapshot(self.root))
        firmware = self.root / 'lib/firmware/panel.bin'
        firmware.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'Protected stock content changed'):
            overlay.compare_protected(before, overlay.snapshot(self.root))
        firmware.chmod(before['lib/firmware/panel.bin']['mode'])
        firmware.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'Protected stock content changed'):
            overlay.compare_protected(before, overlay.snapshot(self.root))

    def test_ownership_only_changes_are_part_of_protected_identity(self):
        self.protected()
        before = overlay.snapshot(self.root)
        self.additions()
        after = overlay.snapshot(self.root)
        path = self.root / 'lib/firmware/panel.bin'
        details = list(path.lstat())
        details[4] += 1
        details[5] += 1
        with patch.object(Path, 'lstat', return_value=os.stat_result(details)):
            changed = overlay.node_identity(path)
        self.assertEqual(changed['sha256'], before['lib/firmware/panel.bin']['sha256'])
        self.assertEqual(changed['mode'], before['lib/firmware/panel.bin']['mode'])
        after['lib/firmware/panel.bin'] = changed
        with self.assertRaisesRegex(ValueError, 'Protected stock content changed'):
            overlay.compare_protected(before, after)

    def test_protected_subtree_symlink_cannot_hide_its_contents(self):
        self.protected()
        original = self.root / 'usr/share/APPLaunch'
        relocated = self.root / 'usr/share/hidden-launcher'
        original.rename(relocated)
        original.symlink_to('hidden-launcher')
        with self.assertRaisesRegex(ValueError, 'real directory'):
            overlay.snapshot(self.root)
        original.unlink()
        relocated.rename(original)
        record = self.root / 'etc/cardputerzero-dtoverlays.commit'
        record.rename(record.with_suffix('.actual'))
        record.symlink_to('cardputerzero-dtoverlays.actual')
        with self.assertRaisesRegex(ValueError, 'regular file'):
            overlay.snapshot(self.root)

    def test_retargeted_firmware_alternative_is_rejected(self):
        self.protected()
        before = overlay.snapshot(self.root)
        self.additions()
        routing = self.root / 'etc/alternatives/regulatory.db'
        routing.unlink()
        routing.symlink_to('/lib/firmware/other-regulatory.db')
        with self.assertRaisesRegex(ValueError, 'Protected stock content changed: etc/alternatives/regulatory.db'):
            overlay.compare_protected(before, overlay.snapshot(self.root))

    def test_usrmerge_parent_symlink_is_allowed(self):
        self.protected()
        original = self.root / 'lib'
        relocated = self.root / 'usr/lib'
        original.rename(relocated)
        original.symlink_to('usr/lib')
        recorded = overlay.snapshot(self.root)
        self.assertIn('lib/modules/test/extra/lcd.ko', recorded)

    def test_exact_xz_size_metadata_is_validated_before_guest_writes(self):
        source = self.write('stock.xz', 'compressed bytes')
        size = source.stat().st_size
        file = f'file\t1\t1\t{size}\t1234\t0.1\tCRC64\t0'
        total = f'totals\t1\t1\t{size}\t1234\t0.1\tCRC64\t0\t1'
        with patch.object(overlay.shutil, 'which', return_value='/host/tool/xz'), \
                patch.object(overlay, 'command', return_value=file + '\n' + total + '\n') as invoke:
            self.assertEqual(overlay.xz_uncompressed_size(source), 1234)
            self.assertEqual(invoke.call_args.args[0][:4], ['/host/tool/xz', '--robot', '--list', '--'])
        for data in (file, file + '\n' + total.replace('\t1234\t', '\t1235\t'),
                     file + '\n' + total[:-1] + '2',
                     file.replace(f'\t{size}\t', '\t999\t') + '\n' + total,
                     file + '\n' + file + '\n' + total):
            with self.subTest(data=data), \
                    patch.object(overlay.shutil, 'which', return_value='/host/tool/xz'), \
                    patch.object(overlay, 'command', return_value=data):
                with self.assertRaises(ValueError):
                    overlay.xz_uncompressed_size(source)

    def test_decompression_rejects_overlong_short_and_truncated_streams(self):
        source = self.root / 'stock.xz'
        target = self.root / 'bounded.img'
        original = lzma.compress(b'raw stock content')
        source.write_bytes(original)
        for expected in (1, len(b'raw stock content') + 1):
            with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, 'exact locked raw size'):
                overlay.decompress(source, target, expected)
            self.assertFalse(target.exists())
            self.assertEqual(source.read_bytes(), original)
        source.write_bytes(original[:-3])
        with self.assertRaises((EOFError, lzma.LZMAError)):
            overlay.decompress(source, target, len(b'raw stock content'))
        self.assertFalse(target.exists())

    def test_guest_free_space_failure_precedes_creating_the_raw_image(self):
        source = self.root / 'stock.xz'
        source.write_bytes(lzma.compress(b'raw'))
        target = self.root / 'copy.img'
        with patch.object(overlay.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 0})()):
            with self.assertRaisesRegex(ValueError, 'Insufficient guest output space'):
                overlay.decompress(source, target, 3)
        self.assertFalse(target.exists())

    def test_unrecorded_additions_and_tyrell_symlinks_are_rejected(self):
        self.protected()
        before = overlay.snapshot(self.root)
        self.additions()
        extra = self.write('usr/share/APPLaunch/extra')
        with self.assertRaisesRegex(ValueError, 'Unexpected protected tree additions'):
            overlay.compare_protected(before, overlay.snapshot(self.root))
        extra.unlink()
        icon = self.root / 'usr/share/APPLaunch/share/images/tyrell-status.png'
        icon.unlink()
        icon.symlink_to('vendor.png')
        with self.assertRaisesRegex(ValueError, 'regular files'):
            overlay.compare_protected(before, overlay.snapshot(self.root))

    def test_sanitization_erases_credentials_and_preserves_vendor_oobe(self):
        self.write('etc/machine-id', 'private identity')
        self.write('var/lib/dbus/machine-id', 'private identity')
        self.write('etc/passwd', 'root:x:0:0:root:/root:/bin/bash\npi:x:1000:1000::/home/pi:/bin/bash\n')
        shadow = self.write('etc/shadow', 'root:$secret:1:0:99999:7:::\npi:$secret:1:0:99999:7:::\n')
        shadow.chmod(0o640)
        self.write('etc/shadow-', 'old account password hash')
        self.write('etc/gshadow-', 'old group password hash')
        self.write('etc/ssh/ssh_host_ed25519_key', 'private key')
        self.write('etc/NetworkManager/system-connections/home.nmconnection', 'wifi secret')
        self.write('home/pi/.ssh/authorized_keys', 'personal key')
        self.write('home/pi/.bash_history', 'history')
        self.write('usr/lib/systemd/system/LaunchWizard.service')
        for name, target in [('etc/systemd/system/multi-user.target.wants/ssh.service', '/usr/lib/systemd/system/ssh.service'),
                              ('etc/systemd/system/sockets.target.wants/ssh.socket', '/usr/lib/systemd/system/ssh.socket'),
                              ('etc/systemd/user/default.target.wants/APPLaunch.service', '/usr/lib/systemd/user/APPLaunch.service')]:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(target)
        report = overlay.sanitize(self.root)
        self.assertEqual(report['ssh'], 'disabled')
        self.assertEqual((self.root / 'etc/machine-id').read_text(), '')
        self.assertEqual((self.root / 'var/lib/dbus/machine-id').readlink(), Path('../../../etc/machine-id'))
        self.assertNotIn('$secret', shadow.read_text())
        self.assertFalse((self.root / 'etc/shadow-').exists())
        self.assertFalse((self.root / 'etc/gshadow-').exists())
        self.assertEqual(stat.S_IMODE(shadow.stat().st_mode), 0o640)
        self.assertFalse((self.root / 'home/pi/.ssh/authorized_keys').exists())
        self.assertFalse((self.root / 'home/pi/.bash_history').exists())
        self.assertFalse(list((self.root / 'etc/ssh').glob('ssh_host_*')))
        self.assertFalse(list((self.root / 'etc/NetworkManager/system-connections').iterdir()))
        self.assertTrue((self.root / 'var/lib/LaunchWizard/run-oobe').is_file())
        self.assertTrue((self.root / 'var/lib/LaunchWizard/run-keyboard-guide').is_file())
        self.assertTrue((self.root / 'etc/systemd/system/multi-user.target.wants/LaunchWizard.service').is_symlink())

    def test_boot_provisioning_seeds_fail_before_root_sanitation(self):
        machine = self.write('etc/machine-id', 'unchanged until reviewed')
        for name in ('userconf', 'userconf.txt', 'ssh', 'ssh.txt', 'firstrun.sh'):
            with self.subTest(name=name):
                seed = self.write('boot/firmware/' + name, 'initial provisioning')
                with self.assertRaisesRegex(ValueError, 'provisioning seed requiring review'):
                    overlay.sanitize(self.root)
                self.assertEqual(seed.read_text(), 'initial provisioning')
                self.assertEqual(machine.read_text(), 'unchanged until reviewed')
                seed.unlink()

    def test_cleanup_refuses_parent_symlink_escaping_image(self):
        self.write('home/pi/.ssh/authorized_keys', 'inside')
        outside = self.root / 'outside'
        outside.mkdir()
        escaped = self.root / 'home/escape'
        escaped.symlink_to(outside.parent.parent)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            overlay.unlink_if_present(self.root, 'home/escape/private')

    def test_install_is_network_isolated_and_restores_service_policy_on_failure(self):
        package = self.write('input.deb', 'verified package')
        policy = self.write('usr/sbin/policy-rc.d', '#!/bin/sh\nexit 0\n')
        policy.chmod(0o750)
        (self.root / 'var/tmp').mkdir(parents=True)
        calls = []
        def failing_command(args):
            calls.append(args)
            self.assertEqual(policy.read_text(), '#!/bin/sh\nexit 101\n')
            raise RuntimeError('dependency failure')
        with patch.object(overlay, 'command', side_effect=failing_command):
            with self.assertRaisesRegex(RuntimeError, 'dependency failure'):
                overlay.install_package(self.root, package)
        self.assertEqual(calls[0][:4], ['unshare', '--net', '--', 'chroot'])
        self.assertEqual(policy.read_text(), '#!/bin/sh\nexit 0\n')
        self.assertEqual(stat.S_IMODE(policy.stat().st_mode), 0o750)
        self.assertFalse((self.root / 'var/tmp/tyrell-stock-overlay.deb').exists())

    def test_install_audit_failure_does_not_leave_temporary_policy(self):
        package = self.write('input.deb')
        (self.root / 'usr/sbin').mkdir(parents=True)
        (self.root / 'var/tmp').mkdir(parents=True)
        with patch.object(overlay, 'command', side_effect=['', 'half-configured package']):
            with self.assertRaisesRegex(ValueError, 'audit'):
                overlay.install_package(self.root, package)
        self.assertFalse((self.root / 'usr/sbin/policy-rc.d').exists())

    def test_failed_unmount_retains_mount_directory_and_loop_for_inspection(self):
        incoming, output = self.root / 'input', self.root / 'output'
        (incoming / 'image').mkdir(parents=True)
        (incoming / 'assets').mkdir()
        output.mkdir()
        compressed = incoming / 'assets/stock.img.xz'
        compressed.write_bytes(b'authenticated download')
        package = incoming / 'assets' / overlay.PACKAGE_NAME
        package.write_bytes(b'package')
        lockpath = incoming / 'image/stock-image.json'
        lockpath.write_text('{}')
        lock = {'image': {'name': compressed.name, 'bytes': compressed.stat().st_size,
                          'sha256': overlay.sha(compressed)}}
        metadata = {'package_sha256': overlay.sha(package), 'package_version': '0.1.0-1',
                    'stock_image_lock_sha256': overlay.sha(lockpath),
                    'stock_base_sha256': lock['image']['sha256'],
                    'build_mode': 'stock-image', 'apt_profile': 'stock-image', 'stock_uncompressed_bytes': 4}
        (incoming / 'image-metadata.json').write_text(json.dumps(metadata))
        (incoming / 'package-manifest.json').write_text(json.dumps({'sha256': {package.name: overlay.sha(package)}}))
        mounted = self.root / 'retained-mount'
        (mounted / 'boot/firmware').mkdir(parents=True)
        calls = []
        def simulated_command(args, **kwargs):
            calls.append(args)
            if args[0] == 'dpkg-deb':
                return {'Package': 'tyrell-status', 'Version': '0.1.0-1', 'Architecture': 'arm64'}[args[-1]]
            if args[:2] == ['sfdisk', '--json']:
                return '{}'
            if args[:2] == ['losetup', '--find']:
                return '/dev/loop9'
            if args[:2] == ['losetup', '--json']:
                return json.dumps({'loopdevices': [{'back-file': str(output / overlay.RAW_NAME)}]})
            if args[0] == 'blkid':
                return 'vfat' if str(args[-1]).endswith('p1') else 'ext4'
            if args[0] == 'umount' and args[1] == mounted / 'boot/firmware':
                raise RuntimeError('busy boot mount')
            return ''
        with patch.object(overlay.os, 'geteuid', return_value=0), \
                patch.object(overlay.os, 'uname') as uname, \
                patch.object(overlay.shutil, 'which', return_value='/tool'), \
                patch.object(overlay.runpy, 'run_path', return_value={'load_lock': lambda p: lock}), \
                patch.object(overlay, 'xz_uncompressed_size', return_value=4), \
                patch.object(overlay, 'decompress', side_effect=lambda s, d, expected: d.write_bytes(b'copy')), \
                patch.object(overlay, 'validate_partitions', return_value=[]), \
                patch.object(Path, 'is_block_device', return_value=True), \
                patch.object(overlay.tempfile, 'mkdtemp', return_value=str(mounted)), \
                patch.object(overlay, 'stock_identity', side_effect=ValueError('identity mismatch')), \
                patch.object(overlay, 'command', side_effect=simulated_command), \
                patch.object(overlay.shutil, 'rmtree') as remove:
            uname.return_value.machine = 'aarch64'
            with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
                overlay.overlay(incoming, output)
            remove.assert_not_called()
        self.assertTrue(mounted.is_dir())
        self.assertFalse(any(args[:2] == ['losetup', '--detach'] for args in calls))


if __name__ == '__main__':
    unittest.main()
