#!/usr/bin/env python3
"""Build a file image in the existing ARM64 VM; never access the handheld."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tarfile
import time
import tempfile
import uuid
import sys

ROOT = Path(__file__).resolve().parents[1]
B = runpy.run_path(str(ROOT / 'scripts/builder'))
BASELINE = '5f4c44f3431bf1dfb968c10f7228ad6fa0c66cf4'
UNIT = 'tyrell-image-build.service'
GUEST_LOCK = '/var/tmp/tyrell-locks/builder.lock'
LOCK = runpy.run_path(str(ROOT / 'image/build_lock.py'))['builder_lock']
OUTPUT = '/var/tmp/tyrell-image-work/output'


def sha(path):
    return B['digest'](path)


def storage(check_space=True, override=None):
    configured = ROOT / 'build/image-storage.json'
    path = override or os.environ.get('TYRELL_IMAGE_STORAGE')
    if not path and configured.exists():
        path = json.loads(configured.read_text())['path']
    target = Path(path).expanduser() if path else ROOT / 'build/image-storage'
    if path and not target.exists():
        raise RuntimeError('Configured image storage is unavailable: ' + str(target))
    target.mkdir(parents=True, exist_ok=True)
    if check_space and shutil.disk_usage(target).free < 15 * 1024**3:
        raise RuntimeError('Image storage needs at least 15 GiB free; VM backing storage also needs room')
    return target.resolve()


def fetch(spec, destination):
    path = destination / spec['name']
    if Path(spec['name']).name != spec['name']:
        raise RuntimeError('Unsafe asset filename')
    if path.is_symlink():
        raise RuntimeError('Cached asset must be a regular file: ' + str(path))
    if path.exists():
        if not path.is_file() or sha(path) != spec['sha256'] or path.stat().st_size != spec['bytes']:
            raise RuntimeError('Cached asset has wrong checksum: ' + str(path))
        return path
    partial = path.with_suffix(path.suffix + '.partial')
    if partial.is_symlink() or (partial.exists() and not partial.is_file()):
        raise RuntimeError('Unsafe partial asset: ' + str(partial))
    subprocess.run(['curl', '--fail', '--location', '--retry', '3', '--connect-timeout', '20',
                    '--proto', '=https', '--proto-redir', '=https',
                    '--silent', '--show-error', '--output', str(partial), spec['url']], check=True)
    if sha(partial) != spec['sha256'] or partial.stat().st_size != spec['bytes']:
        raise RuntimeError('Downloaded asset failed verification: ' + spec['name'])
    partial.replace(path)
    print('Verified input: ' + path.name, flush=True)
    return path


def stock_tools():
    return runpy.run_path(str(ROOT / 'image/stock-image.py'))


def stock_image_size(path):
    return runpy.run_path(str(ROOT / 'image/overlay-stock.py'))['xz_uncompressed_size'](path)


def preflight_stock_host(target, compressed_bytes):
    resources = runpy.run_path(str(ROOT / 'image/resources.py'))
    # Reserve both the cached image and its captured archive copy before either
    # is written. The regular image budget additionally covers export staging.
    return resources['preflight'](ROOT, target, operation='prepare',
                                   input_bytes=2 * compressed_bytes)


def prepare_stock(target, job_directory, lock_path):
    """Capture an explicit official image lock and an offline Tyrell overlay."""
    lock_path = Path(lock_path).expanduser().absolute()
    if lock_path.is_symlink() or not lock_path.is_file():
        raise RuntimeError('Stock image lock must be a regular file')
    tools = stock_tools()
    lock_sha = sha(lock_path)
    lock = tools['load_lock'](lock_path)
    preflight_stock_host(target, lock['image']['bytes'])
    assets = target / 'downloads/stock-images' / lock['image']['sha256']
    assets.mkdir(parents=True, exist_ok=True)
    stock = fetch(lock['image'], assets)
    raw_bytes = stock_image_size(stock)
    package = ROOT / 'artifacts/tyrell-status_0.1.0-1_arm64.deb'
    manifest = ROOT / 'artifacts/manifest.json'
    if not package.is_file() or package.is_symlink() or not manifest.is_file() or manifest.is_symlink():
        raise RuntimeError('Run make package first; its verified .deb and manifest are required')
    package_manifest = json.loads(manifest.read_text())
    package_sha = sha(package)
    if package_manifest['sha256'].get(package.name) != package_sha:
        raise RuntimeError('Custom package does not match its build manifest')
    # Every selected lock is captured at the same canonical path. It can live
    # outside the checkout without allowing arbitrary files into the recipe.
    recipe = {str(p.relative_to(ROOT)): p for p in sorted((ROOT / 'image').rglob('*'))
              if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    recipe['image/stock-image.json'] = lock_path
    for name in ('build-stock-image.sh', 'ensure-image-deps.sh', 'image-dependencies.txt'):
        recipe['builder/' + name] = ROOT / 'builder' / name
    recipe['scripts/builder'] = ROOT / 'scripts/builder'
    recipe['Makefile'] = ROOT / 'Makefile'
    for name, path in recipe.items():
        if path.is_symlink() or path.name.startswith('.env') or not path.is_file():
            raise RuntimeError('Unsafe stock image recipe input: ' + name)
        if name.startswith('image/') and path.suffix not in {'.py', '.json', '.md', '.sh', '.conf'}:
            raise RuntimeError('Unexpected image recipe input: ' + name)
    metadata = {
        'job_id': job_directory.name, 'build_mode': 'stock-image', 'apt_profile': 'stock-image',
        'image_name': 'tyrell-trixie-arm64', 'pi_gen_commit': lock['pi_gen_commit'],
        'drivers_commit': lock['drivers_commit'], 'kernel_release': lock['kernel_release'],
        'applaunch_version': lock['applaunch_version'], 'package_version': '0.1.0-1',
        'package_filename': package.name, 'package_sha256': package_sha,
        'package_manifest_sha256': sha(manifest),
        'package_source_revision': package_manifest['source']['source_revision'],
        'project_commit': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
        'stock_image_lock_sha256': lock_sha, 'stock_base_sha256': lock['image']['sha256'],
        'stock_uncompressed_bytes': raw_bytes,
        'stock_release': lock['upstream']['tag'],
        'recipe_sha256': {name: sha(path) for name, path in recipe.items()},
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'first_boot': {'wizard': 'vendor LaunchWizard', 'ssh_enabled': False,
                       'bootstrap_user_password': 'disabled', 'wifi': 'unprovisioned'},
        'scope': 'Official stock image copy plus offline Tyrell installation; no handheld or SD access.',
        'reproducibility': 'Exact upstream OS image and Tyrell package locked; no live image APT; output bytes not claimed identical.',
    }
    metadata_path = job_directory / 'image-metadata.json'
    write_json(metadata_path, metadata)
    bundle = job_directory / 'image-input.tar'
    partial = bundle.with_suffix('.partial')
    # All inputs above are checked regular files. Capture their contents even
    # when a selected lock also appears elsewhere in the repository recipe.
    with tarfile.open(partial, 'w', dereference=True) as archive:
        for name, path in recipe.items():
            archive.add(path, arcname=name, recursive=False)
        archive.add(stock, arcname='assets/' + stock.name)
        archive.add(package, arcname='assets/' + package.name)
        archive.add(manifest, arcname='package-manifest.json')
        archive.add(metadata_path, arcname='image-metadata.json')
    partial.replace(bundle)
    verify_bundle(bundle, metadata)
    return bundle


def prepare(target, job_directory, apt_repository=None, discovery=False):
    apt_lock_sha = None
    if apt_repository is not None:
        apt_lock_sha = sha(apt_repository / 'apt-lock.json')
        source_lock = ROOT / 'locks/image-apt.json'
        if not source_lock.is_file() or sha(source_lock) != apt_lock_sha:
            raise RuntimeError('Frozen APT repository does not match locks/image-apt.json')
        verifier = runpy.run_path(str(ROOT / 'image/apt-lock.py'))
        verifier['verify_repository'](apt_repository)
        if sha(apt_repository / 'apt-lock.json') != apt_lock_sha:
            raise RuntimeError('APT lock changed during repository verification')
    elif not discovery:
        raise RuntimeError('A frozen APT repository is required; use --apt-repository or explicitly --discovery')
    lock = json.loads((ROOT / 'image/baseline-apps.json').read_text())
    assets = target / 'downloads'
    assets.mkdir(exist_ok=True)
    specs = [lock['launcher'], lock['drivers'], *lock['apps']]
    camera_lock = ROOT / 'image/camera-packages.json'
    if camera_lock.exists():
        specs.extend(json.loads(camera_lock.read_text())['packages'])
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda spec: fetch(spec, assets), specs))
    package = ROOT / 'artifacts/tyrell-status_0.1.0-1_arm64.deb'
    manifest = ROOT / 'artifacts/manifest.json'
    if not package.is_file() or not manifest.is_file():
        raise RuntimeError('Run make package first; its verified .deb and manifest are required')
    package_manifest = json.loads(manifest.read_text())
    package_sha = sha(package)
    if package_manifest['sha256'].get(package.name) != package_sha:
        raise RuntimeError('Custom package does not match its build manifest')
    vendor = target / 'sources/pi-gen'
    vendor.parent.mkdir(exist_ok=True)
    if not vendor.exists():
        subprocess.run(['git', 'init', '--quiet', '--initial-branch=vendor', str(vendor)], check=True)
        subprocess.run(['git', '-C', str(vendor), 'remote', 'add', 'origin',
                        'https://github.com/CardputerZero/pi-gen.git'], check=True)
        subprocess.run(['git', '-C', str(vendor), 'fetch', '--depth=1', 'origin', BASELINE], check=True)
        subprocess.run(['git', '-C', str(vendor), 'checkout', '--detach', 'FETCH_HEAD'], check=True)
    head = subprocess.check_output(['git', '-C', str(vendor), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(vendor), 'status', '--porcelain'], text=True)
    if head != BASELINE or dirty:
        raise RuntimeError('Vendor cache must be the pristine pinned baseline')
    recipe = []
    for p in sorted((ROOT / 'image').rglob('*')):
        if '__pycache__' in p.parts or p.suffix == '.pyc':
            continue
        if p.is_symlink() or p.name.startswith('.env'):
            raise RuntimeError('Unsafe image recipe input: ' + str(p))
        if p.is_file():
            if p.suffix not in {'.py', '.json', '.md', '.sh', '.conf'}:
                raise RuntimeError('Unexpected image recipe file: ' + str(p))
            recipe.append(p)
    recipe.extend(ROOT / ('builder/' + name) for name in
                  ['build-image.sh', 'ensure-image-deps.sh', 'image-dependencies.txt'])
    recipe.extend([ROOT / 'scripts/builder', ROOT / 'Makefile'])
    if (ROOT / 'locks/image-apt.json').is_file():
        recipe.append(ROOT / 'locks/image-apt.json')
    recipe_hashes = {str(p.relative_to(ROOT)): sha(p) for p in recipe}
    metadata = {
        'job_id': job_directory.name,
        'apt_profile': 'discovery' if discovery else 'frozen',
        'apt_lock_sha256': apt_lock_sha,
        'image_name': 'tyrell-trixie-arm64', 'pi_gen_commit': BASELINE,
        'drivers_commit': lock['drivers']['commit'], 'kernel_release': '6.18.39+rpt-rpi-v8',
        'applaunch_version': '0.6.105', 'package_version': '0.1.0-1',
        'package_filename': package.name, 'package_sha256': package_sha,
        'package_source_revision': package_manifest['source']['source_revision'],
        'project_commit': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
        'recipe_sha256': recipe_hashes, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'first_boot': {'wizard': 'vendor LaunchWizard', 'ssh_enabled': False,
                       'bootstrap_user_password': 'disabled', 'wifi': 'unprovisioned; world regulatory domain'},
        'scope': 'Built from vendor pi-gen source. No CardputerZero access, flashing or SD card writes.',
        'reproducibility': ('Vendor/driver/app inputs pinned; image APT package bytes and stage selections frozen; '
                            'builder tool versions recorded; byte-identical output not claimed.' if not discovery else
                            'Discovery profile: vendor/driver/app inputs pinned; image APT inputs mutable; '
                            'builder tool versions recorded; byte-identical output not claimed.'),
    }
    metadata_path = job_directory / 'image-metadata.json'
    metadata_path.write_text(json.dumps(metadata, indent=2) + '\n')
    bundle = job_directory / 'image-input.tar'
    with tarfile.open(bundle.with_suffix('.partial'), 'w') as tar:
        for p in recipe:
            tar.add(p, arcname=str(p.relative_to(ROOT)), recursive=False)
        tar.add(vendor, arcname='vendor/pi-gen')
        for spec in specs:
            tar.add(assets / spec['name'], arcname='assets/' + spec['name'])
        tar.add(package, arcname='assets/' + package.name)
        tar.add(manifest, arcname='package-manifest.json')
        tar.add(metadata_path, arcname='image-metadata.json')
        if apt_repository is not None:
            tar.add(apt_repository, arcname='apt-repository')
    bundle.with_suffix('.partial').replace(bundle)
    verify_bundle(bundle, metadata)
    return bundle


def verify_bundle(bundle, metadata=None):
    """Verify the bytes actually captured, detecting edits during preparation."""
    import hashlib
    with tarfile.open(bundle) as archive:
        metadata_member = archive.getmember('image-metadata.json')
        if not metadata_member.isfile() or metadata_member.issym() or metadata_member.islnk():
            raise RuntimeError('Unsafe captured image metadata')
        captured = json.load(archive.extractfile(metadata_member))
        if metadata is not None and captured != metadata:
            raise RuntimeError('Captured image metadata changed during preparation')
        names = archive.getnames()
        if len(names) != len(set(names)):
            raise RuntimeError('Duplicate image input archive member')
        for name, expected in captured['recipe_sha256'].items():
            member = archive.getmember(name)
            if not member.isfile() or member.issym() or member.islnk():
                raise RuntimeError('Unsafe captured recipe: ' + name)
            digest = hashlib.file_digest(archive.extractfile(member), 'sha256').hexdigest()
            if digest != expected:
                raise RuntimeError('Recipe changed while preparing input: ' + name)
        package_member = archive.getmember('assets/' + captured['package_filename'])
        if not package_member.isfile() or package_member.issym() or package_member.islnk():
            raise RuntimeError('Unsafe captured application package')
        package = archive.extractfile(package_member)
        if hashlib.file_digest(package, 'sha256').hexdigest() != captured['package_sha256']:
            raise RuntimeError('Package changed while preparing image input')
        if captured.get('apt_profile') == 'frozen':
            verify_captured_apt(archive, captured)
        elif captured.get('apt_profile') == 'stock-image':
            verify_captured_stock(archive, captured)
    return captured


def verify_captured_stock(archive, metadata):
    """Bind the stock base bytes to the exact selected lock, without extraction."""
    import hashlib
    from pathlib import PurePosixPath
    for member in archive.getmembers():
        name = PurePosixPath(member.name)
        if (not member.isfile() or member.issym() or member.islnk() or name.is_absolute() or
                '..' in name.parts or str(name) != member.name):
            raise RuntimeError('Unsafe captured stock image input: ' + member.name)
    member = archive.getmember('image/stock-image.json')
    if not member.isfile() or member.issym() or member.islnk():
        raise RuntimeError('Unsafe captured stock image lock')
    raw = archive.extractfile(member).read()
    lock_sha = hashlib.sha256(raw).hexdigest()
    if (lock_sha != metadata.get('stock_image_lock_sha256') or
            lock_sha != metadata['recipe_sha256'].get('image/stock-image.json')):
        raise RuntimeError('Stock image lock changed while preparing input')
    lock = stock_tools()['validate_lock'](json.loads(raw))
    if metadata.get('build_mode') != 'stock-image' or metadata.get('stock_base_sha256') != lock['image']['sha256']:
        raise RuntimeError('Stock image identity differs from captured metadata')
    # Earlier retained jobs predate the guest-capacity check. Keep their
    # status/export inputs readable; every newly prepared build records size.
    if 'stock_uncompressed_bytes' in metadata and (type(metadata['stock_uncompressed_bytes']) is not int or metadata['stock_uncompressed_bytes'] <= 0):
        raise RuntimeError('Stock image metadata lacks a valid uncompressed size')
    for key in ('pi_gen_commit', 'drivers_commit', 'kernel_release', 'applaunch_version'):
        if metadata.get(key) != lock[key]:
            raise RuntimeError('Stock image metadata differs from lock: ' + key)
    image = archive.getmember('assets/' + lock['image']['name'])
    if (not image.isfile() or image.issym() or image.islnk() or image.size != lock['image']['bytes'] or
            hashlib.file_digest(archive.extractfile(image), 'sha256').hexdigest() != lock['image']['sha256']):
        raise RuntimeError('Stock image changed while preparing input')
    manifest = archive.getmember('package-manifest.json')
    if (not manifest.isfile() or manifest.issym() or manifest.islnk() or
            hashlib.file_digest(archive.extractfile(manifest), 'sha256').hexdigest() != metadata.get('package_manifest_sha256')):
        raise RuntimeError('Package manifest changed while preparing input')
    expected_names = set(metadata['recipe_sha256']) | {
        'image-metadata.json', 'package-manifest.json',
        'assets/' + metadata['package_filename'], 'assets/' + lock['image']['name']}
    if set(archive.getnames()) != expected_names:
        raise RuntimeError('Captured stock image bundle differs from its file allowlist')


def verify_captured_apt(archive, metadata):
    """Check captured repository bytes directly in the tar without extraction."""
    import hashlib
    import re
    from pathlib import PurePosixPath
    expected = metadata.get('apt_lock_sha256')
    manifest_name = 'apt-repository/apt-lock.json'
    manifest = archive.getmember(manifest_name)
    if not manifest.isfile() or manifest.issym() or manifest.islnk():
        raise RuntimeError('Unsafe captured APT lock')
    if hashlib.file_digest(archive.extractfile(manifest), 'sha256').hexdigest() != expected:
        raise RuntimeError('APT lock changed while preparing input')
    if metadata['recipe_sha256'].get('locks/image-apt.json') != expected:
        raise RuntimeError('Captured APT lock differs from the project source lock')
    lock = json.load(archive.extractfile(manifest))
    files = lock.get('files', {})
    if lock.get('schema') != 1 or lock.get('architecture') != 'arm64' or not files:
        raise RuntimeError('Invalid captured APT lock')
    actual = set()
    for member in archive.getmembers():
        if member.name.startswith('apt-repository/') and not member.isdir():
            if not member.isfile() or member.issym() or member.islnk():
                raise RuntimeError('Unsafe captured APT repository path')
            actual.add(member.name.removeprefix('apt-repository/'))
    if actual != set(files) | {'apt-lock.json'}:
        raise RuntimeError('Captured APT repository differs from its file allowlist')
    for name, spec in files.items():
        relative = PurePosixPath(name)
        if (not name or relative.is_absolute() or '..' in relative.parts or str(relative) != name
                or not re.fullmatch('[0-9a-f]{64}', spec.get('sha256', ''))
                or type(spec.get('bytes')) is not int or spec['bytes'] < 0):
            raise RuntimeError('Invalid captured APT file record')
        member = archive.getmember('apt-repository/' + name)
        if (member.size != spec['bytes'] or
                hashlib.file_digest(archive.extractfile(member), 'sha256').hexdigest() != spec['sha256']):
            raise RuntimeError('APT file changed while preparing input: ' + name)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.partial')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def new_job(target):
    identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]
    directory = target / 'jobs' / identifier
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def load_job(target, identifier=None):
    import re
    if identifier is None:
        pointer = ROOT / 'build/image-job.json'
        if not pointer.is_file():
            raise RuntimeError('No tracked image job; specify --job ID or start a build')
        current = json.loads(pointer.read_text())
        if Path(current['storage']).resolve() != target:
            raise RuntimeError('Latest job uses another storage directory: ' + current['storage'])
        identifier = current['job_id']
    if not re.fullmatch(r'[0-9]{8}T[0-9]{12}Z-[0-9a-f]{8}', identifier):
        raise RuntimeError('Invalid image job identifier')
    path = target / 'jobs' / identifier / 'job.json'
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('Missing image job record: ' + identifier)
    job = json.loads(path.read_text())
    if job.get('job_id') != identifier or job.get('guest_input') != '/var/tmp/tyrell-image-jobs/' + identifier + '/input':
        raise RuntimeError('Inconsistent image job record')
    bundle = path.parent / 'image-input.tar'
    if bundle.is_symlink() or not bundle.is_file() or sha(bundle) != job['bundle_sha256']:
        raise RuntimeError('Image input archive is missing or changed')
    metadata = verify_bundle(bundle)
    if metadata.get('job_id') != identifier:
        raise RuntimeError('Image input archive belongs to another job')
    if 'guest_output' in job and job['guest_output'] != '/var/tmp/tyrell-image-jobs/' + identifier + '/output':
        raise RuntimeError('Invalid per-job output directory')
    if metadata.get('build_mode') == 'stock-image' and 'guest_output' not in job:
        raise RuntimeError('Stock image job lacks its isolated output directory')
    return job


def unit_state():
    result = B['guest']('systemctl', 'show', UNIT, '-p', 'LoadState', '-p', 'ActiveState',
                        '-p', 'SubState', '-p', 'Result', '-p', 'ExecMainStatus', '-p', 'Environment',
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def running(state):
    return (state.get('ActiveState') in {'activating', 'deactivating', 'reloading'}
            or state.get('ActiveState') == 'active' and state.get('SubState') != 'exited')


def assert_idle():
    state = unit_state()
    if running(state):
        raise RuntimeError('An image build is already running; use image status, logs, or wait')
    # Root jobs and user package jobs share this same lock inode. Create it as
    # the regular builder user so later package jobs can open it as well.
    B['guest']('mkdir', '-p', '-m', '700', '/var/tmp/tyrell-locks')
    B['guest']('touch', GUEST_LOCK)
    B['guest']('flock', '--nonblock', GUEST_LOCK, 'true')
    return state


def result_state(job):
    state = unit_state()
    result_path = job['guest_input'].removesuffix('/input') + '/result.json'
    result = B['guest']('python3', '-c',
                        "from pathlib import Path; import sys; p=Path(sys.argv[1]); print(p.read_text() if p.is_file() else 'null')",
                        result_path, stdout=subprocess.PIPE, text=True)
    outcome = json.loads(result.stdout)
    if outcome is not None:
        if (outcome.get('job_id') != job['job_id']
                or outcome.get('bundle_sha256') != job['bundle_sha256']):
            raise RuntimeError('Guest result belongs to different image inputs')
        if type(outcome.get('exit_status')) is not int:
            raise RuntimeError('Invalid guest result status')
    import shlex
    identity = 'TYRELL_IMAGE_JOB_ID=' + job['job_id']
    matches = identity in shlex.split(state.get('Environment', ''))
    if matches and running(state):
        return {'job_id': job['job_id'], 'guest_input': job['guest_input'], 'state': 'running', 'unit': state}
    if outcome is not None:
        return {'job_id': job['job_id'], 'guest_input': job['guest_input'], 'state': 'succeeded' if outcome['exit_status'] == 0 else 'failed',
                'exit_status': outcome['exit_status'], 'unit': state}
    if matches and state.get('ActiveState') == 'failed':
        return {'job_id': job['job_id'], 'guest_input': job['guest_input'], 'state': 'failed', 'unit': state}
    raise RuntimeError('No matching running job or durable result; refusing stale image state')


def wait_job(job):
    last = None
    while True:
        state = result_state(job)
        if state['state'] != last:
            print('Image job ' + job['job_id'] + ': ' + state['state'], flush=True)
            last = state['state']
        if state['state'] == 'succeeded':
            return
        if state['state'] == 'failed':
            raise RuntimeError('Image job failed; inspect image logs --job ' + job['job_id'])
        time.sleep(10)


def verify_checksums(directory):
    checksums = directory / 'SHA256SUMS'
    if not checksums.is_file() or checksums.is_symlink():
        raise RuntimeError('Missing exported checksums')
    recorded = {}
    for line in checksums.read_text().splitlines():
        expected, name = line.split('  ', 1)
        path = directory / name
        if (name in recorded or len(expected) != 64 or path.is_symlink() or not path.is_file()
                or not path.resolve().is_relative_to(directory.resolve()) or sha(path) != expected):
            raise RuntimeError('Image export checksum mismatch: ' + name)
        recorded[name] = expected
    actual = {str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file() and p.name != 'SHA256SUMS'}
    if actual != set(recorded):
        raise RuntimeError('Exported checksums do not cover every artifact')
    return recorded


def export_job(target, job):
    assert_idle()
    if result_state(job)['state'] != 'succeeded':
        raise RuntimeError('Only a successfully completed image job can be exported')
    directory = target / 'jobs' / job['job_id']
    bundle = directory / 'image-input.tar'
    metadata = verify_bundle(bundle)
    if sha(bundle) != job['bundle_sha256']:
        raise RuntimeError('Image input archive changed before export')
    images = target / 'images'
    images.mkdir(exist_ok=True)
    destination = images / job['job_id']
    if destination.exists():
        verify_checksums(destination)
        if sha(destination / 'image-input.tar') != job['bundle_sha256']:
            raise RuntimeError('Existing export belongs to different inputs')
        publish_export(destination, job)
        print('Verified existing image artifacts: ' + str(destination), flush=True)
        return destination
    output = job.get('guest_output', OUTPUT)
    sizes = B['guest']('sudo', '-n', 'python3', '-c',
                       "from pathlib import Path; import sys; print(sum(p.stat().st_size + 4096 for p in Path(sys.argv[1]).rglob('*') if p.is_file()))",
                       output, stdout=subprocess.PIPE, text=True)
    resources = runpy.run_path(str(ROOT / 'image/resources.py'))
    resources['preflight'](ROOT, target, operation='export', export_bytes=int(sizes.stdout.strip()) + bundle.stat().st_size)
    temporary = Path(tempfile.mkdtemp(prefix='.' + job['job_id'] + '-', dir=images))
    transfer = temporary / 'image-export.tar'
    try:
        with transfer.open('wb') as out:
            B['guest']('sudo', '-n', 'flock', '--shared', '--nonblock', GUEST_LOCK,
                       'tar', '-C', output, '-cf', '-', '.', stdout=out)
        extracted = temporary / 'export'
        extracted.mkdir()
        with tarfile.open(transfer) as archive:
            for member in archive.getmembers():
                if (member.issym() or member.islnk()
                        or not (extracted / member.name).resolve().is_relative_to(extracted.resolve())
                        or not (member.isfile() or member.isdir())):
                    raise RuntimeError('Unsafe image output path/type')
            archive.extractall(extracted, filter='data')
        files = verify_checksums(extracted)
        if json.loads((extracted / 'build-inputs.json').read_text()) != metadata:
            raise RuntimeError('Guest image output does not match the tracked input bundle')
        manifest = json.loads((extracted / 'image-manifest.json').read_text())
        if manifest.get('inputs') != metadata or manifest.get('verification', {}).get('status') != 'passed':
            raise RuntimeError('Image manifest does not prove this job passed verification')
        shutil.copyfile(bundle, extracted / 'image-input.tar')
        if sha(extracted / 'image-input.tar') != job['bundle_sha256']:
            raise RuntimeError('Archived input copy failed verification')
        manifest['provenance'] = {'job_id': job['job_id'], 'input_archive': 'image-input.tar',
                                  'input_archive_sha256': job['bundle_sha256']}
        manifest['sha256']['image-input.tar'] = job['bundle_sha256']
        write_json(extracted / 'image-manifest.json', manifest)
        files['image-input.tar'] = job['bundle_sha256']
        files['image-manifest.json'] = sha(extracted / 'image-manifest.json')
        (extracted / 'SHA256SUMS').write_text(''.join(f'{digest}  {name}\n' for name, digest in sorted(files.items())))
        verify_checksums(extracted)
        extracted.rename(destination)
    finally:
        shutil.rmtree(temporary)
    publish_export(destination, job)
    print('Verified image artifacts: ' + str(destination), flush=True)
    return destination


def publish_export(destination, job):
    """Atomically publish both new and previously completed exports."""
    link = ROOT / 'image-artifacts'
    if link.exists() and not link.is_symlink():
        raise RuntimeError('Preserve or rename image-artifacts directory before publishing the export')
    replacement = ROOT / ('image-artifacts-' + job['job_id'] + '.partial')
    if replacement.is_symlink():
        # A prior crash may have left precisely this job's temporary symlink.
        replacement.unlink()
    elif replacement.exists():
        raise RuntimeError('Preserve or rename the unexpected image publication temporary path')
    replacement.symlink_to(destination, target_is_directory=True)
    try:
        replacement.replace(link)
    finally:
        if replacement.is_symlink():
            replacement.unlink()


def start_job(target, args):
    resources = runpy.run_path(str(ROOT / 'image/resources.py'))
    resources['preflight'](ROOT, target, operation='prepare' if args.prepare_only else 'build')
    B['up']()
    old = assert_idle()  # Must precede downloads, bundling, and any shared guest input writes.
    directory = new_job(target)
    apt_repository = Path(args.apt_repository).expanduser().resolve() if args.apt_repository else target / 'apt-repository'
    if args.discovery:
        apt_repository = None
    if args.stock_lock:
        bundle = prepare_stock(target, directory, args.stock_lock)
    else:
        bundle = prepare(target, directory, apt_repository=apt_repository, discovery=args.discovery)
    job = {'job_id': directory.name, 'bundle_sha256': sha(bundle),
           'guest_input': '/var/tmp/tyrell-image-jobs/' + directory.name + '/input'}
    if args.stock_lock:
        job['guest_output'] = '/var/tmp/tyrell-image-jobs/' + directory.name + '/output'
    write_json(directory / 'job.json', job)
    print('Verified image input bundle: ' + str(bundle), flush=True)
    if args.prepare_only:
        return
    resources['preflight'](ROOT, target, operation='build', input_bytes=bundle.stat().st_size)
    if args.stock_lock:
        # Physical space on the VM backing volume does not establish free
        # space inside its fixed-size filesystem. Check before any large copy.
        preflight_stock_guest(bundle, resources)
    guest_directory = job['guest_input'].removesuffix('/input')
    B['guest']('mkdir', '-p', job['guest_input'])
    guest_bundle = guest_directory + '/image-input.tar'
    B['lima']('copy', '--backend=scp', str(bundle), B['INSTANCE'] + ':' + guest_bundle)
    check = B['guest']('sha256sum', guest_bundle, stdout=subprocess.PIPE, text=True)
    if check.stdout.split()[0] != job['bundle_sha256']:
        raise RuntimeError('Guest input transfer checksum mismatch')
    B['guest']('tar', '-xf', guest_bundle, '-C', job['guest_input'])
    write_json(ROOT / 'build/image-job.json', {'job_id': job['job_id'], 'storage': str(target)})
    if old.get('LoadState') == 'loaded':
        B['guest']('sudo', '-n', 'systemctl', 'reset-failed', UNIT)
        B['guest']('sudo', '-n', 'systemctl', 'stop', UNIT)
    B['guest']('sudo', '-n', 'systemd-run', '--unit=' + UNIT.removesuffix('.service'), '--no-block',
               '--service-type=oneshot', '--property=RemainAfterExit=yes',
               '--property=Environment=TYRELL_IMAGE_JOB_ID=' + job['job_id'],
               '--property=StandardOutput=append:' + guest_directory + '/build.log',
               '--property=StandardError=inherit', '/usr/bin/flock', '--nonblock', GUEST_LOCK,
               '/bin/bash', job['guest_input'] + '/image/run-job.sh', job['job_id'], job['guest_input'],
               args.resume_from or 'fresh', job['bundle_sha256'])
    print('Started image job ' + job['job_id'], flush=True)
    if not args.no_wait:
        wait_job(job)
        export_job(target, job)


def preflight_stock_guest(bundle, resources):
    with tarfile.open(bundle) as archive:
        metadata = json.load(archive.extractfile('image-metadata.json'))
        unpacked = sum(member.size + 4096 for member in archive.getmembers())
    available = B['guest']('python3', '-c',
                           'import shutil; print(shutil.disk_usage("/var/tmp").free)',
                           stdout=subprocess.PIPE, text=True)
    result = resources['check_stock_guest_space'](
        int(available.stdout.strip()), bundle.stat().st_size,
        unpacked, metadata['stock_uncompressed_bytes'])
    print('Verified guest stock-build space: ' + json.dumps(result), flush=True)
    return result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=['build', 'status', 'logs', 'wait', 'export', 'resume'], default='build')
    parser.add_argument('--storage', help='Existing image cache/output directory')
    parser.add_argument('--job', help='Tracked job ID; defaults to latest job')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--resume-from', choices=['stage2', 'stage4'])
    parser.add_argument('--no-wait', action='store_true', help='Launch the detached job and return')
    parser.add_argument('--apt-repository', help='Verified frozen APT repository (default: STORAGE/apt-repository)')
    parser.add_argument('--discovery', action='store_true', help='Explicitly use mutable APT to capture a new lock')
    parser.add_argument('--stock-lock', help='Build from this locked official stock image, installing Tyrell offline')
    parser.add_argument('--profile', help='Captured source-build profile; Zero uses its separate locked runner')
    parser.add_argument('--from', dest='resume_stage', choices=['stage2'], help='Zero retry begins at completed-stage1 boundary')
    parser.add_argument('--attempt', type=int, help='Observe/export a specific Zero attempt (default: current)')
    parser.add_argument('--policy', choices=['baseline'], help='Zero power preset; optimized policies need later qualification')
    parser.add_argument('--follow', action='store_true', help='Follow the selected job log')
    parser.add_argument('--lines', type=int, default=100)
    args = parser.parse_args(argv)
    if args.action not in {'build', 'resume'} and any([args.prepare_only, args.resume_from, args.no_wait, args.apt_repository, args.discovery, args.stock_lock, args.profile, args.policy]):
        parser.error('Build options are only valid with the build action')
    if args.profile and any([args.stock_lock, args.discovery, args.resume_from]):
        parser.error('--profile cannot be combined with stock/discovery/legacy resume options')
    if args.policy and not args.profile:
        parser.error('--policy requires a Zero profile build')
    if args.action == 'resume':
        if not args.job or args.resume_stage != 'stage2':
            parser.error('Zero resume requires --job ID --from stage2')
        if any([args.profile,args.policy,args.stock_lock,args.discovery,args.apt_repository,args.prepare_only,args.resume_from,args.attempt]):
            parser.error('Zero resume reuses captured inputs; new selections are not allowed')
    elif args.resume_stage:
        parser.error('--from is only valid with Zero resume')
    if args.attempt is not None and (args.attempt < 1 or args.action in {'build','resume'}):
        parser.error('--attempt requires a positive observed/exported attempt')
    if args.stock_lock and any([args.resume_from, args.apt_repository, args.discovery]):
        parser.error('--stock-lock cannot be combined with source-build APT/discovery/resume options')
    if args.action != 'logs' and args.follow:
        parser.error('--follow requires logs')
    if args.action == 'build' and args.job:
        parser.error('--job selects existing jobs; it cannot be used with build')
    if args.lines < 1:
        parser.error('--lines must be positive')
    return args


def main(argv=None):
    args = parse_args(argv)
    # Observational commands work even when little disk space remains. They do
    # not start the VM or prepare inputs. Use make builder-up if it is stopped.
    target = storage(check_space=False, override=args.storage)
    zero = runpy.run_path(str(ROOT / 'image/zero_pipeline.py'))
    if args.profile or args.action == 'resume' or (args.action != 'build' and zero['is_zero_job'](target, args.job)):
        zero['main'](args, globals())
        return
    if args.attempt is not None:
        raise RuntimeError('--attempt applies only to recorded Zero jobs')
    if args.action == 'build':
        with LOCK(ROOT):
            start_job(target, args)
    else:
        job = load_job(target, args.job)
        if args.action == 'status':
            print(json.dumps(result_state(job), indent=2))
        elif args.action == 'logs':
            path = job['guest_input'].removesuffix('/input') + '/build.log'
            print('Image job input: ' + job['guest_input'], flush=True)
            B['guest']('sudo', '-n', 'tail', '-n', str(args.lines), *(['-F'] if args.follow else []), path)
        elif args.action == 'wait':
            wait_job(job)
        elif args.action == 'export':
            with LOCK(ROOT):
                export_job(target, job)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print('image: ' + str(error), file=sys.stderr)
        sys.exit(1)
