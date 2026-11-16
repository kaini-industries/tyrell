#!/usr/bin/env python3
"""Offline native Wizard compilation. Receives only an allowlisted source bundle."""
import hashlib
import json
import os
from pathlib import Path
import platform
import runpy
import shutil
import socket
import subprocess
import sys


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(args, **kw):
    return subprocess.run(args, check=True, **kw)


def main():
    source, output = map(Path, sys.argv[1:])
    assert platform.machine() == 'aarch64'
    assert subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip() == 'arm64'
    # This driver is invoked inside a fresh network namespace, with no interfaces
    # except a down loopback device. An SDK regression cannot silently fetch.
    assert [name for _, name in socket.if_nameindex()] == ['lo']
    manifest = json.loads((source / 'source-manifest.json').read_text())
    for name, identity in manifest['source_files'].items():
        assert sha(source / name) == identity, name
    output.mkdir()
    api = runpy.run_path(str(source / 'builder/zero-vendor.py'))
    lock = api['load_lock'](source / 'locks/tyrell-zero-vendor-inputs.json')
    vendor = source.parent / 'vendor-source'
    stage = api['stage'](lock, source / 'build/zero-vendor/downloads', vendor)
    patcher = source / 'packages/tyrell-zero-session/wizard/apply-patches.py'
    run(['python3', str(patcher), str(vendor)])
    config = runpy.run_path(str(patcher.parent / 'native_config.py'))['generate'](vendor)
    env = dict(os.environ, CONFIG_DEFAULT_FILE='tyrell_native_config.mk', SOURCE_DATE_EPOCH='1794528000',
               TZ='UTC', LC_ALL='C.UTF-8', PYTHONDONTWRITEBYTECODE='1')
    with (output / 'wizard-build.log').open('w') as log:
        result = subprocess.run(['scons', '-j2'], cwd=vendor / 'projects/LaunchWizard',
                                env=env, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        print((output / 'wizard-build.log').read_text()[-12000:])
        result.check_returncode()
    candidates = list((vendor / 'projects/LaunchWizard/dist').glob('*'))
    candidates = [p for p in candidates if p.is_file() and p.read_bytes()[:4] == b'\x7fELF']
    assert len(candidates) == 1, candidates
    binary = output / 'LaunchWizard'
    shutil.copyfile(candidates[0], binary); binary.chmod(0o755)
    elf = subprocess.check_output(['readelf', '-h', '-d', str(binary)], text=True)
    assert 'AArch64' in elf
    (output / 'wizard-elf.txt').write_text(elf)
    linked = subprocess.run(['ldd', str(binary)], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert linked.returncode == 0 and 'not found' not in linked.stdout, linked.stdout
    (output / 'wizard-linked-libraries.txt').write_text(linked.stdout)
    tools = subprocess.check_output(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\n'], text=True)
    (output / 'builder-packages.tsv').write_text(tools)
    receipt = dict(schema=1, milestone='native-wizard-build', architecture='arm64', status='passed',
                   source_sha256=manifest['source_sha256'], source_files=manifest['source_files'],
                   vendor_stage=stage, native_config=config, patch_identity=json.loads((patcher.parent / 'patches/identity.json').read_text()),
                   wizard_sha256=sha(binary), network_disabled=True, device_access=False,
                   hardware_qualified=False)
    (output / 'manifest.json').write_text(json.dumps(receipt, indent=2) + '\n')
    sums = [sha(p) + '  ' + p.name for p in sorted(output.iterdir()) if p.is_file()]
    (output / 'SHA256SUMS').write_text('\n'.join(sums) + '\n')


if __name__ == '__main__':
    main()
