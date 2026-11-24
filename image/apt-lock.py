#!/usr/bin/env python3
"""Capture checksum-locked package bytes and replay APT without upstream sources.

Capture reads retained pi-gen stage filesystems; it never mounts an image or
accesses a device. A successful capture publishes a new repository atomically.
The repository is a local trust boundary, not an independently signed upstream
snapshot. Keep apt-lock.json with the image's checksummed source-input archive.
"""
import argparse
import base64
from datetime import datetime, timezone
import gzip
import hashlib
import http.server
import functools
import fcntl
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

STAGES = tuple('stage' + str(n) for n in range(5))
IDENTITY = ('Package', 'Version', 'Architecture')
ABSENT_REMOVAL_PACKAGE = 'firmware-marvell-prestera'
ABSENT_REMOVAL_FILE = 'stage2/02-net-tweaks/00-packages'
ABSENT_REMOVAL_SOURCE_SHA256 = 'cd85c096f317e549a88d5bfe2233db6e84be473f21d3bc44e70dae00f3f7ebd9'
ABSENT_REMOVAL_RECORD = 'tyrell-frozen-removal-normalization.json'
ABSENT_REMOVAL_GUARD = '''# The pinned vendor removal is a no-op only for this absent frozen package.
on_chroot << 'TYRELL_ABSENT_FIRMWARE_REMOVAL'
set -e
query_status=0
package_state="$(dpkg-query --show --showformat='${Status}' firmware-marvell-prestera 2>/dev/null)" || query_status=$?
case "$query_status:$package_state" in
    1:|"0:unknown ok not-installed") ;;
    *) echo 'Frozen removal normalization requires firmware-marvell-prestera to be absent' >&2; exit 1 ;;
esac
TYRELL_ABSENT_FIRMWARE_REMOVAL
'''


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def paragraphs(lines):
    data = {}
    last = None
    for line in lines:
        line = line.rstrip('\n')
        if not line:
            if data:
                yield data
            data, last = {}, None
        elif line[0].isspace() and last:
            data[last] += '\n' + line
        else:
            last, value = line.split(':', 1)
            if last in data:
                raise ValueError('Duplicate Debian control field: ' + last)
            data[last] = value.strip()
    if data:
        yield data


def identity(fields):
    return tuple(fields[k] for k in IDENTITY)


def inventory(root):
    with (root / 'var/lib/dpkg/status').open() as stream:
        return {identity(item) for item in paragraphs(stream)
                if item.get('Status', '').endswith(' ok installed')}


def deb_fields(path):
    output = subprocess.check_output(['dpkg-deb', '--field', str(path)], text=True)
    return next(paragraphs(output.splitlines()))


def safe_relative(value):
    path = Path(value)
    return bool(value) and not path.is_absolute() and '..' not in path.parts


