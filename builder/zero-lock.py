#!/usr/bin/env python3
"""Reproduce Zero runtime locks with retained ARM64 inputs, never a device/image.

Build native integration first with builder/zero-integration.py. This command
copies the retained locked stage2 filesystem, resolves only cached packages,
checks the four exact custom packages, and exports auditable runtime maps. It
never installs into the live builder. Existing repositories/artifacts survive.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def record(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected a regular captured input: ' + str(path))
    return dict(input_path=str(path.relative_to(ROOT)), filename=path.name,
                sha256=sha(path), bytes=path.stat().st_size)


def export(api, guest_job, output, names):
    blob = api['guest']('sudo', '-n', 'tar', '-C', guest_job, '-cf', '-', *names,
                        stdout=subprocess.PIPE).stdout
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        for member in archive:
            if (Path(member.name).is_absolute() or '..' in Path(member.name).parts
                    or not member.isfile()):
                raise ValueError('Unexpected qualification export member')
        archive.extractall(output, filter='data')


def verify_custom_inputs(integration, status_deb, power_deb):
    """Authenticate all selected custom bytes before any privileged mutation."""
    native = json.loads((integration / 'manifest.json').read_text())
    if (native.get('milestone') != 'native-wizard-store-integration'
            or native.get('status') != 'passed' or native.get('architecture') != 'arm64'):
        raise ValueError('Passed native integration artifacts required')
    status = json.loads((ROOT / 'dist/tyrell-status/0.1.0/provenance.json').read_text())
    power = json.loads((power_deb.parent / 'manifest.json').read_text())
    selected = [(status_deb, 'tyrell-status', status['debian_version'], status['package_sha256']),
                (power_deb, 'tyrell-zero-power-policy', power['version'], power['package_sha256'])]
    for package, version in [('tyrell-zero-session', '0.3.1-1'), ('tyrell-zero-store', '0.1.1-1')]:
        path = integration / (package + '_' + version + '_arm64.deb')
        selected.append((path, package, version, native['packages'].get(path.name)))
    if power.get('package') != 'tyrell-zero-power-policy' or power.get('architecture') != 'arm64':
        raise ValueError('Power package build manifest identity differs')
    for path, package, version, expected in selected:
        record(path)
        if sha(path) != expected:
            raise ValueError('Custom package differs from retained provenance: ' + package)
        fields = subprocess.check_output(['dpkg-deb', '--field', str(path), 'Package', 'Version', 'Architecture'], text=True)
        actual = dict(line.split(': ', 1) for line in fields.splitlines())
        if actual != dict(Package=package, Version=version, Architecture='arm64'):
            raise ValueError('Custom package control identity differs: ' + package)
        control = subprocess.check_output(['dpkg-deb', '--ctrl-tarfile', str(path)])
        with tarfile.open(fileobj=io.BytesIO(control)) as archive:
            if any(Path(item.name).name in {'preinst', 'postinst', 'prerm', 'postrm', 'triggers', 'config'} for item in archive):
                raise ValueError('Unexpected executable maintainer hook: ' + package)
    return [path for path, _, _, _ in selected]


def apps_lock(integration, status_deb, power_deb, output):
    native_manifest = json.loads((integration / 'manifest.json').read_text())
    if (native_manifest.get('milestone') != 'native-wizard-store-integration'
            or native_manifest.get('status') != 'passed' or native_manifest.get('architecture') != 'arm64'):
        raise ValueError('Passed native integration artifacts required')
    session = integration / 'tyrell-zero-session_0.3.1-1_arm64.deb'
    store = integration / 'tyrell-zero-store_0.1.1-1_arm64.deb'
    for path in [session, store]:
        if native_manifest['packages'].get(path.name) != sha(path):
            raise ValueError('Native integration package identity changed')
    pairs = [(status_deb, ROOT / 'dist/tyrell-status/0.1.0/provenance.json'),
             (power_deb, power_deb.parent / 'manifest.json'),
             (session, integration / 'session-build-manifest.json'),
             (store, integration / 'store-build-manifest.json')]
    packages = []
    for path, manifest in pairs:
        fields = subprocess.check_output(['dpkg-deb', '--field', str(path), 'Package', 'Version', 'Architecture'], text=True)
        item = record(path); item.update(dict(line.split(': ', 1) for line in fields.splitlines()))
        item['source_manifest_sha256'] = sha(manifest); packages.append(item)
    for source, name in [(integration / 'manifest.json', 'native-build-manifest.json'),
                         (power_deb.parent / 'manifest.json', 'power-policy-build-manifest.json'),
                         (ROOT / 'dist/tyrell-status/0.1.0/provenance.json', 'status-provenance.json')]:
        shutil.copyfile(source, output / name)
    evidence = [integration / name for name in ['source-manifest.json', 'session-build-manifest.json',
                'store-build-manifest.json', 'store-lifecycle.json', 'session-tests.txt', 'store-tests.txt']]
    evidence += [output / name for name in ['native-build-manifest.json', 'power-policy-build-manifest.json',
                'status-provenance.json', 'system-qualification.json', 'greetd-authentication.json', 'native-ldd.txt',
                'custom-install.txt', 'custom-apt-check.txt', 'installed-system.json', 'installed-with-apps.json']]
    evidence += sorted((output / 'authentication').iterdir())
    return dict(schema=1, profile='tyrell-zero', complete=True,
                scope='exact-custom-package-and-source-qualification-inputs', packages=packages,
                qualification=record(integration / 'pre-image-qualification.json'),
                evidence=[record(path) for path in evidence],
                limitations=['No exported image, physical boot, hardware or battery qualification.'])


def qualify(args):
    if args.repository_output.exists() or args.repository_output.is_symlink():
        raise ValueError('Select a new repository output; preserving existing bytes')
    api = runpy.run_path(str(ROOT / 'scripts/builder'))
    zero = runpy.run_path(str(ROOT / 'image/zero-lock.py'))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]
    output = ROOT / 'artifacts/tyrell-zero-locks' / stamp
    output.mkdir(parents=True)
    job = '/var/tmp/tyrell-zero-lock-' + stamp
    integration = args.integration_artifacts.resolve()
    custom = verify_custom_inputs(integration, args.status_deb.resolve(), args.power_deb.resolve())
    profile = runpy.run_path(str(ROOT / 'image/profiles.py'))['load_profile'](root=ROOT)
    seed_lock = ROOT / profile['lock_paths']['seed_apt']
    if sha(seed_lock) != profile['pins']['seed_apt_lock_sha256']:
        raise ValueError('Selected seed lock differs from the reviewed Zero profile')
    if sha(args.seed_repository / 'apt-lock.json') != sha(seed_lock):
        raise ValueError('Selected seed repository differs from the reviewed Zero profile')
    # Validate the prior package build before any guest mutation.
    manifest = json.loads((integration / 'manifest.json').read_text())
    if manifest.get('status') != 'passed' or any(manifest.get('packages', {}).get(p.name) != sha(p) for p in custom[2:]):
        raise ValueError('Exact passed native integration build required')
    with api['shared_lock']():
        before, _ = api['instance_state']()
        if before not in {'Running', 'Stopped'}:
            raise ValueError('Existing retained ARM64 builder required')
        try:
            api['up'](); api['assert_guest_idle']()
            api['guest']('mkdir', '-m', '700', job)
            files = [(ROOT / 'image/apt-lock.py', 'apt-lock.py'),
                     (ROOT / 'builder/zero-lock-guest.py', 'zero-lock-guest.py'),
                     (ROOT / profile['lock_paths']['seed_apt'], 'seed-lock.json'),
                     (ROOT / 'locks/tyrell-zero-greetd.json', 'greetd-lock.json'),
                     (ROOT / 'image/profiles/tyrell-zero.json', 'profile.json'),
                     (args.greetd_deb.resolve(), 'greetd.deb'),
                     (integration / 'pre-image-qualification.json', 'pre-image-qualification.json')]
            for source, name in files:
                record(source)
                api['lima']('copy', '--backend=scp', str(source), 'tyrell-build:' + job + '/' + name)
            api['guest']('mkdir', job + '/custom-debs')
            for source in custom:
                record(source)
                api['lima']('copy', '--backend=scp', str(source), 'tyrell-build:' + job + '/custom-debs/' + source.name)
            def action(name, *extra):
                api['guest']('sudo', '-n', 'unshare', '--mount', '--net', '--propagation', 'private', '--',
                             'python3', job + '/zero-lock-guest.py', name, '--job', job, *extra)
            action('authenticate-greetd')
            action('prepare', '--seed-repository', args.guest_seed_repository, '--retained-root', args.retained_root)
            action('install'); action('install-custom')
            action('snapshot-native', '--retained-root', args.retained_root)
            names = ['system-qualification.json', 'greetd-authentication.json', 'installed-system.json',
                     'transaction.json', 'apt-update.txt', 'apt-simulation.txt', 'dpkg-install.txt', 'apt-check.txt',
                     'dpkg-audit.txt', 'native-compat.json', 'native-ldd.txt', 'installed-with-apps.json',
                     'custom-install.txt', 'custom-apt-check.txt', 'live-builder-packages-before.txt', 'live-builder-packages-after.txt']
            export(api, job, output, names)
            folder = api['guest']('sudo', '-n', 'python3', '-c',
                    'import pathlib;print(next(x for x in pathlib.Path(' + repr(job) + ').glob("authentication-*") if (x/"Packages.gz").exists()))',
                    stdout=subprocess.PIPE, text=True).stdout.strip()
            (output / 'authentication').mkdir()
            export(api, folder, output / 'authentication', ['InRelease', 'Release', 'Packages.gz', 'debian-archive-keyring.pgp', 'greetd-control.json'])
        finally:
            if before == 'Stopped': api['lima']('stop', 'tyrell-build')
    zero['capture'](args.seed_repository, output / 'installed-system.json', output / 'greetd-authentication.json',
                    output / 'system-qualification.json', args.repository_output, [args.greetd_deb])
    generated = output / 'locks'; generated.mkdir()
    shutil.copyfile(args.repository_output / 'apt-lock.json', generated / 'tyrell-zero-apt.json')
    shutil.copyfile(output / 'native-compat.json', generated / 'tyrell-zero-native-compat.json')
    apps = apps_lock(integration, args.status_deb.resolve(), args.power_deb.resolve(), output)
    (generated / 'tyrell-zero-apps.json').write_text(json.dumps(apps, indent=2) + '\n')
    if args.write_locks:
        for path in generated.iterdir(): shutil.copyfile(path, ROOT / 'locks' / path.name)
        zero['validate_inputs'](ROOT)
    print(json.dumps({'artifacts': str(output), 'generated_locks': str(generated),
                      'repository': str(args.repository_output), 'guest_disposable_root': job + '/rootfs',
                      'image_built': False, 'hardware_qualified': False}, indent=2))


def canonical_metadata():
    profile_path = ROOT / 'image/profiles/tyrell-zero.json'
    profile = json.loads(profile_path.read_text())
    return dict(schema=1, profile='tyrell-zero', profile_id='tyrell-zero', build_mode='zero-native',
                apt_profile='zero-frozen', stages=profile['stages'], selected_power_preset='baseline',
                profile_sha256=sha(profile_path),
                hardware_contract_sha256=profile['contracts']['hardware']['sha256'],
                power_contract_sha256=profile['contracts']['power']['sha256'],
                locks_sha256={name: sha(ROOT / 'locks' / name) for name in [
                    'tyrell-zero-apt.json', 'tyrell-zero-apps.json', 'tyrell-zero-native-compat.json']},
                pi_gen_commit=profile['pins']['pi_gen_commit'], drivers_commit=profile['pins']['bsp_commit'],
                kernel_release=profile['pins']['kernel_release'], applaunch_version=profile['pins']['applaunch_version'],
                output=profile['output'], image_name=profile['output']['basename'],
                hardware_qualified=False, battery_qualified=False)


def qualify_root(args):
    """Activate and independently verify one fresh, already captured guest root."""
    api = runpy.run_path(str(ROOT / 'scripts/builder'))
    runpy.run_path(str(ROOT / 'image/zero-lock.py'))['validate_inputs'](ROOT)
    if not args.guest_root.startswith('/var/tmp/tyrell-zero-lock-') or not args.guest_root.endswith('/rootfs'):
        raise ValueError('Only this workflow\'s disposable guest roots may be qualified')
    verify_only = args.action == 'verify-root'
    vendor = args.vendor_source.resolve() if not verify_only else None
    profile = json.loads((ROOT / 'image/profiles/tyrell-zero.json').read_text())
    if not verify_only and subprocess.check_output(['git', '-C', str(vendor), 'rev-parse', 'HEAD'], text=True).strip() != profile['pins']['pi_gen_commit']:
        raise ValueError('Pinned vendor source required for runtime APT configuration')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]
    output = ROOT / 'artifacts/tyrell-zero-root-qualification' / stamp; output.mkdir(parents=True)
    canonical = canonical_metadata()
    if verify_only:
        metadata = json.loads(args.metadata.read_text())
        if any(metadata.get(key) != value for key, value in canonical.items()):
            raise ValueError('Existing root metadata differs from current runtime locks/profile; use a fresh capture')
    else:
        metadata = canonical; metadata['job_id'] = stamp
    (output / 'image-metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    apps = json.loads((ROOT / 'locks/tyrell-zero-apps.json').read_text())
    files = ['image/zero_activation.py', 'image/verify-zero.py', 'image/profiles/tyrell-zero.json',
             profile['contracts']['hardware']['path'], profile['contracts']['power']['path'],
             'packages/tyrell-zero-session/src/state.py', 'packages/tyrell-zero-store/activate-image.py',
             'packages/tyrell-zero-store/guard.py', 'locks/tyrell-zero-apt.json',
             'locks/tyrell-zero-apps.json', 'locks/tyrell-zero-native-compat.json']
    if not verify_only:
        for name in ['debian.sources', 'raspi.sources']:
            text = (vendor / 'stage0/00-configure-apt/files' / name).read_text().replace('RELEASE', 'trixie')
            (output / name).write_text(text)
    archive = output / 'input.tar'
    with tarfile.open(archive, 'w') as stream:
        for name in files: stream.add(ROOT / name, arcname=name, recursive=False)
        for name in ['image-metadata.json', *([] if verify_only else ['debian.sources', 'raspi.sources'])]:
            stream.add(output / name, arcname=name, recursive=False)
        stream.add(ROOT / apps['qualification']['input_path'], arcname='pre-image-qualification.json', recursive=False)
    guest = '/var/tmp/tyrell-zero-root-qualification-' + stamp
    result = None
    with api['shared_lock']():
        before, _ = api['instance_state']()
        try:
            api['up'](); api['assert_guest_idle'](); api['guest']('mkdir', '-m', '700', guest)
            api['lima']('copy', '--backend=scp', str(archive), 'tyrell-build:' + guest + '/input.tar')
            api['guest']('tar', '-xf', guest + '/input.tar', '-C', guest)
            if not verify_only:
                api['guest']('sudo', '-n', 'python3', guest + '/image/zero_activation.py', '--prepare-runtime', '--root', args.guest_root)
            code = '''from pathlib import Path
import os,runpy,stat,sys
r,s=map(Path,sys.argv[1:]);api=runpy.run_path(str(s/'image/zero_activation.py'));root=api['OfflineRoot'](r)
root.write('/etc/tyrell-image.json',(s/'image-metadata.json').read_text())
for n in ['/etc/apt/sources.list.d/00-temp.list','/etc/apt/apt.conf.d/51cache','/var/lib/dbus/machine-id']:
 root.files.secure(str(Path(n).parent),directory=True);p=root.at(n)
 if os.path.lexists(p):
  info=p.lstat()
  if info.st_uid!=0 or not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):raise RuntimeError('unsafe cleanup input')
  p.unlink()
root.write('/etc/apt/sources.list','')
# Match the pinned vendor export-image/05-finalise/01-run.sh first-boot policy.
root.write('/etc/machine-id','uninitialized\\n')
for n in ['debian.sources','raspi.sources']:
 root.write('/etc/apt/sources.list.d/'+n,(s/n).read_text())
'''
            if not verify_only:
                api['guest']('sudo', '-n', 'python3', '-c', code, args.guest_root, guest)
                # Match the image's stage99 policy without touching the retained
                # lower root or the live builder's account database.
                api['guest']('sudo', '-n', 'chroot', args.guest_root, 'passwd', '-l', 'root')
                api['guest']('sudo', '-n', 'python3', guest + '/image/zero_activation.py', '--root', args.guest_root,
                             '--qualification', guest + '/pre-image-qualification.json', '--native-compat', guest + '/locks/tyrell-zero-native-compat.json')
            try:
                api['guest']('sudo', '-n', 'python3', guest + '/image/verify-zero.py', '--root', args.guest_root,
                             '--metadata', guest + '/image-metadata.json', '--locks-dir', guest + '/locks', '--output-dir', guest + '/verification')
            except subprocess.CalledProcessError as error:
                result = error
            export(api, guest + '/verification', output, ['image-verification.json'])
        finally:
            if before == 'Stopped': api['lima']('stop', 'tyrell-build')
    print(json.dumps({'artifacts': str(output), 'report': str(output / 'image-verification.json')}, indent=2))
    if result is not None: raise result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    capture = commands.add_parser('capture')
    capture.add_argument('--integration-artifacts', type=Path, required=True)
    capture.add_argument('--power-deb', type=Path, required=True)
    capture.add_argument('--status-deb', type=Path, default=ROOT / 'dist/tyrell-status/0.1.0/tyrell-status_0.1.0-1_arm64.deb')
    capture.add_argument('--greetd-deb', type=Path, default=ROOT / 'build/zero-inputs/greetd_0.10.3-4_arm64.deb')
    capture.add_argument('--seed-repository', type=Path, required=True)
    capture.add_argument('--guest-seed-repository', default='/var/tmp/tyrell-apt-repository')
    capture.add_argument('--retained-root', required=True)
    capture.add_argument('--repository-output', type=Path, required=True)
    capture.add_argument('--write-locks', action='store_true', help='Replace only the three source-reviewed runtime locks after successful capture')
    check = commands.add_parser('qualify-root')
    check.add_argument('--guest-root', required=True)
    check.add_argument('--vendor-source', type=Path, required=True)
    verify = commands.add_parser('verify-root')
    verify.add_argument('--guest-root', required=True)
    verify.add_argument('--metadata', required=True, type=Path)
    args = parser.parse_args()
    if args.action == 'capture': qualify(args)
    else: qualify_root(args)


if __name__ == '__main__':
    main()
