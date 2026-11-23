#!/usr/bin/env python3
"""Three-stage Zero package locks and offline repository replay.

Legacy five-stage capture remains unchanged. This module loads a private copy of
its audited index/byte verifier with only the Zero stage set substituted. Custom
files-only packages remain independent captured job inputs, never seed mutations.
"""
import argparse
import base64
import functools
import gzip
import hashlib
import http.server
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import runpy
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STAGES = ('stage0', 'stage1', 'stage2')
DIGEST = re.compile(r'[0-9a-f]{64}\Z')
CUSTOM = {'tyrell-status', 'tyrell-zero-session', 'tyrell-zero-store', 'tyrell-zero-power-policy'}
FORBIDDEN = {'lightdm', 'labwc', 'xserver-xorg', 'xserver-xorg-core', 'wayfire'}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def legacy_api():
    api = runpy.run_path(str(Path(__file__).with_name('apt-lock.py')))
    api['verify_repository'].__globals__['STAGES'] = STAGES
    return api


def validate(lock):
    if (not isinstance(lock, dict) or type(lock.get('schema')) is not int
            or lock.get('complete') is not True or any(lock.get(k) != v for k, v in {
            'schema': 1, 'profile': 'tyrell-zero', 'kind': 'tyrell-zero-apt',
            'architecture': 'arm64', 'complete': True}.items())):
        raise ValueError('Unsupported/incomplete Zero APT lock')
    if set(lock.get('stages', {})) != set(STAGES):
        raise ValueError('Zero APT lock requires exactly stages0-2')
    packages = lock.get('packages', [])
    identities = set(); names = set()
    for item in packages:
        key = tuple(item.get(k) for k in ['Package', 'Version', 'Architecture'])
        if (not all(isinstance(x, str) and x for x in key) or key in identities
                or key[2] not in {'arm64', 'all'} or not DIGEST.fullmatch(item.get('sha256', ''))
                or item.get('file') != 'pool/' + item['sha256'] + '.deb'
                or type(item.get('bytes')) is not int or item['bytes'] <= 0 or key[0] in CUSTOM):
            raise ValueError('Invalid/duplicate Zero package record')
        identities.add(key); names.add(key[0])
    if not identities or 'greetd' not in names or names & FORBIDDEN:
        raise ValueError('Zero APT lock omits greetd or retains desktop server roots')
    for stage, rows in lock['stages'].items():
        if (not rows or len(rows) != len({tuple(x) for x in rows})
                or len(rows) != len({(x[0], x[2]) for x in rows})
                or any(tuple(x) not in identities for x in rows)):
            raise ValueError('Invalid Zero stage selection: ' + stage)
    installed = lock.get('installed', [])
    if (not isinstance(installed, list) or not installed or any(not isinstance(x, list) or len(x) != 3 for x in installed)
            or len(installed) != len({x[0] for x in installed}) or any(tuple(x) not in identities for x in installed)):
        raise ValueError('Invalid exact Zero installed closure')
    if not set(map(tuple, installed)).issubset(set(map(tuple, lock['stages']['stage2']))):
        raise ValueError('Final installed closure is absent from stage2 candidates')
    evidence = lock.get('solver_evidence', {})
    if evidence.get('apt_check') != 'passed' or evidence.get('dpkg_audit') != 'clean' or not DIGEST.fullmatch(evidence.get('receipt_sha256', '')):
        raise ValueError('Missing real isolated-root solver evidence')
    auth = lock.get('greetd_authentication', {})
    if (auth.get('package_sha256_verified') is not True or not DIGEST.fullmatch(auth.get('inrelease_sha256', ''))
            or not DIGEST.fullmatch(auth.get('index_sha256', '')) or not DIGEST.fullmatch(auth.get('keyring_sha256', ''))):
        raise ValueError('Missing authenticated greetd metadata chain')
    return lock


def verify_repository(repository):
    repository = Path(repository)
    validate(json.loads((repository / 'apt-lock.json').read_text()))
    return legacy_api()['verify_repository'](repository)


def configure_vendor(vendor, repository):
    verify_repository(repository)
    return legacy_api()['configure_vendor'](Path(vendor), Path(repository))


