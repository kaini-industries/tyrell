#!/usr/bin/env python3
"""Validate compatibility evidence without granting package or hardware approval.

This reads committed evidence only. It never starts a builder, fetches inputs,
opens device nodes, or follows ignored local artifact links.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = 'research/compatibility-ledger.json'
APP_ROOTS = {
    'status': ['packages/tyrell-status', 'packages/tyrell-app-common', 'locks/build-inputs.json'],
    'video': ['packages/tyrell-video', 'packages/tyrell-app-common', 'locks/build-inputs.json', 'locks/video-build-inputs.json'],
    'chess': ['packages/tyrell-chess', 'packages/tyrell-app-common', 'packages/tyrell-game', 'scripts/chess',
              'scripts/game', 'locks/game-build-inputs.json'],
    'sandline': ['packages/tyrell-tanks', 'packages/tyrell-game', 'scripts/tanks',
                 'scripts/game', 'locks/game-build-inputs.json'],
    'beacon': ['packages/tyrell-game', 'scripts/game', 'locks/game-build-inputs.json'],
}
TARGETS = {'original-m5', 'tyrell-zero', 'gh0st', 'stock-derived', 'full-source'}
SCOPES = {'source-inspected', 'automated-tested', 'device-tested', 'not-tested'}
RESULTS = {'passed', 'failed', 'not-tested'}
KINDS = {'current-source', 'retained-package', 'documented-package', 'file-image'}
HEX256 = re.compile(r'[0-9a-f]{64}\Z')


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def safe_file(root: Path, value: str) -> Path:
    """Allow repository-relative regular files, without traversing symlinks."""
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('expected a repository-relative POSIX path')
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {'.', '..'} for part in value.split('/')):
        raise ValueError('absolute, empty-component and traversal paths are forbidden')
    if '' in value.split('/'):
        raise ValueError('empty path components are forbidden')
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('symlink paths are forbidden: ' + value)
    if not path.is_file():
        raise ValueError('missing regular file: ' + value)
    return path


def source_digest(root: Path, roots: list[str]) -> str:
    """Hash names and bytes of the declared app/dependency source boundary.

    Python bytecode is generated output. Everything else under these roots,
    including new source files, participates. Historical manifests are not
    compared to this moving source boundary.
    """
    files: dict[str, Path] = {}
    for name in roots:
        location = root / name
        if location.is_symlink():
            raise ValueError('source root is a symlink: ' + name)
        if location.is_dir():
            for path in location.rglob('*'):
                relative = path.relative_to(root)
                if '__pycache__' in relative.parts or path.suffix == '.pyc':
                    continue
                if path.is_symlink():
                    raise ValueError('source contains a symlink: ' + relative.as_posix())
                if path.is_file():
                    files[relative.as_posix()] = safe_file(root, relative.as_posix())
        else:
            files[name] = safe_file(root, name)
    if not files:
        raise ValueError('empty source boundary')
    value = hashlib.sha256()
    for name, path in sorted(files.items()):
        value.update(name.encode('utf-8') + b'\0' + sha256(path).encode('ascii') + b'\n')
    return value.hexdigest()


def _unique(items: object, label: str, errors: list[str]) -> dict[str, dict]:
    if not isinstance(items, list) or not items:
        errors.append(label + ': expected a nonempty list')
        return {}
    result = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']:
            errors.append(label + ': every entry requires a nonempty id')
            continue
        if item['id'] in result:
            errors.append(label + ': duplicate id ' + item['id'])
        result[item['id']] = item
    return result


def _text(item: dict, key: str, label: str, errors: list[str]) -> None:
    if not isinstance(item.get(key), str) or not item[key].strip():
        errors.append(f'{label}: {key} must be nonempty text')


def _digest(item: dict, key: str, label: str, errors: list[str]) -> None:
    if not isinstance(item.get(key), str) or not HEX256.fullmatch(item[key]):
        errors.append(f'{label}: {key} must be lowercase SHA-256')


def _member(value: object, choices: object) -> bool:
    return isinstance(value, str) and value in choices


def _reference(mapping: dict, value: object) -> dict | None:
    return mapping.get(value) if isinstance(value, str) else None


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def load_ledger(root: Path, ledger_path: str = DEFAULT_LEDGER) -> dict:
    return json.loads(safe_file(root, ledger_path).read_text(), object_pairs_hook=_unique_keys)


def validate_ledger(root: Path, ledger: dict | None = None) -> list[str]:
    """Return validation errors; an empty list validates data, not compatibility.

    Current-source identities must match this checkout. Historical exact
    package/image results remain historical and never qualify changed sources.
    External images/documented packages are not opened or revalidated here.
    """
    root = root.resolve()
    errors: list[str] = []
    if ledger is None:
        try:
            ledger = load_ledger(root)
        except (OSError, ValueError) as error:
            return [str(error)]
    if not isinstance(ledger, dict):
        return ['ledger must be an object']
    if type(ledger.get('schema')) is not int or ledger['schema'] != 1:
        errors.append('unsupported compatibility ledger schema')
    if ledger.get('kind') != 'compatibility-evidence-ledger':
        errors.append('incorrect ledger kind')
    if ledger.get('grants_release_approval') is not False:
        errors.append('ledger must explicitly deny release approval')
    for key in ('reviewed_date', 'reviewed_revision', 'scope'):
        _text(ledger, key, 'ledger', errors)
    if not re.fullmatch(r'[0-9a-f]{40}', str(ledger.get('reviewed_revision', ''))):
        errors.append('reviewed_revision must identify an exact Git commit')
    evidence = _unique(ledger.get('evidence'), 'evidence', errors)
    subjects = _unique(ledger.get('subjects'), 'subjects', errors)
    records = _unique(ledger.get('records'), 'records', errors)
    for identity, item in evidence.items():
        _digest(item, 'sha256', identity, errors)
        try:
            if sha256(safe_file(root, item.get('path'))) != item.get('sha256'):
                errors.append(identity + ': evidence bytes changed; review and rebind evidence')
        except (OSError, ValueError, TypeError) as error:
            errors.append(f'{identity}: {error}')
    current_apps = set()
    for identity, subject in subjects.items():
        kind = subject.get('kind')
        if not _member(kind, KINDS):
            errors.append(identity + ': unsupported subject kind')
        for key in ('component', 'version', 'source_identity', 'availability'):
            _text(subject, key, identity, errors)
        _digest(subject, 'sha256', identity, errors)
        try:
            if kind == 'current-source':
                app = subject.get('component')
                if not _member(app, APP_ROOTS) or subject.get('roots') != APP_ROOTS.get(app):
                    errors.append(identity + ': incorrect or incomplete app source boundary')
                else:
                    if app in current_apps:
                        errors.append(identity + ': duplicate current-source app')
                    current_apps.add(app)
                    if source_digest(root, subject['roots']) != subject.get('sha256'):
                        errors.append(identity + ': current source changed; new identity and qualification required')
                if subject.get('availability') != 'checkout':
                    errors.append(identity + ': current source must be available in checkout')
            elif kind == 'retained-package':
                if subject.get('availability') != 'checkout':
                    errors.append(identity + ': retained package must be available in checkout')
                if sha256(safe_file(root, subject.get('path'))) != subject.get('sha256'):
                    errors.append(identity + ': retained package bytes do not match')
                manifest = subject.get('manifest')
                if not _member(manifest, evidence):
                    errors.append(identity + ': retained package requires a bound manifest')
                else:
                    data = json.loads(safe_file(root, evidence[manifest]['path']).read_text())
                    declared = data.get('package_sha256', data.get('sha256', {}).get(Path(subject['path']).name))
                    if declared != subject.get('sha256'):
                        errors.append(identity + ': package digest differs from bound manifest')
            elif _member(kind, {'documented-package', 'file-image'}):
                if subject.get('availability') != 'external-not-revalidated' or 'path' in subject:
                    errors.append(identity + ': external artifacts must not imply local byte verification')
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            errors.append(f'{identity}: {error}')
    if current_apps != set(APP_ROOTS):
        errors.append('ledger must include exactly one current-source identity for each of the five apps')
    coverage = set()
    for identity, record in records.items():
        subject = _reference(subjects, record.get('subject'))
        if subject is None:
            errors.append(identity + ': unknown subject')
        target, scope, result = record.get('target'), record.get('evidence_scope'), record.get('result')
        if not _member(target, TARGETS) or not _member(scope, SCOPES) or not _member(result, RESULTS):
            errors.append(identity + ': invalid target, evidence scope or result')
        if not _member(record.get('implementation'), {'implemented', 'planned'}):
            errors.append(identity + ': invalid implementation state')
        for key in ('scenario', 'toolchain'):
            _text(record, key, identity, errors)
        limits = record.get('limitations')
        if not isinstance(limits, list) or not limits or any(not isinstance(v, str) or not v for v in limits):
            errors.append(identity + ': explicit limitations are required')
        refs = record.get('evidence')
        if not isinstance(refs, list) or not refs or any(not _member(ref, evidence) for ref in refs):
            errors.append(identity + ': unknown or missing evidence reference')
        elif subject and _member(subject.get('kind'), {'documented-package', 'file-image'}):
            # An external subject must at least bind the actual artifact digest
            # named in its report. This still does not open those artifact bytes.
            try:
                reports = [safe_file(root, evidence[ref]['path']).read_text() for ref in refs]
                if not any(subject.get('sha256', '') in report for report in reports):
                    errors.append(identity + ': external artifact digest is absent from bound evidence')
            except (OSError, ValueError, TypeError, KeyError) as error:
                errors.append(f'{identity}: {error}')
        if _member(result, {'passed', 'failed'}) and (scope == 'not-tested' or record.get('implementation') != 'implemented'):
            errors.append(identity + ': an untested/planned scenario cannot claim a test outcome')
        if result == 'not-tested' and not _member(scope, {'not-tested', 'source-inspected'}):
            errors.append(identity + ': test evidence scope requires a test outcome')
        if subject and subject.get('kind') == 'current-source':
            if isinstance(subject.get('component'), str) and isinstance(target, str):
                coverage.add((subject['component'], target))
            if scope == 'device-tested':
                errors.append(identity + ': physical acceptance requires exact package/image bytes, not current source')
        if not _member(record.get('binding'), {'current-source', 'historical-exact-artifact'}):
            errors.append(identity + ': explicit current/historical binding required')
        if subject and ((subject.get('kind') == 'current-source') != (record.get('binding') == 'current-source')):
            errors.append(identity + ': subject and current/historical binding disagree')
    required = {(app, target) for app in APP_ROOTS for target in ('original-m5', 'tyrell-zero', 'gh0st')}
    if not required <= coverage:
        errors.append('current-source coverage missing: ' + ', '.join(f'{a}/{t}' for a, t in sorted(required - coverage)))
    for target in ('stock-derived', 'full-source', 'tyrell-zero'):
        rows = [r for r in records.values() if r.get('target') == target and
                (_reference(subjects, r.get('subject')) or {}).get('kind') == 'file-image']
        if not rows:
            errors.append('missing file-image evidence boundary for ' + target)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['validate'])
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--ledger', default=DEFAULT_LEDGER, help='repository-relative ledger path')
    parser.add_argument('--json', action='store_true', dest='json_output')
    args = parser.parse_args(argv)
    try:
        data = load_ledger(args.root.resolve(), args.ledger)
        errors = validate_ledger(args.root, data)
    except (OSError, ValueError) as error:
        errors = [str(error)]
    if args.json_output:
        print(json.dumps({'valid': not errors, 'errors': errors,
                          'scope': 'evidence consistency only; no release or hardware approval'}, indent=2))
    else:
        for error in errors:
            print('ledger: ' + error, file=sys.stderr)
        if not errors:
            print('Compatibility ledger validated; no release or hardware approval granted.')
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
