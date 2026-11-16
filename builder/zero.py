#!/usr/bin/env python3
"""Read-only Zero profile inspection and isolated ARM64 foundation builds."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import runpy
import shutil
import subprocess
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {'session': 'tyrell-zero-session', 'power': 'tyrell-zero-power-policy'}
MAX_EXPORT = 64 * 1024**2


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def regular(path, root):
    path, root = Path(path), Path(root)
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError('Missing or unsafe build input: ' + str(path))
    for parent in path.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise RuntimeError('Symlinked build input parent: ' + str(parent))
    return path


def generated_path(path, root=None):
    """Reject redirects anywhere inside a generated workspace namespace."""
    root = ROOT if root is None else Path(root)
    path = Path(path).absolute()
    try:
        parts = path.relative_to(root.absolute()).parts
    except ValueError as error:
        raise RuntimeError('Generated path must be inside the workspace') from error
    if '..' in parts:
        raise RuntimeError('Generated path contains parent traversal')
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError('Symlinked generated path: ' + str(current))
    return path


def bundle(job, selected):
    lock_path = regular(ROOT / 'locks/tyrell-zero-build-inputs.json', ROOT)
    lock = json.loads(lock_path.read_text())
    if lock.get('schema_version') != 1 or type(lock.get('source_date_epoch')) is not int:
        raise RuntimeError('Unsupported Zero foundation build lock')
    files = [lock_path]
    for name in ['builder/zero.py', 'builder/zero-guest.py', 'builder/build-zero-packages.sh',
                 'image/contracts/tyrell-zero-power.json']:
        files.append(regular(ROOT / name, ROOT))
    for package in selected:
        base = ROOT / 'packages' / package
        regular(base / 'build-package.py', ROOT)
        for path in sorted(base.rglob('*')):
            if any(p in {'__pycache__', '.git', 'build', 'dist'} for p in path.relative_to(base).parts):
                continue
            if path.is_symlink():
                raise RuntimeError('Symlink in package source: ' + str(path))
            if path.is_file():
                if path.name.startswith('.env') or path.suffix in {'.pyc', '.pyo', '.deb'}:
                    if path.name.startswith('.env'):
                        raise RuntimeError('Credentials are not build inputs')
                    continue
                files.append(regular(path, ROOT))
    hashes = {str(p.relative_to(ROOT)): digest(p) for p in sorted(set(files))}
    source_hash = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    manifest = {'schema_version': 1, 'milestone': 'foundation-only',
                'selected_packages': selected, 'source_files': hashes,
                'source_sha256': source_hash, 'source_date_epoch': lock['source_date_epoch'],
                'expected_versions': {p: lock['packages'][p] for p in selected},
                'device_access': False, 'image_build': False,
                'installation': 'Isolated disposable dpkg roots only; dependencies not certified.'}
    manifest_path = job / 'source-manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    archive = job / 'input.tar'

    def normalize(info):
        info.uid = info.gid = 0
        info.uname = info.gname = 'root'
        info.mtime = lock['source_date_epoch']
        return info

    with tarfile.open(archive, 'w') as tar:
        for path in sorted(set(files)):
            tar.add(path, arcname=str(path.relative_to(ROOT)), recursive=False, filter=normalize)
        tar.add(manifest_path, arcname='source-manifest.json', filter=normalize)
    return archive, manifest


def extract_export(archive, destination):
    with tarfile.open(archive) as tar:
        total, names = 0, set()
        for member in tar.getmembers():
            p = PurePosixPath(member.name)
            canonical = str(p)
            if p.is_absolute() or '..' in p.parts or canonical in names \
                    or (canonical == '.' and not member.isdir()):
                raise RuntimeError('Unsafe or duplicate export path: ' + member.name)
            names.add(canonical)
            if not (member.isfile() or member.isdir()) or member.issym() or member.islnk():
                raise RuntimeError('Unsafe export member: ' + member.name)
            total += member.size
        if total > MAX_EXPORT:
            raise RuntimeError('Unexpectedly large Zero package export')
        tar.extractall(destination, filter='data')
    sums = destination / 'SHA256SUMS'
    if not sums.is_file():
        raise RuntimeError('Missing package export checksums')
    checked = set()
    for line in sums.read_text().splitlines():
        expected, name = line.split('  ', 1)
        p = PurePosixPath(name)
        canonical = str(p)
        if p.is_absolute() or '..' in p.parts or canonical in checked:
            raise RuntimeError('Unsafe checksum path')
        checked.add(canonical)
        if digest(regular(destination / name, destination)) != expected:
            raise RuntimeError('Export checksum mismatch: ' + name)
    actual = {str(p.relative_to(destination)) for p in destination.rglob('*') if p.is_file()}
    if actual != checked | {'SHA256SUMS'}:
        raise RuntimeError('Export checksum inventory is incomplete')


def build_packages(which):
    api = runpy.run_path(str(ROOT / 'scripts/builder'))
    selected = list(PACKAGES.values()) if which == 'all' else [PACKAGES[which]]
    if not shutil.which('limactl'):
        raise RuntimeError('Install Lima 2.x before building Zero packages')
    with api['shared_lock']():
        api['up']()
        api['assert_guest_idle']()
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        job_id = stamp + '-' + uuid.uuid4().hex[:8]
        base = generated_path(ROOT / 'build/zero-packages/jobs')
        base.mkdir(parents=True, exist_ok=True)
        job = base / job_id
        job.mkdir(mode=0o700)
        archive, manifest = bundle(job, selected)
        guest_job = '/var/tmp/tyrell-zero-packages/' + job_id
        guest_input, guest_output = guest_job + '/input', guest_job + '/output'
        free = api['guest']('python3', '-c', "import shutil;print(shutil.disk_usage('/var/tmp').free)",
                            stdout=subprocess.PIPE, text=True)
        if int(free.stdout.strip()) < 256 * 1024**2 + 3 * archive.stat().st_size:
            raise RuntimeError('Insufficient guest space for isolated package build')
        api['require_space'](job, 3 * archive.stat().st_size + MAX_EXPORT, 'Zero package job')
        api['guest']('mkdir', '-p', guest_input)
        api['lima']('copy', '--backend=scp', str(archive), 'tyrell-build:' + guest_job + '/input.tar')
        api['guest']('python3', '-c',
                     'import hashlib,pathlib,sys;assert hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()==sys.argv[2]',
                     guest_job + '/input.tar', digest(archive))
        api['guest']('tar', '-xf', guest_job + '/input.tar', '-C', guest_input)
        log_path = job / 'build.log'
        with log_path.open('w') as log:
            try:
                api['guest']('bash', guest_input + '/builder/build-zero-packages.sh', guest_input,
                             guest_output, stdout=log, stderr=subprocess.STDOUT)
            except subprocess.CalledProcessError as error:
                (job / 'result.json').write_text(json.dumps({
                    'job_id': job_id, 'status': 'failed', 'source_sha256': manifest['source_sha256'],
                    'build_log': str(log_path), 'device_access': False}, indent=2) + '\n')
                raise RuntimeError('Zero package build failed; retained log: ' + str(log_path)) from error
        output_tar = job / 'output.tar'
        with output_tar.open('wb') as stream:
            api['guest']('tar', '-C', guest_output, '-cf', '-', '.', stdout=stream)
        stage = job / 'export'
        stage.mkdir()
        extract_export(output_tar, stage)
        result = json.loads((stage / 'manifest.json').read_text())
        if result.get('source_sha256') != manifest['source_sha256'] or result.get('selected_packages') != selected:
            raise RuntimeError('Export belongs to a different source build')
        output_base = generated_path(ROOT / 'artifacts/tyrell-zero')
        output_base.mkdir(parents=True, exist_ok=True)
        destination = output_base / job_id
        stage.rename(destination)
        latest = output_base / 'LATEST.json'
        if latest.is_symlink():
            raise RuntimeError('Symlinked latest package result')
        temporary = output_base / ('.latest-' + job_id + '.json')
        temporary.write_text(json.dumps({'job_id': job_id, 'source_sha256': manifest['source_sha256'],
                                         'artifact_directory': str(destination.relative_to(ROOT)),
                                         'milestone': 'foundation-only'}, indent=2) + '\n')
        temporary.replace(latest)
        (job / 'result.json').write_text(json.dumps({
            'job_id': job_id, 'status': 'passed', 'source_sha256': manifest['source_sha256'],
            'artifacts': str(destination), 'device_access': False}, indent=2) + '\n')
        print(json.dumps({'job_id': job_id, 'artifacts': str(destination),
                          'milestone': 'foundation-only', 'device_access': False}, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    check = commands.add_parser('check', help='Read-only profile structure and missing-gate report')
    check.add_argument('--profile', type=Path, default=ROOT / 'image/profiles/tyrell-zero.json')
    build = commands.add_parser('build-packages', help='Build/test foundation packages in existing ARM64 VM')
    build.add_argument('--package', choices=['all', *PACKAGES], default='all')
    commands.add_parser('build-wizard', help='Offline native ARM64 Wizard build from locked vendor sources')
    integrated = commands.add_parser('build-integration', help='Build and qualify native Wizard/Store packages in disposable roots')
    integrated.add_argument('--wizard-artifacts', type=Path, required=True)
    integrated.add_argument('--vendor-deb', type=Path)
    args = parser.parse_args(argv)
    if args.action == 'check':
        api = runpy.run_path(str(ROOT / 'image/profiles.py'))
        print(json.dumps(api['inspect_profile'](args.profile, root=ROOT), indent=2))
    elif args.action == 'build-packages':
        build_packages(args.package)
    else:
        command = 'build-wizard' if args.action == 'build-wizard' else 'build-packages'
        extra = [] if command == 'build-wizard' else ['--wizard-artifacts', str(args.wizard_artifacts)]
        if command != 'build-wizard' and args.vendor_deb:
            extra += ['--vendor-deb', str(args.vendor_deb)]
        subprocess.run([sys.executable, str(ROOT / 'builder/zero-integration.py'), command, *extra], check=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print('zero: ' + str(error), file=sys.stderr)
        sys.exit(1)
