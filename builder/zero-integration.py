#!/usr/bin/env python3
"""Build vendor-backed Zero integration in the existing ARM64 VM, never the handheld."""
import argparse
import datetime
import hashlib
import io
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VENDOR_DEB = ROOT / 'build/zero-vendor/downloads/applaunch_0.6.111-m5stack1_arm64.deb'


def baseline_assets(baseline):
    api = runpy.run_path(str(ROOT / 'scripts/builder'))
    safe = runpy.run_path(str(ROOT / 'builder/zero.py'))
    if baseline.is_symlink() or not baseline.is_file() or api['digest'](baseline) != '8b1097120bd1abb3e264674e5f25a7bc715a2495ecd2cc008002b6dcc277c2b2':
        raise RuntimeError('Exact cached vendor applaunch package required')
    members = subprocess.check_output(['ar', 't', str(baseline)], text=True).splitlines()
    data = [name for name in members if name.startswith('data.tar')]
    if len(data) != 1:
        raise RuntimeError('Invalid vendor Debian archive')
    payload = subprocess.check_output(['ar', 'p', str(baseline), data[0]])
    folder = safe['generated_path'](ROOT / 'build/zero-integration/vendor' / api['digest'](baseline))
    folder.mkdir(parents=True, exist_ok=True)
    assets = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:*') as archive:
        for name, identity in {'M5CardputerZero-APPLaunch':'e4e7972155601a65689d86a9bc9cce247a6c0ca204a97e6daee84440f38fe7e7',
                               'M5CardputerZero-AppStore':'cb1a9db6224c90e3b4efdfd6da2936aa4d6e535c40fede64517ba7b09aa661aa'}.items():
            expected = 'usr/share/APPLaunch/bin/' + name
            entries = [m for m in archive.getmembers() if m.name.removeprefix('./') == expected]
            if len(entries) != 1 or not entries[0].isfile() or entries[0].size > 32 * 1024**2:
                raise RuntimeError('Invalid vendor executable member')
            content = archive.extractfile(entries[0]).read()
            if hashlib.sha256(content).hexdigest() != identity:
                raise RuntimeError('Vendor executable identity mismatch')
            target = folder / name
            if target.exists() or target.is_symlink():
                if api['digest'](safe['regular'](target, ROOT)) != identity:
                    raise RuntimeError('Cached vendor executable changed')
            else:
                with target.open('xb') as stream: stream.write(content)
                target.chmod(0o755)
            assets['vendor/' + name] = target
    assets['vendor/applaunch.deb'] = baseline
    return assets


def wizard_inputs():
    safe = runpy.run_path(str(ROOT / 'builder/zero.py'))
    vendor = runpy.run_path(str(ROOT / 'builder/zero-vendor.py'))
    lock = vendor['load_lock']()
    vendor['verify_inputs'](lock, ROOT / 'build/zero-vendor/downloads')
    names = ['builder/zero-vendor.py', 'builder/zero-native-guest.py', 'builder/build-zero-native.sh',
             'locks/tyrell-zero-vendor-inputs.json']
    for path in (ROOT / 'packages/tyrell-zero-session/wizard').rglob('*'):
        if '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        if path.name.startswith('.env') or path.is_symlink():
            raise RuntimeError('Unsafe native Wizard source')
        if path.is_file():
            names.append(str(path.relative_to(ROOT)))
    names += ['build/zero-vendor/downloads/' + e['filename'] for e in lock['archives']]
    paths = {name:safe['regular'](ROOT / name, ROOT) for name in sorted(names)}
    return paths


def wizard_build():
    return execute(wizard_inputs(), 'wizard')


