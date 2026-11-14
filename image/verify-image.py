#!/usr/bin/env python3
"""Validate a CardputerZero image offline using read-only loop devices.

Run as root inside the native ARM64 Linux builder. Never accepts a block device,
never mounts the image read-write, and never boots or contacts the handheld.
"""
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

PI_GEN_COMMIT = '5f4c44f3431bf1dfb968c10f7228ad6fa0c66cf4'
DRIVERS_COMMIT = '75518549a2640486f5d13ea72ca4935b198d7c20'
KERNEL = '6.18.39+rpt-rpi-v8'
KERNEL_PACKAGE = '1:6.18.39-1+rpt1'
PACKAGE_VERSION = '0.1.0-1'
LAUNCHER_VERSION = '0.6.105'
CLEAN_ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
             'LC_ALL': 'C.UTF-8', 'TZ': 'UTC'}
STOCK_PROTECTED_ROOTS = ('boot/firmware', 'lib/modules', 'lib/firmware',
                         'usr/share/APPLaunch', 'etc/alternatives',
                         'etc/cardputerzero-dtoverlays.commit')
STOCK_ADDITIONS = {'usr/share/APPLaunch/applications/tyrell-status.desktop',
                   'usr/share/APPLaunch/share/images/tyrell-status.png'}
STOCK_IDENTITY_FIELDS = {'file': {'type', 'mode', 'uid', 'gid', 'sha256'},
                         'directory': {'type', 'mode', 'uid', 'gid'},
                         'symlink': {'type', 'mode', 'uid', 'gid', 'target'}}


class VerificationError(RuntimeError):
    pass


def command(args, *, accepted=(0,), timeout=120):
    result = subprocess.run([str(x) for x in args], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=CLEAN_ENV, timeout=timeout)
    if result.returncode not in accepted:
        raise VerificationError(f'{Path(str(args[0])).name} exited {result.returncode}: '
                                + (result.stderr or result.stdout).strip()[-2000:])
    return result.stdout


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def expected_identity(lock=None):
    """Use the selected stock contract, retaining the original source defaults."""
    if lock is None:
        return {'pi_gen_commit': PI_GEN_COMMIT, 'drivers_commit': DRIVERS_COMMIT,
                'kernel_release': KERNEL, 'kernel_package_version': KERNEL_PACKAGE,
                'applaunch_version': LAUNCHER_VERSION}
    return {name: lock[name] for name in ('pi_gen_commit', 'drivers_commit',
            'kernel_release', 'kernel_package_version', 'applaunch_version')}


def stock_relative_path(value):
    if not isinstance(value, str) or not value or value.startswith('/'):
        raise VerificationError('Invalid stock protected path')
    path = Path(value)
    if '..' in path.parts or '.' in path.parts or str(path) != value:
        raise VerificationError('Invalid stock protected path: ' + value)
    if not any(value == base or value.startswith(base + '/') for base in STOCK_PROTECTED_ROOTS):
        raise VerificationError('Path outside stock protected scope: ' + value)
    return path


