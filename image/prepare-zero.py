#!/usr/bin/env python3
"""Prepare stages0-2 from the pinned vendor baseline and captured Zero inputs."""
import argparse
import json
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def prepare(vendor, assets, metadata_path):
    vendor,assets,metadata_path=map(Path,(vendor,assets,metadata_path))
    metadata=json.loads(metadata_path.read_text())
    if (metadata.get('profile_id')!='tyrell-zero' or metadata.get('stages')!=['stage0','stage1','stage2']
            or metadata.get('selected_power_preset')!='baseline'):
        raise ValueError('Unsupported Zero recipe identity')
    apps=json.loads((ROOT/'locks/tyrell-zero-apps.json').read_text())
    apt=json.loads((ROOT/'locks/tyrell-zero-apt.json').read_text())
    profile=json.loads((ROOT/'image/profiles/tyrell-zero.json').read_text())
    subprocess.run([sys.executable,str(ROOT/'image/prepare-vendor.py'),str(vendor),str(assets),str(metadata_path)],check=True)
    # Reuse the reviewed vendor patching without exporting a desktop stage.
    shutil.rmtree(vendor/'stage4/99-tyrell')
    (vendor/'stage2/SKIP_IMAGES').unlink()
    lock_api=runpy.run_path(str(ROOT/'image/zero-lock.py'))
    lock_api['configure_vendor'](vendor,ROOT/'apt-repository')
    selected={row[0]:row[1] for row in apt['installed']}
    roots=sorted({name for names in profile['runtime_roots'].values() for name in names})
    if any(name not in selected for name in roots):raise ValueError('Zero runtime roots absent from frozen closure')
    runtime=' '.join(shlex.quote(name+'='+selected[name]) for name in roots)
    package_files=' '.join(shlex.quote('/var/tmp/tyrell-zero-packages/'+p['filename']) for p in apps['packages'])
    qualification=apps['qualification']['filename']
    if Path(qualification).name!=qualification:raise ValueError('Unsafe qualification filename')
    lines=['#!/bin/bash -e','set -euo pipefail',
           'install -d -m 0755 "$ROOTFS_DIR/var/tmp/tyrell-zero-packages"']
    for package in apps['packages']:
        name=package['filename']
        if Path(name).name!=name:raise ValueError('Unsafe custom filename')
        lines.append('install -m 0644 '+shlex.quote(str(assets/name))+' "$ROOTFS_DIR"/'+shlex.quote('var/tmp/tyrell-zero-packages/'+name))
    lines += ['# The diversion is a profile operation, never a package-install side effect.',
              'unmount "$ROOTFS_DIR"',
              'python3 '+shlex.quote(str(ROOT/'packages/tyrell-zero-store/activate-image.py'))+' prepare --root "$ROOTFS_DIR"',
              "on_chroot <<'TYRELL_ZERO_INSTALL'",'set -e',
              'apt-get install -y --no-install-recommends '+runtime+' '+package_files,
              'dpkg --audit','apt-get check','rm -rf /var/tmp/tyrell-zero-packages',
              'TYRELL_ZERO_INSTALL','unmount "$ROOTFS_DIR"',
              'python3 '+shlex.quote(str(ROOT/'image/zero_activation.py'))+' --prepare-runtime --root "$ROOTFS_DIR"',
              '# Restore authenticated runtime repositories before freezing the final Store base.',
              'rm -f "$ROOTFS_DIR/etc/apt/sources.list.d/00-temp.list" "$ROOTFS_DIR/etc/apt/apt.conf.d/51cache"',
              'true > "$ROOTFS_DIR/etc/apt/sources.list"',
              'install -m 0644 "$BASE_DIR/tyrell-runtime-apt/debian.sources" "$ROOTFS_DIR/etc/apt/sources.list.d/debian.sources"',
              'install -m 0644 "$BASE_DIR/tyrell-runtime-apt/raspi.sources" "$ROOTFS_DIR/etc/apt/sources.list.d/raspi.sources"',
              'install -m 0644 '+shlex.quote(str(metadata_path))+' "$ROOTFS_DIR/etc/tyrell-image.json"',
              'python3 '+shlex.quote(str(ROOT/'image/zero_activation.py'))+' --root "$ROOTFS_DIR" --qualification '+shlex.quote(str(assets/qualification))+' --native-compat '+shlex.quote(str(ROOT/'locks/tyrell-zero-native-compat.json'))]
    stage=vendor/'stage2/99-tyrell/00-run.sh';stage.parent.mkdir(parents=True)
    stage.write_text('\n'.join(lines)+'\n');stage.chmod(0o755)
    # Frozen stage2 already installed/checked the qualified closure. Export must
    # not upgrade/autoremove it or overwrite the protected runtime APT policy.
    export=vendor/'export-image/02-set-sources/01-run.sh'
    export.write_text('#!/bin/bash -e\nfind "$ROOTFS_DIR/var/lib/apt/lists" -type f -delete\n'
                      'rm -f "$ROOTFS_DIR/var/cache/apt/archives/"*.deb\n')
    report_path=vendor/'tyrell-prepared.json'
    report=json.loads(report_path.read_text())
    report.update(profile_id='tyrell-zero',stages=metadata['stages'],output=metadata['output'],
                  profile_sha256=metadata['profile_sha256'],locks_sha256=metadata['locks_sha256'])
    sha=runpy.run_path(str(ROOT/'image/zero_pipeline.py'))['digest']
    report['prepared_files_sha256'].pop('stage4/99-tyrell/00-run.sh',None)
    report['prepared_files_sha256'].pop('stage2/SKIP_IMAGES',None)
    for name in ['stage2/99-tyrell/00-run.sh','export-image/02-set-sources/01-run.sh']:
        report['prepared_files_sha256'][name]=sha(vendor/name)
    report_path.write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('vendor',type=Path);p.add_argument('assets',type=Path);p.add_argument('metadata',type=Path)
    a=p.parse_args();prepare(a.vendor,a.assets,a.metadata)