def verify_repository(path):
    """Verify the complete file allowlist and repository structure; no writes."""
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise ValueError('APT repository must be a regular directory')
    manifest = path / 'apt-lock.json'
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError('Missing regular apt-lock.json')
    lock = json.loads(manifest.read_text())
    if lock.get('schema') != 1 or lock.get('architecture') != 'arm64':
        raise ValueError('Unsupported frozen APT repository schema/architecture')
    if set(lock.get('stages', {})) != set(STAGES):
        raise ValueError('Frozen repository must contain all five image stages')
    files = lock.get('files', {})
    if not files or not lock.get('packages'):
        raise ValueError('Frozen repository has no package closure')
    actual = set()
    for item in path.rglob('*'):
        if item.is_symlink() or not (item.is_dir() or item.is_file()):
            raise ValueError('Unsafe frozen repository path: ' + str(item))
        if item.is_file():
            actual.add(str(item.relative_to(path)))
    if actual != set(files) | {'apt-lock.json'}:
        raise ValueError('Frozen repository file allowlist differs from lock')
    for name, spec in files.items():
        if not safe_relative(name) or not re.fullmatch('[0-9a-f]{64}', spec.get('sha256', '')):
            raise ValueError('Invalid frozen repository file record: ' + name)
        item = path / name
        if item.stat().st_size != spec['bytes'] or digest(item) != spec['sha256']:
            raise ValueError('Frozen repository checksum mismatch: ' + name)
    package_keys = set()
    packages = {}
    for item in lock['packages']:
        key = identity(item)
        if key[0] == 'tyrell-status':
            raise ValueError('Custom Tyrell Status must remain a separate per-job package input')
        if key[2] not in {'arm64', 'all'}:
            raise ValueError('Frozen image closure only supports ARM64/all packages')
        if key in package_keys or item['file'] not in files:
            raise ValueError('Duplicate or missing frozen package')
        package_keys.add(key)
        packages[key] = item
        if files[item['file']]['sha256'] != item['sha256']:
            raise ValueError('Frozen package hash differs from file lock')
    for stage, items in lock['stages'].items():
        names = set()
        for item in items:
            key = tuple(item)
            if len(key) != 3 or key not in package_keys or (key[0], key[2]) in names:
                raise ValueError('Ambiguous or missing frozen stage package: ' + stage)
            names.add((key[0], key[2]))
        if not items or f'dists/{stage}/Release' not in files:
            raise ValueError('Incomplete frozen stage: ' + stage)
        index = path / 'dists' / stage / 'main/binary-arm64/Packages'
        with index.open() as stream:
            entries = list(paragraphs(stream))
        if {identity(item) for item in entries} != {tuple(item) for item in items} or len(entries) != len(items):
            raise ValueError('Frozen stage index differs from exact selection: ' + stage)
        for entry in entries:
            expected = packages[identity(entry)]
            if entry.get('Filename') != expected['file'] or entry.get('SHA256') != expected['sha256'] or int(entry.get('Size', -1)) != expected['bytes']:
                raise ValueError('Frozen stage package index differs from byte lock: ' + stage)
    for stage in (*STAGES, 'trixie'):
        suite = path / 'dists' / stage
        expected_indices = {f'{component}/binary-{arch}/Packages{suffix}'
                            for component in ['main', 'contrib', 'non-free']
                            for arch in ['arm64', 'armhf'] for suffix in ['', '.gz']}
        prefix = f'dists/{stage}/'
        actual_metadata = {name[len(prefix):] for name in files if name.startswith(prefix)}
        if actual_metadata != expected_indices | {'Release'}:
            raise ValueError('Unsupported or incomplete frozen index layout: ' + stage)
        for relative in sorted(expected_indices):
            if relative.endswith('.gz'):
                continue
            plain = (suite / relative).read_bytes()
            try:
                with gzip.open(suite / (relative + '.gz'), 'rb') as compressed:
                    expanded = compressed.read(len(plain) + 1)
            except (OSError, EOFError) as error:
                raise ValueError('Invalid frozen compressed index: ' + stage + '/' + relative) from error
            if expanded != plain:
                raise ValueError('Frozen compressed index differs from plain index: ' + stage + '/' + relative)
            if relative != 'main/binary-arm64/Packages' and plain:
                raise ValueError('Unexpected packages in unsupported component/architecture: ' + stage + '/' + relative)
        with (suite / 'Release').open() as stream:
            entries = list(paragraphs(stream))
        if len(entries) != 1:
            raise ValueError('Invalid frozen repository Release paragraphs: ' + stage)
        release = entries[0]
        allowed_fields = {'Origin', 'Label', 'Suite', 'Codename', 'Architectures', 'Components', 'Description', 'SHA256'}
        if (set(release) != allowed_fields or release.get('Codename') != stage or release.get('Suite') != stage
                or set(release.get('Architectures', '').split()) != {'arm64', 'armhf'}
                or set(release.get('Components', '').split()) != {'main', 'contrib', 'non-free'}):
            raise ValueError('Invalid frozen repository Release: ' + stage)
        covered = set()
        for line in release.get('SHA256', '').splitlines():
            if not line.strip():
                continue
            checksum, size, relative = line.split()
            name = str(Path('dists') / stage / relative)
            if (not safe_relative(relative) or relative in covered or name not in files
                    or files[name] != {'bytes': int(size), 'sha256': checksum}):
                raise ValueError('Frozen Release metadata differs from file lock: ' + stage)
            covered.add(relative)
        if covered != expected_indices:
            raise ValueError('Frozen Release does not cover every index exactly once: ' + stage)
    if {name for name in files if name.startswith('dists/')} != {
            f'dists/{stage}/{relative}' for stage in (*STAGES, 'trixie')
            for relative in expected_indices | {'Release'}}:
        raise ValueError('Unexpected frozen repository distribution metadata')
    if (path / 'dists/trixie/main/binary-arm64/Packages').read_bytes() != (path / 'dists/stage0/main/binary-arm64/Packages').read_bytes():
        raise ValueError('Bootstrap repository differs from stage0')
    return lock


