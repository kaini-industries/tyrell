#!/usr/bin/env python3
"""Discover and lock official stock CardputerZero images without downloading them.

`check` reports a candidate without modifying the active lock. `refresh --output`
creates a separate lock and refuses to replace an existing file. Image builds
consume the lock offline; they never resolve "latest" themselves.
"""
import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote
from urllib.request import Request, urlopen

REPOSITORY = 'CardputerZero/pi-gen'
API = 'https://api.github.com/repos/' + REPOSITORY
DRIVER_API = 'https://api.github.com/repos/m5stack/m5stack-linux-dtoverlays'
DEFAULT_LOCK = Path(__file__).with_name('stock-image.json')
IMAGE_NAME = re.compile(r'([0-9]{4}-[0-9]{2}-[0-9]{2})-cardputerzero-trixie-arm64\.img\.xz')
# Discovery must recognize stock releases more broadly than the image format
# this builder supports. Otherwise a newer OS/architecture/compression format
# would disappear from the candidate list and make an old lock look current.
STOCK_IMAGE_NAME = re.compile(r'.*cardputerzero.*(?:\.img(?:\.[a-z0-9]+)?|\.zip)', re.IGNORECASE)
TAG = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}')
HEX40 = re.compile(r'[0-9a-f]{40}')
HEX64 = re.compile(r'[0-9a-f]{64}')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def parse_json(data):
    return json.loads(data, object_pairs_hook=unique_object)


def download(url, maximum=8 * 1024 * 1024):
    request = Request(url, headers={'User-Agent': 'Tyrell-stock-image/1',
                                    'Accept': 'application/vnd.github+json'})
    with urlopen(request, timeout=45) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('Upstream metadata exceeds the size limit')
    return data


def api_json(url):
    return parse_json(download(url))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def timestamp(value):
    require(isinstance(value, str), 'Missing release publication timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError('Invalid release publication timestamp') from error
    require(parsed.tzinfo is not None, 'Release timestamp must include its timezone')
    return parsed.astimezone(timezone.utc)


def positive_int(value):
    return type(value) is int and value > 0


def image_name(name):
    match = IMAGE_NAME.fullmatch(name) if isinstance(name, str) else None
    require(match is not None, 'Unsupported image: expected CardputerZero Trixie ARM64 .img.xz')
    try:
        date.fromisoformat(match[1])
    except ValueError as error:
        raise ValueError('Invalid date in stock image filename') from error
    return name


def kernel_release(version):
    match = re.fullmatch(r'(?:[0-9]+:)?([0-9]+\.[0-9]+\.[0-9]+)-[0-9]+\+rpt[0-9]+',
                         version) if isinstance(version, str) else None
    require(match is not None, 'Unsupported Raspberry Pi kernel package version')
    return match[1] + '+rpt-rpi-v8'


def validate_asset(asset, tag, image=False):
    require(isinstance(asset, dict), 'Missing stock asset metadata')
    name = image_name(asset.get('name')) if image else asset.get('name')
    require(image or name == 'build-info.json', 'Unexpected build-info filename')
    require(positive_int(asset.get('id')) and positive_int(asset.get('bytes')),
            'Invalid stock asset identity or size')
    require(isinstance(asset.get('sha256'), str) and HEX64.fullmatch(asset['sha256']),
            'Missing stock asset SHA-256')
    expected_url = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'
    require(asset.get('url') == expected_url, 'Stock asset URL does not match its release and filename')
    if not image:
        require(asset['bytes'] <= 65536, 'Build-info metadata exceeds the size limit')


def validate_lock(lock):
    """Validate an in-memory lock; never resolve or fetch remote information."""
    require(isinstance(lock, dict) and type(lock.get('schema_version')) is int and
            lock['schema_version'] == 1 and lock.get('kind') == 'cardputerzero-stock-image',
            'Unsupported stock image lock schema')
    upstream = lock.get('upstream')
    require(isinstance(upstream, dict) and upstream.get('repository') == REPOSITORY,
            'Unsupported stock image repository')
    tag = upstream.get('tag')
    require(isinstance(tag, str) and TAG.fullmatch(tag), 'Invalid stock release tag')
    require(positive_int(upstream.get('release_id')) and type(upstream.get('prerelease')) is bool,
            'Invalid stock release identity or channel')
    timestamp(upstream.get('published_at'))
    require(upstream.get('url') == f'https://github.com/{REPOSITORY}/releases/tag/{tag}',
            'Stock release URL does not match its identity')
    validate_asset(lock.get('image'), tag, image=True)
    validate_asset(lock.get('build_info'), tag)
    fields = lock['build_info'].get('fields')
    require(isinstance(fields, dict), 'Missing verified build-info fields')
    for name in ('pi_gen_commit', 'drivers_commit'):
        require(isinstance(lock.get(name), str) and HEX40.fullmatch(lock[name]),
                'Stock lock requires a full source commit: ' + name)
    require(fields.get('pigen_commit') == lock['pi_gen_commit'],
            'Recipe commit disagrees with build-info')
    prefix = fields.get('driver_commit')
    require(isinstance(prefix, str) and re.fullmatch(r'[0-9a-f]{7,40}', prefix) and
            lock['drivers_commit'].startswith(prefix), 'Driver commit disagrees with build-info')
    require(lock.get('board') == 'v5' and fields.get('config_cardputero_v0_5') is True,
            'Only the documented stock v5 board configuration is supported')
    version = lock.get('kernel_package_version')
    require(fields.get('kernel_version') == version and
            lock.get('kernel_release') == kernel_release(version),
            'Kernel identity disagrees with build-info')
    launcher = lock.get('applaunch_version')
    require(isinstance(launcher, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}', launcher) and
            fields.get('launcher_version') == launcher,
            'Launcher version disagrees with build-info')
    return lock


def load_lock(path):
    """Read and validate a lock without network access."""
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'Stock lock must be a regular file')
    require(path.stat().st_size <= 256 * 1024, 'Stock lock exceeds the size limit')
    return validate_lock(parse_json(path.read_text()))


