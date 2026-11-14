#!/usr/bin/env python3
"""Add Tyrell to a verified copy of a stock image inside the ARM64 builder.

Only regular image files are accepted. The downloaded image remains untouched;
no physical device, host filesystem, or live package repository is used.
"""
import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import runpy
import shutil
import stat
import subprocess
import tempfile


CLEAN_ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
             'LC_ALL': 'C.UTF-8', 'TZ': 'UTC', 'DEBIAN_FRONTEND': 'noninteractive',
             'SYSTEMD_OFFLINE': '1'}
PROTECTED_ROOTS = ('boot/firmware', 'lib/modules', 'lib/firmware',
                   'usr/share/APPLaunch', 'etc/alternatives',
                   'etc/cardputerzero-dtoverlays.commit')
TYRELL_ADDITIONS = frozenset(('usr/share/APPLaunch/applications/tyrell-status.desktop',
                              'usr/share/APPLaunch/share/images/tyrell-status.png'))
PACKAGE_NAME = 'tyrell-status_0.1.0-1_arm64.deb'
RAW_NAME = 'tyrell-trixie-arm64.img'
GIB = 1024 ** 3


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def regular(path):
    path = Path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('Expected a regular file: ' + str(path))
    return path


def command(args, *, timeout=1800):
    result = subprocess.run([str(item) for item in args], text=True, env=CLEAN_ENV,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout)
    if result.returncode:
        raise RuntimeError(Path(str(args[0])).name + ' failed: ' +
                           (result.stderr or result.stdout).strip()[-3000:])
    return result.stdout


def image_path(root, relative):
    relative = str(relative).lstrip('/')
    if not relative or '..' in Path(relative).parts:
        raise ValueError('Unsafe image path: ' + relative)
    path = Path(root) / relative
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('Image symlink escapes mounted root: ' + relative)
    return path


def image_leaf(root, relative):
    """Resolve the parent safely while allowing an absolute symlink leaf."""
    relative = str(relative).lstrip('/')
    if not relative or '..' in Path(relative).parts:
        raise ValueError('Unsafe image path: ' + relative)
    return image_path(root, str(Path(relative).parent)) / Path(relative).name


def validate_partitions(table, image_bytes):
    """Validate the complete DOS layout before asking losetup to expose it."""
    data = table['partitiontable']
    parts = data.get('partitions', [])
    if data.get('label') != 'dos' or data.get('sectorsize') != 512 or len(parts) != 2:
        raise ValueError('Stock image requires two DOS partitions with 512-byte sectors')
    boot, root = parts
    if str(boot.get('type', '')).lower() not in {'b', 'c', '0b', '0c'}:
        raise ValueError('Stock boot partition must be FAT')
    if str(root.get('type', '')).lower() != '83':
        raise ValueError('Stock root partition must be Linux ext4')
    for part in parts:
        if (type(part.get('start')) is not int or type(part.get('size')) is not int
                or part['start'] <= 0 or part['size'] <= 0
                or (part['start'] + part['size']) * 512 > image_bytes):
            raise ValueError('Stock partition is invalid or exceeds the image file')
    if boot['start'] + boot['size'] > root['start']:
        raise ValueError('Stock image partitions overlap')
    if boot['size'] * 512 != 512 * 1024 * 1024:
        raise ValueError('Stock boot partition must be 512 MiB')
    return parts