def package_build(wizard_artifacts, vendor_deb=DEFAULT_VENDOR_DEB):
    api = runpy.run_path(str(ROOT / 'scripts/builder'))
    safe = runpy.run_path(str(ROOT / 'builder/zero.py'))
    wizard_artifacts = safe['generated_path'](wizard_artifacts)
    receipt_path = safe['regular'](wizard_artifacts / 'manifest.json', ROOT)
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get('schema') != 1 or receipt.get('status') != 'passed'
            or receipt.get('architecture') != 'arm64' or receipt.get('network_disabled') is not True
            or receipt.get('device_access') is not False or receipt.get('milestone') != 'native-wizard-build'):
        raise RuntimeError('Verified native Wizard receipt required')
    expected = {name:api['digest'](path) for name,path in sorted(wizard_inputs().items())}
    source_identity = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
    if receipt.get('source_files') != expected or receipt.get('source_sha256') != source_identity:
        raise RuntimeError('Native Wizard input inventory changed; rebuild before packaging')
    patch = json.loads((ROOT / 'packages/tyrell-zero-session/wizard/patches/identity.json').read_text())
    config_api = runpy.run_path(str(ROOT / 'packages/tyrell-zero-session/wizard/native_config.py'))
    config = receipt.get('native_config', {})
    if (receipt.get('patch_identity') != patch or config.get('inputs') != config_api['INPUT_SHA256']
            or config.get('sha256') != '1df551d93c93925eeaa1050fb1bb71c7e82813464e0a27fe6be3dd201075344b'
            or config.get('config') != config_api['OUTPUT'] or config.get('cross_toolchain') is not False):
        raise RuntimeError('Native Wizard patch/configuration identity mismatch')
    wizard = safe['regular'](wizard_artifacts / 'LaunchWizard', ROOT)
    if api['digest'](wizard) != receipt['wizard_sha256']:
        raise RuntimeError('Native Wizard artifact changed')
    names = ['builder/zero-integration-guest.py', 'builder/build-zero-native.sh',
             'builder/zero-store-lifecycle.py', 'locks/tyrell-zero-build-inputs.json']
    for package in ['tyrell-zero-session', 'tyrell-zero-store']:
        for path in sorted((ROOT / 'packages' / package).rglob('*')):
            if any(p in {'__pycache__', '.git', 'build', 'dist'} for p in path.relative_to(ROOT / 'packages' / package).parts):
                continue
            if path.is_symlink() or path.name.startswith('.env'):
                raise RuntimeError('Unsafe package source')
            if path.is_file() and path.suffix not in {'.pyc', '.pyo', '.deb'}:
                names.append(str(path.relative_to(ROOT)))
    paths = {name:safe['regular'](ROOT / name, ROOT) for name in names}
    paths.update({'wizard/LaunchWizard':wizard, 'wizard/manifest.json':receipt_path})
    for app, path in {'status':'dist/tyrell-status/0.1.0/tyrell-status_0.1.0-1_arm64.deb',
                      'video-old':'dist/tyrell-video/0.1.6/tyrell-video_0.1.6-1_arm64.deb',
                      'video-new':'dist/tyrell-video/0.1.7/tyrell-video_0.1.7-1_arm64.deb'}.items():
        paths['apps/' + app + '.deb'] = safe['regular'](ROOT / path, ROOT)
    paths.update(baseline_assets(vendor_deb))
    return execute(paths, 'integration')


