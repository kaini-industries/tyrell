#!/usr/bin/env python3
"""Independently inspect a frozen Tyrell Zero filesystem or read-only raw image.

No image program is executed. Root mode checks an already detached filesystem;
image mode uses private read-only loop mounts inside the Linux ARM64 builder.
A passed report never certifies a physical boot, peripherals or battery benefit.
"""
import argparse
import collections
import configparser
import gzip
import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import shutil
import stat
import struct
import subprocess
import sys
import tempfile

PROFILE = 'tyrell-zero'
STAGES = ['stage0', 'stage1', 'stage2']
KERNEL = '6.18.39+rpt-rpi-v8'
MAIN = '/usr/share/APPLaunch/bin/M5CardputerZero-APPLaunch'
STORE = '/usr/share/APPLaunch/bin/M5CardputerZero-AppStore'
ORIGINAL = STORE + '.vendor'
MAIN_SHA = '9f26df9329160385c68794d26e0c7153271c6a69bb048938580e1495418c968c'
STORE_SHA = 'e5fe24b1dd26cd224c64ef4df2e7a61e0fce3fb63b3b04ac90d38c5092ccc05e'
INTEGRATION = '/usr/share/tyrell-zero/session-integration.json'
QUALIFICATION = '/usr/share/tyrell-zero/pre-image-qualification.json'
ACTIVATION = '/usr/share/tyrell-zero/image-activation.json'
BASELINE = '/usr/share/tyrell-zero/base-invariants.json'
MASKS = tuple('/etc/systemd/system/' + name for name in (
    'greetd.service', 'getty@tty1.service', 'getty@tty7.service',
    'LaunchWizard.service', 'applaunch-updater.service', 'userconfig.service', 'lightdm.service',
    'display-manager.service')) + ('/etc/systemd/user/APPLaunch.service',)
DIGEST = re.compile(r'[0-9a-f]{64}\Z')
PACKAGE = re.compile(r'[a-z0-9][a-z0-9+.-]*(?::(?:arm64|all))?\Z')
ASSETS = {'launcher': MAIN, 'wizard': '/usr/libexec/tyrell-zero/LaunchWizard',
          'store_dispatcher': STORE, 'store_original': ORIGINAL,
          'store_backend': '/usr/libexec/tyrell-zero/appstore-package-helper'}
RUNTIME = {'/usr/lib/tyrell-zero/session/state.py', '/usr/lib/tyrell-zero/session/supervisor.py',
           '/usr/lib/tyrell-zero/session/wizard_complete.py', '/usr/libexec/tyrell-zero-session-state',
           '/usr/libexec/tyrell-zero-session', '/usr/libexec/tyrell-zero-session-recovery',
           '/usr/libexec/tyrell-zero-wizard-complete', '/usr/lib/tyrell-zero/store/guard.py',
           '/usr/share/tyrell-zero/store-policy.json', '/usr/share/tyrell-zero/store-catalog.json'}
NATIVE_FILES = {*ASSETS.values(), *RUNTIME, INTEGRATION, QUALIFICATION, ACTIVATION, BASELINE,
                '/usr/share/tyrell-zero/source-pre-image-qualification.json'}
MODULES = ['bq27xxx_battery', 'bq27xxx_battery_i2c', 'm5ioe1', 'tca8418_keypad_m5stack',
           'es8389_m5stack', 'pwm_bl_m5stack', 'st7789v_m5stack', 'cardputerzero-audio',
           'gpio-forwarder', 'powerfail_suo', 'panel-mipi-dbi-m', 'simple-amplifier']
TRIGGERS = {'man-db', 'desktop-file-utils', 'hicolor-icon-theme', 'shared-mime-info', 'mime-support'}
SCRIPTS = {'preinst', 'postinst', 'prerm', 'postrm', 'config', 'triggers'}


class VerificationError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key: ' + key)
            result[key] = value
        return result
    def invalid(value):
        raise VerificationError('nonfinite JSON value: ' + value)
    value = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
    require(isinstance(value, dict), 'JSON object required')
    return value


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def load_json(path):
    path = Path(path)
    require(not path.is_symlink() and path.is_file(), 'regular JSON input required: ' + str(path))
    require(path.stat().st_size <= 128 * 1024 * 1024, 'oversized JSON input')
    return strict_json(path.read_bytes())


def absolute(value):
    require(isinstance(value, str) and value.startswith('/') and '\x00' not in value,
            'absolute image path required')
    require(str(PurePosixPath(value)) == value and '..' not in PurePosixPath(value).parts,
            'noncanonical image path: ' + value)
    return value