def validate_stock_evidence(lock, evidence, lock_sha):
    """Validate a pre-overlay snapshot's pinned source identity, not its verdict."""
    if not isinstance(evidence, dict) or evidence.get('schema_version') != 1:
        raise VerificationError('Unsupported stock evidence schema')
    if evidence.get('stock_lock_sha256') != lock_sha:
        raise VerificationError('Stock evidence belongs to a different lock')
    base = evidence.get('stock_base', {})
    if not isinstance(base, dict) or any(base.get(key) != lock['image'][key]
                                       for key in ('name', 'bytes', 'sha256')):
        # Older snapshots may call the descriptive name "filename".
        if not (isinstance(base, dict) and base.get('filename') == lock['image']['name']
                and base.get('bytes') == lock['image']['bytes']
                and base.get('sha256') == lock['image']['sha256']):
            raise VerificationError('Stock evidence base differs from pinned image')
    if type(base.get('uncompressed_bytes')) is not int or base['uncompressed_bytes'] <= 0:
        raise VerificationError('Missing exact uncompressed stock image size')
    for name, value in [('uncompressed stock image', base.get('uncompressed_sha256')),
                        ('final raw image', evidence.get('raw_image_sha256')),
                        ('installed package', evidence.get('package_sha256'))]:
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
            raise VerificationError('Missing SHA-256 for ' + name)
    if evidence.get('identity') != expected_identity(lock):
        raise VerificationError('Stock evidence identity differs from lock')
    packages = evidence.get('packages_before')
    if not isinstance(packages, dict) or not packages or 'tyrell-status' in packages:
        raise VerificationError('Stock evidence lacks pristine package inventory')
    for name, fields in packages.items():
        if not isinstance(name, str) or not isinstance(fields, dict) or set(fields) != {'Version', 'Architecture', 'Status'}:
            raise VerificationError('Invalid stock package inventory entry')
        if any(not isinstance(value, str) or not value for value in fields.values()):
            raise VerificationError('Incomplete stock package inventory entry: ' + name)
    protected = evidence.get('protected_files')
    if not isinstance(protected, dict) or not protected:
        raise VerificationError('Stock evidence lacks protected file snapshot')
    required = {*STOCK_PROTECTED_ROOTS, 'boot/firmware/config.txt',
                'boot/firmware/start.elf', 'boot/firmware/fixup.dat',
                'usr/share/APPLaunch/bin/M5CardputerZero-APPLaunch',
                'lib/modules/' + lock['kernel_release'] + '/extra'}
    if not required.issubset(protected):
        raise VerificationError('Stock evidence omits required protected paths')
    if STOCK_ADDITIONS.intersection(protected):
        raise VerificationError('Pristine stock snapshot already contains Tyrell app paths')
    for name in STOCK_PROTECTED_ROOTS:
        expected_type = 'file' if name == 'etc/cardputerzero-dtoverlays.commit' else 'directory'
        if not isinstance(protected[name], dict) or protected[name].get('type') != expected_type:
            raise VerificationError('Unsupported protected stock root type: ' + name)
    for name, identity in protected.items():
        stock_relative_path(name)
        if not isinstance(identity, dict) or identity.get('type') not in {'file', 'directory', 'symlink'}:
            raise VerificationError('Invalid protected file identity: ' + name)
        if set(identity) != STOCK_IDENTITY_FIELDS[identity['type']]:
            raise VerificationError('Incomplete or unsupported protected file identity: ' + name)
        if identity['type'] == 'file' and (not isinstance(identity.get('sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', identity['sha256'])):
            raise VerificationError('Invalid protected file checksum: ' + name)
        if identity['type'] == 'symlink' and not isinstance(identity.get('target'), str):
            raise VerificationError('Invalid protected symlink: ' + name)
        if type(identity['mode']) is not int or not 0 <= identity['mode'] <= 0o7777:
            raise VerificationError('Invalid protected file mode: ' + name)
        for field in ('uid', 'gid'):
            if type(identity[field]) is not int or not 0 <= identity[field] <= 0xffffffff:
                raise VerificationError('Invalid protected file ' + field + ': ' + name)
    return evidence


def load_stock_inputs(lock_path=None, evidence_path=None):
    if bool(lock_path) != bool(evidence_path):
        raise VerificationError('--stock-lock and --stock-evidence must be supplied together')
    if lock_path is None:
        return None
    lock_path, evidence_path = Path(lock_path), Path(evidence_path)
    for path in (lock_path, evidence_path):
        if path.is_symlink() or not path.is_file():
            raise VerificationError('Stock verification input must be a regular file: ' + str(path))
    loader = runpy.run_path(str(Path(__file__).with_name('stock-image.py')))['load_lock']
    lock = loader(lock_path)
    evidence = json.loads(evidence_path.read_text())
    lock_sha = sha256(lock_path)
    validate_stock_evidence(lock, evidence, lock_sha)
    return {'lock': lock, 'lock_sha256': lock_sha, 'evidence': evidence,
            'evidence_sha256': sha256(evidence_path)}


def protected_path(root, relative):
    """Permit a symlink itself, while never traversing outside the image."""
    path = root / stock_relative_path(relative)
    if not path.parent.resolve().is_relative_to(root.resolve()):
        raise VerificationError('Stock protected path escapes image: ' + relative)
    return path


def protected_snapshot(root):
    result = {}

    def inspect(relative):
        path = protected_path(root, relative)
        details = path.lstat()
        if stat.S_ISLNK(details.st_mode):
            identity = {'type': 'symlink', 'target': os.readlink(path)}
        elif stat.S_ISREG(details.st_mode):
            identity = {'type': 'file', 'sha256': sha256(path)}
        elif stat.S_ISDIR(details.st_mode):
            identity = {'type': 'directory'}
        else:
            raise VerificationError('Unexpected protected stock file type: ' + relative)
        identity['mode'] = stat.S_IMODE(details.st_mode)
        identity['uid'] = details.st_uid
        identity['gid'] = details.st_gid
        result[relative] = identity
        if identity['type'] == 'directory':
            for child in sorted(path.iterdir()):
                inspect(relative + '/' + child.name)

    for relative in STOCK_PROTECTED_ROOTS:
        # A symlink at a protected subtree root would hide its target contents
        # from this non-following walk. Reject that unsupported layout while
        # retaining ordinary symlink leaves such as kernel header links.
        path = protected_path(root, relative)
        details = path.lstat()
        expected_type = stat.S_ISREG if relative == 'etc/cardputerzero-dtoverlays.commit' else stat.S_ISDIR
        if not expected_type(details.st_mode):
            raise VerificationError('Unsupported protected stock root type: ' + relative)
        inspect(relative)
    return result


def compare_stock_protected(root, evidence):
    """Compare final content and the complete path set to the pre-overlay copy."""
    expected = evidence['protected_files']
    actual = protected_snapshot(Path(root))
    missing, added = set(expected) - set(actual), set(actual) - set(expected)
    if missing or added != STOCK_ADDITIONS:
        raise VerificationError('Protected stock path set differs: missing=' + repr(sorted(missing))
                                + ' unexpected=' + repr(sorted(added - STOCK_ADDITIONS))
                                + ' absent Tyrell additions=' + repr(sorted(STOCK_ADDITIONS - added)))
    for name, identity in expected.items():
        if any(actual[name].get(key) != value for key, value in identity.items()):
            raise VerificationError('Protected stock content differs: ' + name)
    return len(expected)


def compare_stock_packages(packages, evidence):
    """Only the independently supplied Tyrell package may change dpkg inventory."""
    original = evidence['packages_before']
    actual = {name: {key: fields.get(key) for key in ('Version', 'Architecture', 'Status')}
              for name, fields in packages.items()}
    if set(actual) != set(original) | {'tyrell-status'}:
        raise VerificationError('Stock package inventory path set differs')
    for name, fields in original.items():
        if actual[name] != fields:
            raise VerificationError('Stock package inventory differs: ' + name)
    if actual['tyrell-status'] != {'Version': PACKAGE_VERSION, 'Architecture': 'arm64',
                                  'Status': 'install ok installed'}:
        raise VerificationError('Unexpected Tyrell package inventory entry')
    return len(original)


def validate_expected_metadata(metadata, identity, package_sha, stock=None):
    expected = {key: identity[key] for key in ('pi_gen_commit', 'drivers_commit',
                'kernel_release', 'applaunch_version')}
    expected.update(package_version=PACKAGE_VERSION, package_sha256=package_sha)
    if stock is not None:
        expected.update(stock_image_lock_sha256=stock['lock_sha256'],
                        stock_base_sha256=stock['lock']['image']['sha256'],
                        stock_uncompressed_bytes=stock['evidence']['stock_base']['uncompressed_bytes'],
                        build_mode='stock-image', apt_profile='stock-image')
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise VerificationError('Image metadata differs from selected inputs: ' + key)
    return expected


def unprovisioned_password(username, password, stock_mode=False):
    """Stock derivatives lock accounts; source OOBE may retain a blank staging user."""
    return password.startswith(('!', '*')) or (not stock_mode and username != 'root' and password == '')


def image_path(root, path):
    relative = str(path).lstrip('/')
    if '..' in Path(relative).parts:
        raise VerificationError('Parent traversal in image path')
    candidate = root / relative
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise VerificationError('Image symlink escapes mounted root: ' + relative)
    return candidate


def read(root, path):
    return image_path(root, path).read_text()


def parse_packages(text):
    packages = {}
    for block in text.split('\n\n'):
        fields = {}
        for line in block.splitlines():
            if line and not line[0].isspace() and ': ' in line:
                key, value = line.split(': ', 1)
                fields[key] = value
        if fields.get('Package'):
            packages[fields['Package']] = fields
    return packages


def installed(package):
    # APT holds change dpkg's desired state while keeping the package fully
    # installed. Kernel/header holds are intentional in this image profile.
    return package.get('Status') in ('install ok installed', 'hold ok installed')


def compare_package_payload(package, root):
    count = 0
    process = subprocess.Popen(['dpkg-deb', '--fsys-tarfile', str(package)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=CLEAN_ENV)
    try:
        with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
            for member in archive:
                if member.isdir():
                    continue
                target = image_path(root, member.name)
                if member.isfile():
                    source = archive.extractfile(member)
                    expected = hashlib.file_digest(source, 'sha256').hexdigest()
                    if not target.is_file() or sha256(target) != expected:
                        raise VerificationError('Installed package payload differs: ' + member.name)
                    count += 1
                elif member.issym():
                    if not target.is_symlink() or os.readlink(target) != member.linkname:
                        raise VerificationError('Installed package symlink differs: ' + member.name)
                    count += 1
                else:
                    raise VerificationError('Unexpected package payload type: ' + member.name)
        stderr = process.communicate(timeout=30)[1]
        if process.returncode:
            raise VerificationError('dpkg-deb payload read failed: ' + stderr.decode()[-1000:])
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    return count


def verify(args, report):
    def check(name, condition, detail=None):
        report['checks'].append({'name': name, 'passed': bool(condition), 'detail': detail})
        if not condition:
            raise VerificationError(name + (': ' + str(detail) if detail else ''))

    stock = load_stock_inputs(getattr(args, 'stock_lock', None), getattr(args, 'stock_evidence', None))
    identity = expected_identity(stock['lock'] if stock else None)
    drivers_commit = identity['drivers_commit']
    kernel = identity['kernel_release']
    kernel_package = identity['kernel_package_version']
    launcher_version = identity['applaunch_version']
    check('root_privileges', os.geteuid() == 0)
    image = args.image.resolve(strict=True)
    check('regular_image_file', stat.S_ISREG(image.stat().st_mode))
    check('native_arm64_builder', os.uname().machine == 'aarch64')
    check('output_outside_image', not args.output_dir.resolve().is_relative_to(image))
    for tool in ('sfdisk', 'losetup', 'mount', 'umount', 'blkid', 'chroot',
                 'modinfo', 'dpkg', 'dpkg-deb', 'e2fsck', 'fsck.vfat'):
        check('tool_' + tool, shutil.which(tool) is not None)

    report['image'] = {'name': image.name, 'bytes': image.stat().st_size,
                       'sha256_before': sha256(image)}
    if stock:
        check('stock_evidence_final_image_size',
              stock['evidence']['stock_base']['uncompressed_bytes'] == report['image']['bytes'])
        check('stock_evidence_final_image', stock['evidence']['raw_image_sha256'] == report['image']['sha256_before'])
        report['stock_reference'] = {'stock_image_lock_sha256': stock['lock_sha256'],
                                     'stock_evidence_sha256': stock['evidence_sha256'],
                                     'stock_base': stock['evidence']['stock_base'],
                                     'identity': identity,
                                     'proof_scope': 'Final protected contents compared to the snapshot recorded from the digest-verified stock image before overlay; physical boot is untested.'}
    partition_table = json.loads(command(['sfdisk', '--json', image]))['partitiontable']
    partitions = partition_table.get('partitions', [])
    report['partition_table'] = partition_table
    check('dos_partition_table', partition_table.get('label') == 'dos')
    check('sector_size_512', partition_table.get('sectorsize') == 512)
    check('two_partitions', len(partitions) == 2)
    boot, root_partition = partitions
    check('fat_boot_partition_type', str(boot.get('type', '')).lower() in ('b', 'c', '0b', '0c'))
    check('linux_root_partition_type', str(root_partition.get('type', '')).lower() == '83')
    check('boot_partition_512mib', boot['size'] * 512 == 512 * 1024 * 1024)
    check('nonoverlapping_partitions', boot['start'] > 0
          and boot['start'] + boot['size'] <= root_partition['start'])
    check('partitions_fit_image', (root_partition['start'] + root_partition['size']) * 512 <= image.stat().st_size)

    package_sha = args.expected_package_sha
    if args.package:
        check('regular_deb_file', args.package.is_file())
        measured = sha256(args.package)
        check('deb_sha256', package_sha is None or measured == package_sha, measured)
        package_sha = measured
        check('deb_package_name', command(['dpkg-deb', '-f', args.package, 'Package']).strip() == 'tyrell-status')
        check('deb_package_version', command(['dpkg-deb', '-f', args.package, 'Version']).strip() == PACKAGE_VERSION)
        check('deb_package_architecture', command(['dpkg-deb', '-f', args.package, 'Architecture']).strip() == 'arm64')
    check('expected_package_sha_available', bool(package_sha and re.fullmatch('[0-9a-f]{64}', package_sha)))
    if stock:
        check('stock_evidence_package', stock['evidence']['package_sha256'] == package_sha)

    loop = None
    mountpoints = []
    temporary = Path(tempfile.mkdtemp(prefix='tyrell-image-verify-'))
    cleanup_errors = []
    try:
        loop = command(['losetup', '--find', '--show', '--partscan', '--read-only', image]).strip()
        check('loop_device_name', bool(re.fullmatch(r'/dev/loop[0-9]+', loop)))
        loop_json = json.loads(command(['losetup', '--json', '--output', 'NAME,BACK-FILE,RO', loop]))['loopdevices'][0]
        check('loop_is_read_only', loop_json.get('ro') in (True, 1, '1'))
        backing = loop_json.get('back-file', loop_json.get('back_file', ''))
        check('loop_backs_expected_image', Path(backing).resolve() == image)
        boot_device, root_device = Path(loop + 'p1'), Path(loop + 'p2')
        for device in (boot_device, root_device):
            check('partition_device_' + device.name, device.is_block_device())
        check('boot_fstype', command(['blkid', '-s', 'TYPE', '-o', 'value', boot_device]).strip() == 'vfat')
        check('root_fstype', command(['blkid', '-s', 'TYPE', '-o', 'value', root_device]).strip() == 'ext4')
        partuuids = {label: command(['blkid', '-s', 'PARTUUID', '-o', 'value', device]).strip()
                     for label, device in [('boot', boot_device), ('root', root_device)]}
        report['partition_uuids'] = partuuids
        root = temporary / 'root'
        root.mkdir()
        command(['mount', '-t', 'ext4', '-o', 'ro,noload', root_device, root])
        mountpoints.append(root)
        boot_mount = image_path(root, '/boot/firmware')
        check('boot_mountpoint_exists', boot_mount.is_dir())
        command(['mount', '-t', 'vfat', '-o', 'ro', boot_device, boot_mount])
        mountpoints.append(boot_mount)

        metadata_path = image_path(root, '/etc/tyrell-image.json')
        check('image_metadata_regular_file', metadata_path.is_file() and not metadata_path.is_symlink())
        metadata = json.loads(metadata_path.read_text())
        report['image_metadata'] = metadata
        expected_metadata = validate_expected_metadata(metadata, identity, package_sha, stock)
        for key, value in expected_metadata.items():
            check('metadata_' + key, metadata.get(key) == value)
        if stock:
            protected_count = compare_stock_protected(root, stock['evidence'])
            check('stock_protected_contents_unchanged', protected_count > 0, {'paths': protected_count})
            stable_hashes = {'/' + path: record['sha256']
                             for path, record in stock['evidence']['protected_files'].items()
                             if path.startswith('boot/firmware/') and record['type'] == 'file'}
        else:
            stable_hashes = metadata.get('boot_sha256', {})
        check('stable_baseline_hashes_present', isinstance(stable_hashes, dict) and bool(stable_hashes))
        baseline_paths = {path.lstrip('/') for path in stable_hashes}
        required_hashes = {'boot/firmware/start.elf', 'boot/firmware/fixup.dat', 'boot/firmware/config.txt',
                           'boot/firmware/overlays/cardputerzero-v5-overlay.dtbo',
                           'boot/firmware/overlays/bq27220_v5.dtbo',
                           'boot/firmware/overlays/bmi270_bmm150_overlay.dtbo'}
        check('critical_baseline_hashes_present', required_hashes.issubset(baseline_paths))
        for path, expected in sorted(stable_hashes.items()):
            check('baseline_sha256_' + path, bool(re.fullmatch('[0-9a-f]{64}', expected))
                  and sha256(image_path(root, path)) == expected)

        status_text = read(root, '/var/lib/dpkg/status')
        packages = parse_packages(status_text)
        if stock:
            unchanged_packages = compare_stock_packages(packages, stock['evidence'])
            check('stock_package_inventory_unchanged', unchanged_packages > 0,
                  {'original_packages': unchanged_packages, 'added': 'tyrell-status'})
        inventory = '\n'.join('\t'.join([name, p.get('Version', ''), p.get('Architecture', ''), p.get('Status', '')])
                              for name, p in sorted(packages.items())) + '\n'
        (args.output_dir / 'image-packages.tsv').write_text(inventory)
        for name, version in [('tyrell-status', PACKAGE_VERSION), ('applaunch', launcher_version),
                              ('linux-image-rpi-v8', kernel_package), ('linux-headers-rpi-v8', kernel_package)]:
            package = packages.get(name, {})
            check('installed_package_' + name, package.get('Version') == version
                  and installed(package), package.get('Version'))
        check('tyrell_arm64', packages['tyrell-status'].get('Architecture') == 'arm64')
        for name in ('fastfetch', 'cmatrix', 'm5cardputerzero-keyboard-guide'):
            check('vendor_package_' + name, installed(packages.get(name, {})))
        if args.package:
            count = compare_package_payload(args.package, root)
            check('exact_installed_package_payload', count > 0, {'files': count})
        check('dpkg_verify_tyrell', not command(['chroot', root, '/usr/bin/dpkg', '--verify', 'tyrell-status']).strip())
        desktop = configparser.ConfigParser(interpolation=None)
        desktop.read(image_path(root, '/usr/share/APPLaunch/applications/tyrell-status.desktop'))
        check('launcher_entry', desktop.get('Desktop Entry', 'Exec', fallback='') == '/usr/bin/tyrell-status'
              and desktop.get('Desktop Entry', 'Type', fallback='') == 'Application')
        icon = image_path(root, '/usr/share/APPLaunch/share/images/tyrell-status.png')
        check('launcher_icon', icon.is_file() and icon.stat().st_size > 0)
        check('launcher_binary', image_path(root, '/usr/share/APPLaunch/bin/M5CardputerZero-APPLaunch').is_file())
        check('launcher_user_unit', 'ExecStart=/usr/share/APPLaunch/bin/M5CardputerZero-APPLaunch'
              in read(root, '/usr/lib/systemd/user/APPLaunch.service'))

        config = read(root, '/boot/firmware/config.txt')
        cmdline = read(root, '/boot/firmware/cmdline.txt').split()
        fstab = read(root, '/etc/fstab')
        check('root_partuuid_cmdline', 'root=PARTUUID=' + partuuids['root'] in cmdline)
        check('fstab_partuuids', 'PARTUUID=' + partuuids['boot'] in fstab and 'PARTUUID=' + partuuids['root'] in fstab)
        check('no_partition_placeholders', not any(x in fstab + ' '.join(cmdline) for x in ('ROOTDEV', 'BOOTDEV')))
        check('no_uboot_override', not re.search(r'^\s*kernel\s*=\s*u-boot\.bin', config, re.M)
              and not image_path(root, '/boot/firmware/u-boot.bin').exists())
        check('quiet_no_legacy_splash', 'quiet' in cmdline and 'splash' not in cmdline
              and 'plymouth.ignore-serial-consoles' not in cmdline and 'console=serial0,115200' not in cmdline)
        for overlay in ('cardputerzero-v5-overlay', 'bq27220_v5', 'bmi270_bmm150_overlay'):
            check('overlay_' + overlay, 'dtoverlay=' + overlay in config
                  and image_path(root, '/boot/firmware/overlays/' + overlay + '.dtbo').is_file())
        for filename in ('start.elf', 'fixup.dat', 'kernel8.img'):
            path = image_path(root, '/boot/firmware/' + filename)
            check('boot_asset_' + filename, path.is_file() and path.stat().st_size > 0)
        check('driver_source_commit', read(root, '/etc/cardputerzero-dtoverlays.commit').strip() == drivers_commit)
        module_root = image_path(root, '/lib/modules/' + kernel + '/extra')
        report['module_abi'] = {}
        for name in ('bq27xxx_battery', 'bq27xxx_battery_i2c', 'm5ioe1', 'tca8418_keypad_m5stack',
                     'es8389_m5stack', 'pwm_bl_m5stack', 'st7789v_m5stack', 'cardputerzero-audio',
                     'gpio-forwarder', 'powerfail_suo', 'panel-mipi-dbi-m', 'simple-amplifier'):
            matches = sorted(module_root.glob(name + '.ko*'))
            check('module_' + name, bool(matches))
            vermagic = command(['modinfo', '-F', 'vermagic', matches[0]]).strip()
            report['module_abi'][name] = vermagic
            check('module_abi_' + name, vermagic.startswith(kernel + ' '))
        lcd_firmware = image_path(root, '/lib/firmware/cardputerzero,st7789v_lcd.bin')
        check('lcd_panel_firmware', lcd_firmware.is_file() and lcd_firmware.stat().st_size > 0)

        check('machine_id_firstboot', read(root, '/etc/machine-id').strip() in ('', 'uninitialized'))
        dbus_id = root / 'var/lib/dbus/machine-id'
        check('no_personal_dbus_id', not dbus_id.exists() or (dbus_id.is_symlink()
              and os.readlink(dbus_id) in ('/etc/machine-id', '../../../etc/machine-id')))
        check('launchwizard_binary', image_path(root, '/usr/share/APPLaunch/bin/LaunchWizard').is_file())
        check('launchwizard_unit', 'ExecStart=/usr/share/APPLaunch/bin/LaunchWizard'
              in read(root, '/usr/lib/systemd/system/LaunchWizard.service'))
        for marker in ('run-oobe', 'run-keyboard-guide'):
            check('firstboot_' + marker, image_path(root, '/var/lib/LaunchWizard/' + marker).is_file())
        wizard_link = image_path(root, '/etc/systemd/system/multi-user.target.wants') / 'LaunchWizard.service'
        check('launchwizard_enabled', wizard_link.is_symlink()
              and os.readlink(wizard_link).endswith('/LaunchWizard.service'))
        check('launcher_not_globally_enabled', not os.path.lexists(root / 'etc/systemd/user/default.target.wants/APPLaunch.service'))
        check('ssh_disabled_until_provisioned', not os.path.lexists(root / 'etc/systemd/system/multi-user.target.wants/ssh.service')
              and not os.path.lexists(root / 'etc/systemd/system/sockets.target.wants/ssh.socket'))
        for group, directory, pattern in [('ssh_host_keys', '/etc/ssh', 'ssh_host_*_key*'),
                                          ('network_profiles', '/etc/NetworkManager/system-connections', '*')]:
            directory_path = image_path(root, directory)
            found = [p.name for p in directory_path.glob(pattern) if p.is_file()]
            check('no_' + group, not found, found)
        identities = [root / 'root'] + list(image_path(root, '/home').iterdir())
        for home in identities:
            home = image_path(root, home.relative_to(root))
            for relative in ('.ssh/authorized_keys', '.bash_history', '.zsh_history'):
                check('no_personal_file_' + str(home.relative_to(root)) + '/' + relative,
                      not os.path.lexists(home / relative))
        shadow = {line.split(':', 1)[0]: line.split(':')[1] for line in read(root, '/etc/shadow').splitlines() if ':' in line}
        users = [line.split(':') for line in read(root, '/etc/passwd').splitlines() if ':' in line]
        for user in users:
            if user[0] == 'root' or (int(user[2]) >= 1000 and user[-1] not in ('/usr/sbin/nologin', '/bin/false')):
                password = shadow.get(user[0], '')
                # The vendor rename-user/OOBE path can retain an empty staging
                # account. It has no embedded password hash and SSH stays off.
                check('no_preconfigured_password_' + user[0],
                      unprovisioned_password(user[0], password, stock_mode=stock is not None))

        version = command(['chroot', root, '/usr/bin/tyrell-status', '--version']).strip()
        report['application_version'] = version
        check('chroot_version', version.startswith('tyrell-status 0.1.0 ('))
        telemetry = json.loads(command(['chroot', root, '/usr/bin/tyrell-status', '--json']))
        (args.output_dir / 'image-cli-status.json').write_text(json.dumps(telemetry, indent=2) + '\n')
        check('chroot_json', telemetry.get('version') == '0.1.0')
        check('chroot_has_no_host_proc', telemetry.get('uptime_seconds') is None)
        tmp = image_path(root, '/tmp')
        command(['mount', '-t', 'tmpfs', '-o', 'size=8M,nosuid,nodev', 'tmpfs', tmp])
        mountpoints.append(tmp)
        for page in ('system', 'network'):
            target = '/tmp/status-' + page + '.ppm'
            command(['chroot', root, '/usr/bin/tyrell-status', '--headless-screenshot', target,
                     '--page', page, '--frames', '2'])
            ppm = image_path(root, target)
            check('chroot_render_' + page, ppm.read_bytes().startswith(b'P6\n320 170\n255\n'))
            shutil.copyfile(ppm, args.output_dir / ('image-status-' + page + '.ppm'))

        for mountpoint in reversed(mountpoints[:]):
            command(['umount', mountpoint])
            mountpoints.remove(mountpoint)
        command(['fsck.vfat', '-n', boot_device])
        check('fat_fsck_readonly', True)
        command(['e2fsck', '-f', '-n', root_device])
        check('ext4_fsck_readonly', True)
    finally:
        for mountpoint in reversed(mountpoints):
            try:
                command(['umount', mountpoint])
            except Exception as error:
                cleanup_errors.append(str(error))
        if loop:
            try:
                command(['losetup', '--detach', loop])
            except Exception as error:
                cleanup_errors.append(str(error))
        # Never recursively remove a directory that might still contain mounts.
        if not cleanup_errors:
            shutil.rmtree(temporary)
        report['cleanup_errors'] = cleanup_errors
        if cleanup_errors:
            raise VerificationError('Image cleanup failed; retained mount directory ' + str(temporary))
    report['image']['sha256_after'] = sha256(image)
    check('image_bytes_unchanged', report['image']['sha256_before'] == report['image']['sha256_after'])
    check('no_remaining_image_loops', not command(['losetup', '--noheadings', '--output', 'NAME', '--associated', image]).strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--expected-package-sha')
    parser.add_argument('--package', type=Path)
    parser.add_argument('--stock-lock', type=Path, help='Pinned official stock-image contract')
    parser.add_argument('--stock-evidence', type=Path, help='Pre-overlay stock snapshot and derivation evidence')
    args = parser.parse_args()
    if bool(args.stock_lock) != bool(args.stock_evidence):
        parser.error('--stock-lock and --stock-evidence must be supplied together')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = {'status': 'failed', 'checks': [], 'proof_scope': 'Offline contents, filesystem, package and chroot checks only; physical boot and peripherals are untested.'}
    try:
        verify(args, report)
        report['status'] = 'passed'
    except Exception as error:
        report['error'] = str(error)
    finally:
        (args.output_dir / 'image-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'checks': len(report['checks']),
                      'report': str(args.output_dir / 'image-verification.json'),
                      'error': report.get('error')}, sort_keys=True))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