def release_assets(release):
    assets = release.get('assets')
    require(isinstance(assets, list), 'Release is missing its asset list')
    images = [asset for asset in assets if isinstance(asset, dict) and
              isinstance(asset.get('name'), str) and IMAGE_NAME.fullmatch(asset['name'])]
    infos = [asset for asset in assets if isinstance(asset, dict) and asset.get('name') == 'build-info.json']
    if not images:
        unsupported = [asset['name'] for asset in assets if isinstance(asset, dict) and
                       isinstance(asset.get('name'), str) and STOCK_IMAGE_NAME.fullmatch(asset['name'])]
        require(not unsupported, 'Unsupported stock image format in selected release: ' + ', '.join(unsupported))
    require(len(images) == 1 and len(infos) == 1,
            'Stock release must have exactly one Trixie ARM64 image and build-info.json')
    return images[0], infos[0]


def select_release(include_prereleases=False, release_tag=None):
    if release_tag is not None:
        require(isinstance(release_tag, str) and TAG.fullmatch(release_tag), 'Invalid stock release tag')
        release = api_json(API + '/releases/tags/' + quote(release_tag, safe=''))
        require(isinstance(release, dict) and release.get('tag_name') == release_tag,
                'Release API returned a different tag')
        require(not release.get('draft'), 'Draft stock images cannot be selected')
        require(include_prereleases or release.get('prerelease') is False,
                'Prerelease stock images require --include-prereleases')
        release_assets(release)
        return release
    candidates = []
    for page in range(1, 101):
        releases = api_json(API + f'/releases?per_page=100&page={page}')
        require(isinstance(releases, list), 'Release API did not return a release list')
        for release in releases:
            require(isinstance(release, dict), 'Invalid release metadata')
            if release.get('draft') or (release.get('prerelease') and not include_prereleases):
                continue
            assets = release.get('assets', [])
            require(isinstance(assets, list), 'Release is missing its asset list')
            if not any(isinstance(asset, dict) and isinstance(asset.get('name'), str) and
                       (STOCK_IMAGE_NAME.fullmatch(asset['name']) or asset['name'] == 'build-info.json')
                       for asset in assets):
                continue  # For example, the asset-free v1.0.0 project tag.
            candidates.append(release)
        if len(releases) < 100:
            break
    else:
        raise ValueError('Release listing exceeded pagination limit; refusing an incomplete selection')
    require(candidates, 'No eligible official CardputerZero stock image was found')
    selected = max(candidates, key=lambda item: (timestamp(item.get('published_at')), item.get('id', 0)))
    release_assets(selected)  # An incomplete newest image must not cause a silent downgrade.
    return selected


