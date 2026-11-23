#!/usr/bin/env python3
"""Offline Zero dependency transactions, exclusively inside a disposable root.

The retained seed repository and stage2 filesystem are read-only inputs. Service
starts are suppressed in the copied root; the live builder is never installed to.
"""
import argparse
import hashlib
import gzip
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import stat
import subprocess
import uuid


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def write(path, data):
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def prepare(job, seed, retained):
    api = runpy.run_path(str(job / 'apt-lock.py'))
    expected = json.loads((job / 'seed-lock.json').read_text())
    if sha(seed / 'apt-lock.json') != sha(job / 'seed-lock.json'):
        raise ValueError('Seed repository differs from captured seed lock')
    api['verify_repository'](seed)
    if api['inventory'](retained) != {tuple(x) for x in expected['stages']['stage2']}:
        raise ValueError('Retained stage2 inventory differs from locked seed')
    root = job / 'rootfs'
    if root.exists():
        raise ValueError('Preserving existing disposable root')
    root.mkdir()
    run(['rsync', '-aH', '--numeric-ids', '--exclude=/var/cache/apt/archives/*.deb',
         '--exclude=/var/lib/apt/lists/*', str(retained) + '/', str(root) + '/'])
    policy = root / 'usr/sbin/policy-rc.d'
    (job / 'original-policy-rc.d').write_bytes(policy.read_bytes() if policy.exists() else b'')
    policy.write_text('#!/bin/sh\nexit 101\n'); policy.chmod(0o755)
    extra = job / 'extra'; extra.mkdir()
    greetd = json.loads((job / 'greetd-lock.json').read_text())
    if sha(job / 'greetd.deb') != greetd['sha256']:
        raise ValueError('Cached greetd byte identity differs')
    fields = api['deb_fields'](job / 'greetd.deb')
    if [fields[k] for k in api['IDENTITY']] != [greetd['package'], greetd['version'], greetd['architecture']]:
        raise ValueError('Cached greetd package identity differs')
    shutil.copyfile(job / 'greetd.deb', extra / 'greetd.deb')
    fields.update(Filename='greetd.deb', Size=str(greetd['bytes']), SHA256=greetd['sha256'])
    (extra / 'Packages').write_text(''.join(k + ': ' + v + '\n' for k, v in fields.items()) + '\n')
    solver = job / 'solver'
    for name in ['lists/partial', 'archives/partial']:
        (solver / name).mkdir(parents=True)
    (solver / 'sources.list').write_text('deb [trusted=yes] file:' + str(seed) + ' stage4 main\n' +
                                        'deb [trusted=yes] file:' + str(extra) + ' ./\n')
    args = ['apt-get', '-o', 'Dir::Etc::sourcelist=' + str(solver / 'sources.list'),
            '-o', 'Dir::Etc::sourceparts=-', '-o', 'Dir::Etc::preferences=-', '-o', 'Dir::Etc::preferencesparts=-',
            '-o', 'Dir::State::status=' + str(root / 'var/lib/dpkg/status'),
            '-o', 'Dir::State::lists=' + str(solver / 'lists'), '-o', 'Dir::Cache::archives=' + str(solver / 'archives'),
            '-o', 'APT::Architecture=arm64', '-o', 'APT::Sandbox::User=root',
            '-o', 'Acquire::Languages=none', '-o', 'Debug::NoLocking=true']
    write(job / 'apt-arguments.json', args)
    with (job / 'apt-update.txt').open('w') as out:
        run(args + ['update'], stdout=out, stderr=subprocess.STDOUT)
    profile = json.loads((job / 'profile.json').read_text())
    roots = sorted({p for group in profile['runtime_roots'].values() for p in group})
    with (job / 'apt-simulation.txt').open('w') as out:
        run(args + ['--simulate', '--no-install-recommends', 'install', *roots], stdout=out, stderr=subprocess.STDOUT)
    text = (job / 'apt-simulation.txt').read_text()
    installs = re.findall(r'^Inst ([^ ]+)(?: \[[^\]]+\])? \(([^ ]+)', text, re.M)
    removals = re.findall(r'^Remv ([^ ]+)', text, re.M)
    allowed = {(p['Package'], p['Version']): p for p in expected['packages']}
    allowed[('greetd', greetd['version'])] = {'Package': 'greetd', 'Version': greetd['version'],
        'Architecture': 'arm64', 'sha256': greetd['sha256'], 'bytes': greetd['bytes'],
        'source': greetd['url'], 'file': 'pool/' + greetd['sha256'] + '.deb'}
    selected = []; payload = root / 'tmp/tyrell-zero-debs'; payload.mkdir()
    for name, version in installs:
        record = allowed.get((name, version))
        if record is None:
            raise ValueError('Unpinned resolver candidate: ' + name + '=' + version)
        source = job / 'greetd.deb' if name == 'greetd' else seed / record['file']
        shutil.copyfile(source, payload / (record['sha256'] + '.deb')); selected.append(record)
    write(job / 'transaction.json', {'roots': roots, 'installs': selected, 'removals': removals,
        'seed_lock_sha256': sha(job / 'seed-lock.json'), 'retained_stage2_inventory_verified': True})
    print(json.dumps({'root': str(root), 'install': [[p['Package'], p['Version']] for p in selected], 'remove': removals}))