def materialize(lock_path, output, asset_dirs=()):
    """Replay only preverified local package bytes; never fetch from the network."""
    lock_path, output = Path(lock_path), Path(output)
    lock = validate(json.loads(lock_path.read_text()))
    if output.exists() or output.is_symlink():
        raise ValueError('Preserving existing Zero repository')
    available = {}
    for directory in asset_dirs:
        directory = Path(directory)
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError('Package asset directory must be a real directory')
        for path in directory.rglob('*.deb'):
            if path.is_file() and not path.is_symlink():
                available[sha(path)] = path
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.zero-apt-', dir=output.parent))
    try:
        for record in lock['packages']:
            source = available.get(record['sha256'])
            if source is None:
                raise ValueError('Supply exact locally cached bytes for ' + record['Package'])
            destination = staging / record['file']; destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        for name, data in lock['repository_metadata'].items():
            if not legacy_api()['safe_relative'](name) or not name.startswith('dists/'):
                raise ValueError('Unsafe Zero repository metadata')
            destination = staging / name; destination.parent.mkdir(parents=True, exist_ok=True)
            expected = lock['files'].get(name, {})
            size = expected.get('bytes')
            if type(size) is not int or not 0 <= size <= 16 * 1024 * 1024:
                raise ValueError('Invalid/bounded Zero repository metadata size')
            import io
            with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(data, validate=True))) as stream:
                expanded = stream.read(size + 1)
            if len(expanded) != size or hashlib.sha256(expanded).hexdigest() != expected.get('sha256'):
                raise ValueError('Zero repository metadata size/digest differs')
            destination.write_bytes(expanded)
        (staging / 'apt-lock.json').write_bytes(lock_path.read_bytes())
        verify_repository(staging); staging.rename(output)
    except BaseException:
        shutil.rmtree(staging); raise
    return output


def capture(seed_repository, installed_path, authentication_path, receipt_path,
            output, extra_debs=()):
    """Freeze observed installed identities, keeping the five-stage seed intact.

    The supplied receipt is produced only after real isolated dpkg configuration
    and APT check. This operation verifies every copied byte and builds indices;
    it does not invent a solver or hardware-acceptance result.
    """
    seed_repository, output = Path(seed_repository), Path(output)
    original = runpy.run_path(str(Path(__file__).with_name('apt-lock.py')))
    seed = original['verify_repository'](seed_repository)
    installed = json.loads(Path(installed_path).read_text())
    authentication = json.loads(Path(authentication_path).read_text())
    receipt = json.loads(Path(receipt_path).read_text())
    if (receipt.get('apt_check') != 'passed' or receipt.get('dpkg_audit') != 'clean'
            or receipt.get('installed_sha256') != sha(installed_path)
            or receipt.get('seed_apt_lock_sha256') != sha(seed_repository / 'apt-lock.json')
            or receipt.get('greetd_authentication_sha256') != sha(authentication_path)):
        raise ValueError('Isolated-root qualification is not bound to selected inputs')
    if output.exists() or output.is_symlink():
        raise ValueError('Preserving existing Zero repository')
    records = {tuple(p[k] for k in ['Package', 'Version', 'Architecture']): dict(p) for p in seed['packages']}
    sources = {key: seed_repository / p['file'] for key, p in records.items()}
    for path in map(Path, extra_debs):
        if path.is_symlink() or not path.is_file():
            raise ValueError('Extra package must be a regular file')
        fields = original['deb_fields'](path)
        key = tuple(fields[k] for k in ['Package', 'Version', 'Architecture'])
        checksum = sha(path)
        if key != ('greetd', '0.10.3-4', 'arm64') or checksum != authentication.get('package', {}).get('SHA256'):
            raise ValueError('Extra package lacks authenticated pinned greetd identity')
        records[key] = {'Package': key[0], 'Version': key[1], 'Architecture': key[2],
                        'file': 'pool/' + checksum + '.deb', 'bytes': path.stat().st_size,
                        'sha256': checksum, 'source': 'https://deb.debian.org/debian/' + authentication['package']['Filename']}
        sources[key] = path
    selected = {stage: {tuple(x) for x in seed['stages'][stage]} for stage in STAGES[:2]}
    selected['stage2'] = {tuple(x) for x in installed}
    required = set.union(*selected.values())
    if any(key not in records for key in required):
        raise ValueError('Observed package lacks locked retained bytes')
    records = {key: records[key] for key in sorted(required)}
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.zero-capture-', dir=output.parent))
    try:
        controls = {}
        for key, record in records.items():
            source = sources[key]
            if source.stat().st_size != record['bytes'] or sha(source) != record['sha256']:
                raise ValueError('Retained package changed: ' + key[0])
            destination = staging / record['file']; destination.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, destination)
            controls[key] = original['deb_fields'](destination)
            if tuple(controls[key][k] for k in ['Package', 'Version', 'Architecture']) != key:
                raise ValueError('Retained package control identity differs: ' + key[0])
        api = legacy_api(); api['write_indices'](staging, selected, records, controls)
        lock = {'schema': 1, 'kind': 'tyrell-zero-apt', 'profile': 'tyrell-zero', 'complete': True,
                'architecture': 'arm64', 'created_utc': datetime.now(timezone.utc).isoformat(),
                'scope': 'Observed isolated stage0-2 runtime closure; custom packages are separate job inputs.',
                'trust': 'Immutable seed byte lock plus signed Debian Release/Packages authentication for greetd.',
                'seed_apt_lock_sha256': sha(seed_repository / 'apt-lock.json'),
                'stages': {stage: [list(x) for x in sorted(rows)] for stage, rows in selected.items()},
                'installed': installed, 'packages': list(records.values()),
                'solver_evidence': {'apt_check': 'passed', 'dpkg_audit': 'clean',
                                    'receipt_sha256': sha(receipt_path), 'receipt': receipt},
                'greetd_authentication': authentication,
                'repository_metadata': {str(p.relative_to(staging)): base64.b64encode(gzip.compress(p.read_bytes(), mtime=0)).decode()
                                        for p in sorted((staging / 'dists').rglob('*')) if p.is_file()},
                'files': {str(p.relative_to(staging)): {'bytes': p.stat().st_size, 'sha256': sha(p)}
                          for p in sorted(staging.rglob('*')) if p.is_file()}}
        (staging / 'apt-lock.json').write_text(json.dumps(lock, indent=2) + '\n')
        verify_repository(staging); staging.rename(output)
        return lock
    except BaseException:
        shutil.rmtree(staging); raise


