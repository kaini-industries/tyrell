#!/usr/bin/env python3
"""Build files-only native packages and qualify their offline integration."""
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def qualification(source, output, provenance):
    # This temporary tree checks exact installed payload identities. Real dpkg
    # operations/diversion/guard behavior are separately exercised by the root
    # lifecycle harness; this is not a complete or bootable Zero filesystem.
    with tempfile.TemporaryDirectory(prefix='qualification-', dir=source.parent) as directory:
        root = Path(directory)
        run(['dpkg-deb', '--extract', str(source / 'vendor/applaunch.deb'), str(root)])
        public = root / 'usr/share/APPLaunch/bin/M5CardputerZero-AppStore'
        shutil.copyfile(public, str(public) + '.vendor')
        Path(str(public) + '.vendor').chmod(0o755)
        for deb in sorted(output.glob('*.deb')):
            run(['dpkg-deb', '--extract', str(deb), str(root)])
        sys.path.insert(0, str(source / 'packages/tyrell-zero-session/src'))
        import state
        files = state.Files(root, owner=os.getuid())
        assets = {name: {'path':path, 'sha256':files.digest(path)} for name,path in state.ASSETS.items()}
        runtime = set(state.REQUIRED_RUNTIME_FILES)
        # Preserve vendor font coverage for the real GUI. The future image must
        # retain the rest of applaunch's resources as well as its hardware stack.
        for font in ['DejaVuSans.ttf', 'DejaVuSerif.ttf', 'NotoSansCJK-Regular.ttc', 'NotoSerifCJK-Regular.ttc']:
            runtime.add('/usr/share/APPLaunch/share/font/' + font)
        hashes = {name: files.digest(name) for name in sorted(runtime)}
        receipt = dict(schema=1, profile=state.PROFILE, workflow=state.WORKFLOW,
                       status='passed', scope='pre-image-integration', assets=assets,
                       runtime_files=hashes, suites={'session':'passed','wizard':'passed','store':'passed'},
                       physical_hardware_qualified=False, source_sha256=provenance['source_sha256'],
                       store_lifecycle_sha256=sha(output / 'store-lifecycle.json'),
                       limitations=['Builder-derived modeled vendor root, not frozen Zero APT closure.',
                                    'No physical GUI/account-provisioning/boot or battery acceptance.'])
        files.write_json(state.QUALIFICATION, receipt)
        manifest = dict(schema=1, profile=state.PROFILE, workflow=state.WORKFLOW,
                        complete=True, applaunch_version='0.6.111', assets=assets, runtime_files=hashes,
                        qualification_sha256=files.digest(state.QUALIFICATION))
        files.write_json(state.INTEGRATION, manifest)
        identity = state.integration(files)
        # The packaged helper and the generated receipt must actually control
        # readiness. A changed module is rejected even if its wrapper is intact.
        changed = files.path('/usr/lib/tyrell-zero/session/wizard_complete.py')
        changed.write_bytes(changed.read_bytes() + b'\n# qualification tamper probe\n')
        try:
            state.integration(files)
        except state.StateError as error:
            assert str(error) == 'integration-runtime-changed'
        else:
            raise RuntimeError('Changed Wizard module was accepted')
        (output / 'pre-image-qualification.json').write_bytes(files.path(state.QUALIFICATION).read_bytes())
        (output / 'qualified-session-integration.json').write_bytes(files.path(state.INTEGRATION).read_bytes())
        return identity


def main():
    source, output = map(Path, sys.argv[1:])
    provenance = json.loads((source / 'source-manifest.json').read_text())
    for name, identity in provenance['source_files'].items():
        assert sha(source / name) == identity, name
    output.mkdir()
    shutil.copyfile(source / 'source-manifest.json', output / 'source-manifest.json')
    env = dict(os.environ, SOURCE_DATE_EPOCH='1794528000', LC_ALL='C.UTF-8', TZ='UTC', PYTHONDONTWRITEBYTECODE='1')
    run(['python3', str(source / 'packages/tyrell-zero-session/build-package.py'), '--output-dir', str(output),
         '--wizard-binary', str(source / 'wizard/LaunchWizard')], env=env)
    run(['python3', str(source / 'packages/tyrell-zero-store/build-package.py'), '--output-dir', str(output)], env=env)
    packaging = output / '_CPack_Packages'
    if packaging.is_symlink():
        raise RuntimeError('Unexpected CPack directory link')
    if packaging.exists():
        shutil.rmtree(packaging)
    for deb in output.glob('*.deb'):
        control = subprocess.check_output(['dpkg-deb', '--ctrl-tarfile', str(deb)])
        with tarfile.open(fileobj=io.BytesIO(control)) as tar:
            forbidden = {'preinst','postinst','prerm','postrm','triggers','config'}
            assert not any(Path(m.name).name in forbidden for m in tar), deb
        rows = subprocess.check_output(['dpkg-deb', '--contents', str(deb)], text=True)
        assert '.wants/' not in rows and '.requires/' not in rows
    # The root harness creates a new mount/network namespace and drops escape
    # capabilities inside chroot before invoking any package code.
    run(['sudo', '-n', 'unshare', '--mount', '--net', '--propagation', 'private', '--',
         'python3', str(source / 'builder/zero-store-lifecycle.py'), str(source), str(output)])
    lifecycle = json.loads((output / 'store-lifecycle.json').read_text())
    assert lifecycle['status'] == 'passed'
    identity = qualification(source, output, provenance)
    inventory = subprocess.check_output(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\n'], text=True)
    (output / 'builder-packages.tsv').write_text(inventory)
    result = dict(schema=1, milestone='native-wizard-store-integration', status='passed',
                  source_sha256=provenance['source_sha256'], architecture='arm64',
                  packages={p.name:sha(p) for p in sorted(output.glob('*.deb'))},
                  wizard_sha256=sha(source / 'wizard/LaunchWizard'), integration_sha256=identity,
                  service_activation_performed=False, device_access=False, image_build=False,
                  hardware_qualified=False, zero_apt_closure_qualified=False)
    (output / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    # Root-produced lifecycle reports are read-only to the builder; all other
    # outputs are files owned by this build. Do not export disposable roots.
    sums = [sha(p) + '  ' + p.name for p in sorted(output.iterdir()) if p.is_file()]
    (output / 'SHA256SUMS').write_text('\n'.join(sums) + '\n')


if __name__ == '__main__':
    main()