def execute(paths, kind):
    api = runpy.run_path(str(ROOT / 'scripts/builder'))
    safe = runpy.run_path(str(ROOT / 'builder/zero.py'))
    hashes = {name:api['digest'](p) for name,p in sorted(paths.items())}
    identity = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    with api['shared_lock']():
        api['up'](); api['assert_guest_idle']()
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        job_id = stamp + '-' + uuid.uuid4().hex[:8]
        job = safe['generated_path'](ROOT / 'build/zero-native/jobs' / job_id)
        job.mkdir(parents=True, mode=0o700)
        manifest = {'schema': 1, 'source_sha256': identity, 'source_files': hashes,
                    'device_access': False, 'network_disabled': True}
        (job / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        archive = job / 'input.tar'
        with tarfile.open(archive, 'w') as tar:
            for name,path in sorted(paths.items()):
                tar.add(path, arcname=name, recursive=False)
            tar.add(job / 'source-manifest.json', arcname='source-manifest.json')
        api['require_space'](job, 3 * archive.stat().st_size + 64 * 1024**2, 'Native Wizard build')
        guest_job = '/var/tmp/tyrell-zero-native/' + job_id
        free = api['guest']('python3', '-c', 'import shutil;print(shutil.disk_usage("/var/tmp").free)', stdout=subprocess.PIPE, text=True)
        if int(free.stdout.strip()) < 2 * 1024**3 + 3 * archive.stat().st_size:
            raise RuntimeError('Insufficient ARM64 builder space')
        api['guest']('mkdir', '-p', guest_job + '/input')
        api['lima']('copy', '--backend=scp', str(archive), 'tyrell-build:' + guest_job + '/input.tar')
        api['guest']('python3', '-c', 'import hashlib,pathlib,sys;assert hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()==sys.argv[2]', guest_job + '/input.tar', api['digest'](archive))
        api['guest']('tar', '-xf', guest_job + '/input.tar', '-C', guest_job + '/input')
        with (job / 'build.log').open('w') as log:
            try:
                api['guest']('bash', guest_job + '/input/builder/build-zero-native.sh', guest_job + '/input', guest_job + '/output', kind, stdout=log, stderr=subprocess.STDOUT)
            except subprocess.CalledProcessError as error:
                raise RuntimeError('Native ' + kind + ' build failed; log: ' + str(job / 'build.log')) from error
        with (job / 'output.tar').open('wb') as stream:
            api['guest']('tar', '-C', guest_job + '/output', '-cf', '-', '.', stdout=stream)
        export = job / 'export'; export.mkdir()
        safe['extract_export'](job / 'output.tar', export)
        result = json.loads((export / 'manifest.json').read_text())
        if result.get('source_sha256') != identity or result.get('status') != 'passed':
            raise RuntimeError('Native export identity mismatch')
        if kind == 'wizard' and result.get('wizard_sha256') != api['digest'](export / 'LaunchWizard'):
            raise RuntimeError('Native Wizard binary identity mismatch')
        destination = safe['generated_path'](ROOT / 'artifacts/tyrell-zero-native' / job_id)
        destination.parent.mkdir(parents=True, exist_ok=True); export.rename(destination)
        (job / 'result.json').write_text(json.dumps({'status':'passed', 'artifacts':str(destination), 'source_sha256':identity}, indent=2) + '\n')
        print(json.dumps({'job_id':job_id, 'artifacts':str(destination), 'wizard_sha256':result['wizard_sha256']}, indent=2))


def deps(check=False):
    api = runpy.run_path(str(ROOT / 'scripts/builder'))
    names = ['ensure-zero-vendor-deps.sh', 'zero-vendor-dependencies.txt']
    packages = (ROOT / 'builder' / names[1]).read_text().splitlines()
    if not packages or len(set(packages)) != len(packages) or any(
            not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*', p) for p in packages):
        raise RuntimeError('Invalid native vendor build dependency list')
    with api['shared_lock']():
        api['up']()
        api['assert_guest_idle']()
        target = '/var/tmp/tyrell-zero-deps-' + uuid.uuid4().hex
        api['guest']('mkdir', '-m', '700', target)
        hashes = {}
        for name in names:
            p = ROOT / 'builder' / name
            if p.is_symlink() or not p.is_file():
                raise RuntimeError('Unsafe dependency provisioning input')
            hashes[name] = hashlib.sha256(p.read_bytes()).hexdigest()
            api['lima']('copy', '--backend=scp', str(p), 'tyrell-build:' + target + '/' + name)
        api['guest']('python3', '-c',
                     'import hashlib,json,pathlib,sys;p=pathlib.Path(sys.argv[1]);d=json.loads(sys.argv[2]);assert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==h for n,h in d.items())',
                     target, json.dumps(hashes))
        api['guest']('sudo', '-n', 'flock', '-n', '/var/tmp/tyrell-locks/builder.lock',
                     'bash', target + '/' + names[0], '--check' if check else 'install')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('deps').add_argument('--check', action='store_true')
    commands.add_parser('build-wizard')
    packages = commands.add_parser('build-packages')
    packages.add_argument('--wizard-artifacts', type=Path, required=True)
    packages.add_argument('--vendor-deb', type=Path, default=DEFAULT_VENDOR_DEB)
    args = parser.parse_args(argv)
    if args.action == 'deps': deps(args.check)
    elif args.action == 'build-wizard': wizard_build()
    else: package_build(args.wizard_artifacts, args.vendor_deb)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print('zero integration: ' + str(error), file=sys.stderr)
        sys.exit(1)
