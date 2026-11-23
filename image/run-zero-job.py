#!/usr/bin/env python3
"""Root-owned, per-attempt guest execution; never installed as a device service."""
import argparse
import os
from pathlib import Path
import re
import runpy
import signal
import subprocess
import sys


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',required=True)
    parser.add_argument('--attempt',required=True,type=int)
    parser.add_argument('--bundle-sha256',required=True)
    parser.add_argument('--mode',choices=['fresh','stage2'],required=True)
    args=parser.parse_args(argv)
    if (os.geteuid()!=0 or not re.fullmatch(r'[0-9]{8}T[0-9]{12}Z-[0-9a-f]{8}',args.job)
            or not re.fullmatch(r'[0-9a-f]{64}',args.bundle_sha256) or args.attempt<1
            or (args.attempt==1)!=(args.mode=='fresh')):
        raise ValueError('Invalid Zero execution identity')
    root=Path('/var/tmp/tyrell-zero-image-jobs')/args.job
    for path in (root.parent,root,root/'input'):
        if path.is_symlink() or path.stat().st_uid!=0 or path.stat().st_mode&0o022:
            raise ValueError('Untrusted Zero execution root')
    for name,value in [('TYRELL_IMAGE_JOB_ID',args.job),('TYRELL_IMAGE_ATTEMPT_ID',str(args.attempt)),
                       ('TYRELL_IMAGE_INPUT_SHA256',args.bundle_sha256)]:
        if os.environ.get(name)!=value:raise ValueError('Systemd execution identity differs')
    tools=runpy.run_path(str(root/'input/image/zero_pipeline.py'))
    folder=root/'attempts'/f'{args.attempt:04d}'
    if folder.is_symlink() or folder.stat().st_uid!=0:raise ValueError('Untrusted Zero attempt directory')
    result=folder/'result.json'
    if os.path.lexists(result):raise ValueError('Preserving completed Zero attempt')
    identity=dict(job_id=args.job,attempt_id=args.attempt,bundle_sha256=args.bundle_sha256)
    status=1;manifest_sha=None
    def interrupted(_signum,_frame):raise InterruptedError('Zero attempt interrupted')
    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGINT,interrupted)
    try:
        if tools['digest'](root/'image-input.tar')!=args.bundle_sha256:
            raise ValueError('Root-staged archive changed')
        metadata=tools['verify_bundle'](root/'image-input.tar')
        if metadata['job_id']!=args.job:raise ValueError('Captured job differs')
        # Recheck extracted recipe, locks and package bytes before executing it.
        for name,item in metadata['files'].items():
            path=root/'input'/name
            if path.is_symlink() or tools['digest'](path)!=item['sha256']:
                raise ValueError('Root-staged input changed: '+name)
        output=folder/'output';output.mkdir(mode=0o700)
        tools['write_json'](output/'attempt.json',identity)
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',DEBIAN_FRONTEND='noninteractive',
                 LANG='C.UTF-8',LC_ALL='C.UTF-8',TZ='UTC')
        subprocess.run(['/bin/bash',str(root/'input/builder/build-zero-image.sh'),str(root/'input'),
                        str(root/'work'),str(output),args.mode,args.job,args.bundle_sha256],check=True,env=env)
        manifest=tools['read_json']((output/'image-manifest.json').read_text())
        if manifest.get('inputs')!=metadata or manifest.get('verification',{}).get('status')!='passed':
            raise ValueError('Zero output not independently verified')
        manifest_sha=tools['digest'](output/'image-manifest.json');status=0
    except (Exception,KeyboardInterrupt) as error:
        print('Zero attempt failed: '+str(error),file=sys.stderr,flush=True)
    finally:
        tools['write_json'](result,{**identity,'exit_status':status,'manifest_sha256':manifest_sha})
    return status


if __name__=='__main__':sys.exit(main())