def materialize(lock_path, output, asset_dirs=()):
    """Recreate the locked repository on any host, using SHA-verified bytes."""
    lock_path, output = Path(lock_path), Path(output)
    lock = json.loads(lock_path.read_text())
    if lock.get('schema') != 1 or not lock.get('repository_metadata'):
        raise ValueError('Lock has no portable repository metadata')
    if output.exists():
        raise ValueError('Preserving existing repository; select a new output path')
    local = {}
    for directory in asset_dirs:
        for path in Path(directory).rglob('*.deb'):
            if path.is_file() and not path.is_symlink():
                local[digest(path)] = path
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.apt-materialize-', dir=output.parent))
    try:
        for record in lock['packages']:
            name, checksum = record['file'], record['sha256']
            if not safe_relative(name) or not re.fullmatch('[0-9a-f]{64}', checksum):
                raise ValueError('Unsafe package lock record')
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if checksum in local:
                shutil.copyfile(local[checksum], target)
            else:
                url = record['source']
                if not url.startswith('https://'):
                    raise ValueError('Supply the exact local package bytes with --assets-dir: ' + record['Package'])
                with urllib.request.urlopen(url, timeout=90) as stream, target.open('wb') as out:
                    shutil.copyfileobj(stream, out)
            if target.stat().st_size != record['bytes'] or digest(target) != checksum:
                raise ValueError('Materialized package checksum differs: ' + record['Package'])
        for name, data in lock['repository_metadata'].items():
            if not safe_relative(name) or not name.startswith('dists/'):
                raise ValueError('Unsafe repository metadata path')
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(gzip.decompress(base64.b64decode(data, validate=True)))
        (staging / 'apt-lock.json').write_text(lock_path.read_text())
        verify_repository(staging)
        staging.rename(output)
    except BaseException:
        shutil.rmtree(staging)
        raise
    print('Materialized verified frozen repository: ' + str(output), flush=True)


def package_indices(root):
    """Read retained APT indexes, preserving their checksum and official URI."""
    records = {}
    for path in sorted((root / 'var/lib/apt/lists').glob('*Packages*')):
        name = path.name
        if name.startswith('deb.debian.org_debian-security_'):
            base = 'https://deb.debian.org/debian-security/'
        elif name.startswith('deb.debian.org_debian_'):
            base = 'https://deb.debian.org/debian/'
        elif name.startswith('archive.raspberrypi.com_debian_'):
            base = 'https://archive.raspberrypi.com/debian/'
        else:
            continue  # Camera/vendor package bytes are supplied separately.
        process = subprocess.Popen(['/usr/lib/apt/apt-helper', 'cat-file', str(path)],
                                   stdout=subprocess.PIPE, text=True)
        try:
            for item in paragraphs(process.stdout):
                if all(k in item for k in (*IDENTITY, 'SHA256', 'Filename', 'Size')):
                    if not safe_relative(item['Filename']):
                        raise ValueError('Unsafe upstream package filename')
                    records[identity(item)] = {**item, 'url': base + item['Filename']}
        finally:
            process.stdout.close()
            if process.wait() != 0:
                raise ValueError('Cannot read retained APT package index: ' + str(path))
    return records