def install(job):
    root = job / 'rootfs'; tx = json.loads((job / 'transaction.json').read_text())
    env = dict(os.environ, DEBIAN_FRONTEND='noninteractive', SYSTEMD_OFFLINE='1')
    with (job / 'dpkg-install.txt').open('w') as out:
        if tx['removals']:
            run(['chroot', root, 'dpkg', '--remove', *tx['removals']], env=env, stdout=out, stderr=subprocess.STDOUT)
        debs = ['/tmp/tyrell-zero-debs/' + p.name for p in sorted((root / 'tmp/tyrell-zero-debs').glob('*.deb'))]
        if debs:
            run(['chroot', root, 'dpkg', '--install', *debs], env=env, stdout=out, stderr=subprocess.STDOUT)
        run(['chroot', root, 'dpkg', '--configure', '-a'], env=env, stdout=out, stderr=subprocess.STDOUT)
    audit = subprocess.check_output(['chroot', str(root), 'dpkg', '--audit'], text=True)
    (job / 'dpkg-audit.txt').write_text(audit)
    if audit.strip():
        raise ValueError('Disposable root has incomplete dpkg state')
    api = runpy.run_path(str(job / 'apt-lock.py')); inventory = sorted(api['inventory'](root))
    write(job / 'installed-system.json', [list(x) for x in inventory])
    args = json.loads((job / 'apt-arguments.json').read_text())
    with (job / 'apt-check.txt').open('w') as out:
        run(args + ['check'], stdout=out, stderr=subprocess.STDOUT)
    seed = json.loads((job / 'seed-lock.json').read_text())
    write(job / 'system-qualification.json', dict(schema=1, scope='real-isolated-stage2-system-runtime',
          seed_apt_lock_sha256=sha(job / 'seed-lock.json'),
          retained_stage2_installed_count=len(seed['stages']['stage2']), installed_count=len(inventory),
          installed_sha256=sha(job / 'installed-system.json'),
          greetd_authentication_sha256=sha(job / 'greetd-authentication.json'),
          apt_check='passed', dpkg_audit='clean', transaction=tx,
          logs_sha256={name: sha(job / name) for name in ['apt-update.txt', 'apt-simulation.txt', 'dpkg-install.txt', 'apt-check.txt', 'dpkg-audit.txt']},
          image_build=False, hardware_qualified=False))
    seed = json.loads((job / 'seed-lock.json').read_text())
    receipt = dict(schema=1, scope='real-isolated-stage2-system-runtime',
                   seed_apt_lock_sha256=sha(job / 'seed-lock.json'),
                   retained_stage2_installed_count=len(seed['stages']['stage2']),
                   installed_count=len(inventory), installed_sha256=sha(job / 'installed-system.json'),
                   greetd_authentication_sha256=sha(job / 'greetd-authentication.json'),
                   apt_check='passed', dpkg_audit='clean', transaction=tx,
                   logs_sha256={name: sha(job / name) for name in ['apt-update.txt', 'apt-simulation.txt',
                                'dpkg-install.txt', 'apt-check.txt', 'dpkg-audit.txt']},
                   image_build=False, hardware_qualified=False)
    write(job / 'system-qualification.json', receipt)
    print(json.dumps({'installed_system_packages': len(inventory), 'dpkg_audit': 'clean', 'apt_check': 'passed'}))


