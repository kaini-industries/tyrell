"""Validate retained stages against a freshly prepared recipe before moving work."""
import hashlib
import json
from pathlib import Path
import re
import sys


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def preparation(tree):
    report = json.loads((tree / 'tyrell-prepared.json').read_text())
    source = report.get('input_root', '')
    if not Path(source).is_absolute() or len(Path(source).parts) < 4:
        raise ValueError('Retained preparation lacks a supported immutable input root; start a fresh build')
    assets = report.get('input_assets_sha256')
    if not assets or any(not re.fullmatch('[0-9a-f]{64}', value) for value in assets.values()):
        raise ValueError('Missing exact input asset identities; start a fresh build')
    recorded = report.get('prepared_files_sha256', {})
    if not recorded:
        raise ValueError('Missing prepared source hashes')
    for name, expected in recorded.items():
        path = tree / name
        if path.is_symlink() or not path.resolve().is_relative_to(tree.resolve()):
            raise ValueError('Unsafe prepared source: ' + name)
        if not path.is_file() or digest(path) != expected:
            raise ValueError('Prepared source changed after preparation: ' + name)
    return report


def sources(tree, prefixes):
    result = {}
    for prefix in prefixes:
        base = tree / prefix
        paths = [base, *base.rglob('*')] if base.is_dir() else [base]
        for path in paths:
            name = str(path.relative_to(tree))
            if re.fullmatch(r'stage[0-4]/SKIP', name):
                continue  # The runner deliberately creates these after validation.
            if path.is_symlink():
                result[name] = ('symlink', str(path.readlink()).encode())
            elif path.is_file():
                result[name] = ('file', path.read_bytes(), path.stat().st_mode & 0o111)
            elif path.is_dir():
                result[name] = ('directory',)
    return result


def validate(previous, current, mode):
    previous, current = Path(previous), Path(current)
    if mode not in {'stage2', 'stage4'}:
        raise ValueError('Unsupported resume stage')
    old, new = preparation(previous), preparation(current)
    for field in ('pi_gen_commit', 'drivers_commit', 'input_assets_sha256', 'apt_profile', 'apt_lock_sha256'):
        if old.get(field) != new.get(field):
            raise ValueError('Resume inputs differ: ' + field)
    if new.get('apt_profile') == 'frozen' and not re.fullmatch('[0-9a-f]{64}', new.get('apt_lock_sha256') or ''):
        raise ValueError('Frozen resume requires an exact APT lock')
    prefixes = ['build.sh', 'scripts', 'config', 'tyrell-inputs.conf', 'tyrell-apt-lock.json',
                'tyrell-runtime-apt', *['stage' + str(n) for n in range(int(mode[-1]))]]
    a, b = sources(previous, prefixes), sources(current, prefixes)
    if a.keys() != b.keys():
        raise ValueError('Retained stage source file set differs')
    for name, value in a.items():
        other = b[name]
        if value[0] == 'file' and other[0] == 'file':
            # Only a verified input-root relocation may differ between jobs.
            value = (value[0], value[1].replace(old['input_root'].encode(), b'@TYRELL_INPUT_ROOT@'), *value[2:])
            other = (other[0], other[1].replace(new['input_root'].encode(), b'@TYRELL_INPUT_ROOT@'), *other[2:])
        if value != other:
            raise ValueError('Retained stage source differs: ' + name)


if __name__ == '__main__':
    validate(*sys.argv[1:])