def write_indices(destination, stage_packages, package_records, controls):
    """Each stage exposes one exact version per package/architecture."""
    for stage, selected in stage_packages.items():
        suite = destination / 'dists' / stage
        entries = []
        for key in sorted(selected):
            record = package_records[key]
            fields = dict(controls[key])
            fields.update(Filename=record['file'], Size=str(record['bytes']),
                          SHA256=record['sha256'])
            for optional in ['MD5sum', 'SHA1', 'SHA512']:
                fields.pop(optional, None)
            entries.append('\n'.join(k + ': ' + v for k, v in fields.items()) + '\n\n')
        data = ''.join(entries).encode()
        for arch in ['arm64', 'armhf']:
            directory = suite / 'main' / ('binary-' + arch)
            directory.mkdir(parents=True)
            # The vendor enables the foreign armhf architecture, but this
            # profile explicitly accepts only ARM64/all package closures.
            content = data if arch == 'arm64' else b''
            (directory / 'Packages').write_bytes(content)
            (directory / 'Packages.gz').write_bytes(gzip.compress(content, mtime=0))
        for component in ['contrib', 'non-free']:
            for arch in ['arm64', 'armhf']:
                directory = suite / component / ('binary-' + arch)
                directory.mkdir(parents=True)
                (directory / 'Packages').write_bytes(b'')
                (directory / 'Packages.gz').write_bytes(gzip.compress(b'', mtime=0))
        release = ['Origin: Tyrell frozen local repository', 'Label: Tyrell',
                   'Suite: ' + stage, 'Codename: ' + stage,
                   'Architectures: arm64 armhf', 'Components: main contrib non-free',
                   'Description: Checksum-verified captured package bytes', 'SHA256:']
        for path in sorted(suite.rglob('*')):
            if path.is_file():
                release.append(f' {digest(path)} {path.stat().st_size} {path.relative_to(suite)}')
        (suite / 'Release').write_text('\n'.join(release) + '\n')
    # debootstrap looks up the Debian codename while normal stages use their
    # independent exact package candidates.
    shutil.copytree(destination / 'dists/stage0', destination / 'dists/trixie')
    release = destination / 'dists/trixie/Release'
    release.write_text(release.read_text().replace('Suite: stage0', 'Suite: trixie')
                       .replace('Codename: stage0', 'Codename: trixie'))


def finalize_repository(staging, selected, records, controls):
    write_indices(staging, selected, records, controls)
    lock = {'schema': 1, 'architecture': 'arm64', 'created_utc': datetime.now(timezone.utc).isoformat(),
            'scope': 'Per-stage image APT package bytes; custom packages are separate per-job inputs; builder toolchain is recorded separately.',
            'trust': 'Captured retained official APT indexes/caches and already locked vendor inputs; lock must be retained with recipe.',
            'stages': {stage: [list(item) for item in sorted(items)] for stage, items in selected.items()},
            'packages': [records[key] for key in sorted(records)],
            'repository_metadata': {str(p.relative_to(staging)): base64.b64encode(gzip.compress(p.read_bytes(), mtime=0)).decode()
                                    for p in sorted((staging / 'dists').rglob('*')) if p.is_file()},
            'files': {str(p.relative_to(staging)): {'bytes': p.stat().st_size, 'sha256': digest(p)}
                      for p in sorted(staging.rglob('*')) if p.is_file()}}
    (staging / 'apt-lock.json').write_text(json.dumps(lock, indent=2) + '\n')
    verify_repository(staging)
    return lock