def xz_uncompressed_size(path):
    """Read one verified XZ file's exact raw size without decompressing it."""
    path = regular(path)
    executable = shutil.which('xz')
    if executable is None:
        raise ValueError('Missing stock image tool: xz')
    # Resolve xz before supplying CLEAN_ENV, which intentionally excludes host
    # toolchain directories such as Homebrew's bin directory.
    output = command([executable, '--robot', '--list', '--', path], timeout=60)
    rows = [line.split('\t') for line in output.splitlines()]
    files = [row for row in rows if row[0] == 'file']
    totals = [row for row in rows if row[0] == 'totals']
    if len(files) != 1 or len(totals) != 1 or len(files[0]) != 8 or len(totals[0]) != 9:
        raise ValueError('Expected one complete XZ image size record')
    record, total = files[0], totals[0]
    if (any(not re.fullmatch(r'[1-9][0-9]*', record[index]) for index in (1, 2, 3, 4))
            or record[1:5] != total[1:5] or total[8] != '1'):
        raise ValueError('Inconsistent XZ image size metadata')
    if int(record[3]) != path.stat().st_size:
        raise ValueError('XZ compressed size differs from the input file')
    return int(record[4])


def require_output_space(directory, raw_bytes, compressed_bytes):
    """Reserve the copied image, worst-case compressed output, and working room."""
    if (type(raw_bytes) is not int or raw_bytes <= 0 or
            type(compressed_bytes) is not int or compressed_bytes <= 0):
        raise ValueError('Stock image sizes must be positive integers')
    required = raw_bytes + max(raw_bytes, compressed_bytes) + GIB
    available = shutil.disk_usage(directory).free
    if available < required:
        raise ValueError(f'Insufficient guest output space: need {required} bytes; '
                         f'only {available} available. Preserve or export retained jobs first.')
    return required


def decompress(source, destination, expected_bytes):
    regular(source)
    require_output_space(destination.parent, expected_bytes, source.stat().st_size)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Preserving existing image copy: ' + str(destination))
    created = False
    try:
        with destination.open('xb') as outgoing:
            created = True
            with lzma.open(source, 'rb') as incoming:
                written = 0
                for block in iter(lambda: incoming.read(1024 * 1024), b''):
                    if written + len(block) > expected_bytes:
                        raise ValueError('Stock image exceeds the exact locked raw size')
                    outgoing.write(block)
                    written += len(block)
                if written != expected_bytes:
                    raise ValueError('Stock image is shorter than the exact locked raw size')
        regular(destination)
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def node_identity(path):
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    attributes = {'mode': mode, 'uid': info.st_uid, 'gid': info.st_gid}
    if stat.S_ISLNK(info.st_mode):
        return {'type': 'symlink', 'target': os.readlink(path), **attributes}
    if stat.S_ISREG(info.st_mode):
        return {'type': 'file', 'sha256': sha(path), **attributes}
    if stat.S_ISDIR(info.st_mode):
        return {'type': 'directory', **attributes}
    raise ValueError('Unsupported protected image file: ' + str(path))


def snapshot(root):
    """Do not follow protected symlink directories or absolute module links."""
    result = {}
    for name in PROTECTED_ROOTS:
        base = image_leaf(root, name)
        mode = base.lstat().st_mode
        if name == 'etc/cardputerzero-dtoverlays.commit':
            if not stat.S_ISREG(mode):
                raise ValueError('Stock driver record must be a regular file')
        elif not stat.S_ISDIR(mode):
            raise ValueError('Protected stock subtree must be a real directory: ' + name)
        pending = [(name, base)]
        while pending:
            relative, path = pending.pop()
            entry = node_identity(path)
            result[relative] = entry
            if entry['type'] == 'directory':
                pending.extend((relative + '/' + child.name, child)
                               for child in sorted(path.iterdir(), reverse=True))
    return dict(sorted(result.items()))


def compare_protected(before, after):
    for path, expected in before.items():
        if after.get(path) != expected:
            raise ValueError('Protected stock content changed: ' + path)
    additions = set(after) - set(before)
    if additions != TYRELL_ADDITIONS:
        raise ValueError('Unexpected protected tree additions: ' + repr(sorted(additions)))
    if any(after[path]['type'] != 'file' for path in additions):
        raise ValueError('Tyrell launcher additions must be regular files')


def parse_status(text):
    result = {}
    for block in text.split('\n\n'):
        fields = dict(line.split(': ', 1) for line in block.splitlines()
                      if line and not line[0].isspace() and ': ' in line)
        if fields.get('Package'):
            result[fields['Package']] = fields
    return result


