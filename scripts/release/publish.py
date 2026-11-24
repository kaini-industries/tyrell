#!/usr/bin/env python3
"""Publish one approved candidate; default operation only prepares local assets."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from urllib.parse import quote

import candidate
import github
import promotion

PART_BYTES = 1024 ** 3


def publication_files(archive, directory, approval, output, *, part_bytes=PART_BYTES):
    """Split without changing candidate bytes; every download is hash-addressed."""
    archive, directory, output = Path(archive), Path(directory), Path(output)
    if not 1 <= part_bytes <= PART_BYTES:
        raise ValueError('Publication parts must be at most 1 GiB')
    manifest = candidate.verify_directory(directory, approval['candidate_sha256'])
    github.local(archive.parent, archive.name)
    if archive.name != 'candidate-' + approval['candidate_sha256'] + '.tar':
        raise ValueError('Candidate archive name must bind its manifest identity')
    if (github.sha(archive) != approval['archive_sha256'] or approval.get('status') != 'approved'
            or manifest['source_commit'] != approval['source_commit']
            or approval['tag_name'] != promotion.tag_name(manifest['release_version'])):
        raise ValueError('Approval does not match candidate publication bytes')
    original_size = archive.stat().st_size
    if output.absolute() != output.resolve():
        raise ValueError('Publication output cannot traverse a redirected parent')
    output.mkdir(parents=True, exist_ok=False)
    parts = []
    complete = hashlib.sha256()
    with archive.open('rb') as stream:
        index, offset = 0, 0
        while True:
            block = stream.read(min(1024 * 1024, part_bytes))
            if not block:
                break
            name = archive.name + f'.part-{index:05d}'
            path = output / name
            total = 0
            digest = hashlib.sha256()
            with path.open('xb') as target:
                while block:
                    target.write(block)
                    digest.update(block)
                    complete.update(block)
                    total += len(block)
                    if total == part_bytes:
                        break
                    block = stream.read(min(1024 * 1024, part_bytes - total))
            parts.append({'path': name, 'bytes': total, 'sha256': digest.hexdigest(), 'offset': offset})
            offset += total
            index += 1
    if (complete.hexdigest() != approval['archive_sha256'] or offset != original_size
            or archive.stat().st_size != original_size or github.sha(archive) != approval['archive_sha256']):
        raise ValueError('Candidate archive changed while preparing publication parts')
    if not parts:
        raise ValueError('Empty candidate archive')
    shutil.copyfile(directory / 'release-manifest.json', output / 'release-manifest.json')
    (output / 'promotion.json').write_text(json.dumps(approval, indent=2) + '\n')
    record = {'schema': 1, 'kind': 'tyrell-candidate-download-index',
              'candidate_sha256': approval['candidate_sha256'], 'archive_sha256': approval['archive_sha256'],
              'archive': archive.name, 'archive_bytes': original_size,
              'source_commit': manifest['source_commit'], 'parts': parts,
              'reassembly': 'Concatenate parts in the recorded order, verify archive_sha256, then use the trusted candidate verifier before extracting.'}
    (output / 'release-index.json').write_text(json.dumps(record, indent=2) + '\n')
    files = {p.name: {'sha256': github.sha(p), 'bytes': p.stat().st_size} for p in sorted(output.iterdir())}
    (output / 'SHA256SUMS').write_text(''.join(f'{v["sha256"]}  {name}\n' for name, v in files.items()))
    files['SHA256SUMS'] = {'sha256': github.sha(output / 'SHA256SUMS'),
                           'bytes': (output / 'SHA256SUMS').stat().st_size}
    return files


def publish(repository, approval, files, directory, *, receipt_path=None):
    """No replace, resume, deletion, clobber, latest movement or stable release."""
    if (approval.get('status') != 'approved' or approval.get('prerelease') is not True
            or approval.get('make_latest') is not False or approval.get('repository') != repository):
        raise ValueError('Expected an explicit prerelease approval')
    tag = approval['tag_name']
    def progress(status, **fields):
        if receipt_path is not None:
            path = Path(receipt_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({'schema': 1, 'kind': 'tyrell-publication-progress',
                                        'status': status, 'tag_name': tag,
                                        'candidate_sha256': approval['candidate_sha256'], **fields}, indent=2) + '\n')
    state = github.existing(repository, tag)
    if not state['immutable_releases_enabled'] or state['release_exists'] or state['tag_exists']:
        raise ValueError('Immutable publication requires enabled protection and a new release/tag')
    # Recheck package-version collisions immediately before creating any ref.
    manifest = github.read(Path(directory) / 'release-manifest.json')
    for asset in manifest['assets']:
        if asset['kind'] != 'app-deb':
            continue
        for previous in state['package_versions']:
            if all(previous[k] == asset[k] for k in ('package', 'version', 'architecture')) and previous['sha256'] != asset['sha256']:
                raise ValueError('An accepted package version already has different bytes')
    notes = ('Explicit ' + approval['level'] + ' candidate promotion.\n\n'
             'Candidate SHA-256: ' + approval['candidate_sha256'] + '\n'
             'Archive SHA-256: ' + approval['archive_sha256'] + '\n'
             'Source: ' + approval['source_commit'] + '\n\n'
             'Read release-manifest.json, promotion.json and the bundled known issues, qualification and recovery guidance. '
             'Download all archive parts in release-index.json order and verify their hashes before reassembly. '
             'This release does not move an update channel or authorize device writing.')
    # Atomic create, never update: a concurrent tag creator makes this fail.
    github.api(f'repos/{repository}/git/refs', 'POST',
                {'ref': 'refs/tags/' + tag, 'sha': approval['source_commit']})
    progress('tag-reserved', source_commit=approval['source_commit'])
    release = github.api(f'repos/{repository}/releases', 'POST', {
        'tag_name': tag, 'target_commitish': approval['source_commit'],
        'name': manifest['release_version'] + ' (' + approval['level'] + ')',
        'body': notes, 'draft': True, 'prerelease': True, 'make_latest': 'false'})
    release_id = release['id']
    progress('draft-created', release_id=release_id, url=release.get('html_url'),
             recovery='Preserve and inspect this draft/ref. Automatic replacement or resume is forbidden.')
    prefix = f'https://uploads.github.com/repos/{repository}/releases/{release_id}/assets'
    if release.get('upload_url') != prefix + '{?name,label}' or release.get('draft') is not True:
        raise ValueError('Unexpected release upload endpoint or non-draft response')
    for name, identity in files.items():
        path = github.local(directory, name)
        if github.sha(path) != identity['sha256'] or path.stat().st_size != identity['bytes']:
            raise ValueError('Publication file changed after preparation')
        result = json.loads(github.gh('api', '--method', 'POST', '-H', 'Content-Type: application/octet-stream',
                                     prefix + '?name=' + quote(name, safe=''), '--input', path))
        if result.get('digest') != 'sha256:' + identity['sha256'] or result.get('size') != identity['bytes']:
            raise ValueError('Server did not bind the uploaded asset digest; preserve the draft for inspection')
    remote = github.pages(f'repos/{repository}/releases/{release_id}/assets')
    if {a['name'] for a in remote} != set(files) or len(remote) != len(files):
        raise ValueError('Draft contains unexpected or missing assets')
    for item in remote:
        if item.get('digest') != 'sha256:' + files[item['name']]['sha256'] or item.get('size') != files[item['name']]['bytes']:
            raise ValueError('Draft asset checksum differs from approved publication')
    progress('draft-assets-verified', release_id=release_id, url=release.get('html_url'))
    # Refresh the immutability prerequisite at the final irreversible boundary.
    if github.api(f'repos/{repository}/immutable-releases').get('enabled') is not True:
        raise ValueError('Release immutability no longer enabled; preserve draft')
    ref = github.api(f'repos/{repository}/git/ref/tags/{quote(tag, safe="")}')
    if ref.get('object', {}).get('sha') != approval['source_commit'] or ref.get('object', {}).get('type') != 'commit':
        raise ValueError('Reserved tag changed before publication; preserve draft')
    published = github.api(f'repos/{repository}/releases/{release_id}', 'PATCH',
                            {'draft': False, 'prerelease': True, 'make_latest': 'false'})
    if published.get('immutable') is not True or published.get('draft') is not False or published.get('prerelease') is not True:
        raise ValueError('Published release did not confirm immutable prerelease status')
    ref = github.api(f'repos/{repository}/git/ref/tags/{quote(tag, safe="")}')
    if ref.get('object', {}).get('sha') != approval['source_commit'] or ref.get('object', {}).get('type') != 'commit':
        raise ValueError('Published tag does not bind the candidate source')
    return {'schema': 1, 'kind': 'tyrell-immutable-publication', 'status': 'published',
            'release_id': release_id, 'url': published['html_url'], 'tag_name': tag,
            'candidate_sha256': approval['candidate_sha256'], 'archive_sha256': approval['archive_sha256'],
            'immutable': True, 'prerelease': True, 'latest_requested': False, 'assets': files}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('archive', 'directory', 'approval', 'output', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--repository')
    args = parser.parse_args()
    approval = github.read(args.approval)
    files = publication_files(args.archive, args.directory, approval, args.output)
    result = {'status': 'prepared-only', 'files': files, 'publication_performed': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
