#!/usr/bin/env python3
"""Validate a failed Zero stage2 attempt before quarantining its generated work."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess


def validate(root,bundle_sha,previous_attempt,*,mounts=None,unit=None,authorized_attempt=None,owner=0):
    root=Path(root)
    if root.is_symlink() or root!=root.resolve() or root==Path('/'):
        raise ValueError('Canonical isolated Zero job root required')
    if not re.fullmatch(r'[0-9a-f]{64}',bundle_sha) or type(previous_attempt)is not int or previous_attempt<1:
        raise ValueError('Invalid Zero resume identity')
    metadata=json.loads((root/'input/image-metadata.json').read_text())
    identity=json.loads((root/'work/zero-job-identity.json').read_text())
    expected={'job_id':root.name,'bundle_sha256':bundle_sha,'profile_sha256':metadata['profile_sha256'],
              'capture_sha256':metadata['capture_sha256']}
    if any(identity.get(k)!=v for k,v in expected.items()):raise ValueError('Zero retained work belongs to other inputs')
    config=root/'work/pi-gen/config'
    if hashlib.sha256(config.read_bytes()).hexdigest()!=identity.get('config_sha256'):
        raise ValueError('Zero retained configuration changed')
    result_path=root/'attempts'/f'{previous_attempt:04d}'/'result.json'
    terminal=False
    authorized=False
    if authorized_attempt is not None:
        if authorized_attempt!=previous_attempt+1:raise ValueError('Invalid authorized attempt sequence')
        p=root/f'resume-authorized-{authorized_attempt:04d}.json'
        if p.is_symlink() or not p.is_file() or p.stat().st_uid!=owner or p.stat().st_mode&0o022:
            raise ValueError('Missing trusted resume authorization')
        receipt=json.loads(p.read_text())
        if receipt!={**expected,'previous_attempt':previous_attempt,'authorized_attempt':authorized_attempt,
                     'config_sha256':identity['config_sha256']}:
            raise ValueError('Resume authorization belongs to other inputs')
        authorized=terminal=True
    if result_path.is_file() and not result_path.is_symlink():
        result=json.loads(result_path.read_text())
        if any(result.get(k)!=v for k,v in {'job_id':root.name,'attempt_id':previous_attempt,'bundle_sha256':bundle_sha}.items()):
            raise ValueError('Mismatched previous Zero result')
        if type(result.get('exit_status'))is not int or result['exit_status']==0:
            raise ValueError('Successful or invalid Zero attempt cannot resume')
        terminal=True
    if unit is None:
        text=subprocess.check_output(['systemctl','show','tyrell-image-build.service','-p','ActiveState','-p','SubState','-p','Environment'],text=True)
        unit=dict(line.split('=',1) for line in text.splitlines() if '='in line)
    env=shlex.split(unit.get('Environment',''))
    if unit.get('ActiveState')in {'active','activating','deactivating','reloading'}:
        own=authorized and all(k+'='+str(v)in env for k,v in {
            'TYRELL_IMAGE_JOB_ID':root.name,'TYRELL_IMAGE_ATTEMPT_ID':authorized_attempt,
            'TYRELL_IMAGE_INPUT_SHA256':bundle_sha}.items())
        if not own:raise ValueError('Live Zero attempt cannot resume')
    if not terminal:
        terminal=unit.get('ActiveState')=='failed' and all(k+'='+str(v)in env for k,v in {
            'TYRELL_IMAGE_JOB_ID':root.name,'TYRELL_IMAGE_ATTEMPT_ID':previous_attempt,'TYRELL_IMAGE_INPUT_SHA256':bundle_sha}.items())
    if not terminal:raise ValueError('No authoritative failed Zero attempt')
    if mounts is None:mounts=subprocess.check_output(['findmnt','-rn','-o','TARGET'],text=True).splitlines()
    if any(Path(name)==root or root in Path(name).parents for name in mounts):
        raise ValueError('Retained Zero runtime mounts must be detached')
    work=root/'work/pi-gen/work/tyrell-zero-trixie-arm64'
    stage1=work/'stage1/rootfs/etc/os-release'
    if not stage1.is_file() or 'trixie'not in stage1.read_text():raise ValueError('Completed Zero stage1 root is missing')
    log=root/'work/pi-gen/work/tyrell-zero-trixie-arm64/build.log'
    if not log.is_file() or not re.search(r'\] End [^\n]*/pi-gen/stage1\s*$',log.read_text(),re.M):
        raise ValueError('Zero stage1 lacks a completion record')
    if metadata.get('stages')!=['stage0','stage1','stage2'] or metadata.get('profile_id')!='tyrell-zero':
        raise ValueError('Cross-profile Zero resume refused')
    return identity


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job-root',type=Path,required=True);p.add_argument('--bundle-sha256',required=True)
    p.add_argument('--previous-attempt',type=int,required=True)
    group=p.add_mutually_exclusive_group()
    group.add_argument('--authorize-attempt',type=int);group.add_argument('--authorized-attempt',type=int)
    a=p.parse_args()
    if a.authorize_attempt is not None and a.authorize_attempt!=a.previous_attempt+1:
        p.error('Resume attempt must immediately follow the failed attempt')
    identity=validate(a.job_root,a.bundle_sha256,a.previous_attempt,authorized_attempt=a.authorized_attempt)
    if a.authorize_attempt is not None:
        import os
        if os.geteuid()!=0:raise ValueError('Root-owned resume authorization required')
        target=a.job_root/f'resume-authorized-{a.authorize_attempt:04d}.json'
        if target.exists() or target.is_symlink():raise ValueError('Preserving prior resume authorization')
        record={key:identity[key] for key in ('job_id','bundle_sha256','profile_sha256','capture_sha256','config_sha256')}
        record.update(previous_attempt=a.previous_attempt,authorized_attempt=a.authorize_attempt)
        with target.open('x') as out:
            os.fchmod(out.fileno(),0o600);json.dump(record,out);out.flush();os.fsync(out.fileno())


if __name__=='__main__':main()