def package_inventory(root):
    return {name: {field: record.get(field, '') for field in ('Version', 'Architecture', 'Status')}
            for name, record in parse_status(image_path(root, 'var/lib/dpkg/status').read_text()).items()}


def compare_packages(before, after, version):
    if 'tyrell-status' in before:
        raise ValueError('Stock base already contains Tyrell Status')
    if set(after) != set(before) | {'tyrell-status'}:
        raise ValueError('Offline overlay changed the stock package set')
    for name, record in before.items():
        if after.get(name) != record:
            raise ValueError('Offline overlay changed a stock package: ' + name)
    if after['tyrell-status'] != {'Version': version, 'Architecture': 'arm64', 'Status': 'install ok installed'}:
        raise ValueError('Tyrell package is not fully installed at the expected version')


def stock_identity(root, lock):
    packages = parse_status(image_path(root, 'var/lib/dpkg/status').read_text())
    for name, expected in [('linux-image-rpi-v8', lock['kernel_package_version']),
                            ('linux-headers-rpi-v8', lock['kernel_package_version']),
                            ('applaunch', lock['applaunch_version'])]:
        package = packages.get(name, {})
        if (package.get('Version') != expected or
                package.get('Status') not in {'install ok installed', 'hold ok installed'}):
            raise ValueError('Stock package identity differs: ' + name)
    driver = image_path(root, 'etc/cardputerzero-dtoverlays.commit').read_text().strip()
    if driver != lock['drivers_commit']:
        raise ValueError('Stock driver source identity differs from the release lock')
    issue = image_path(root, 'etc/rpi-issue').read_text()
    if lock['pi_gen_commit'] not in issue:
        raise ValueError('Stock image pi-gen provenance differs from the release lock')
    release = image_path(root, 'etc/os-release').read_text()
    if not re.search(r'^VERSION_CODENAME=[\"\']?trixie[\"\']?$', release, re.M):
        raise ValueError('Only stock Trixie images are supported')
    if not image_path(root, 'lib/modules/' + lock['kernel_release']).is_dir():
        raise ValueError('Stock kernel module directory differs from release lock')
    config = image_path(root, 'boot/firmware/config.txt').read_text()
    if lock['board'] != 'v5':
        raise ValueError('Unsupported stock board profile')
    for overlay in ('cardputerzero-v5-overlay', 'bq27220_v5', 'bmi270_bmm150_overlay'):
        if 'dtoverlay=' + overlay not in config:
            raise ValueError('Stock board overlay is missing: ' + overlay)
    return {key: lock[key] for key in ('pi_gen_commit', 'drivers_commit', 'kernel_release',
                                      'kernel_package_version', 'applaunch_version')}


def unlink_if_present(root, name):
    path = image_leaf(root, name)
    if os.path.lexists(path):
        if path.is_dir() and not path.is_symlink():
            raise ValueError('Expected a file during provisioning cleanup: ' + name)
        path.unlink()


