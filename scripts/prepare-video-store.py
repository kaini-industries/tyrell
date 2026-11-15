#!/usr/bin/env python3
"""Assemble a reviewed Store handoff locally; never upload or publish."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SHA256S = {
    '0.1.6': '3e6b8f50e0f076e783943e77f61e2b57b856a7168f44886dd7d7646013fb239f',
    '0.1.7': '7ddf4ead5f9d27e44d92ab510b8cca503847af36762d60b315fea62de4450d79',
}
PERMISSIONS = dict(camera=False, microphone=False, imu=False, network=False,
                   additional_hardware=False, background_service=True, external_display=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_file(base, relative):
    path = base / relative
    if (Path(relative).is_absolute() or '..' in Path(relative).parts or
            path.is_symlink() or not path.resolve().is_relative_to(base.resolve()) or
            not path.is_file()):
        raise ValueError('Unsafe or missing preparation file: ' + relative)
    return path.read_bytes()


def assemble(version):
    PREP = ROOT / 'store/tyrell-video' / version
    DIST = ROOT / 'dist/tyrell-video' / version
    PACKAGE = f'tyrell-video_{version}-1_arm64.deb'
    PACKAGE_SHA256 = PACKAGE_SHA256S[version]
    expected = {}
    for line in read_file(PREP, 'SHA256SUMS').decode().splitlines():
        checksum, relative = line.split('  ', 1)
        if relative in expected:
            raise ValueError('Duplicate checksum path: ' + relative)
        expected[relative] = checksum
        if digest(read_file(PREP, relative)) != checksum:
            raise ValueError('Preparation checksum mismatch: ' + relative)
    app = json.loads(read_file(PREP, 'app-builder.json'))
    listing = app['store']
    if (app['package_name'] != 'tyrell-video' or app['version'] != version + '-1' or
            app['bin_name'] != 'tyrell-video' or 'source_repo' in listing or
            listing['permissions'] != PERMISSIONS or
            any(type(v) is not bool for v in listing['permissions'].values())):
        raise ValueError('Package identity, privacy or permissions changed; review again')
    validation = json.loads(read_file(PREP, 'validation.json'))
    if validation['metadata']['errors']:
        raise ValueError('Metadata validation has unresolved errors')
    for relative, checksum in validation['metadata']['validated_inputs'].items():
        if digest(read_file(PREP, relative)) != checksum:
            raise ValueError('Validated metadata/image changed: ' + relative)
    portal = json.loads(read_file(PREP, 'portal-fields.json'))
    wanted_portal = dict(title=app['app_name'], summary=listing['summary'],
                         description=listing['description'], categories=','.join(listing['categories']),
                         display_name=listing['author']['display_name'], license=listing['license'],
                         share_code=listing['share_code'], permissions=listing['permissions'],
                         hide_email=True, icon=listing['icon'], screenshots=listing['screenshots'])
    if portal != wanted_portal:
        raise ValueError('Portal fields differ from the canonical listing')
    images = [listing['icon'], *listing['screenshots']]
    if not 1 <= len(listing['screenshots']) <= 6:
        raise ValueError('Expected 1–6 screenshot PNGs')
    for relative in images:
        data = read_file(PREP, relative)
        if len(data) < 24 or data[:8] != b'\x89PNG\r\n\x1a\n' or len(data) > 512 * 1024:
            raise ValueError('Invalid or oversized PNG: ' + relative)
        width, height = struct.unpack('>II', data[16:24])
        if relative == listing['icon']:
            valid = width == height and 128 <= width <= 512
        else:
            valid = (width, height) == (320, 170)
        if not valid:
            raise ValueError('Wrong image dimensions: ' + relative)

    # Every archive member is explicitly chosen; no repo crawl, credentials,
    # private source tree or raw device-results directory can enter the ZIP.
    relative_files = ['app-builder.json', 'portal-fields.json', 'permissions.md', 'LICENSE.txt', 'THIRD_PARTY.md',
                      'reviewer-notes.md', 'recording-guide.md', 'validation.json',
                      'assets/README.md', 'assets/manifest.json', *images,
                      'demo/README.md', 'demo/generate.py', 'demo/manifest.json',
                      'demo/TYRELL-DEMO.mp4', 'demo/TYRELL-DEMO.srt', 'demo/TYRELL-DEMO-poster.png']
    if version == '0.1.7':
        relative_files += ['qualification/README.md', 'qualification/hdmi-validation.json',
                           'qualification/startup-recovery.json', 'qualification/endurance.json',
                           'qualification/lifecycle.json', 'qualification/user-observations.json',
                           'qualification/fixture-profile.json',
                           'qualification/kms-activation-fault.c',
                           'qualification/paused-before.png', 'qualification/paused-after.png',
                           'qualification/selected-subtitle-track.png',
                           'recording/README.md', 'recording/manifest.json',
                           'recording/Tyrell-Video-0.1.7.mp4', 'recording/poster.jpg']
    missing = set(relative_files + ['UPLOAD.md']) - expected.keys()
    if missing:
        raise ValueError('Missing preparation checksums: ' + ', '.join(sorted(missing)))
    files = {name: read_file(PREP, name) for name in relative_files}
    files['README.md'] = read_file(PREP, 'UPLOAD.md')
    package = read_file(DIST, PACKAGE)
    if digest(package) != PACKAGE_SHA256:
        raise ValueError('Immutable package checksum mismatch')
    files[PACKAGE] = package
    provenance = json.loads(read_file(DIST, 'provenance.json'))
    package_meta = {key: provenance[key] for key in
                    ['package', 'debian_version', 'architecture', 'package_file',
                     'package_sha256', 'source_revision', 'source_sha256']}
    package_meta['size'] = len(package)
    files['package.json'] = (json.dumps(package_meta, indent=2) + '\n').encode()
    for name in ['store-policy.json', 'device-validation.json', 'device-audit.json']:
        files['package-checks/' + name] = read_file(DIST, name)
    files['SHA256SUMS'] = ''.join(digest(data) + '  ' + name + '\n'
                                for name, data in sorted(files.items())).encode()
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', choices=sorted(PACKAGE_SHA256S), default='0.1.7')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    files = assemble(args.version)
    output = (args.output or ROOT / f'build/store/tyrell-video-{args.version}-submission.zip').absolute()
    if output.is_symlink() or output.suffix != '.zip':
        raise ValueError('Output must be a regular .zip path')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.zip', delete=False) as stream:
            temporary = Path(stream.name)
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(files.items()):
                entry = zipfile.ZipInfo(f'tyrell-video-{args.version}-submission/' + name, (2026, 11, 14, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, data, compresslevel=9)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(str(output))
    print('SHA256 ' + digest(output.read_bytes()))
    print('Prepared locally; nothing uploaded. See validation.json for completed checks and remaining review evidence.')


if __name__ == '__main__':
    main()
