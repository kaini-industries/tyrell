"""Captured-input Zero image jobs with isolated work and durable attempts.

The legacy desktop and stock pipelines keep their own runners. This module
never selects or writes a block device and never connects to the handheld.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
JOB = re.compile(r'[0-9]{8}T[0-9]{12}Z-[0-9a-f]{8}\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')
GUEST_BASE = '/var/tmp/tyrell-zero-image-jobs'
LOCKS = ('tyrell-zero-build-inputs.json', 'tyrell-zero-session-source.json',
         'tyrell-zero-apt.json', 'tyrell-zero-apps.json', 'tyrell-zero-native-compat.json',
         'tyrell-zero-greetd.json', 'tyrell-zero-vendor-inputs.json')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_json(value):
    def pairs(rows):
        out = {}
        for key, item in rows:
            if key in out:
                raise ValueError('Duplicate JSON field: ' + key)
            out[key] = item
        return out
    return json.loads(value, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON constant')))


def write_json(path, value):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Refusing redirected job record')
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, sort_keys=True, indent=2)
            out.write('\n'); out.flush(); os.fsync(out.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if os.path.exists(name): os.unlink(name)


def relative(name):
    path = PurePosixPath(name)
    if (not isinstance(name, str) or not name or path.is_absolute() or
            '..' in path.parts or str(path) != name or '\x00' in name
            or any(part.startswith('.env') for part in path.parts)):
        raise ValueError('Unsafe captured input path')
    return name


def regular(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Missing regular Zero input: ' + str(path))
    return path


def api():
    return runpy.run_path(str(ROOT / 'scripts/builder'))


def prepare_inputs(target, directory, profile_path, repository, *, qualification=False):
    profiles = runpy.run_path(str(ROOT / 'image/profiles.py'))
    report = profiles['inspect_profile'](profile_path)
    if not qualification and not report['build_ready']:
        raise profiles['ProfileNotReady'](report)
    profile = profiles['load_profile'](profile_path)
    repository = Path(repository).resolve()
    locks_api = runpy.run_path(str(ROOT / 'image/zero-lock.py'))
    locks_api['validate_inputs'](ROOT)
    apt = locks_api['verify_repository'](repository)
    if digest(repository / 'apt-lock.json') != digest(ROOT / 'locks/tyrell-zero-apt.json'):
        raise ValueError('Zero repository differs from its checked-in lock')
    apps = read_json((ROOT / 'locks/tyrell-zero-apps.json').read_text())
    source_profile = profiles['repository_path'](ROOT, profile_path)
    files = {}

    def add(name, path):
        name = relative(name)
        path = regular(path)
        if name in files and files[name] != path:
            raise ValueError('Conflicting captured asset name: ' + name)
        files[name] = path

    for path in sorted((ROOT / 'image').rglob('*')):
        if '__pycache__' in path.parts or path.suffix in {'.pyc', '.pyo'}: continue
        if path.is_symlink(): raise ValueError('Symlinked image recipe input')
        if path.is_file():
            if path.suffix not in {'.py','.json','.md','.sh','.conf'}:
                raise ValueError('Unexpected image recipe file: '+str(path))
            add(str(path.relative_to(ROOT)), path)
    # Normalize a selected profile to the canonical captured name.
    files['image/profiles/tyrell-zero.json'] = regular(source_profile)
    for name in LOCKS: add('locks/' + name, ROOT / 'locks' / name)
    build=read_json((ROOT/'locks/tyrell-zero-build-inputs.json').read_text())
    proof_reference=build.get('pipeline_qualification')
    if proof_reference:
        proof_path=regular(ROOT/relative(proof_reference['path']))
        if digest(proof_path)!=proof_reference['sha256']:raise ValueError('Pipeline qualification changed')
        add(proof_reference['path'],proof_path)
        proof=read_json(proof_path.read_text())
        for key in ('runtime_verification','runner_prepare','runner_failure_fixture','bsp_replay','host_tests'):
            ref=proof.get(key)
            if ref:
                path=regular(ROOT/relative(ref['path']))
                if digest(path)!=ref['sha256']:raise ValueError('Pipeline evidence changed: '+key)
                add(ref['path'],path)
    for name in ('build-zero-image.sh', 'ensure-image-deps.sh', 'image-dependencies.txt'):
        add('builder/' + name, ROOT / 'builder' / name)
    for package in ('tyrell-zero-session', 'tyrell-zero-store', 'tyrell-zero-power-policy'):
        base = ROOT / 'packages' / package
        for path in sorted(base.rglob('*')):
            if '__pycache__' in path.parts or path.suffix in {'.pyc', '.pyo', '.deb'}: continue
            if path.is_symlink() or path.name.startswith('.env'):
                raise ValueError('Unsafe native source input')
            if path.is_file(): add(str(path.relative_to(ROOT)), path)
    add('scripts/builder', ROOT / 'scripts/builder')
    add('locks/image-apt.json', ROOT / 'locks/image-apt.json')
    for record in apps['packages']:
        source = ROOT / relative(record['input_path'])
        if (digest(regular(source)) != record['sha256'] or source.stat().st_size != record['bytes']):
            raise ValueError('Custom Zero package differs from apps lock')
        name = record['filename']
        if Path(name).name != name: raise ValueError('Unsafe package filename')
        add('assets/' + name, source)
    for record in [apps['qualification'], *apps.get('evidence', [])]:
        source=regular(ROOT/relative(record['input_path']))
        name=record['filename']
        if (Path(name).name!=name or digest(source)!=record['sha256'] or source.stat().st_size!=record['bytes']):
            raise ValueError('Native qualification evidence differs')
        add('assets/'+name,source)
    baseline = read_json((ROOT / 'image/baseline-apps.json').read_text())
    camera = read_json((ROOT / 'image/camera-packages.json').read_text())
    specs = [baseline['launcher'], baseline['drivers'], *baseline['apps'], *camera['packages']]
    for spec in specs:
        name = spec['name']
        source = Path(target) / 'downloads' / name
        if digest(regular(source)) != spec['sha256']:
            raise ValueError('Vendor input missing or changed: ' + name)
        add('assets/' + name, source)
    vendor = Path(target) / 'sources/pi-gen'
    head = subprocess.check_output(['git', '-C', str(vendor), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(vendor), 'status', '--porcelain'], text=True)
    if head != profile['pins']['pi_gen_commit'] or dirty:
        raise ValueError('Zero requires the pristine pinned vendor source cache')
    names = subprocess.check_output(['git', '-C', str(vendor), 'ls-files', '-z']).decode().split('\0')
    for name in filter(None, names): add('vendor/pi-gen/' + relative(name), vendor / name)
    for path in sorted(repository.rglob('*')):
        if path.is_symlink(): raise ValueError('Symlinked Zero APT input')
        if path.is_file(): add('apt-repository/' + str(path.relative_to(repository)), path)
    identities = {name: {'sha256': digest(path), 'bytes': path.stat().st_size,
                         'mode': 0o755 if path.stat().st_mode & 0o111 else 0o644}
                  for name, path in sorted(files.items())}
    runpy.run_path(str(ROOT/'image/resources.py'))['preflight'](
        ROOT,target,operation='prepare',input_bytes=2*sum(item['bytes'] for item in identities.values()))
    status = next(p for p in apps['packages'] if p['Package'] == 'tyrell-status')
    metadata = dict(schema=1, profile='tyrell-zero', profile_id='tyrell-zero', build_mode='zero-native', apt_profile='zero-frozen',
                    job_id=directory.name, stages=profile['stages'], output=profile['output'],
                    selected_power_preset='baseline', profile_sha256=digest(source_profile),
                    hardware_contract_sha256=profile['contracts']['hardware']['sha256'],
                    power_contract_sha256=profile['contracts']['power']['sha256'],
                    locks_sha256={name: digest(ROOT / 'locks' / name) for name in LOCKS},
                    source_commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),
                    source_dirty=bool(subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True)),
                    files=identities, capture_sha256=hashlib.sha256(json.dumps(identities,sort_keys=True).encode()).hexdigest(),
                    pi_gen_commit=profile['pins']['pi_gen_commit'], drivers_commit=profile['pins']['bsp_commit'],
                    kernel_release=profile['pins']['kernel_release'], applaunch_version=profile['pins']['applaunch_version'],
                    package_filename=status['filename'], package_version=status['Version'], package_sha256=status['sha256'],
                    image_name='tyrell-zero-trixie-arm64', hardware_qualified=False, battery_qualified=False)
    write_json(directory / 'image-metadata.json', metadata)
    archive = directory / 'image-input.tar'
    partial = archive.with_suffix('.partial')
    try:
        with tarfile.open(partial, 'w', dereference=True) as out:
            for name, path in sorted(files.items()):
                def normalize(member, name=name):
                    member.uid = member.gid = 0; member.uname = member.gname = 'root'
                    member.mode = identities[name]['mode']; member.mtime = 1794528000
                    return member
                out.add(path, arcname=name, recursive=False, filter=normalize)
            out.add(directory / 'image-metadata.json', arcname='image-metadata.json', recursive=False)
        verify_bundle(partial, metadata)
        partial.rename(archive)
    finally:
        partial.unlink(missing_ok=True)
    return archive


def verify_bundle(path, expected=None):
    with tarfile.open(regular(path)) as archive:
        members = archive.getmembers()
        names = [relative(item.name) for item in members]
        if len(names) != len(set(names)) or any(not item.isfile() or item.islnk() for item in members):
            raise ValueError('Unsafe or duplicate Zero archive members')
        if archive.getmember('image-metadata.json').size>16*1024*1024:
            raise ValueError('Oversized Zero capture metadata')
        metadata = read_json(archive.extractfile('image-metadata.json').read())
        if not isinstance(metadata,dict):raise ValueError('Zero metadata must be an object')
        if expected is not None and metadata != expected: raise ValueError('Zero capture metadata changed')
        if metadata.get('profile_id') != 'tyrell-zero' or metadata.get('build_mode') != 'zero-native':
            raise ValueError('Not captured Zero inputs')
        files = metadata['files']
        capture = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        if capture != metadata.get('capture_sha256') or set(names) != set(files) | {'image-metadata.json'}:
            raise ValueError('Zero capture inventory differs')
        for name, identity in files.items():
            member = archive.getmember(name)
            if (member.size != identity['bytes'] or member.mode != identity['mode'] or
                    hashlib.file_digest(archive.extractfile(member), 'sha256').hexdigest() != identity['sha256']):
                raise ValueError('Changed captured Zero input: ' + name)
        for name, sha in metadata['locks_sha256'].items():
            if files['locks/' + name]['sha256'] != sha: raise ValueError('Captured lock differs')
        if files['image/profiles/tyrell-zero.json']['sha256'] != metadata['profile_sha256']:
            raise ValueError('Captured Zero profile differs')
        if files['apt-repository/apt-lock.json']['sha256'] != metadata['locks_sha256']['tyrell-zero-apt.json']:
            raise ValueError('Captured Zero APT repository differs')
        return metadata


def load_job(target, identifier=None):
    if identifier is None:
        pointer = read_json((ROOT / 'build/image-job.json').read_text())
        if Path(pointer['storage']).resolve() != Path(target).resolve():
            raise ValueError('Latest image job uses different storage')
        identifier = pointer['job_id']
    if not JOB.fullmatch(identifier): raise ValueError('Invalid Zero job ID')
    directory = Path(target) / 'jobs' / identifier
    job = read_json(regular(directory / 'job.json').read_text())
    if (job.get('schema') != 1 or job.get('build_mode') != 'zero-native' or
            job.get('job_id') != identifier or job.get('guest_input') != GUEST_BASE + '/' + identifier + '/input'):
        raise ValueError('Inconsistent Zero job identity')
    bundle = regular(directory / 'image-input.tar')
    if digest(bundle) != job.get('bundle_sha256'): raise ValueError('Zero archive missing or changed')
    metadata = verify_bundle(bundle)
    if metadata['job_id'] != identifier or metadata['capture_sha256'] != job.get('capture_sha256'):
        raise ValueError('Zero job capture differs')
    attempts = job.get('attempts')
    if not isinstance(attempts, list) or job.get('active_attempt') != len(attempts):
        raise ValueError('Invalid Zero attempt selection')
    for number, attempt in enumerate(attempts, 1):
        if attempt != attempt_record(job, number, 'fresh' if number == 1 else 'stage2'):
            raise ValueError('Invalid Zero attempt identity')
    return job


def attempt_record(job, number, mode):
    if type(number) is not int or number < 1 or mode not in {'fresh','stage2'}:
        raise ValueError('Invalid Zero attempt')
    return dict(job_id=job['job_id'], attempt_id=number, bundle_sha256=job['bundle_sha256'],
                mode=mode, guest_dir=GUEST_BASE + '/' + job['job_id'] + '/attempts/' + f'{number:04d}')


def selected(job, number=None):
    number = job['active_attempt'] if number is None else number
    if type(number) is not int or not 1 <= number <= len(job['attempts']):
        raise ValueError('No such Zero attempt')
    return job['attempts'][number - 1]


def outcome(job, attempt, common):
    import shlex
    state = common['unit_state']()
    fields = {'TYRELL_IMAGE_JOB_ID':job['job_id'], 'TYRELL_IMAGE_ATTEMPT_ID':str(attempt['attempt_id']),
              'TYRELL_IMAGE_INPUT_SHA256':job['bundle_sha256']}
    env = shlex.split(state.get('Environment', ''))
    matches = all(key + '=' + value in env for key,value in fields.items())
    b = api()
    text = b['guest']('sudo','-n','python3','-c',
        'from pathlib import Path;import sys;p=Path(sys.argv[1]);print(p.read_text() if p.is_file() and not p.is_symlink() else "null")',
        attempt['guest_dir'] + '/result.json', stdout=subprocess.PIPE, text=True).stdout
    result = read_json(text)
    if result is not None and (any(result.get(key) != attempt[key] for key in ('job_id','attempt_id','bundle_sha256'))
                               or type(result.get('exit_status')) is not int):
        raise ValueError('Stale or mismatched Zero attempt result')
    if result is not None and result['exit_status']==0 and not SHA.fullmatch(result.get('manifest_sha256','')):
        raise ValueError('Successful Zero result lacks verified output identity')
    if matches and common['running'](state): status = 'running'
    elif result is not None: status = 'succeeded' if result['exit_status'] == 0 else 'failed'
    elif matches and state.get('ActiveState') == 'failed': status = 'failed'
    else: raise ValueError('No authoritative Zero attempt state; reattach rather than retry')
    return dict(job_id=job['job_id'], attempt_id=attempt['attempt_id'], state=status, result=result)


def stage_guest(job, directory):
    """Copy then verify into a root-owned namespace before any root execution."""
    b = api()
    transfer = '/var/tmp/tyrell-zero-transfer-' + job['job_id'] + '.tar'
    b['lima']('copy','--backend=scp',str(directory / 'image-input.tar'),b['INSTANCE'] + ':' + transfer)
    installer = '''import hashlib,os,pathlib,re,shutil,stat,sys,tarfile
source,job,expected=sys.argv[1:]
assert re.fullmatch(r'[0-9]{8}T[0-9]{12}Z-[0-9a-f]{8}',job)
base=pathlib.Path('/var/tmp/tyrell-zero-image-jobs')
if base.is_symlink():raise ValueError('redirected Zero job root')
base.mkdir(mode=0o700,exist_ok=True)
assert base.stat().st_uid==0 and base.stat().st_mode&0o077==0
destination=base/job
if destination.exists() or destination.is_symlink():raise ValueError('preserving existing Zero job')
destination.mkdir(mode=0o700)
bundle=destination/'image-input.tar'
with open(source,'rb') as src,bundle.open('xb') as out:shutil.copyfileobj(src,out)
with bundle.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
assert actual==expected,'transferred Zero archive changed'
root=destination/'input';root.mkdir(mode=0o700)
with tarfile.open(bundle) as archive:
 names=set()
 for item in archive.getmembers():
  p=pathlib.PurePosixPath(item.name)
  if (not item.isfile() or item.islnk() or p.is_absolute() or '..' in p.parts or
      str(p)!=item.name or item.name in names):raise ValueError('unsafe Zero archive')
  names.add(item.name)
 archive.extractall(root,filter='data')
os.unlink(source)
'''
    b['guest']('sudo','-n','python3','-c',installer,transfer,job['job_id'],job['bundle_sha256'])


def launch(target, job, common, *, mode='fresh'):
    b = api()
    old = common['assert_idle']()
    resources = runpy.run_path(str(ROOT / 'image/resources.py'))
    directory = Path(target) / 'jobs' / job['job_id']
    size = (directory / 'image-input.tar').stat().st_size
    resources['preflight'](ROOT, target, operation='build', input_bytes=size)
    free = int(b['guest']('python3','-c','import shutil;print(shutil.disk_usage("/var/tmp").free)',
                         stdout=subprocess.PIPE,text=True).stdout.strip())
    if free < 30_000_000_000 + 2 * size:
        raise ValueError('Zero build needs 30 GB guest work reserve plus captured inputs')
    number = len(job['attempts']) + 1
    attempt = attempt_record(job, number, mode)
    # The guest validates terminal state, stage1 and mount cleanup before it
    # quarantines failed stage2 work. No current-checkout inputs enter a retry.
    if mode == 'stage2':
        b['guest']('sudo','-n','python3',job['guest_input'] + '/image/zero_resume.py',
                   '--job-root',GUEST_BASE + '/' + job['job_id'],
                   '--bundle-sha256',job['bundle_sha256'],'--previous-attempt',str(job['active_attempt']),
                   '--authorize-attempt',str(number))
    job['attempts'].append(attempt); job['active_attempt'] = number
    write_json(directory / 'job.json', job)
    write_json(ROOT / 'build/image-job.json', {'job_id':job['job_id'],'storage':str(Path(target).resolve())})
    if old.get('LoadState') == 'loaded':
        b['guest']('sudo','-n','systemctl','reset-failed',common['UNIT'])
        b['guest']('sudo','-n','systemctl','stop',common['UNIT'])
    try:
        b['guest']('sudo','-n','mkdir','-m','700','-p',attempt['guest_dir'])
        b['guest']('sudo','-n','systemd-run','--unit=' + common['UNIT'].removesuffix('.service'),
            '--no-block','--service-type=oneshot','--property=RemainAfterExit=yes',
            '--property=Environment=TYRELL_IMAGE_JOB_ID=' + job['job_id'],
            '--property=Environment=TYRELL_IMAGE_ATTEMPT_ID=' + str(number),
            '--property=Environment=TYRELL_IMAGE_INPUT_SHA256=' + job['bundle_sha256'],
            '--property=StandardOutput=append:' + attempt['guest_dir'] + '/build.log',
            '--property=StandardError=inherit','/usr/bin/flock','--nonblock',common['GUEST_LOCK'],
            '/usr/bin/python3',job['guest_input'] + '/image/run-zero-job.py',
            '--job',job['job_id'],'--attempt',str(number),'--bundle-sha256',job['bundle_sha256'],'--mode',mode)
    except (OSError,subprocess.CalledProcessError):
        if not reconcile_launch(job,attempt,common,b):raise
    print(f"Started Zero job {job['job_id']} attempt {number}",flush=True)
    return attempt


def reconcile_launch(job,attempt,common,b):
    """Reattach a bound service, or durably record a proven pre-start failure."""
    import shlex
    state=common['unit_state']()
    env=shlex.split(state.get('Environment',''))
    if all(k+'='+str(v)in env for k,v in {'TYRELL_IMAGE_JOB_ID':job['job_id'],
            'TYRELL_IMAGE_ATTEMPT_ID':attempt['attempt_id'],'TYRELL_IMAGE_INPUT_SHA256':job['bundle_sha256']}.items()):
        return True
    if state.get('LoadState')not in {'loaded','not-found'}:
        raise ValueError('Cannot establish whether Zero launch started; reattach before retry')
    failed={key:attempt[key] for key in ('job_id','attempt_id','bundle_sha256')}
    failed.update(exit_status=125,manifest_sha256=None,launch_failed=True)
    code='''import json,os,pathlib,sys
p=pathlib.Path(sys.argv[1]);value=json.loads(sys.argv[2])
assert p.is_absolute() and str(p).startswith('/var/tmp/tyrell-zero-image-jobs/')
assert p.parents[1].is_dir() and p.parents[1].stat().st_uid==0
p.mkdir(mode=0o700,parents=True,exist_ok=True)
assert not p.is_symlink() and p.stat().st_uid==0 and not p.stat().st_mode&0o022
target=p/'result.json'
with target.open('x') as out:
 os.fchmod(out.fileno(),0o600);json.dump(value,out);out.flush();os.fsync(out.fileno())
'''
    b['guest']('sudo','-n','python3','-c',code,attempt['guest_dir'],json.dumps(failed))
    return False


def wait(job, attempt, common):
    import time
    while True:
        result = outcome(job, attempt, common)
        if result['state'] == 'succeeded': return
        if result['state'] == 'failed': raise ValueError('Zero attempt failed; inspect its log before resume')
        time.sleep(10)


def export(target, job, attempt, common):
    common['assert_idle']()
    state=outcome(job, attempt, common)
    if state['state'] != 'succeeded':
        raise ValueError('Only a verified successful Zero attempt can be exported')
    b = api()
    identity = {key:attempt[key] for key in ('job_id','attempt_id','bundle_sha256')}
    images = Path(target) / 'images'; images.mkdir(exist_ok=True)
    destination = images / (job['job_id'] + '-attempt-' + f"{attempt['attempt_id']:04d}")
    if destination.is_symlink(): raise ValueError('Redirected Zero export')
    if destination.exists():
        recorded=common['verify_checksums'](destination)
        if read_json((destination / 'attempt.json').read_text()) != identity:
            raise ValueError('Existing export belongs to another attempt')
        verify_guest_manifest(destination,state['result']['manifest_sha256'],recorded)
    else:
        size = int(b['guest']('sudo','-n','python3','-c',
             'import pathlib,sys;print(sum(p.stat().st_size+4096 for p in pathlib.Path(sys.argv[1]).rglob("*") if p.is_file()))',
             attempt['guest_dir'] + '/output',stdout=subprocess.PIPE,text=True).stdout.strip())
        bundle = Path(target) / 'jobs' / job['job_id'] / 'image-input.tar'
        runpy.run_path(str(ROOT/'image/resources.py'))['preflight'](
            ROOT,target,operation='export',export_bytes=size+bundle.stat().st_size)
        staging = Path(tempfile.mkdtemp(prefix='.zero-export-',dir=images))
        try:
            transfer = staging/'export.tar'
            with transfer.open('wb') as out:
                b['guest']('sudo','-n','flock','--shared','--nonblock',common['GUEST_LOCK'],
                           'tar','-C',attempt['guest_dir']+'/output','-cf','-','.',stdout=out)
            root = staging/'files';root.mkdir()
            with tarfile.open(transfer) as archive:
                seen=set()
                for member in archive.getmembers():
                    name=PurePosixPath(member.name)
                    if (name.is_absolute() or '..' in name.parts or str(name) in seen or
                            not(member.isfile() or member.isdir()) or member.issym() or member.islnk()):
                        raise ValueError('Unsafe Zero export')
                    seen.add(str(name))
                archive.extractall(root,filter='data')
            recorded=common['verify_checksums'](root)
            if digest(root/'image-manifest.json')!=state['result']['manifest_sha256']:
                raise ValueError('Guest output differs from durable result')
            if read_json((root/'attempt.json').read_text()) != identity:
                raise ValueError('Stale Zero output attempt')
            metadata=verify_bundle(bundle)
            manifest=read_json((root/'image-manifest.json').read_text())
            if (manifest.get('inputs') != metadata or manifest.get('verification',{}).get('status') != 'passed'
                    or read_json((root/'build-inputs.json').read_text()) != metadata):
                raise ValueError('Zero output does not prove captured inputs passed')
            shutil.copyfile(root/'image-manifest.json',root/'guest-image-manifest.json')
            verify_guest_manifest(root,state['result']['manifest_sha256'],recorded)
            shutil.copyfile(bundle,root/'image-input.tar')
            if digest(root/'image-input.tar') != job['bundle_sha256']:raise ValueError('Archived input copy changed')
            manifest['provenance']={**identity,'input_archive':'image-input.tar',
                                    'guest_manifest_sha256':state['result']['manifest_sha256']}
            manifest['sha256']['image-input.tar']=job['bundle_sha256']
            write_json(root/'image-manifest.json',manifest)
            files={str(p.relative_to(root)):digest(p) for p in sorted(root.rglob('*'))
                   if p.is_file() and p.name!='SHA256SUMS'}
            (root/'SHA256SUMS').write_text(''.join(f'{sha}  {name}\n' for name,sha in sorted(files.items())))
            recorded=common['verify_checksums'](root)
            root.rename(destination)
        finally:shutil.rmtree(staging)
    validate_host_export(destination,job,attempt,state['result']['manifest_sha256'],common,recorded=recorded)
    link=ROOT/'image-artifacts-zero'
    if link.exists() and not link.is_symlink():raise ValueError('Preserve existing Zero artifact directory')
    temp=ROOT/('.image-artifacts-zero-'+job['job_id'])
    if os.path.lexists(temp):raise ValueError('Preserve previous Zero publication temporary path')
    temp.symlink_to(destination,target_is_directory=True);temp.replace(link)
    print('Verified Zero artifacts: '+str(destination),flush=True)
    return destination


def verify_guest_manifest(directory, expected, recorded):
    path=regular(Path(directory)/'guest-image-manifest.json')
    if digest(path)!=expected:raise ValueError('Exported guest manifest differs from durable result')
    guest=read_json(path.read_text())
    if guest.get('verification',{}).get('status')!='passed':raise ValueError('Guest image verification failed')
    for name,sha in guest['sha256'].items():
        relative(name)
        if recorded.get(name)!=sha:raise ValueError('Export no longer matches verified guest artifact: '+name)


def validate_host_export(directory,job,attempt,guest_sha,common,*,recorded=None):
    import copy
    directory=Path(directory)
    if recorded is None:recorded=common['verify_checksums'](directory)
    verify_guest_manifest(directory,guest_sha,recorded)
    if recorded.get('image-input.tar')!=job['bundle_sha256']:
        raise ValueError('Exported input archive differs from captured job')
    expected=copy.deepcopy(read_json((directory/'guest-image-manifest.json').read_text()))
    identity={key:attempt[key] for key in ('job_id','attempt_id','bundle_sha256')}
    expected['provenance']={**identity,'input_archive':'image-input.tar','guest_manifest_sha256':guest_sha}
    expected['sha256']['image-input.tar']=job['bundle_sha256']
    if read_json((directory/'image-manifest.json').read_text())!=expected:
        raise ValueError('Host manifest differs from verified guest augmentation')
    if read_json((directory/'attempt.json').read_text())!=identity:
        raise ValueError('Export attempt identity differs')


def is_zero_job(target, identifier=None):
    if identifier is None:
        pointer=ROOT/'build/image-job.json'
        if not pointer.is_file():return False
        data=read_json(pointer.read_text())
        if Path(data['storage']).resolve()!=Path(target).resolve():return False
        identifier=data['job_id']
    if not JOB.fullmatch(identifier):raise ValueError('Invalid image job ID')
    record=Path(target)/'jobs'/identifier/'job.json'
    return record.is_file() and not record.is_symlink() and read_json(record.read_text()).get('build_mode')=='zero-native'


def main(args, common):
    target=common['storage'](check_space=False,override=args.storage)
    if args.action=='build':
        with common['LOCK'](ROOT):
            resources=runpy.run_path(str(ROOT/'image/resources.py'))
            resources['preflight'](ROOT,target,operation='prepare' if args.prepare_only else 'build')
            directory=common['new_job'](target)
            repository=Path(args.apt_repository) if args.apt_repository else target/'zero/apt-repository'
            bundle=prepare_inputs(target,directory,args.profile,repository,qualification=args.prepare_only)
            metadata=verify_bundle(bundle)
            job=dict(schema=1,build_mode='zero-native',job_id=directory.name,bundle_sha256=digest(bundle),
                     capture_sha256=metadata['capture_sha256'],guest_input=GUEST_BASE+'/'+directory.name+'/input',
                     active_attempt=0,attempts=[])
            write_json(directory/'job.json',job)
            print('Verified Zero input bundle: '+str(bundle),flush=True)
            if args.prepare_only:return
            b=api();b['up']();common['assert_idle']();stage_guest(job,directory)
            attempt=launch(target,job,common)
            if not args.no_wait:wait(job,attempt,common);export(target,job,attempt,common)
    else:
        job=load_job(target,args.job)
        if job['active_attempt']==0:
            if args.action=='status' and args.attempt is None:
                print(json.dumps({'job_id':job['job_id'],'state':'prepared',
                                  'bundle_sha256':job['bundle_sha256'],'attempts':0},indent=2))
                return
            raise ValueError('Zero inputs are prepared; no image attempt has been launched')
        if args.action=='resume':
            with common['LOCK'](ROOT):
                attempt=selected(job)
                common['assert_idle']()
                if outcome(job,attempt,common)['state']!='failed':
                    raise ValueError('Only an authoritatively failed attempt can resume')
                attempt=launch(target,job,common,mode='stage2')
                if not args.no_wait:wait(job,attempt,common);export(target,job,attempt,common)
            return
        attempt=selected(job,args.attempt)
        if args.action=='status':print(json.dumps(outcome(job,attempt,common),indent=2))
        elif args.action=='logs':api()['guest']('sudo','-n','tail','-n',str(args.lines),
                                  *(['-F'] if args.follow else []),attempt['guest_dir']+'/build.log')
        elif args.action=='wait':wait(job,attempt,common)
        elif args.action=='export':
            with common['LOCK'](ROOT):export(target,job,attempt,common)