def capture(work, assets, output):
    work, assets, output = map(lambda p: Path(p).resolve(), (work, assets, output))
    if output.exists():
        raise ValueError('Preserving existing APT repository; select a new output path')
    roots = {stage: work / stage / 'rootfs' for stage in STAGES}
    installed = {stage: inventory(root) for stage, root in roots.items()}
    cached = {}
    controls = {}
    input_sources = {}
    for filename, keys in [('baseline-apps.json', ('launcher', 'apps')), ('camera-packages.json', ('packages',))]:
        path = assets.parent / 'image' / filename
        if path.is_file():
            data = json.loads(path.read_text())
            for key in keys:
                specs = data[key] if isinstance(data[key], list) else [data[key]]
                input_sources.update({item['sha256']: item['url'] for item in specs})
    stage_extra = {}
    for stage, root in roots.items():
        stage_extra[stage] = set()
        for path in sorted((root / 'var/cache/apt/archives').glob('*.deb')):
            fields = deb_fields(path)
            key = identity(fields)
            cached.setdefault(key, path)
            controls[key] = fields
            stage_extra[stage].add(key)
    for path in sorted(assets.glob('*.deb')):
        fields = deb_fields(path)
        key = identity(fields)
        if key in cached and digest(cached[key]) != digest(path):
            raise ValueError('Same package identity has differing bytes: ' + repr(key))
        cached[key], controls[key] = path, fields
    # Keep transient packages used in a stage, even when removed before its
    # final inventory, while using that stage's final version for upgrades.
    selected = {}
    earlier = set()
    for stage in STAGES:
        # Custom packages are installed from the independently verified per-job
        # .deb. Do not freeze an earlier build of the same custom version into
        # the operating-system dependency pool.
        requested = {key for key in installed[stage] | stage_extra[stage] | earlier if key[0] != 'tyrell-status'}
        by_name = {(p, a): (p, v, a) for p, v, a in installed[stage] if p != 'tyrell-status'}
        for key in sorted(requested):
            name = (key[0], key[2])
            if name in by_name:
                continue
            choices = {item for item in requested if (item[0], item[2]) == name}
            if len(choices) != 1:
                raise ValueError('Ambiguous transient versions in ' + stage + ': ' + repr(sorted(choices)))
            by_name[name] = key
        selected[stage] = set(by_name.values())
        earlier = selected[stage]
    needed = set().union(*selected.values())
    if any(key[2] not in {'arm64', 'all'} for key in needed):
        raise ValueError('Only ARM64/all package closures are supported')
    print('Capturing ' + str(len(needed)) + ' exact package identities', flush=True)
    upstream = {}
    for root in roots.values():
        upstream.update(package_indices(root))
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.apt-repository-', dir=output.parent))
    records = {}
    try:
        (staging / 'pool').mkdir()
        # The vendor intentionally retains an older kernel which has left the
        # rolling APT index. Its baseline manifest still authenticates all bytes.
        kernel_manifest = work.parents[1] / 'stage0/02-firmware/files/kernel-packages.sha256'
        for line in kernel_manifest.read_text().splitlines():
            checksum, name = line.split()
            url = 'https://archive.raspberrypi.com/debian/pool/main/l/linux/' + name
            input_sources[checksum] = url
            if not any(digest(source) == checksum for source in cached.values()
                       if source.name == name):
                source = staging / 'download.partial'
                with urllib.request.urlopen(url, timeout=90) as stream, source.open('wb') as out:
                    shutil.copyfileobj(stream, out)
                if digest(source) != checksum:
                    raise ValueError('Vendor kernel manifest checksum mismatch: ' + name)
                fields = deb_fields(source)
                key = identity(fields)
                retained = staging / 'pool' / (checksum + '.deb')
                source.rename(retained)
                cached[key], controls[key] = retained, fields
        for number, key in enumerate(sorted(needed), 1):
            source = cached.get(key)
            index = upstream.get(key)
            if source is None:
                if index is None:
                    raise ValueError('No retained byte source for package: ' + repr(key))
                source = staging / 'download.partial'
                with urllib.request.urlopen(index['url'], timeout=60) as stream, source.open('wb') as out:
                    shutil.copyfileobj(stream, out)
                controls[key] = deb_fields(source)
                if identity(controls[key]) != key:
                    raise ValueError('Downloaded package identity differs: ' + repr(key))
            checksum, size = digest(source), source.stat().st_size
            if index and (checksum != index['SHA256'] or size != int(index['Size'])):
                raise ValueError('Retained/downloaded package differs from APT index: ' + repr(key))
            relative = 'pool/' + checksum + '.deb'
            if source != staging / relative:
                shutil.copyfile(source, staging / relative)
            records[key] = {**dict(zip(IDENTITY, key)), 'file': relative, 'bytes': size,
                            'sha256': checksum, 'source': index['url'] if index else input_sources.get(checksum, 'local-build-input')}
            if number % 100 == 0:
                print(f'Captured {number}/{len(needed)} packages', flush=True)
        (staging / 'download.partial').unlink(missing_ok=True)
        finalize_repository(staging, selected, records, controls)
        staging.rename(output)
        print('Verified frozen APT repository: ' + str(output), flush=True)
    except BaseException:
        shutil.rmtree(staging)
        raise


