#!/usr/bin/env python3
"""Trusted GitHub provenance adapter. Downloaded candidate files are data only."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SUITES = ('docs', 'python', 'game', 'chess', 'chess-native', 'status', 'video', 'zero-native')
JOBS = {'plan', 'compatibility gate', *('portable (' + x + ')' for x in SUITES[:4]),
        *('native (' + x + ')' for x in SUITES[4:])}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked(value, pattern, label):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError('Invalid ' + label)
    return value


def local(root, name):
    if not isinstance(name, str) or not name or '\\' in name or any(
            p in ('', '.', '..') for p in name.split('/')) or PurePosixPath(name).is_absolute():
        raise ValueError('Unsafe local input path')
    root = Path(root)
    for part in (*root.parents, root):
        if part.is_symlink():
            raise ValueError('Redirected local input root')
    path = root
    for part in name.split('/'):
        path = path / part
        if path.is_symlink():
            raise ValueError('Redirected local input')
    if not path.is_file():
        raise ValueError('Missing regular local input: ' + name)
    return path


def read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_text(), object_pairs_hook=unique)


def gh(*args, payload=None, env=None):
    result = subprocess.run(['gh', *map(str, args)],
                            input=None if payload is None else json.dumps(payload),
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if result.returncode:
        raise RuntimeError('GitHub request failed: ' + result.stderr.strip())
    return result.stdout


def api(path, method='GET', payload=None):
    args = ['api', '--method', method, '-H', 'Accept: application/vnd.github+json', path]
    if payload is not None:
        args += ['--input', '-']
    env = None
    if path.endswith('/immutable-releases') and os.environ.get('GH_RELEASE_READ_TOKEN'):
        env = dict(os.environ, GH_TOKEN=os.environ['GH_RELEASE_READ_TOKEN'])
    return json.loads(gh(*args, payload=payload, env=env))


def pages(path, key=None):
    result = []
    for page in range(1, 102):
        data = api(path + ('&' if '?' in path else '?') + f'per_page=100&page={page}')
        batch = data[key] if key else data
        if not isinstance(batch, list):
            raise ValueError('Malformed GitHub listing')
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise ValueError('GitHub listing exceeds supported bound')


def run(repository, run_id, path, source=None):
    checked(repository, r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', 'repository')
    checked(str(run_id), r'[1-9][0-9]*', 'run ID')
    data = api(f'repos/{repository}/actions/runs/{run_id}')
    if (data.get('id') != int(run_id) or data.get('path') != path
            or data.get('repository', {}).get('full_name') != repository
            or data.get('head_repository', {}).get('full_name') != repository
            or data.get('event') not in ('push', 'pull_request', 'workflow_dispatch')
            or data.get('status') != 'completed' or data.get('conclusion') != 'success'
            or (source is not None and data.get('head_sha') != source)):
        raise ValueError('Run does not authenticate the selected repository/workflow/revision')
    jobs = pages(f'repos/{repository}/actions/runs/{run_id}/jobs?filter=latest', 'jobs')
    names = [job['name'] for job in jobs]
    if len(names) != len(set(names)) or any(
            j.get('status') != 'completed' or j.get('conclusion') != 'success' for j in jobs):
        raise ValueError('Workflow contains duplicate, missing or unsuccessful jobs')
    if path == '.github/workflows/checks.yml' and set(names) != JOBS:
        raise ValueError('Promotion requires all eight suites and the compatibility gate')
    return {'id': data['id'], 'repository': repository, 'path': path,
            'head_sha': data['head_sha'], 'status': data['status'], 'conclusion': data['conclusion'],
            'event': data['event'], 'jobs': [{'name': j['name'], 'status': j['status'],
                                           'conclusion': j['conclusion']} for j in jobs]}


def artifact(repository, run_id, name, head):
    items = [x for x in pages(f'repos/{repository}/actions/runs/{run_id}/artifacts', 'artifacts')
             if x['name'] == name]
    if len(items) != 1:
        raise ValueError('Expected exactly one retained artifact: ' + name)
    item = items[0]
    if (item.get('expired') is not False or item.get('workflow_run', {}).get('id') != int(run_id)
            or item.get('workflow_run', {}).get('head_sha') != head):
        raise ValueError('Artifact is expired or belongs to a different run/revision')
    return item


def ancestor(root, revision, default_revision):
    checked(revision, r'[0-9a-f]{40}', 'trusted revision')
    subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', revision,
                    default_revision], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def authenticate_selection(repository, run_id, source, inputs, selection_sha, *, download_root):
    checked(source, r'[0-9a-f]{40}', 'source revision')
    checked(selection_sha, r'[0-9a-f]{64}', 'selection checksum')
    path = local(inputs, 'release-selection.json')
    if sha(path) != selection_sha:
        raise ValueError('Selected input recipe checksum mismatch')
    selection = read(path)
    if selection.get('source_commit') != source:
        raise ValueError('Selection source differs from requested revision')
    source_run = run(repository, run_id, '.github/workflows/checks.yml', source)
    expected = {}
    source_digest = None
    for suite in SUITES:
        name = f'checks-{suite}-{source}'
        artifact(repository, run_id, name, source)
        destination = Path(download_root) / suite
        if destination.exists():
            raise ValueError('Preserve existing download directory')
        gh('run', 'download', str(run_id), '--repo', repository, '--name', name,
           '--dir', destination)
        report = local(destination, suite + '.json')
        data = read(report)
        identity = data.get('source', {})
        if (data.get('suite') != suite or data.get('status') != 'passed'
                or identity.get('commit') != source or identity.get('dirty') is not False):
            raise ValueError('Hosted suite report does not bind the selected clean source')
        if source_digest is None:
            source_digest = identity.get('worktree_sha256')
        if not source_digest or identity.get('worktree_sha256') != source_digest:
            raise ValueError('Hosted suites describe different source inputs')
        expected[suite] = sha(report)
    selected = {}
    for item in selection.get('checks', []):
        report = local(inputs, item['path'])
        data = read(report)
        suite = data.get('suite')
        if suite not in expected or suite in selected or item.get('sha256') != expected[suite] or sha(report) != expected[suite]:
            raise ValueError('Selection report is not the authenticated hosted report')
        selected[suite] = expected[suite]
    if set(selected) != set(SUITES):
        raise ValueError('Selection must retain every authenticated source suite report')
    return {'source_run': source_run, 'report_sha256': selected, 'selection_sha256': selection_sha}


def retained_versions(root):
    result = []
    for path in sorted((Path(root) / 'dist').glob('*/*/*.deb')):
        name, version, arch = path.name[:-4].rsplit('_', 2)
        result.append({'package': name, 'version': version, 'architecture': arch, 'sha256': sha(path)})
    return result


def existing(repository, tag, root=ROOT):
    # A failed/unauthorized setting read must block, never silently mean enabled.
    enabled = api(f'repos/{repository}/immutable-releases')
    releases = pages(f'repos/{repository}/releases')
    tags = pages(f'repos/{repository}/tags')
    versions = retained_versions(root)
    for release in releases:
        if release['tag_name'].startswith('candidate-') and not release.get('draft') and release.get('immutable') is not True:
            raise ValueError('An earlier R1 release lacks immutable protection')
        for item in release['assets']:
            if item['name'].endswith('.deb'):
                identity = item['name'][:-4].rsplit('_', 2)
                checksum = item.get('digest', '')
                if len(identity) != 3 or not isinstance(checksum, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', checksum):
                    raise ValueError('Legacy package asset needs an explicit immutable identity before promotion')
                versions.append(dict(zip(('package', 'version', 'architecture'), identity), sha256=checksum[7:]))
            if item['name'] != 'release-manifest.json':
                continue
            if item['size'] > 4 * 1024 * 1024:
                raise ValueError('Existing release manifest exceeds supported size')
            text = gh('api', '-H', 'Accept: application/octet-stream',
                      f'repos/{repository}/releases/assets/{item["id"]}')
            manifest = json.loads(text)
            for asset in manifest['assets']:
                if asset['kind'] == 'app-deb':
                    versions.append({k: asset[k] for k in ('package', 'version', 'architecture', 'sha256')})
    return {'immutable_releases_enabled': enabled.get('enabled') is True,
            'release_exists': any(r['tag_name'] == tag for r in releases),
            'tag_exists': any(t['name'] == tag for t in tags), 'package_versions': versions}


def candidate_report(directory, archive, candidate_id, archive_sha, tooling, run_id):
    import candidate
    checked(tooling, r'[0-9a-f]{40}', 'tooling revision')
    manifest = candidate.verify_directory(directory, candidate_id)
    if sha(archive) != archive_sha:
        raise ValueError('Candidate archive differs from selected bytes')
    return {'schema': 1, 'kind': 'tyrell-candidate-verification', 'status': 'passed',
            'candidate_sha256': candidate_id, 'archive_sha256': archive_sha,
            'source_commit': manifest['source_commit'], 'tooling_commit': tooling,
            'run_id': int(run_id), 'artifact_name': 'release-candidate-' + str(run_id)}


def record_candidate(assembly, source_run_id, tooling, run_id, destination):
    import candidate
    data = read(assembly)
    with tempfile.TemporaryDirectory(prefix='tyrell-candidate-recheck-') as temporary:
        candidate.verify_archive(data['archive'], data['archive_sha256'], data['candidate_id'], Path(temporary).resolve())
    report = candidate_report(data['directory'], data['archive'], data['candidate_id'],
                              data['archive_sha256'], tooling, run_id)
    report['source_run_id'] = int(source_run_id)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    # These are the only files retained as the assembly workflow artifact.
    import shutil
    shutil.copyfile(data['archive'], destination / Path(data['archive']).name)
    (destination / 'candidate-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def prepare(repository, run_id, candidate_id, archive_sha, request_path, request_sha,
            output, *, root=ROOT):
    import candidate
    import promotion
    checked(candidate_id, r'[0-9a-f]{64}', 'candidate ID')
    checked(archive_sha, r'[0-9a-f]{64}', 'archive checksum')
    request_file = local(root, request_path)
    if sha(request_file) != request_sha:
        raise ValueError('Reviewed promotion request checksum mismatch')
    request = read(request_file)
    if request.get('candidate_sha256') != candidate_id or request.get('archive_sha256') != archive_sha:
        raise ValueError('Dispatch and reviewed promotion request differ')
    # Only reviewed default-branch files can approve a device receipt digest.
    qualification = local(root, request['qualification_path'])
    if sha(qualification) != request['qualification_sha256']:
        raise ValueError('Reviewed qualification receipt checksum mismatch')
    branch = api(f'repos/{repository}')['default_branch']
    checked(branch, r'[A-Za-z0-9_./-]+', 'default branch')
    default = 'refs/remotes/origin/' + branch
    candidate_run = run(repository, run_id, '.github/workflows/image-candidate.yml')
    if candidate_run['event'] != 'workflow_dispatch':
        raise ValueError('Candidate was not explicitly assembled')
    ancestor(root, candidate_run['head_sha'], default)
    ancestor(root, request['source_commit'], default)
    name = 'release-candidate-' + str(run_id)
    retained = artifact(repository, run_id, name, candidate_run['head_sha'])
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    downloaded = output / 'downloaded'
    gh('run', 'download', str(run_id), '--repo', repository, '--name', name, '--dir', downloaded)
    report = read(local(downloaded, 'candidate-verification.json'))
    archive = local(downloaded, 'candidate-' + candidate_id + '.tar')
    if sha(archive) != archive_sha:
        raise ValueError('Retained candidate archive checksum mismatch')
    # This invokes trusted verifier code, not any script contained in the tar.
    verified = candidate.verify_archive(archive, archive_sha, candidate_id, output / 'verified')
    directory = Path(verified['directory'])
    manifest = candidate.verify_directory(directory, candidate_id)
    source_id = report.get('source_run_id')
    source_run = run(repository, source_id, '.github/workflows/checks.yml', request['source_commit'])
    checks = {'source_run': source_run, 'candidate_run': candidate_run,
              'candidate_report': report, 'candidate_artifact': retained}
    approval = promotion.evaluate(manifest, candidate_id, request, checks, repository=repository,
                                   approved_qualification_sha256=sha(qualification),
                                   existing=existing(repository, promotion.tag_name(manifest['release_version']), root))
    approval['request_sha256'] = request_sha
    approval['promotion_tooling_commit'] = subprocess.check_output(
        ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    (output / 'promotion.json').write_text(json.dumps(approval, indent=2) + '\n')
    return {'approval': approval, 'directory': str(directory), 'archive': str(archive)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    auth = sub.add_parser('authenticate-selection')
    for name in ('repository', 'run-id', 'source-sha', 'inputs-root', 'selection-sha256', 'download-root', 'report'):
        auth.add_argument('--' + name, required=True)
    prep = sub.add_parser('prepare-promotion')
    for name in ('repository', 'run-id', 'candidate-id', 'archive-sha256', 'request', 'request-sha256', 'output', 'report'):
        prep.add_argument('--' + name, required=True)
    publish = sub.add_parser('publish-reviewed')
    for name in ('repository', 'run-id', 'candidate-id', 'archive-sha256', 'request', 'request-sha256', 'output', 'publication-output', 'report'):
        publish.add_argument('--' + name, required=True)
    record = sub.add_parser('record-candidate')
    for name in ('assembly', 'source-run-id', 'tooling-sha', 'run-id', 'destination', 'report'):
        record.add_argument('--' + name, required=True)
    args = parser.parse_args()
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = execute(args)
    except Exception as error:
        progress = read(report) if report.is_file() else {}
        progress['last_state'] = progress.get('status', 'before-publication')
        progress.update(status='failed', error=str(error))
        report.write_text(json.dumps(progress, indent=2) + '\n')
        print('release: ' + str(error), file=__import__('sys').stderr)
        return 1
    report.write_text(json.dumps(result, indent=2) + '\n')
    return 0


def execute(args):
    if args.command == 'authenticate-selection':
        result = authenticate_selection(args.repository, args.run_id, args.source_sha,
                                         args.inputs_root, args.selection_sha256,
                                         download_root=args.download_root)
    elif args.command in ('prepare-promotion', 'publish-reviewed'):
        result = prepare(args.repository, args.run_id, args.candidate_id, args.archive_sha256,
                         args.request, args.request_sha256, args.output)
        if args.command == 'publish-reviewed':
            import publish as publication
            files = publication.publication_files(result['archive'], result['directory'], result['approval'], args.publication_output)
            result = publication.publish(args.repository, result['approval'], files, args.publication_output,
                                          receipt_path=args.report)
    else:
        result = record_candidate(args.assembly, args.source_run_id, args.tooling_sha,
                                  args.run_id, args.destination)
    return result


if __name__ == '__main__':
    raise SystemExit(main())