def sanitize(root):
    """Keep vendor OOBE while removing preconfigured credentials and identities."""
    # These Raspberry Pi provisioning seeds can recreate credentials or enable
    # SSH on boot. Preserve the authenticated boot partition and fail rather
    # than silently modifying a stock image that contains one.
    for name in ('userconf', 'userconf.txt', 'ssh', 'ssh.txt', 'firstrun.sh'):
        seed = image_leaf(root, 'boot/firmware/' + name)
        if os.path.lexists(seed):
            raise ValueError('Stock boot contains a provisioning seed requiring review: ' + name)
    for name in ('etc/shadow-', 'etc/gshadow-'):
        unlink_if_present(root, name)
    machine = image_path(root, 'etc/machine-id')
    regular(machine)
    machine.write_text('')
    dbus = image_path(root, 'var/lib/dbus')
    dbus.mkdir(parents=True, exist_ok=True)
    unlink_if_present(root, 'var/lib/dbus/machine-id')
    (dbus / 'machine-id').symlink_to('../../../etc/machine-id')
    for directory, pattern in [('etc/ssh', 'ssh_host_*_key*'),
                                ('etc/NetworkManager/system-connections', '*')]:
        base = image_path(root, directory)
        if base.exists():
            for item in base.glob(pattern):
                unlink_if_present(root, str(item.relative_to(root)))
    passwd = image_path(root, 'etc/passwd').read_text()
    accounts = {'root', 'pi'}
    for line in passwd.splitlines():
        fields = line.split(':')
        if len(fields) == 7 and fields[2].isdigit() and int(fields[2]) >= 1000:
            if fields[-1] not in {'/usr/sbin/nologin', '/bin/false'}:
                accounts.add(fields[0])
    shadow = image_path(root, 'etc/shadow')
    regular(shadow)
    rows = []
    for line in shadow.read_text().splitlines():
        fields = line.split(':')
        if fields[0] in accounts:
            fields[1] = '!'
        rows.append(':'.join(fields))
    shadow.write_text('\n'.join(rows) + '\n')
    homes = [image_path(root, 'root')]
    home = image_path(root, 'home')
    if home.exists():
        homes.extend(home.iterdir())
    for directory in homes:
        for name in ('.ssh/authorized_keys', '.bash_history', '.zsh_history'):
            unlink_if_present(root, str((directory / name).relative_to(root)))
    for name in ('etc/systemd/system/multi-user.target.wants/ssh.service',
                 'etc/systemd/system/sockets.target.wants/ssh.socket',
                 'etc/systemd/user/default.target.wants/APPLaunch.service'):
        unlink_if_present(root, name)
    for name in ('run-oobe', 'run-keyboard-guide'):
        marker = image_path(root, 'var/lib/LaunchWizard/' + name)
        marker.parent.mkdir(parents=True, exist_ok=True)
        if marker.is_symlink():
            raise ValueError('Unsafe LaunchWizard marker')
        marker.touch()
    wizard = image_path(root, 'usr/lib/systemd/system/LaunchWizard.service')
    regular(wizard)
    enable = image_leaf(root, 'etc/systemd/system/multi-user.target.wants/LaunchWizard.service')
    unlink_if_present(root, str(enable.relative_to(root)))
    enable.parent.mkdir(parents=True, exist_ok=True)
    enable.symlink_to('/usr/lib/systemd/system/LaunchWizard.service')
    return {'machine_id': 'firstboot', 'ssh': 'disabled', 'passwords': 'locked',
            'network_profiles': 'removed', 'launchwizard': 'enabled',
            'vendor_boot_configuration': 'preserved'}


def install_package(root, package):
    policy = image_path(root, 'usr/sbin/policy-rc.d')
    existed = os.path.lexists(policy)
    if existed:
        regular(policy)
    previous = policy.read_bytes() if existed else None
    mode = stat.S_IMODE(policy.stat().st_mode) if existed else None
    destination = image_path(root, 'var/tmp/tyrell-stock-overlay.deb')
    if os.path.lexists(destination):
        raise ValueError('Stock image already contains the Tyrell install staging file')
    try:
        policy.write_text('#!/bin/sh\nexit 101\n')
        policy.chmod(0o755)
        shutil.copyfile(package, destination)
        # An empty network namespace prevents any maintainer script from using
        # host connectivity. No live APT command or host mount is exposed.
        command(['unshare', '--net', '--', 'chroot', root, '/usr/bin/dpkg',
                 '--install', '/var/tmp/tyrell-stock-overlay.deb'])
        audit = command(['unshare', '--net', '--', 'chroot', root, '/usr/bin/dpkg', '--audit'])
        if audit.strip():
            raise ValueError('Offline package audit reported incomplete packages: ' + audit.strip()[-2000:])
    finally:
        destination.unlink(missing_ok=True)
        if previous is None:
            policy.unlink(missing_ok=True)
        else:
            policy.write_bytes(previous)
            policy.chmod(mode)