def validate_inputs(root=ROOT):
    root = Path(root)
    paths = {name: root / 'locks' / ('tyrell-zero-' + name + '.json') for name in ['apt', 'apps', 'native-compat']}
    apt = validate(json.loads(paths['apt'].read_text()))
    apps = json.loads(paths['apps'].read_text()); native = json.loads(paths['native-compat'].read_text())
    if any(doc.get('schema') != 1 or doc.get('profile') != 'tyrell-zero' or doc.get('complete') is not True for doc in [apps, native]):
        raise ValueError('Incomplete Zero apps/native compatibility lock')
    if {item.get('Package') for item in apps.get('packages', [])} != CUSTOM:
        raise ValueError('Zero custom package set differs')
    for item in apps['packages']:
        relative = Path(item.get('input_path', ''))
        if not str(relative) or relative.is_absolute() or '..' in relative.parts or item.get('filename') != relative.name:
            raise ValueError('Unsafe custom package input path')
        path = root / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size != item['bytes'] or sha(path) != item['sha256']:
            raise ValueError('Custom package bytes unavailable/changed: ' + item['Package'])
    def evidence_record(item):
        relative = Path(item.get('input_path', ''))
        if (not relative.parts or relative.is_absolute() or '..' in relative.parts
                or item.get('filename') != relative.name or not DIGEST.fullmatch(item.get('sha256', ''))):
            raise ValueError('Unsafe Zero evidence record')
        path = root / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size != item['bytes'] or sha(path) != item['sha256']:
            raise ValueError('Zero qualification evidence unavailable/changed')
        return path
    qualification_path = evidence_record(apps.get('qualification', {}))
    qualification = json.loads(qualification_path.read_text())
    if (qualification.get('scope') != 'pre-image-integration' or qualification.get('status') != 'passed'
            or qualification.get('physical_hardware_qualified') is not False
            or qualification.get('suites') != {'session': 'passed', 'wizard': 'passed', 'store': 'passed'}
            or native.get('source_qualification_sha256') != sha(qualification_path)
            or native.get('assets') != qualification.get('assets')
            or native.get('runtime_files') != qualification.get('runtime_files')):
        raise ValueError('Native compatibility differs from actual integration qualification')
    for item in apps.get('evidence', []):
        evidence_record(item)
    return {'apt': apt, 'apps': apps, 'native_compat': native,
            'locks_sha256': {path.name: sha(path) for path in paths.values()}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('verify'); p.add_argument('repository', type=Path)
    p = commands.add_parser('configure'); p.add_argument('vendor', type=Path); p.add_argument('repository', type=Path)
    p = commands.add_parser('materialize'); p.add_argument('--lock', type=Path, required=True); p.add_argument('--output', type=Path, required=True); p.add_argument('--assets-dir', action='append', default=[], type=Path)
    p = commands.add_parser('serve'); p.add_argument('repository', type=Path); p.add_argument('--port-file', type=Path, required=True)
    p = commands.add_parser('capture')
    for name in ['seed-repository', 'installed', 'authentication', 'receipt', 'output']:
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--extra-deb', type=Path, action='append', default=[])
    args = parser.parse_args(argv)
    if args.command == 'verify':
        print('Verified', len(verify_repository(args.repository)['packages']), 'Zero package identities')
    elif args.command == 'configure':
        configure_vendor(args.vendor, args.repository)
    elif args.command == 'materialize':
        materialize(args.lock, args.output, args.assets_dir)
    elif args.command == 'capture':
        capture(args.seed_repository, args.installed, args.authentication, args.receipt, args.output, args.extra_deb)
    else:
        verify_repository(args.repository)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(args.repository.resolve()))
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        args.port_file.write_text(str(server.server_port) + '\n'); server.serve_forever()


if __name__ == '__main__':
    main()