class ImageRoot:
    """Resolve Unix links inside the image, including absolute links and usrmerge."""
    def __init__(self, root, owner=0):
        supplied = Path(root).absolute()
        self.root = supplied.resolve(strict=True)
        require(supplied == self.root and self.root != Path('/') and self.root.is_dir(),
                'canonical offline root required')
        self.owner = owner
        self.fat_boot = False
        mountinfo = Path('/proc/self/mountinfo')
        if mountinfo.is_file():
            for row in mountinfo.read_text().splitlines():
                before, separator, after = row.partition(' - ')
                fields = before.split()
                if separator and len(fields) >= 5:
                    mounted = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4])
                    mounted = Path(mounted)
                    if mounted != self.root and mounted.is_relative_to(self.root):
                        boot = (mounted == self.root / 'boot/firmware'
                                and after.split()[:1] in (['vfat'], ['msdos']))
                        require(boot, 'detach nested mount: ' + str(mounted.relative_to(self.root)))
                        self.fat_boot = True
        for name in ('proc', 'sys', 'dev'):
            require(not (self.root / name).is_mount(), 'detach runtime mount: ' + name)

    def path(self, value, follow=True, trace=None, *, trusted=False, parent_aliases=True):
        remaining = collections.deque(PurePosixPath(absolute(value)).parts[1:])
        parts = []
        links = 0
        def directory(path):
            info = path.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == self.owner and not info.st_mode & 0o022,
                    'untrusted parent: ' + value)
        if trusted:
            directory(self.root)
        while remaining:
            part = remaining.popleft()
            if part in ('', '.'):
                continue
            if part == '..':
                require(bool(parts), 'link escapes image')
                parts.pop()
                continue
            candidate = self.root.joinpath(*parts, part)
            details = candidate.lstat()
            if stat.S_ISLNK(details.st_mode) and (follow or remaining):
                if trusted:
                    require(details.st_uid == self.owner, 'untrusted parent link: ' + value)
                    require(parent_aliases, 'native parent alias: ' + value)
                if trace is not None:
                    trace.append('/' + '/'.join([*parts, part]))
                links += 1
                require(links <= 40, 'image symlink loop')
                target = os.readlink(candidate)
                require('\x00' not in target, 'invalid image link')
                if target.startswith('/'):
                    parts = []
                remaining.extendleft(reversed(PurePosixPath(target).parts[1:] if target.startswith('/')
                                               else PurePosixPath(target).parts))
            else:
                if trusted and remaining:
                    directory(candidate)
                parts.append(part)
        return self.root.joinpath(*parts)

    def exists(self, value):
        try:
            self.path(value, follow=False).lstat()
            return True
        except FileNotFoundError:
            return False

    def secure(self, value, *, executable=False, limit=128 * 1024 * 1024, allow_setid=False,
               parent_aliases=None):
        if parent_aliases is None:
            parent_aliases = value not in NATIVE_FILES
        path = self.path(value, follow=False, trusted=True, parent_aliases=parent_aliases)
        info = path.lstat()
        forbidden = 0o022 if allow_setid else 0o6022
        require(stat.S_ISREG(info.st_mode) and info.st_uid == self.owner
                and not info.st_mode & forbidden, 'untrusted image file: ' + value)
        require(not executable or info.st_mode & 0o111, 'nonexecutable asset: ' + value)
        require(info.st_size <= limit, 'oversized image file: ' + value)
        for parent in (path.parent, *path.parent.parents):
            if parent == self.root.parent:
                break
            p = parent.lstat()
            require(stat.S_ISDIR(p.st_mode) and p.st_uid == self.owner and not p.st_mode & 0o022,
                    'untrusted parent: ' + value)
        return path

    def read(self, value, limit=128 * 1024 * 1024):
        return self.secure(value, limit=limit).read_bytes()

    def text(self, value):
        return self.read(value).decode('utf-8')

    def json(self, value):
        return strict_json(self.read(value))

    def identity(self, value):
        p = self.path(value, follow=False)
        info = p.lstat()
        result = {'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid}
        if stat.S_ISLNK(info.st_mode):
            result.update(type='symlink', target=os.readlink(p))
        elif stat.S_ISREG(info.st_mode):
            result.update(type='file', sha256=digest(p))
        elif stat.S_ISDIR(info.st_mode):
            result.update(type='directory')
        else:
            raise VerificationError('unsupported protected file: ' + value)
        return result

    def tree(self, value):
        result = {}
        def walk(name):
            identity = self.identity(name)
            result[name] = identity
            if identity['type'] == 'directory':
                for child in sorted(self.path(name).iterdir()):
                    walk(name + '/' + child.name)
        walk(value)
        return result


def same_identity(image, path, actual, expected):
    """FAT has no persistent POSIX ownership/mode; retain every content check."""
    if image.fat_boot and (path == '/boot/firmware' or path.startswith('/boot/firmware/')):
        excluded = {'mode', 'uid', 'gid'}
        return ({key: value for key, value in actual.items() if key not in excluded}
                == {key: value for key, value in expected.items() if key not in excluded})
    return actual == expected


def packages(text):
    result = {}
    for paragraph in re.split(r'\n\s*\n', text.strip()):
        if not paragraph:
            continue
        fields = {}
        key = None
        for line in paragraph.splitlines():
            if line[:1].isspace():
                require(key is not None, 'orphan dpkg continuation')
                fields[key] += '\n' + line
            else:
                require(':' in line, 'malformed dpkg status')
                key, value = line.split(':', 1)
                require(key not in fields, 'duplicate dpkg field')
                fields[key] = value.strip()
        name = fields.get('Package', '')
        require(PACKAGE.fullmatch(name) and name not in result, 'invalid/duplicate dpkg package')
        require(fields.get('Status') in {'install ok installed', 'hold ok installed'},
                'unclean dpkg state: ' + name)
        require(fields.get('Architecture') in {'arm64', 'all'} and fields.get('Version'),
                'invalid package identity: ' + name)
        require(not fields.get('Triggers-Pending') and not fields.get('Triggers-Awaited'),
                'pending dpkg triggers: ' + name)
        result[name] = {'Version': fields['Version'], 'Architecture': fields['Architecture']}
    require(bool(result), 'empty dpkg inventory')
    return result


def expected_packages(apt, apps):
    require(isinstance(apt.get('installed'), list) and bool(apt['installed']),
            'APT lock lacks exact final installed set')
    result = {}
    for item in apt['installed']:
        require(isinstance(item, list) and len(item) == 3, 'invalid final APT identity')
        name, version, arch = item
        require(PACKAGE.fullmatch(name) and arch in {'arm64', 'all'} and isinstance(version, str)
                and version and name not in result, 'invalid/duplicate final APT identity')
        result[name] = {'Version': version, 'Architecture': arch}
    require(isinstance(apps.get('packages'), list) and bool(apps['packages']), 'empty apps lock')
    for item in apps['packages']:
        name, version, arch = (item.get(k) for k in ('Package', 'Version', 'Architecture'))
        require(isinstance(name, str) and PACKAGE.fullmatch(name) and isinstance(version, str)
                and version and arch in {'arm64', 'all'} and DIGEST.fullmatch(item.get('sha256', '')),
                'invalid locked application')
        identity = {'Version': version, 'Architecture': arch}
        require(name not in result or result[name] == identity, 'conflicting app/APT identity: ' + name)
        result[name] = identity
    return result


def elf_segments(data):
    require(data[:6] == b'\x7fELF\x02\x01' and len(data) >= 64
            and struct.unpack_from('<H', data, 18)[0] == 183, 'ARM64 ELF required')
    phoff, = struct.unpack_from('<Q', data, 32)
    phsize, count = struct.unpack_from('<HH', data, 54)
    require(count <= 4096 and (not count or phsize >= 56)
            and phoff + phsize * count <= len(data), 'invalid ELF program table')
    segments = []
    for index in range(count):
        offset = phoff + index * phsize
        kind, = struct.unpack_from('<I', data, offset)
        start, address = struct.unpack_from('<QQ', data, offset + 8)
        size, = struct.unpack_from('<Q', data, offset + 32)
        require(start + size <= len(data), 'ELF segment outside file')
        segments.append((kind, start, address, size))
    return segments


def elf_interpreter(data):
    entries = [(start, size) for kind, start, _, size in elf_segments(data) if kind == 3]
    require(len(entries) <= 1, 'duplicate ELF interpreter')
    if not entries:
        return None
    start, size = entries[0]
    require(1 < size <= 4096, 'invalid ELF interpreter size')
    value = data[start:start + size]
    require(value.endswith(b'\x00') and b'\x00' not in value[:-1], 'invalid ELF interpreter string')
    return absolute(value[:-1].decode('ascii'))


def elf_needed(data):
    loads, dynamic = [], None
    for kind, start, address, size in elf_segments(data):
        if kind == 1:
            loads.append((address, size, start))
        elif kind == 2:
            require(dynamic is None, 'duplicate ELF dynamic segment')
            dynamic = (start, size)
    if dynamic is None:
        return []
    tags = {}
    terminated = False
    for offset in range(dynamic[0], dynamic[0] + dynamic[1] - 15, 16):
        key, value = struct.unpack_from('<qQ', data, offset)
        if key == 0:
            terminated = True
            break
        tags.setdefault(key, []).append(value)
    require(terminated, 'unterminated ELF dynamic table')
    if not tags.get(1):
        return []
    require(len(tags.get(5, [])) == len(tags.get(10, [])) == 1, 'ELF dynamic strings absent')
    address, size = tags[5][0], tags[10][0]
    matches = [start + address - base for base, length, start in loads
               if base <= address and address + size <= base + length]
    require(len(matches) == 1, 'ELF dynamic string mapping invalid')
    strings = data[matches[0]:matches[0] + size]
    names = []
    for offset in tags[1]:
        end = strings.find(b'\x00', offset)
        require(0 <= offset < len(strings) and end >= 0, 'ELF dependency string invalid')
        name = strings[offset:end].decode('ascii')
        require(re.fullmatch(r'[A-Za-z0-9_.+~-]{1,255}', name), 'ELF dependency has path')
        names.append(name)
    return names


def verify_elf_closure(image, compat, check):
    """Follow required programs/plugins; explicitly audit ineligible backends too."""
    libraries = compat['library_files']
    roots = compat.get('elf_roots')
    check('elf_roots_present', isinstance(roots, dict) and bool(roots))
    fixed = {MAIN, ORIGINAL, STORE, '/usr/libexec/tyrell-zero/LaunchWizard',
             '/usr/libexec/tyrell-zero-session-recovery', '/usr/bin/tyrell-status'}
    check('required_native_elf_roots', fixed.issubset(roots))
    services = {'greetd', 'systemd-logind', 'systemd-udevd', 'dbus-daemon', 'NetworkManager',
                'wpa_supplicant', 'sshd', 'bluetoothd', 'polkitd', 'pkexec', 'udisksd',
                'pipewire', 'wireplumber', 'rtkit-daemon'}
    check('required_service_elf_roots', services.issubset({PurePosixPath(path).name for path in roots}))
    # Preserve the exact installed library map, while resolving duplicate aliases
    # to one canonical ELF and refusing conflicting SONAME providers.
    index = {}
    canonical_libraries = set()
    for path in libraries:
        index.setdefault(PurePosixPath(path).name, []).append(path)
        canonical_libraries.add('/' + str(image.path(path).relative_to(image.root)))
    required = collections.deque()
    root_metadata = compat.get('elf_root_metadata')
    check('elf_root_metadata_present', isinstance(root_metadata, dict) and bool(root_metadata))
    for path, checksum in roots.items():
        resolved = image.path(path, trusted=True, parent_aliases=path not in NATIVE_FILES)
        canonical = '/' + str(resolved.relative_to(image.root))
        check('elf_root_metadata_' + path, image.identity(canonical) == root_metadata.get(canonical)
              and root_metadata[canonical].get('sha256') == checksum)
        check('elf_root_hash_' + path, isinstance(checksum, str) and DIGEST.fullmatch(checksum)
              and digest(image.secure(canonical, allow_setid=True)) == checksum)
        required.append(canonical)
    check('exact_elf_root_metadata_set', set(root_metadata) == set(required))
    visited = set()
    parsed = {}
    provider_hashes = {}
    def provider_hash(path):
        if path not in provider_hashes:
            provider_hashes[path] = digest(image.path(path))
        return provider_hashes[path]
    def needed(path):
        canonical = '/' + str(image.path(path).relative_to(image.root))
        if canonical not in parsed:
            data = image.path(canonical).read_bytes()
            parsed[canonical] = (elf_needed(data), elf_interpreter(data))
        return canonical, *parsed[canonical]
    while required:
        path = required.popleft()
        canonical, names, interpreter = needed(path)
        if canonical in visited:
            continue
        visited.add(canonical)
        if interpreter is not None:
            resolved = '/' + str(image.path(interpreter).relative_to(image.root))
            check('required_elf_interpreter_' + canonical, resolved in canonical_libraries)
            required.append(resolved)
        check('required_elf_dependencies_' + canonical, all(name in index for name in names),
              sorted(name for name in names if name not in index))
        for name in names:
            choices = sorted(set('/' + str(image.path(p).relative_to(image.root)) for p in index[name]))
            check('unambiguous_soname_' + name, len({provider_hash(p) for p in choices}) == 1)
            required.append(choices[0])
    optional = compat.get('optional_elf_backends', [])
    check('optional_backend_records', isinstance(optional, list))
    exclusions = {}
    for item in optional:
        path = item.get('path')
        check('optional_backend_identity', isinstance(path, str) and path in libraries
              and item.get('runtime_eligible') is False and isinstance(item.get('reason'), str)
              and bool(item['reason'].strip()) and item.get('sha256') == digest(image.path(path)))
        canonical = '/' + str(image.path(path).relative_to(image.root))
        check('optional_backend_not_required_' + path, canonical not in visited and canonical not in exclusions)
        exclusions[canonical] = item
    audited = set()
    for path in libraries:
        resolved = image.path(path)
        canonical = '/' + str(resolved.relative_to(image.root))
        if canonical in audited:
            continue
        audited.add(canonical)
        data = resolved.read_bytes()
        if not data.startswith(b'\x7fELF'):
            continue
        _, names, _ = needed(path)
        missing = sorted(set(name for name in names if name not in index))
        if missing:
            record = exclusions.get(canonical, {})
            check('noneligible_backend_explained_' + canonical, canonical not in visited
                  and record.get('missing_sonames') == missing, missing)
    return {'required_roots': len(roots), 'reachable_elfs': len(visited),
            'library_identities': len(libraries), 'explicit_noneligible_backends': optional}


def verify_root(root, metadata, locks, owner=0):
    """Return a fail-closed report. Fixtures may supply owner; CLI always uses root."""
    report = {'schema_version': 1, 'profile_id': PROFILE, 'status': 'failed', 'checks': [],
              'scope': 'offline-assembled-root', 'hardware_qualified': False,
              'battery_qualified': False, 'physical_boot_qualified': False}
    def check(name, condition=True, detail=None):
        report['checks'].append({'name': name, 'passed': bool(condition), 'detail': detail})
        require(condition, name + (': ' + str(detail) if detail else ''))
    try:
        image = ImageRoot(root, owner)
        expected = metadata if isinstance(metadata, dict) else load_json(metadata)
        paths = ({key: Path(value) for key, value in locks.items()} if isinstance(locks, dict)
                 else {name: Path(locks) / name for name in expected.get('locks_sha256', {})})
        input_root = Path(locks).parent if not isinstance(locks, dict) else paths['tyrell-zero-apt.json'].parent.parent
        profile_path = input_root / 'image/profiles/tyrell-zero.json'
        profile = load_json(profile_path)
        check('profile_identity', profile.get('id') == PROFILE and profile.get('architecture') == 'arm64'
              and profile.get('release') == 'trixie' and profile.get('stages') == STAGES
              and profile.get('integration_stage') == 'stage2/99-tyrell')
        check('stage2_lite_output_identity', profile.get('output', {}).get('raw')
              == 'tyrell-zero-trixie-arm64-lite.img' and profile['output'].get('compressed')
              == 'tyrell-zero-trixie-arm64-lite.img.xz')
        check('profile_hash', expected.get('profile_sha256') == digest(profile_path))
        for key, value in {'profile_id': PROFILE, 'profile': PROFILE, 'build_mode': 'zero-native', 'apt_profile': 'zero-frozen',
                           'stages': STAGES, 'selected_power_preset': 'baseline'}.items():
            check('metadata_' + key, expected.get(key) == value)
        for key, value in {'pi_gen_commit': profile['pins']['pi_gen_commit'],
                           'drivers_commit': profile['pins']['bsp_commit'],
                           'kernel_release': KERNEL, 'applaunch_version': '0.6.105',
                           'output': profile['output'], 'image_name': profile['output']['basename'],
                           'hardware_qualified': False, 'battery_qualified': False}.items():
            check('metadata_' + key, expected.get(key) == value)
        check('image_metadata_exact', image.json('/etc/tyrell-image.json') == expected)
        hashes = expected.get('locks_sha256')
        required_locks = {'tyrell-zero-apt.json', 'tyrell-zero-apps.json', 'tyrell-zero-native-compat.json'}
        check('lock_set_present', isinstance(hashes, dict) and required_locks.issubset(hashes))
        documents = {}
        for name, checksum in hashes.items():
            check('lock_filename_' + name, isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_.-]+\.json', name)
                  and isinstance(checksum, str) and DIGEST.fullmatch(checksum))
            check('lock_hash_' + name, name in paths and digest(paths[name]) == checksum)
            documents[name] = load_json(paths[name])
        apt, apps, compat = (documents[name] for name in ('tyrell-zero-apt.json', 'tyrell-zero-apps.json',
                                                       'tyrell-zero-native-compat.json'))
        for name, doc in [('apt', apt), ('apps', apps), ('native_compat', compat)]:
            check(name + '_lock_complete', type(doc.get('schema')) is int and doc['schema'] == 1
                  and doc.get('profile') == PROFILE and doc.get('complete') is True)
        check('apt_lock_identity', apt.get('kind') == 'tyrell-zero-apt' and apt.get('architecture') == 'arm64'
              and apt.get('seed_apt_lock_sha256') == profile['pins']['seed_apt_lock_sha256'])
        check('apt_stages', set(apt.get('stages', {})) == set(STAGES))
        check('custom_package_selection', {item.get('Package') for item in apps.get('packages', [])}
              == set(profile['custom_packages']) and len(apps['packages']) == len(profile['custom_packages']))
        for name in ('hardware', 'power'):
            entry = profile['contracts'][name]
            require(isinstance(entry.get('path'), str) and not Path(entry['path']).is_absolute()
                    and '..' not in Path(entry['path']).parts, 'invalid contract input path')
            path = input_root / entry['path']
            check(name + '_contract_hash', digest(path) == entry['sha256']
                  == expected.get(name + '_contract_sha256'))
        actual = packages(image.text('/var/lib/dpkg/status'))
        selected = expected_packages(apt, apps)
        check('native_compat_installed_set', compat.get('installed_packages') == selected)
        check('exact_installed_closure', actual == selected,
              {'installed': len(actual), 'expected': len(selected),
               'extra': sorted(set(actual) - set(selected)), 'missing': sorted(set(selected) - set(actual))})
        report['packages'] = actual
        for group in profile['runtime_roots'].values():
            for name in group:
                check('runtime_package_' + name, name in actual)
        for name, version in compat.get('required_packages', {}).items():
            check('native_package_' + name, actual.get(name, {}).get('Version') == version)
        check('greetd_version', actual.get('greetd', {}).get('Version') == '0.10.3-4')
        check('original_applaunch_version', actual.get('applaunch', {}).get('Version') == '0.6.105')
        check('kernel_package_pin', actual.get('linux-image-' + KERNEL, {}).get('Version')
              == profile['pins']['kernel_package'])
        check('system_power_baseline_package_pin', actual.get('raspberrypi-sys-mods', {}).get('Version')
              == profile['pins']['raspberrypi_sys_mods_version'])
        check('no_desktop_activation_packages', not {'lightdm', 'labwc'}.intersection(actual))
        for name in ('xserver-xorg', 'xserver-xorg-core', 'rpd-wayland-core', 'rpd-x-core'):
            check('no_desktop_package_' + name, name not in actual)
        check('native_qualification_scope', compat.get('qualification', {}).get('scope') == 'disposable-offline-runtime-root'
              and compat['qualification'].get('assembled_image_qualified') is False
              and compat['qualification'].get('hardware_qualified') is False)
        for name, path in ASSETS.items():
            identity = compat.get('assets', {}).get(name, {})
            check('asset_path_' + name, identity.get('path') == path)
            asset = image.secure(path, executable=True)
            check('asset_hash_' + name, identity.get('sha256') == digest(asset))
            if name != 'store_backend':
                elf_needed(asset.read_bytes())
                check('asset_arm64_' + name)
        check('original_launcher_unchanged', digest(image.secure(MAIN)) == MAIN_SHA)
        check('original_store_unchanged', digest(image.secure(ORIGINAL)) == STORE_SHA)
        for map_name in ('runtime_files', 'resource_files', 'library_files', 'configuration_files'):
            records = compat.get(map_name)
            check(map_name + '_present', isinstance(records, dict) and bool(records))
            if map_name == 'runtime_files':
                check('required_runtime_files', RUNTIME.issubset(records))
            if map_name == 'configuration_files':
                check('required_pam_files', {'/etc/pam.d/greetd', '/etc/pam.d/common-session',
                      '/etc/pam.d/common-auth', '/etc/pam.d/common-account',
                      '/etc/pam.d/common-password'}.issubset(records))
            for path, identity in records.items():
                absolute(path)
                if isinstance(identity, dict) and 'symlink' in identity:
                    check('link_' + path, map_name != 'runtime_files' and image.path(path, False).is_symlink()
                          and image.path(path, False).lstat().st_uid == owner
                          and os.readlink(image.path(path, False)) == identity['symlink'])
                    resolved = image.path(path, trusted=True)
                    image.secure('/' + str(resolved.relative_to(image.root)))
                else:
                    checksum = identity.get('sha256') if isinstance(identity, dict) else identity
                    check('content_' + path, isinstance(checksum, str) and DIGEST.fullmatch(checksum)
                          and digest(image.secure(path, parent_aliases=map_name != 'runtime_files')) == checksum)
        resources = {}
        resource_roots = compat.get('resource_roots', ['/usr/share/APPLaunch/share'])
        check('vendor_resource_root', '/usr/share/APPLaunch/share' in resource_roots)
        for path in resource_roots:
            tree = image.tree(path)
            check('resource_directory_' + path, tree[path]['type'] == 'directory')
            resources.update({name: identity for name, identity in tree.items()
                              if identity['type'] != 'directory'})
        check('exact_resource_path_set', set(resources) == set(compat['resource_files']))
        report['elf_closure'] = verify_elf_closure(image, compat, check)
        protected = compat.get('protected_files')
        roots = compat.get('protected_roots', ['/lib/modules', '/lib/firmware', '/boot/firmware/overlays'])
        check('complete_hardware_snapshot', isinstance(protected, dict) and bool(protected)
              and isinstance(roots, list) and bool(roots))
        check('static_boot_hardware_snapshot', {'/lib', '/boot/firmware/config.txt',
              '/boot/firmware/kernel8.img', '/boot/firmware/start.elf', '/boot/firmware/fixup.dat',
              '/etc/cardputerzero-dtoverlays.commit'}.issubset(protected))
        check('protected_hardware_roots', {image.path(path) for path in (
              '/lib/modules', '/lib/firmware', '/boot/firmware/overlays')}.issubset(
              {image.path(path) for path in roots}))
        measured = {}
        for path in roots:
            tree = image.tree(path)
            check('protected_tree_complete_' + path, tree[path]['type'] == 'directory')
            measured.update(tree)
        # Additional boot/kernel files may be pinned without making config/cmdline
        # byte-identical across exported partition UUIDs.
        for path in protected:
            if not any(path == base or path.startswith(base + '/') for base in roots):
                measured[path] = image.identity(path)
        check('full_firmware_modules_overlays', set(measured) == set(protected)
              and all(same_identity(image, path, measured[path], identity)
                      for path, identity in protected.items()),
              {'expected': len(protected), 'actual': len(measured)})
        firmware = image.path('/lib/firmware')
        for path, identity in protected.items():
            if identity['type'] == 'symlink' and image.path(str(PurePosixPath(path).parent)).is_relative_to(firmware):
                routes = []
                resolved = image.path(path, trace=routes)
                target = '/' + str(resolved.relative_to(image.root))
                check('firmware_route_pinned_' + path, set(routes).issubset(protected)
                      and target in protected, {'routes': routes, 'target': target})
        check('kernel_release', profile['pins']['kernel_release'] == KERNEL
              and image.path('/lib/modules/' + KERNEL).is_dir())
        check('bsp_source_commit', image.text('/etc/cardputerzero-dtoverlays.commit').strip()
              == profile['pins']['bsp_commit'])
        module_root = image.path('/lib/modules/' + KERNEL + '/extra')
        for name in MODULES:
            matches = sorted(module_root.glob(name + '.ko*'))
            check('module_present_' + name, len(matches) == 1)
            data = matches[0].read_bytes()
            if matches[0].suffix == '.xz':
                data = lzma.decompress(data)
            elif matches[0].suffix == '.gz':
                data = gzip.decompress(data)
            elif matches[0].suffix == '.zst':
                result = subprocess.run(['zstd', '--decompress', '--stdout', str(matches[0])],
                                        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
                data = result.stdout
            check('module_abi_' + name, re.search(rb'(?:^|\x00)vermagic=' + re.escape(KERNEL.encode())
                  + rb' [^\x00]*\x00', data) is not None)
        config = image.text('/boot/firmware/config.txt')
        check('vendor_kernel_boot', not re.search(r'^\s*kernel\s*=\s*u-boot\.bin\s*$', config, re.M)
              and not image.exists('/boot/firmware/u-boot.bin'))
        for overlay in ('cardputerzero-v5-overlay', 'bq27220_v5', 'bmi270_bmm150_overlay'):
            check('overlay_active_' + overlay, re.search(r'^\s*dtoverlay=' + re.escape(overlay)
                  + r'(?:,.*)?\s*$', config, re.M) is not None)
        check('radio_hardware_retained', not re.search(r'^\s*dtoverlay=disable-(?:wifi|bt)\s*$', config, re.M))
        for name in ('kernel8.img', 'start.elf', 'fixup.dat'):
            check('boot_file_' + name, image.secure('/boot/firmware/' + name).stat().st_size > 0)
        check('lcd_firmware', image.secure('/lib/firmware/cardputerzero,st7789v_lcd.bin').stat().st_size > 0)
        verify_activation(image, compat, expected, actual, check)
        verify_firstboot(image, check)
        report['status'] = 'passed'
    except (VerificationError, OSError, ValueError, KeyError, TypeError, struct.error,
            configparser.Error,
            subprocess.SubprocessError) as error:
        report['error'] = str(error)
    report['passed_checks'] = sum(item['passed'] for item in report['checks'])
    report['failed_checks'] = sum(not item['passed'] for item in report['checks'])
    return report


def verify_pam_session(image, configurations, check):
    """Expand only hash-bound PAM includes, respecting typed include semantics."""
    visited = set()
    def expand(service, kinds=None, stack=()):
        require(re.fullmatch(r'[A-Za-z0-9_.+-]{1,128}', service) is not None, 'invalid PAM include')
        require(service not in stack and len(stack) < 64, 'cyclic/deep PAM include: ' + service)
        path = '/etc/pam.d/' + service
        expected = configurations.get(path)
        checksum = expected.get('sha256') if isinstance(expected, dict) else expected
        check('pam_include_bound_' + service, isinstance(checksum, str) and DIGEST.fullmatch(checksum)
              and digest(image.secure(path)) == checksum)
        visited.add(service)
        records = []
        for raw in image.text(path).splitlines():
            line = re.split(r'\s+#', raw.strip(), maxsplit=1)[0].strip()
            if not line or line.startswith('#'):
                continue
            include = re.fullmatch(r'@include\s+([A-Za-z0-9_.+-]{1,128})', line)
            if include:
                records.extend(expand(include[1], kinds, (*stack, service)))
                continue
            record = re.fullmatch(r'-?(auth|account|password|session)\s+(\[[^\]]+\]|\S+)\s+(\S+)(?:\s+.*)?', line)
            require(record is not None, 'invalid PAM rule: ' + service)
            kind, control, module = record.groups()
            if control in {'include', 'substack'}:
                selected = {kind} if kinds is None else kinds.intersection({kind})
                records.extend(expand(module, selected, (*stack, service)))
            elif kinds is None or kind in kinds:
                records.append((kind, control, module))
        return records
    records = expand('greetd')
    direct = '\n'.join(line.strip() for line in image.text('/etc/pam.d/greetd').splitlines()
                       if line.strip() and not line.lstrip().startswith('#'))
    check('pam_real_session', any(kind == 'session' and control in {'optional', 'required'}
          and PurePosixPath(module).name == 'pam_systemd.so' for kind, control, module in records)
          and 'pam_permit.so' not in direct)
    return sorted(visited)


def verify_activation(image, compat, metadata, actual, check):
    for path in MASKS:
        check('mask_' + path, image.path(path, False).is_symlink()
              and os.readlink(image.path(path, False)) == '/dev/null')
    check('native_default_target', os.readlink(image.path('/etc/systemd/system/default.target', False))
          in {'/lib/systemd/system/multi-user.target', '/usr/lib/systemd/system/multi-user.target'})
    link = '/etc/systemd/system/multi-user.target.wants/tyrell-zero.target'
    check('zero_target_enabled', image.path(link, False).is_symlink()
          and os.readlink(image.path(link, False)) in {'/lib/systemd/system/tyrell-zero.target',
                                                     '/usr/lib/systemd/system/tyrell-zero.target'})
    logind = configparser.ConfigParser(interpolation=None)
    logind.read_string(image.text('/etc/systemd/logind.conf.d/90-tyrell-zero.conf'))
    check('no_shell_vts', dict(logind['Login']) == {'nautovts': '0', 'reservevt': '0'})
    for prefix in ('/etc/systemd/system', '/etc/systemd/user'):
        folder = image.path(prefix)
        for p in folder.rglob('*'):
            if p.is_symlink() and '.wants' in p.parent.name:
                check('no_desktop_wants_' + str(p.relative_to(image.root)),
                      p.name not in {'lightdm.service', 'labwc.service', 'APPLaunch.service', 'LaunchWizard.service',
                                     'userconfig.service'})
    verify_pam_session(image, compat['configuration_files'], check)
    unit_contracts = {
        'tyrell-zero-boot-state.service': {
            'Unit': {'Before': 'tyrell-zero-session.service tyrell-zero-wizard.service',
                     'RequiresMountsFor': '/etc/tyrell-zero /var/lib/tyrell-zero /var/lib/LaunchWizard'},
            'Service': {'Type': 'oneshot', 'ExecStart': '/usr/libexec/tyrell-zero-session-state boot',
                        'RemainAfterExit': 'yes', 'TimeoutStartSec': '20', 'UMask': '0022'}},
        'tyrell-zero-session.service': {
            'Unit': {'Requires': 'tyrell-zero-boot-state.service',
                     'After': 'tyrell-zero-boot-state.service systemd-user-sessions.service',
                     'Conflicts': 'getty@tty7.service', 'ConditionPathExists': '!/run/tyrell-zero/setup',
                     'StartLimitIntervalSec': '60', 'StartLimitBurst': '3'},
            'Service': {'Type': 'simple', 'ExecStart': '/usr/sbin/greetd --config /run/tyrell-zero/greetd.config',
                        'Restart': 'on-failure', 'RestartSec': '5', 'KillMode': 'mixed', 'TimeoutStopSec': '10'}},
        'tyrell-zero-wizard.service': {
            'Unit': {'Requires': 'tyrell-zero-boot-state.service',
                     'After': 'tyrell-zero-boot-state.service systemd-user-sessions.service',
                     'ConditionPathExists': '/run/tyrell-zero/setup'},
            'Service': {'Type': 'simple', 'WorkingDirectory': '/usr/share/APPLaunch',
                        'ExecStart': '/usr/libexec/tyrell-zero/LaunchWizard', 'Restart': 'no', 'TimeoutStopSec': '10'}},
        'tyrell-zero.target': {
            'Unit': {'Requires': 'tyrell-zero-boot-state.service',
                     'Wants': 'tyrell-zero-session.service tyrell-zero-wizard.service',
                     'After': 'tyrell-zero-boot-state.service'}}}
    for name, contract in unit_contracts.items():
        for prefix in ('/etc/systemd/system/', '/usr/lib/systemd/system/'):
            check('no_native_unit_dropin_' + prefix + name,
                  not image.exists(prefix + name + '.d'))
        check('no_native_unit_override_' + name,
              not image.exists('/etc/systemd/system/' + name))
        unit = configparser.ConfigParser(interpolation=None, delimiters=('=',))
        unit.optionxform = str
        unit.read_string(image.text('/usr/lib/systemd/system/' + name))
        parsed = {section: dict(unit[section]) for section in unit.sections()}
        parsed.get('Unit', {}).pop('Description', None)
        check('native_unit_contract_' + name, parsed == contract)
    check('store_diversion', image.text('/var/lib/dpkg/diversions').splitlines().count(STORE) == 1
          and [STORE, ORIGINAL, 'tyrell-zero-store'] in [image.text('/var/lib/dpkg/diversions').splitlines()[n:n+3]
             for n in range(0, len(image.text('/var/lib/dpkg/diversions').splitlines()), 3)])
    baseline = image.json(BASELINE)
    catalog = image.json('/usr/share/tyrell-zero/store-catalog.json')
    ordinary = {item['package'] for item in catalog['packages']}
    check('store_baseline_complete', baseline.get('schema') == 1 and baseline.get('complete') is True)
    check('store_baseline_exact_packages', baseline.get('packages')
          == {name: value['Version'] for name, value in actual.items() if name not in ordinary})
    files = baseline.get('files', {})
    check('store_baseline_required_files', {STORE, ORIGINAL, '/var/lib/dpkg/diversions',
          '/usr/lib/tyrell-zero/store/guard.py', '/usr/libexec/tyrell-zero/appstore-package-helper',
          '/etc/systemd/system/default.target',
          '/etc/systemd/system/multi-user.target.wants/tyrell-zero.target',
          '/etc/systemd/logind.conf.d/90-tyrell-zero.conf', '/etc/pam.d/greetd',
          '/etc/tyrell-zero/power-policy.json', *MASKS, *RUNTIME}.issubset(files))
    for path, identity in files.items():
        if isinstance(identity, dict):
            check('store_baseline_link_' + path, set(identity) == {'symlink'}
                  and os.readlink(image.path(path, False)) == identity['symlink'])
        else:
            check('store_baseline_file_' + path, digest(image.secure(path)) == identity)
    check('store_trigger_package_set', set(baseline.get('triggers', {})) == TRIGGERS.intersection(actual))
    for name, record in baseline['triggers'].items():
        check('store_trigger_version_' + name, record['version'] == actual[name]['Version'])
        scripts = {}
        for suffix in SCRIPTS:
            path = '/var/lib/dpkg/info/' + name + '.' + suffix
            if image.exists(path):
                scripts[suffix] = digest(image.secure(path))
        check('store_trigger_scripts_' + name, record['scripts'] == scripts)
    integration, qualification = image.json(INTEGRATION), image.json(QUALIFICATION)
    check('session_receipt_identity', integration.get('schema') == 1 and integration.get('profile') == PROFILE
          and integration.get('complete') is True and integration.get('workflow') == 'native-setup-v1'
          and integration.get('applaunch_version') == '0.6.105')
    check('session_qualification_bound', integration.get('qualification_sha256') == digest(image.secure(QUALIFICATION)))
    check('session_qualification_identity', qualification.get('schema') == 1 and qualification.get('profile') == PROFILE
          and qualification.get('scope') == 'pre-image-integration' and qualification.get('workflow') == 'native-setup-v1'
          and qualification.get('status') == 'passed' and qualification.get('physical_hardware_qualified') is False
          and qualification.get('suites') == {'session': 'passed', 'wizard': 'passed', 'store': 'passed'})
    source_path = '/usr/share/tyrell-zero/source-pre-image-qualification.json'
    source_qualification = image.json(source_path)
    check('source_qualification_bound', isinstance(compat.get('source_qualification_sha256'), str)
          and DIGEST.fullmatch(compat['source_qualification_sha256'])
          and digest(image.secure(source_path)) == compat['source_qualification_sha256'])
    check('source_qualification_passed', source_qualification.get('status') == 'passed'
          and source_qualification.get('scope') == 'pre-image-integration'
          and source_qualification.get('physical_hardware_qualified') is False
          and source_qualification.get('assets') == compat['assets'])
    check('session_assets_bound', integration.get('assets') == qualification.get('assets') == compat['assets'])
    runtime = integration.get('runtime_files', {})
    check('session_runtime_bound', runtime == qualification.get('runtime_files') and RUNTIME.issubset(runtime)
          and BASELINE in runtime)
    for path, checksum in runtime.items():
        check('session_runtime_' + path, digest(image.secure(path, parent_aliases=False)) == checksum)
    activation = image.json(ACTIVATION)
    check('activation_phase', activation.get('schema') == 1 and activation.get('profile_id') == PROFILE
          and activation.get('phase') == 'assembled-root' and activation.get('complete') is True
          and activation.get('physical_hardware_qualified') is False)
    check('activation_power_baseline', activation.get('power_preset') == 'baseline')
    check('activation_locks', activation.get('locks_sha256') == metadata['locks_sha256'])
    check('activation_profile', activation.get('profile_sha256') == metadata['profile_sha256'])
    check('activation_stages', activation.get('stages') == STAGES)
    check('activation_source_qualification', activation.get('source_qualification_sha256')
          == compat['source_qualification_sha256'])
    entries = activation.get('files', {})
    check('activation_required_files', isinstance(entries, dict) and {BASELINE, INTEGRATION, QUALIFICATION,
          '/etc/tyrell-zero/power-policy.json', '/etc/pam.d/greetd', *MASKS}.issubset(entries)
          and ACTIVATION not in entries)
    for path, identity in entries.items():
        check('activation_file_' + path, same_identity(image, path, image.identity(path), identity))
    check('power_selection', image.json('/etc/tyrell-zero/power-policy.json') == {'schema': 1, 'preset': 'baseline'})
    power = image.json('/usr/share/tyrell-zero/power-policy/contract.json')
    check('no_power_qualification_claim', power.get('activation_implemented') is False
          and power.get('qualification') == 'not-tested-for-Zero'
          and digest(image.secure('/usr/share/tyrell-zero/power-policy/contract.json')) == metadata['power_contract_sha256'])
    for path in ('/etc/tmpfiles.d/raspberrypi-sys-mods-ondemand-governor.conf',
                 '/run/tmpfiles.d/raspberrypi-sys-mods-ondemand-governor.conf',
                 '/var/lib/tyrell-zero/power/eligibility.json'):
        check('no_optimized_power_' + path, not image.exists(path))


def verify_firstboot(image, check):
    if image.exists('/run'):
        check('no_live_runtime_state', not any(not p.is_dir() or p.is_symlink()
              for p in image.path('/run').rglob('*')))
    check('machine_id_unprovisioned', image.text('/etc/machine-id').strip() in {'', 'uninitialized'})
    dbus = '/var/lib/dbus/machine-id'
    check('dbus_machine_id_unprovisioned', not image.exists(dbus)
          or image.path(dbus, False).is_symlink() and os.readlink(image.path(dbus, False))
          in {'/etc/machine-id', '../../../etc/machine-id'})
    for path in ('/var/lib/LaunchWizard/run-oobe', '/var/lib/LaunchWizard/run-keyboard-guide'):
        check('setup_marker_' + path, image.secure(path).stat().st_size == 0)
    for path in ('/etc/tyrell-zero/native-account.v1.json', '/var/lib/tyrell-zero/readiness.v1.json',
                 '/var/lib/tyrell-zero/setup-apply.v1.json', '/var/lib/LaunchWizard/account-migration.state',
                 '/run/tyrell-zero'):
        check('no_configured_setup_' + path, not image.exists(path))
    for path in ('/etc/systemd/system/multi-user.target.wants/ssh.service',
                 '/etc/systemd/system/sockets.target.wants/ssh.socket', '/boot/firmware/ssh'):
        check('ssh_unprovisioned_' + path, not image.exists(path))
    for path, pattern in [('/etc/ssh', 'ssh_host_*'),
                          ('/etc/NetworkManager/system-connections', '*')]:
        if image.exists(path):
            check('no_credentials_' + path, not list(image.path(path).glob(pattern)))
    shadow = {}
    for line in image.text('/etc/shadow').splitlines():
        values = line.split(':')
        require(len(values) >= 2 and values[0] not in shadow, 'invalid shadow records')
        shadow[values[0]] = values[1]
    users = []
    for line in image.text('/etc/passwd').splitlines():
        fields = line.split(':')
        require(len(fields) == 7 and fields[2].isdigit(), 'invalid passwd record')
        require(not any(row[0] == fields[0] for row in users), 'duplicate passwd account')
        users.append(fields)
        if fields[0] == 'root' or int(fields[2]) >= 1000 and fields[-1] not in {'/usr/sbin/nologin', '/bin/false'}:
            check('no_bootstrap_password_' + fields[0], shadow.get(fields[0], '').startswith(('!', '*')))
            home = absolute(fields[5])
            for name in ('.ssh/authorized_keys', '.bash_history', '.zsh_history'):
                check('no_personal_identity_' + fields[0] + '_' + name, not image.exists(home + '/' + name))
    recovery = [row for row in users if row[0] == '_greetd']
    check('unprivileged_recovery_account', len(recovery) == 1 and 0 < int(recovery[0][2]) < 1000
          and recovery[0][-1] in {'/usr/sbin/nologin', '/bin/false'})
    groups = {}
    for line in image.text('/etc/group').splitlines():
        values = line.split(':')
        require(len(values) == 4 and values[0] not in groups and values[2].isdigit(), 'invalid group database')
        groups[values[0]] = values[3].split(',')
    check('recovery_device_groups', all('_greetd' in groups.get(name, []) for name in ('video', 'input')))
    for path in ('/etc/wpa_supplicant/wpa_supplicant.conf', '/boot/firmware/wpa_supplicant.conf',
                 '/boot/firmware/custom.toml', '/boot/firmware/userconf', '/boot/firmware/userconf.txt'):
        check('no_imager_credentials_' + path, not image.exists(path))


def verify_exported_boot(image, partition_uuids, metadata, check):
    """Validate permitted export rewrites against measured disk identity."""
    check('export_partition_identities', set(partition_uuids) == {'boot', 'root'}
          and re.fullmatch(r'[0-9a-fA-F]{8}-01', partition_uuids['boot'])
          and re.fullmatch(r'[0-9a-fA-F]{8}-02', partition_uuids['root'])
          and partition_uuids['boot'][:-2] == partition_uuids['root'][:-2])
    command = image.text('/boot/firmware/cmdline.txt')
    tokens = command.split()
    check('export_root_cmdline', len(command.strip().splitlines()) == 1
          and [token for token in tokens if token.startswith('root=')]
          == ['root=PARTUUID=' + partition_uuids['root']])
    check('export_quiet_native_boot', 'quiet' in tokens and not any(token in tokens for token in (
          'splash', 'plymouth.ignore-serial-consoles', 'console=serial0,115200')))
    fstab = image.text('/etc/fstab')
    records = []
    for line in fstab.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        require(len(fields) in {4, 5, 6}, 'malformed active fstab entry')
        records.append(fields)
    for name, target, filesystem in [('root', '/', 'ext4'), ('boot', '/boot/firmware', 'vfat')]:
        entries = [row for row in records if row[1] == target]
        check('export_fstab_' + name, len(entries) == 1
              and entries[0][0] == 'PARTUUID=' + partition_uuids[name]
              and entries[0][2] == filesystem)
    check('no_export_placeholders', not any(value in command + fstab for value in ('ROOTDEV', 'BOOTDEV')))
    check('export_cmdline_copy', image.text('/boot/cmdline.txt') == command)
    issue = image.text('/etc/rpi-issue')
    check('export_stage2_issue', metadata['pi_gen_commit'] in issue
          and re.search(r'\bstage2\b', issue) is not None
          and image.text('/boot/firmware/issue.txt') == issue)
    check('export_boot_issue_link', image.path('/boot/issue.txt', False).is_symlink()
          and os.readlink(image.path('/boot/issue.txt', False)) == 'firmware/issue.txt')


def verify_image(image_path, metadata, locks, output):
    helper = runpy.run_path(str(Path(__file__).with_name('verify-image.py')))
    command = helper['command']
    path = Path(image_path).absolute()
    require(not path.is_symlink() and path.is_file() and stat.S_ISREG(path.stat().st_mode),
            'regular raw image required; block devices are never accepted')
    path = path.resolve()
    require(sys.platform == 'linux' and os.geteuid() == 0 and os.uname().machine in {'aarch64', 'arm64'},
            'raw image verification requires native ARM64 Linux root')
    before = digest(path)
    table = json.loads(command(['sfdisk', '--json', path]))['partitiontable']
    parts = table.get('partitions', [])
    require(table.get('label') == 'dos' and table.get('sectorsize') == 512 and len(parts) == 2,
            'expected two-partition DOS image')
    boot, system = parts
    require(str(boot.get('type', '')).lower() in {'b', 'c', '0b', '0c'} and str(system.get('type')) == '83'
            and boot['size'] * 512 == 512 * 1024 * 1024 and boot['start'] > 0
            and boot['start'] + boot['size'] <= system['start']
            and (system['start'] + system['size']) * 512 <= path.stat().st_size, 'invalid partition geometry')
    work = Path(tempfile.mkdtemp(prefix='tyrell-zero-verify-'))
    loop, mounted, cleanup = None, [], []
    try:
        loop = command(['losetup', '--find', '--show', '--partscan', '--read-only', path]).strip()
        require(re.fullmatch(r'/dev/loop[0-9]+', loop), 'invalid loop device')
        info = json.loads(command(['losetup', '--json', '--output', 'NAME,BACK-FILE,RO', loop]))['loopdevices'][0]
        require(info.get('ro') in {True, 1, '1'} and Path(info.get('back-file', info.get('back_file', ''))).resolve() == path,
                'loop device does not read-only back this image')
        for suffix, filesystem in [('p1', 'vfat'), ('p2', 'ext4')]:
            require(Path(loop + suffix).is_block_device(), 'missing loop partition device')
            require(command(['blkid', '-s', 'TYPE', '-o', 'value', loop + suffix]).strip() == filesystem,
                    'unexpected partition filesystem')
        root = work / 'root';root.mkdir()
        command(['mount', '-t', 'ext4', '-o', 'ro,noload', loop + 'p2', root]);mounted.append(root)
        boot_root = root / 'boot/firmware'
        require(boot_root.is_dir() and not boot_root.is_symlink(), 'regular boot mountpoint required')
        command(['mount', '-t', 'vfat', '-o', 'ro', loop + 'p1', boot_root]);mounted.append(boot_root)
        report = verify_root(root, metadata, locks)
        ids = {name: command(['blkid', '-s', 'PARTUUID', '-o', 'value', loop + part]).strip()
               for name, part in [('boot', 'p1'), ('root', 'p2')]}
        disk = ImageRoot(root)
        def export_check(name, passed, detail=None):
            report['checks'].append({'name': name, 'passed': bool(passed), 'detail': detail})
            require(passed, name)
        verify_exported_boot(disk, ids, load_json(metadata), export_check)
        report['partition_uuids'] = ids
        report['scope'] = 'offline-read-only-image'
        for mount in reversed(mounted[:]):
            command(['umount', mount]);mounted.remove(mount)
        command(['fsck.vfat', '-n', loop + 'p1'])
        command(['e2fsck', '-f', '-n', loop + 'p2'])
        report['checks'].append({'name': 'read_only_filesystem_checks', 'passed': True, 'detail': None})
    finally:
        for mount in reversed(mounted):
            try:command(['umount', mount])
            except Exception as error:cleanup.append(str(error))
        if loop and not cleanup:
            try:command(['losetup', '--detach', loop])
            except Exception as error:cleanup.append(str(error))
        if not cleanup:shutil.rmtree(work)
        require(not cleanup, 'cleanup failed; retained ' + str(work) + ': ' + repr(cleanup))
    after = digest(path)
    require(before == after, 'raw image bytes changed during verification')
    require(not command(['losetup', '--noheadings', '--output', 'NAME', '--associated', path]).strip(),
            'image loop remains attached')
    report['image'] = {'name': path.name, 'bytes': path.stat().st_size,
                       'sha256_before': before, 'sha256_after': after}
    report['passed_checks'] = sum(bool(item['passed']) for item in report['checks'])
    report['failed_checks'] = sum(not item['passed'] for item in report['checks'])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('image', nargs='?', type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--locks-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    if bool(args.root) == bool(args.image):
        parser.error('select exactly one of --root or a raw image file')
    if args.root and args.output_dir.resolve().is_relative_to(args.root.resolve()):
        parser.error('verification output must be outside the inspected root')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = None
    try:
        report = (verify_root(args.root, args.metadata, args.locks_dir) if args.root else
                  verify_image(args.image, args.metadata, args.locks_dir, args.output_dir))
    except (VerificationError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        report = {'schema_version': 1, 'profile_id': PROFILE, 'status': 'failed', 'checks': [], 'error': str(error)}
    (args.output_dir / 'image-verification.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': report['status'], 'checks': len(report['checks']),
                      'error': report.get('error')}, sort_keys=True))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