def asset_lock(asset):
    digest = asset.get('digest')
    require(isinstance(digest, str) and digest.startswith('sha256:') and HEX64.fullmatch(digest[7:]),
            'Official release asset is missing its SHA-256 digest')
    return {'id': asset.get('id'), 'name': asset.get('name'), 'url': asset.get('browser_download_url'),
            'bytes': asset.get('size'), 'sha256': digest[7:]}


def discover(include_prereleases=False, release_tag=None):
    release = select_release(include_prereleases, release_tag)
    image, info = release_assets(release)
    tag = release.get('tag_name')
    require(isinstance(tag, str) and TAG.fullmatch(tag), 'Invalid stock release tag')
    image, info = asset_lock(image), asset_lock(info)
    validate_asset(image, tag, image=True)
    validate_asset(info, tag)
    data = download(info['url'], maximum=65536)
    require(len(data) == info['bytes'] and hashlib.sha256(data).hexdigest() == info['sha256'],
            'Downloaded build-info size or SHA-256 mismatch')
    fields = parse_json(data)
    require(isinstance(fields, dict), 'Build-info must be a JSON object')
    prefix = fields.get('driver_commit')
    require(isinstance(prefix, str) and re.fullmatch(r'[0-9a-f]{7,40}', prefix),
            'Invalid driver source commit in build-info')
    driver = api_json(DRIVER_API + '/commits/' + prefix)
    require(isinstance(driver, dict), 'Driver commit API returned invalid metadata')
    lock = {
        'schema_version': 1, 'kind': 'cardputerzero-stock-image',
        'upstream': {'repository': REPOSITORY, 'release_id': release.get('id'), 'tag': tag,
                     'prerelease': release.get('prerelease'), 'published_at': release.get('published_at'),
                     'url': release.get('html_url')},
        'image': image, 'build_info': {**info, 'fields': fields},
        'pi_gen_commit': fields.get('pigen_commit'), 'drivers_commit': driver.get('sha'),
        'kernel_release': kernel_release(fields.get('kernel_version')),
        'kernel_package_version': fields.get('kernel_version'),
        'applaunch_version': fields.get('launcher_version'), 'board': 'v5',
    }
    return validate_lock(lock)


def write_lock(output, lock):
    """Publish a complete new lock atomically, without replacing any destination."""
    validate_lock(lock)
    output = Path(output)
    require(not output.exists() and not output.is_symlink(), 'Preserving existing stock lock: ' + str(output))
    descriptor, temporary = tempfile.mkstemp(prefix='.' + output.name + '.', dir=output.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(lock, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        try:
            os.link(temporary, output)
        except FileExistsError as error:
            raise ValueError('Preserving existing stock lock: ' + str(output)) from error
    finally:
        Path(temporary).unlink(missing_ok=True)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check', help='Report upstream candidate; write no files')
    check.add_argument('--lock', type=Path, default=DEFAULT_LOCK)
    refresh = commands.add_parser('refresh', help='Create a separate verified stock image lock')
    refresh.add_argument('--output', type=Path, required=True)
    for command in (check, refresh):
        command.add_argument('--release', help='Select an exact official image release tag')
        command.add_argument('--include-prereleases', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'refresh' and (args.output.exists() or args.output.is_symlink()):
        parser.error('Preserving existing stock lock: ' + str(args.output))
    try:
        candidate = discover(args.include_prereleases, args.release)
        if args.command == 'refresh':
            write_lock(args.output, candidate)
            result = {'status': 'created', 'output': str(args.output), 'lock_sha256': sha(args.output),
                      'candidate': candidate}
        else:
            current = load_lock(args.lock) if args.lock.exists() else None
            result = {'status': ('unchanged' if current == candidate else
                                 'update-available' if current else 'lock-missing'),
                      'current_tag': current['upstream']['tag'] if current else None,
                      'candidate': candidate}
        print(json.dumps(result, indent=2))
    except (ValueError, OSError) as error:
        parser.exit(1, 'Stock image update failed: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
