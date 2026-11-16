#!/usr/bin/env python3
"""Build files-only packages and exercise disposable dpkg roots, never live /sys."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def host_packages(names):
    return {name: subprocess.run(['dpkg-query', '-W', '-f=${db:Status-Status} ${Version}', name],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 text=True).stdout.strip() for name in names}


def inspect_package(path, package, version):
    fields = run(['dpkg-deb', '-f', str(path), 'Package', 'Version', 'Architecture', 'Depends'],
                 stdout=subprocess.PIPE, text=True).stdout
    parsed = dict(line.split(': ', 1) for line in fields.splitlines() if ': ' in line)
    if parsed.get('Package') != package or parsed.get('Version') != version \
            or parsed.get('Architecture') not in {'arm64', 'all'}:
        raise RuntimeError('Unexpected package identity: ' + fields)
    for option, control in [('--ctrl-tarfile', True), ('--fsys-tarfile', False)]:
        data = run(['dpkg-deb', option, str(path)], stdout=subprocess.PIPE).stdout
        import io
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for member in archive.getmembers():
                name = PurePosixPath(member.name)
                if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                    raise RuntimeError('Unsafe package member: ' + member.name)
                if member.mode & 0o6000:
                    raise RuntimeError('Privileged file mode in foundation package')
                if control and name.name in {'preinst', 'postinst', 'prerm', 'postrm', 'config', 'triggers'}:
                    raise RuntimeError('Foundation packages must have no maintainer activation: ' + member.name)
                if not control and any(p.endswith(('.wants', '.requires')) for p in name.parts):
                    raise RuntimeError('Foundation packages must not enable services')
    return parsed


def isolated_dpkg(path, package, log):
    with tempfile.TemporaryDirectory(prefix='tyrell-zero-dpkg-') as directory:
        root = Path(directory)
        (root / 'var/lib/dpkg').mkdir(parents=True)
        (root / 'var/log').mkdir(parents=True)
        (root / 'var/lib/dpkg/status').write_text('')
        common = ['dpkg', '--root=' + str(root), '--log=' + str(root / 'var/log/dpkg.log'),
                  '--force-not-root', '--force-depends', '--force-bad-path']
        for action in ['--install', '--install']:
            run([*common, action, str(path)], stdout=log, stderr=subprocess.STDOUT)
        status = run(['dpkg-query', '--admindir=' + str(root / 'var/lib/dpkg'), '-W',
                      '-f=${db:Status-Status}', package], stdout=subprocess.PIPE, text=True).stdout
        if status.strip() != 'installed':
            raise RuntimeError('Isolated installation did not complete')
        run([*common, '--remove', package], stdout=log, stderr=subprocess.STDOUT)
        run([*common, '--purge', package], stdout=log, stderr=subprocess.STDOUT)


def main():
    source, output = map(Path, sys.argv[1:])
    if platform.machine() != 'aarch64' or run(['dpkg', '--print-architecture'], stdout=subprocess.PIPE, text=True).stdout.strip() != 'arm64':
        raise RuntimeError('Zero foundation builds require native ARM64 Debian')
    if output.exists() or output.is_symlink() or output.resolve().is_relative_to(source.resolve()):
        raise RuntimeError('Output must be a fresh generated tree outside inputs')
    manifest = json.loads((source / 'source-manifest.json').read_text())
    for name, expected in manifest['source_files'].items():
        p = source / name
        if p.is_symlink() or not p.resolve().is_relative_to(source.resolve()) or sha(p) != expected:
            raise RuntimeError('Input identity mismatch: ' + name)
    selected = manifest['selected_packages']
    if not selected or not set(selected) <= {'tyrell-zero-session', 'tyrell-zero-power-policy'}:
        raise RuntimeError('Unknown foundation package selection')
    before = host_packages(selected)
    if any(v.startswith('installed') for v in before.values()):
        raise RuntimeError('Refusing to test a foundation already installed in the live builder')
    output.mkdir(parents=True)
    env = os.environ.copy()
    env.update(SOURCE_DATE_EPOCH=str(manifest['source_date_epoch']), TZ='UTC', LC_ALL='C.UTF-8', PYTHONDONTWRITEBYTECODE='1')
    results = []
    for name in selected:
        target = output / name
        target.mkdir()
        with (target / 'build-test.log').open('w') as log:
            run([sys.executable, str(source / 'packages' / name / 'build-package.py'),
                 '--output-dir', str(target)], env=env, stdout=log, stderr=subprocess.STDOUT)
        packages = list(target.glob('*.deb'))
        if len(packages) != 1:
            raise RuntimeError('Expected exactly one .deb for ' + name)
        package = packages[0]
        fields = inspect_package(package, name, manifest['expected_versions'][name])
        with (target / 'isolated-install.log').open('w') as log:
            isolated_dpkg(package, name, log)
        results.append({'package': name, 'file': str(package.relative_to(output)),
                        'sha256': sha(package), 'control': fields,
                        'isolated_install_reinstall_remove_purge': 'passed',
                        'dependency_closure_verified': False})
    after = host_packages(selected)
    if after != before:
        raise RuntimeError('Live builder package state changed during isolated tests')
    shutil.copyfile(source / 'source-manifest.json', output / 'source-manifest.json')
    with (output / 'builder-packages.tsv').open('w') as stream:
        run(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${Architecture}\n'], stdout=stream)
    report = {'schema_version': 1, 'milestone': 'foundation-only',
              'source_sha256': manifest['source_sha256'], 'selected_packages': selected,
              'architecture': 'arm64', 'packages': results, 'device_access': False,
              'image_build': False, 'live_builder_packages_before': before,
              'live_builder_packages_after': after,
              'limitations': ['Wizard/Store integration and Zero APT closure not certified.',
                              'Empty disposable dpkg roots use --force-depends; this is not dependency or boot acceptance.',
                              'No services enabled; no optimized power policy applied; no hardware/battery acceptance.']}
    (output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    files = sorted(p for p in output.rglob('*') if p.is_file())
    (output / 'SHA256SUMS').write_text(''.join(sha(p) + '  ' + str(p.relative_to(output)) + '\n' for p in files))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