def absent_removal_plan(vendor, lock):
    """Normalize only the pinned removal of a package absent from every lock set."""
    path = Path(vendor) / ABSENT_REMOVAL_FILE
    if path.is_symlink() or not path.is_file() or digest(path) != ABSENT_REMOVAL_SOURCE_SHA256:
        raise ValueError('Pinned vendor firmware removal request differs')
    names = {item['Package'] for item in lock['packages']}
    names.update(row[0] for rows in lock['stages'].values() for row in rows)
    names.update(row[0] for row in lock.get('installed', []))
    if ABSENT_REMOVAL_PACKAGE in {name.split(':', 1)[0] for name in names}:
        raise ValueError('Frozen removal target appears in a locked candidate/inventory')
    source = path.read_text()
    token = ABSENT_REMOVAL_PACKAGE + '-'
    if source.split().count(token) != 1:
        raise ValueError('Pinned vendor firmware removal selector differs')
    return source.replace(' ' + token, '', 1)


def configure_vendor(vendor, repository):
    """Patch a prepared tree to use only a guest-loopback frozen APT server."""
    vendor, repository = Path(vendor), Path(repository)
    lock = verify_repository(repository)
    normalized_removal = absent_removal_plan(vendor, lock)
    runtime = vendor / 'tyrell-runtime-apt'
    runtime.mkdir()
    for name in ['debian.sources', 'raspi.sources']:
        source = vendor / 'stage0/00-configure-apt/files' / name
        (runtime / name).write_text(source.read_text().replace('RELEASE', 'trixie'))
    common = vendor / 'scripts/common'
    text = common.read_text()
    marker = 'BOOTSTRAP_ARGS+=(--arch arm64)'
    if text.count(marker) != 1:
        raise ValueError('Unknown vendor debootstrap arguments')
    common.write_text(text.replace(marker, marker + '\n\tBOOTSTRAP_ARGS+=(--no-check-gpg)', 1))
    path = vendor / 'stage0/prerun.sh'
    text = path.read_text()
    original = 'http://deb.debian.org/debian/'
    if text.count(original) != 1:
        raise ValueError('Unknown vendor bootstrap mirror')
    path.write_text(text.replace(original, '"${TYRELL_APT_BASE:?Frozen APT server missing}/"'))
    configure = '''
# Only the checksum-verified guest-loopback repository is available to APT.
rm -f "$ROOTFS_DIR/etc/apt/sources.list.d/"*.list "$ROOTFS_DIR/etc/apt/sources.list.d/"*.sources
mkdir -p "$ROOTFS_DIR/etc/apt/sources.list.d"
printf 'deb [trusted=yes] %s %s main\\n' "${TYRELL_APT_BASE:?Frozen APT server missing}" STAGE > "$ROOTFS_DIR/etc/apt/sources.list"
rm -f "$ROOTFS_DIR/var/lib/apt/lists/"*Packages* "$ROOTFS_DIR/var/lib/apt/lists/"*Release*
on_chroot << 'TYRELL_FROZEN_APT'
set -e
apt-get update
TYRELL_FROZEN_APT
'''
    path = vendor / 'stage0/00-configure-apt/00-run.sh'
    path.write_text('#!/bin/bash -e\n'
                   'install -m 644 files/raspberrypi-archive-keyring.pgp "$ROOTFS_DIR/usr/share/keyrings/"\n'
                   + configure.replace('STAGE', 'stage0').replace('apt-get update',
                     'dpkg --add-architecture armhf\napt-get update'))
    for stage in STAGES[1:]:
        path = vendor / stage / 'prerun.sh'
        guard = ABSENT_REMOVAL_GUARD if stage == 'stage2' else ''
        path.write_text(path.read_text() + '\n' + guard + configure.replace('STAGE', stage))
    (vendor / ABSENT_REMOVAL_FILE).write_text(normalized_removal)
    normalization = {'schema': 1, 'kind': 'tyrell-frozen-removal-normalization',
        'package': ABSENT_REMOVAL_PACKAGE, 'request': ABSENT_REMOVAL_PACKAGE + '-',
        'action': 'no-op-removal-of-absent-package', 'apt_lock_sha256': digest(repository / 'apt-lock.json'),
        'absent_from': ['package_records', 'all_stage_candidates', 'installed_inventory_when_present'],
        'source': {'path': ABSENT_REMOVAL_FILE, 'before_sha256': ABSENT_REMOVAL_SOURCE_SHA256,
                   'after_sha256': digest(vendor / ABSENT_REMOVAL_FILE)},
        'runtime_absence_guard': {'path': 'stage2/prerun.sh', 'sha256': digest(vendor / 'stage2/prerun.sh')}}
    (vendor / ABSENT_REMOVAL_RECORD).write_text(json.dumps(normalization, indent=2) + '\n')
    # Eliminate the direct kernel pool download too; hashes remain the vendor's.
    firmware = vendor / 'stage0/02-firmware/01-run.sh'
    text = firmware.read_text()
    source = '"https://archive.raspberrypi.com/debian/pool/main/l/linux/${filename}"'
    if text.count(source) != 1:
        raise ValueError('Unknown vendor kernel download URL')
    firmware.write_text(text.replace(source, '"${TYRELL_APT_BASE:?}/pool/${checksum}.deb"'))
    export = vendor / 'export-image/02-set-sources/01-run.sh'
    export.write_text(export.read_text() + '''
# Build package resolution is finished. Runtime updates deliberately return to
# vendor repositories; compatibility holds/preferences remain installed.
true > "$ROOTFS_DIR/etc/apt/sources.list"
install -m 0644 "$BASE_DIR/tyrell-runtime-apt/debian.sources" "$ROOTFS_DIR/etc/apt/sources.list.d/debian.sources"
install -m 0644 "$BASE_DIR/tyrell-runtime-apt/raspi.sources" "$ROOTFS_DIR/etc/apt/sources.list.d/raspi.sources"
printf '%s\\n' 'deb [trusted=yes] file:/usr/local/share/tyrell/camera-repo ./' > "$ROOTFS_DIR/etc/apt/sources.list.d/tyrell-camera.list"
find "$ROOTFS_DIR/var/lib/apt/lists" -type f -delete
''')
    # Store selection metadata; configure scripts never touch host APT sources.
    (vendor / 'tyrell-apt-lock.json').write_text(json.dumps(lock, indent=2) + '\n')
    report_path = vendor / 'tyrell-prepared.json'
    report = json.loads(report_path.read_text())
    changed = ['scripts/common', 'stage0/prerun.sh', 'stage0/00-configure-apt/00-run.sh',
               'stage0/02-firmware/01-run.sh', 'export-image/02-set-sources/01-run.sh',
               'tyrell-runtime-apt/debian.sources', 'tyrell-runtime-apt/raspi.sources',
               'tyrell-apt-lock.json', ABSENT_REMOVAL_FILE, ABSENT_REMOVAL_RECORD,
               *(stage + '/prerun.sh' for stage in STAGES[1:])]
    report['prepared_files_sha256'].update({name: digest(vendor / name) for name in changed})
    report['apt_profile'] = 'frozen'
    report['apt_lock_sha256'] = digest(repository / 'apt-lock.json')
    report_path.write_text(json.dumps(report, indent=2) + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('capture')
    command.add_argument('--work-dir', type=Path, required=True)
    command.add_argument('--assets-dir', type=Path, required=True)
    command.add_argument('--output', type=Path, required=True)
    command = commands.add_parser('verify')
    command.add_argument('repository', type=Path)
    command = commands.add_parser('materialize')
    command.add_argument('--lock', type=Path, required=True)
    command.add_argument('--output', type=Path, required=True)
    command.add_argument('--assets-dir', type=Path, action='append', default=[])
    command = commands.add_parser('configure')
    command.add_argument('vendor', type=Path)
    command.add_argument('repository', type=Path)
    command = commands.add_parser('serve')
    command.add_argument('repository', type=Path)
    command.add_argument('--port-file', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'capture':
        # The normal builder creates this user-owned lock outside sticky /tmp;
        # root opens the existing inode without O_CREAT/O_TRUNC.
        with Path('/var/tmp/tyrell-locks/builder.lock').open('rb') as lease:
            fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            capture(args.work_dir, args.assets_dir, args.output)
    elif args.command == 'verify':
        lock = verify_repository(args.repository)
        print('Verified ' + str(len(lock['packages'])) + ' frozen package identities')
    elif args.command == 'materialize':
        materialize(args.lock, args.output, args.assets_dir)
    elif args.command == 'configure':
        configure_vendor(args.vendor, args.repository)
    else:
        handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                    directory=str(args.repository.resolve()))
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        args.port_file.write_text(str(server.server_port) + '\n')
        server.serve_forever()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit('APT freeze: ' + str(error))