def overlay(input_dir, output_dir):
    input_dir, output_dir = Path(input_dir).resolve(), Path(output_dir)
    if os.geteuid() != 0 or os.uname().machine != 'aarch64':
        raise ValueError('Stock image overlay requires the root native ARM64 Linux builder')
    for tool in ('sfdisk', 'losetup', 'mount', 'umount', 'blkid', 'chroot', 'dpkg-deb', 'unshare'):
        if not shutil.which(tool):
            raise ValueError('Missing stock overlay tool: ' + tool)
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise ValueError('Output must be a new regular directory created by the job runner')
    output_dir = output_dir.resolve()
    if output_dir == input_dir or output_dir.is_relative_to(input_dir):
        raise ValueError('Stock output must be outside immutable inputs')
    loader = runpy.run_path(str(input_dir / 'image/stock-image.py'))
    lock_path = regular(input_dir / 'image/stock-image.json')
    lock = loader['load_lock'](lock_path)
    spec = lock['image']
    source = regular(input_dir / 'assets' / spec['name'])
    if source.stat().st_size != spec['bytes'] or sha(source) != spec['sha256']:
        raise ValueError('Stock image bytes differ from the pinned release')
    package = regular(input_dir / 'assets' / PACKAGE_NAME)
    metadata = json.loads(regular(input_dir / 'image-metadata.json').read_text())
    package_manifest = json.loads(regular(input_dir / 'package-manifest.json').read_text())
    package_sha = sha(package)
    if (metadata.get('package_sha256') != package_sha or
            package_manifest.get('sha256', {}).get(PACKAGE_NAME) != package_sha):
        raise ValueError('Tyrell package differs from its build provenance')
    if (metadata.get('stock_image_lock_sha256') != sha(lock_path)
            or metadata.get('stock_base_sha256') != spec['sha256']):
        raise ValueError('Stock input metadata differs from its release lock')
    if metadata.get('build_mode') != 'stock-image' or metadata.get('apt_profile') != 'stock-image':
        raise ValueError('Stock overlay requires explicit stock-image metadata')
    if command(['dpkg-deb', '-f', package, 'Package']).strip() != 'tyrell-status':
        raise ValueError('Unexpected overlay package')
    if command(['dpkg-deb', '-f', package, 'Version']).strip() != metadata['package_version']:
        raise ValueError('Unexpected overlay package version')
    if command(['dpkg-deb', '-f', package, 'Architecture']).strip() != 'arm64':
        raise ValueError('Stock overlay package must be ARM64')
    raw_bytes = xz_uncompressed_size(source)
    if type(metadata.get('stock_uncompressed_bytes')) is not int or metadata['stock_uncompressed_bytes'] != raw_bytes:
        raise ValueError('Stock raw size differs from captured input metadata')
    image = output_dir / RAW_NAME
    decompress(source, image, raw_bytes)
    original_sha = sha(image)
    partitions = validate_partitions(json.loads(command(['sfdisk', '--json', image])), image.stat().st_size)
    loop = None
    mounts = []
    mountdir = Path(tempfile.mkdtemp(prefix='tyrell-stock-mount-', dir=output_dir.parent))
    errors = []
    evidence = None
    try:
        loop = command(['losetup', '--find', '--show', '--partscan', image]).strip()
        if not re.fullmatch(r'/dev/loop[0-9]+', loop):
            raise ValueError('Unexpected loop device returned for copied image')
        actual = json.loads(command(['losetup', '--json', '--output', 'NAME,BACK-FILE', loop]))['loopdevices'][0]
        backing = actual.get('back-file', actual.get('back_file', ''))
        if Path(backing).resolve() != image.resolve():
            raise ValueError('Loop device does not refer to the copied image')
        for number, expected in [(1, 'vfat'), (2, 'ext4')]:
            device = Path(loop + 'p' + str(number))
            if not device.is_block_device() or command(['blkid', '-s', 'TYPE', '-o', 'value', device]).strip() != expected:
                raise ValueError('Copied stock partition filesystem differs')
        command(['mount', '-t', 'ext4', '-o', 'rw', loop + 'p2', mountdir])
        mounts.append(mountdir)
        boot = image_path(mountdir, 'boot/firmware')
        if not boot.is_dir():
            raise ValueError('Stock boot mountpoint is missing')
        command(['mount', '-t', 'vfat', '-o', 'ro', loop + 'p1', boot])
        mounts.append(boot)
        identity = stock_identity(mountdir, lock)
        packages_before = package_inventory(mountdir)
        if 'tyrell-status' in packages_before:
            raise ValueError('Stock base already contains Tyrell Status')
        before = snapshot(mountdir)
        if any(name in before for name in TYRELL_ADDITIONS):
            raise ValueError('Stock image already has Tyrell launcher assets; refusing overwrite')
        installed_kib = int(command(['dpkg-deb', '-f', package, 'Installed-Size']).strip())
        required = installed_kib * 1024 + package.stat().st_size + 32 * 1024**2
        if shutil.disk_usage(mountdir).free < required:
            raise ValueError('Stock root filesystem has insufficient free space for Tyrell; image growth is not automatic')
        install_package(mountdir, package)
        compare_packages(packages_before, package_inventory(mountdir), metadata['package_version'])
        provisioning = sanitize(mountdir)
        metadata['boot_sha256'] = {'/' + name: entry['sha256'] for name, entry in before.items()
                                   if name.startswith('boot/firmware/') and entry['type'] == 'file'}
        metadata['boot_hash_scope'] = 'Every original stock boot file; boot partition mounted read-only during overlay.'
        metadata['stock_base_image'] = {'name': source.name, 'bytes': spec['bytes'], 'sha256': spec['sha256']}
        image_path(mountdir, 'etc/tyrell-image.json').write_text(json.dumps(metadata, indent=2) + '\n')
        compare_protected(before, snapshot(mountdir))
        evidence = {'schema_version': 1, 'stock_lock_sha256': sha(lock_path),
                    'stock_base': {'filename': source.name, 'bytes': spec['bytes'],
                                   'sha256': spec['sha256'], 'uncompressed_sha256': original_sha,
                                   'uncompressed_bytes': raw_bytes},
                    'identity': identity, 'partition_table': partitions,
                    'protected_files': before, 'protected_unchanged': True,
                    'packages_before': packages_before,
                    'package_sha256': package_sha, 'provisioning': provisioning,
                    'proof_scope': 'Authenticated stock image copy before overlay; offline content preservation only; physical boot untested.'}
    finally:
        for path in reversed(mounts):
            try:
                command(['umount', path])
            except Exception as error:
                errors.append(str(error))
        if loop and not errors:
            try:
                command(['losetup', '--detach', loop])
            except Exception as error:
                errors.append(str(error))
        # Never recursively remove a tree when an unmount or detach failed.
        if not errors:
            shutil.rmtree(mountdir)
        if errors:
            raise RuntimeError('Stock overlay cleanup failed; retained mounts at ' + str(mountdir) + ': ' + '; '.join(errors))
    if sha(source) != spec['sha256']:
        raise ValueError('Stock base changed during overlay')
    if command(['losetup', '--noheadings', '--output', 'NAME', '--associated', image]).strip():
        raise ValueError('Copied image still has a loop device after overlay')
    evidence['raw_image_sha256'] = sha(image)
    (output_dir / 'stock-base-verification.json').write_text(json.dumps(evidence, indent=2) + '\n')
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input_dir', type=Path)
    parser.add_argument('output_dir', type=Path)
    args = parser.parse_args()
    image = overlay(args.input_dir, args.output_dir)
    print('Prepared stock-derived image copy: ' + str(image), flush=True)


if __name__ == '__main__':
    main()