def authenticate_greetd(job):
    release = Path('/var/lib/apt/lists/_etc_apt_mirrors_debian.list_dists_trixie_InRelease')
    indices = list(Path('/var/lib/apt/lists').glob('_etc_apt_mirrors_debian.list_dists_trixie_main_binary-arm64_Packages*'))
    if len(indices) != 1:
        raise ValueError('Expected one retained Debian ARM64 package index')
    index = indices[0]
    keyring = Path('/usr/share/keyrings/debian-archive-keyring.pgp')
    evidence = job / ('authentication-' + uuid.uuid4().hex); evidence.mkdir()
    authenticated = evidence / 'Release'
    result = run(['sqv', '--keyring', keyring, '--cleartext', '--output', authenticated, release],
                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    api = runpy.run_path(str(job / 'apt-lock.py'))
    fields = next(api['paragraphs'](authenticated.read_text().splitlines()))
    index_data = subprocess.check_output(['/usr/lib/apt/apt-helper', 'cat-file', str(index)])
    (evidence / 'Packages.gz').write_bytes(gzip.compress(index_data, mtime=0))
    expected = [line.split() for line in fields['SHA256'].splitlines() if line.split() and line.split()[-1] == 'main/binary-arm64/Packages']
    if len(expected) != 1 or hashlib.sha256(index_data).hexdigest() != expected[0][0] or len(index_data) != int(expected[0][1]):
        raise ValueError('Signed Release does not authenticate cached Packages index')
    greetd = json.loads((job / 'greetd-lock.json').read_text())
    matches = [p for p in api['paragraphs'](index_data.decode().splitlines())
               if tuple(p.get(k) for k in api['IDENTITY']) == ('greetd', greetd['version'], 'arm64')]
    if len(matches) != 1 or matches[0]['SHA256'] != greetd['sha256'] or int(matches[0]['Size']) != greetd['bytes']:
        raise ValueError('Authenticated index differs from cached greetd pin')
    for source, name in [(release, 'InRelease'), (keyring, 'debian-archive-keyring.pgp')]:
        shutil.copyfile(source, evidence / name)
    write(evidence / 'greetd-control.json', matches[0])
    versions = {name: subprocess.check_output(['dpkg-query', '-W', '-f=${Version}', name], text=True).strip()
                for name in ['sqv', 'debian-archive-keyring']}
    write(job / 'greetd-authentication.json', {'method': 'Debian Release signature -> SHA256 Packages -> exact package hash',
        'signature_verifier': 'sqv ' + versions['sqv'], 'keyring_package': 'debian-archive-keyring ' + versions['debian-archive-keyring'],
        'signature_output': result.stdout, 'signature_diagnostics': result.stderr,
        'inrelease_sha256': sha(release), 'keyring_sha256': sha(keyring),
        'index_sha256': hashlib.sha256(index_data).hexdigest(), 'index_bytes': len(index_data),
        'index_relative_path': expected[0][2], 'release_date': fields.get('Date'),
        'package': matches[0], 'package_sha256_verified': sha(job / 'greetd.deb') == greetd['sha256']})
    print('Authenticated cached greetd through signed Debian Release and Packages SHA256')


def install_custom(job):
    root = job / 'rootfs'; api = runpy.run_path(str(job / 'apt-lock.py'))
    debs = sorted((job / 'custom-debs').glob('*.deb'))
    fields = {api['deb_fields'](p)['Package']: api['deb_fields'](p) for p in debs}
    if set(fields) != {'tyrell-status', 'tyrell-zero-session', 'tyrell-zero-store', 'tyrell-zero-power-policy'}:
        raise ValueError('Expected exactly the four captured custom packages')
    if fields['tyrell-zero-session']['Version'] != '0.3.0-1':
        raise ValueError('Expected native session 0.3.0')
    public = '/usr/share/APPLaunch/bin/M5CardputerZero-AppStore'
    original = public + '.vendor'
    if sha(root / public.lstrip('/')) != 'e5fe24b1dd26cd224c64ef4df2e7a61e0fce3fb63b3b04ac90d38c5092ccc05e':
        raise ValueError('Original vendor Store differs before diversion')
    live = subprocess.check_output(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${Architecture}\n'])
    (job / 'live-builder-packages-before.txt').write_bytes(live)
    target = root / 'tmp/zero-custom'; target.mkdir()
    for path in debs:
        shutil.copyfile(path, target / path.name)
    env = dict(os.environ, DEBIAN_FRONTEND='noninteractive', SYSTEMD_OFFLINE='1')
    with (job / 'custom-install.txt').open('w') as out:
        run(['chroot', root, 'dpkg-divert', '--package', 'tyrell-zero-store', '--add', '--rename',
             '--divert', original, public], stdout=out, stderr=subprocess.STDOUT)
        run(['chroot', root, 'dpkg', '--install', *['/tmp/zero-custom/' + p.name for p in debs]],
            env=env, stdout=out, stderr=subprocess.STDOUT)
    audit = subprocess.check_output(['chroot', str(root), 'dpkg', '--audit'], text=True)
    if audit.strip():
        raise ValueError('Custom install left incomplete dpkg state: ' + audit)
    args = json.loads((job / 'apt-arguments.json').read_text())
    with (job / 'custom-apt-check.txt').open('w') as out:
        run(args + ['check'], stdout=out, stderr=subprocess.STDOUT)
    final = sorted(api['inventory'](root)); write(job / 'installed-with-apps.json', [list(x) for x in final])
    after = subprocess.check_output(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${Architecture}\n'])
    if live != after:
        raise ValueError('Builder live package database changed')
    (job / 'live-builder-packages-after.txt').write_bytes(after)
    print(json.dumps({'installed_packages': len(final), 'dpkg_audit': 'clean', 'apt_check': 'passed',
                      'live_builder_inventory_unchanged': True}))


def native_snapshot(job, retained):
    root = job / 'rootfs'
    qualification = json.loads((job / 'pre-image-qualification.json').read_text())
    def at(base, name):
        # Only canonical /usr/ trees and regular /etc/ files are traversed.
        return base / name.lstrip('/')
    def resolved(name):
        pending = name.split('/'); parts = []; links = 0
        while pending:
            part = pending.pop(0)
            if part in ('', '.'): continue
            if part == '..':
                if not parts: raise ValueError('Native symlink escapes image root')
                parts.pop(); continue
            candidate = root.joinpath(*parts, part)
            if candidate.is_symlink():
                links += 1
                if links > 40: raise ValueError('Native symlink cycle')
                target = os.readlink(candidate)
                if target.startswith('/'): parts = []
                pending = target.split('/') + pending
            else: parts.append(part)
        return root.joinpath(*parts)
    def identity(path):
        info = path.lstat()
        record = dict(mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
        if stat.S_ISLNK(info.st_mode):
            record.update(type='symlink', target=os.readlink(path))
        elif stat.S_ISREG(info.st_mode):
            record.update(type='file', sha256=sha(path))
        elif stat.S_ISDIR(info.st_mode):
            record.update(type='directory')
        else:
            raise ValueError('Unexpected special file in native dependency tree: ' + str(path))
        return record
    def tree(base, names):
        result = {}
        for name in names:
            start = at(base, name)
            if start.is_symlink() or not start.is_dir():
                raise ValueError('Expected canonical native resource directory: ' + name)
            result[name] = identity(start)
            for directory, folders, files in os.walk(start, followlinks=False):
                for leaf in sorted(folders + files):
                    path = Path(directory) / leaf
                    relative = '/' + str(path.relative_to(base))
                    result[relative] = identity(path)
        return result
    def leaf_record(info):
        return {'symlink': info['target']} if info['type'] == 'symlink' else info['sha256']
    roots = ['/usr/lib/modules', '/usr/lib/firmware', '/boot/firmware/overlays']
    protected = tree(retained, roots)
    observed = tree(root, roots)
    extras = ['/lib', '/boot/firmware/config.txt', '/boot/firmware/kernel8.img', '/boot/firmware/start.elf',
              '/boot/firmware/fixup.dat', '/etc/cardputerzero-dtoverlays.commit']
    extras += sorted({info['target'] for info in protected.values()
                      if info['type'] == 'symlink' and info['target'].startswith('/etc/alternatives/')})
    for name in extras:
        protected[name] = identity(at(retained, name)); observed[name] = identity(at(root, name))
    if observed != protected:
        raise ValueError('Copied native root changed retained hardware trees')
    resources = tree(root, ['/usr/share/APPLaunch/share'])
    resource_files = {p: leaf_record(info) for p, info in resources.items() if info['type'] != 'directory'}
    libraries = {}
    library_root = root / 'usr/lib/aarch64-linux-gnu'
    for directory, folders, files in os.walk(library_root, followlinks=False):
        for name in sorted(folders + files):
            path = Path(directory) / name
            if '.so' in name and (path.is_symlink() or path.is_file()):
                info = identity(path)
                libraries['/' + str(path.relative_to(root))] = {'symlink': info['target']} if info['type'] == 'symlink' else {'sha256': info['sha256']}
    for name in ['/lib/ld-linux-aarch64.so.1']:
        libraries[name] = leaf_record(identity(at(root, name)))
    configuration = {}
    config_roots = ['/etc/pam.d', '/usr/lib/udev/rules.d', '/etc/udev/rules.d',
                    '/usr/share/polkit-1/actions', '/usr/share/polkit-1/rules.d']
    for name in config_roots:
        directory = at(root, name)
        if directory.is_dir() and not directory.is_symlink():
            for path in sorted(directory.rglob('*')):
                if path.is_symlink() or path.is_file():
                    configuration['/' + str(path.relative_to(root))] = leaf_record(identity(path))
    for name in ['greetd.service', 'LaunchWizard.service', 'applaunch-updater.service',
                 'NetworkManager.service', 'bluetooth.service', 'polkit.service',
                 'udisks2.service', 'systemd-logind.service', 'dbus.service']:
        path = root / 'usr/lib/systemd/system' / name
        configuration['/' + str(path.relative_to(root))] = leaf_record(identity(path))
    assets = qualification['assets']
    for record in assets.values():
        if sha(at(root, record['path'])) != record['sha256']:
            raise ValueError('Actual native asset differs from source qualification')
    for name, digest in qualification['runtime_files'].items():
        if sha(at(root, name)) != digest:
            raise ValueError('Actual native runtime differs from source qualification')
    binaries = []
    for path in sorted((root / 'usr/share/APPLaunch/bin').rglob('*')):
        if path.is_file() and not path.is_symlink() and path.open('rb').read(4) == b'\x7fELF':
            binaries.append('/' + str(path.relative_to(root)))
    binaries += ['/usr/libexec/tyrell-zero/LaunchWizard', '/usr/libexec/tyrell-zero-session-recovery', '/usr/bin/tyrell-status']
    # Include separately packaged vendor camera/factory applications, not only
    # the programs bundled under APPLaunch/bin.
    seed = json.loads((job / 'seed-lock.json').read_text())
    vendor_packages = [p['Package'] for p in seed['packages'] if 'github.com' in p.get('source', '')]
    installed_names = {p[0] for p in json.loads((job / 'installed-with-apps.json').read_text())}
    vendor_packages += [p for p in ['python3-libcamera', 'python3-picamera2', 'python3-libgpiod', 'python3-spidev'] if p in installed_names]
    for package in vendor_packages:
        paths = subprocess.check_output(['chroot', str(root), 'dpkg-query', '-L', package], text=True).splitlines()
        for name in paths:
            path = at(root, name)
            if path.is_file() and not path.is_symlink():
                with path.open('rb') as stream:
                    if stream.read(4) == b'\x7fELF': binaries.append(name)
    services = ['/usr/sbin/greetd', '/usr/sbin/NetworkManager', '/usr/sbin/wpa_supplicant',
                '/usr/libexec/bluetooth/bluetoothd', '/usr/libexec/udisks2/udisksd',
                '/usr/lib/polkit-1/polkitd', '/usr/bin/pipewire', '/usr/bin/wireplumber',
                '/usr/bin/dbus-daemon', '/usr/lib/systemd/systemd-logind',
                '/usr/lib/systemd/systemd-udevd', '/usr/sbin/sshd', '/usr/sbin/avahi-daemon',
                '/usr/libexec/rtkit-daemon', '/usr/bin/pkexec', '/usr/bin/pipewire-pulse',
                '/usr/bin/python3', '/usr/bin/apt', '/usr/bin/apt-get', '/usr/bin/dpkg',
                '/usr/bin/dpkg-deb', '/usr/bin/dpkg-query', '/usr/bin/dpkg-divert', '/usr/bin/sudo']
    for name in services:
        if not resolved(name).is_file(): raise ValueError('Required native service ELF absent: ' + name)
        with resolved(name).open('rb') as stream: header = stream.read(64)
        if header.startswith(b'\x7fELF'): binaries.append(name)
        else:
            configuration[name] = sha(resolved(name))
            if header.startswith(b'#!/usr/bin/perl'): binaries.append('/usr/bin/perl')
            else: raise ValueError('Unexpected native service script interpreter: ' + name)
    with (job / 'native-ldd.txt').open('w') as out:
        for name in sorted(set(binaries)):
            result = run(['chroot', root, 'ldd', name], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            out.write(name + '\n' + result.stdout + '\n')
            if 'not found' in result.stdout:
                raise ValueError('Unresolved native ELF dependency: ' + name)
            for provider in re.findall(r'(/[A-Za-z0-9_./+~:-]+)\s+\(', result.stdout):
                info = identity(at(root, provider))
                libraries[provider] = {'symlink': info['target']} if info['type'] == 'symlink' else {'sha256': info['sha256']}
                canonical = resolved(provider)
                libraries['/' + str(canonical.relative_to(root))] = {'sha256': sha(canonical)}
    inventory = json.loads((job / 'installed-with-apps.json').read_text())
    elf_roots = {name: sha(resolved(name)) for name in sorted(set(binaries))}
    # These plugin families are selected by the native camera/media/audio/DRM,
    # PAM and removable-media contracts even when loaded through dlopen.
    plugin_families = ['gstreamer-1.0', 'spa-0.2', 'pipewire-0.3', 'libcamera',
                       'security', 'alsa-lib', 'gio/modules', 'gbm', 'dri']
    for family in plugin_families:
        directory = library_root / family
        if directory.is_dir():
            for path in sorted(directory.rglob('*')):
                if path.is_file() and not path.is_symlink():
                    with path.open('rb') as stream:
                        if stream.read(4) == b'\x7fELF': elf_roots['/' + str(path.relative_to(root))] = sha(path)
    optional_path = '/usr/lib/aarch64-linux-gnu/caca/libgl_plugin.so.0.0.0'
    optional = [dict(path=optional_path, sha256=sha(at(root, optional_path)),
                    reason='Optional libcaca OpenGL display driver; no selected native/vendor/service ELF depends on this driver. Native UI uses the LCD and dedicated DRM/GStreamer backends.',
                    runtime_eligible=False, missing_sonames=['libGLU.so.1', 'libglut.so.3'])]
    elf_root_metadata = {'/' + str(resolved(name).relative_to(root)): identity(resolved(name)) for name in elf_roots}
    lock = dict(schema=1, profile='tyrell-zero', complete=True,
                scope='isolated-retained-stage2-root-with-pinned-runtime-and-custom-packages',
                source_qualification_sha256=sha(job / 'pre-image-qualification.json'),
                system_qualification_sha256=sha(job / 'system-qualification.json'),
                assets=assets, runtime_files=qualification['runtime_files'],
                resource_roots=['/usr/share/APPLaunch/share'], resource_files=resource_files,
                library_files=libraries, configuration_files=configuration,
                elf_roots=elf_roots, optional_elf_backends=optional,
                elf_root_metadata=elf_root_metadata,
                required_packages={name: version for name, version, arch in inventory},
                installed_packages={name: dict(Version=version, Architecture=arch) for name, version, arch in inventory},
                protected_roots=roots, protected_files=protected,
                qualification=dict(scope='disposable-offline-runtime-root', assembled_image_qualified=False, hardware_qualified=False),
                runtime_checks=dict(apt_check='passed', dpkg_audit='clean', native_elf_dependencies='passed',
                                   retained_hardware_trees_unchanged=True, hardware_qualified=False,
                                   physical_boot_qualified=False, assembled_image_file_qualified=False))
    write(job / 'native-compat.json', lock)
    print(json.dumps({'resources': len(resource_files), 'libraries': len(libraries),
                      'configuration_files': len(configuration), 'protected_files': len(protected),
                      'native_elf_binaries': len(binaries), 'installed_packages': len(inventory)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'install', 'authenticate-greetd', 'install-custom', 'snapshot-native'])
    parser.add_argument('--job', type=Path, required=True)
    parser.add_argument('--seed-repository', type=Path)
    parser.add_argument('--retained-root', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Disposable-root operations require root inside the ARM64 builder')
    if args.action == 'prepare':
        prepare(args.job, args.seed_repository, args.retained_root)
    elif args.action == 'install':
        install(args.job)
    elif args.action == 'authenticate-greetd':
        authenticate_greetd(args.job)
    elif args.action == 'install-custom':
        install_custom(args.job)
    else:
        native_snapshot(args.job, args.retained_root)


if __name__ == '__main__':
    main()
