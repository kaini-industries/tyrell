#!/usr/bin/env python3
"""Adversarial packaging regression checks; no VM or device connections."""
import hashlib
from pathlib import Path
import runpy
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]


class PackageToolsTest(unittest.TestCase):
    def snapshot(self, temporary):
        namespace = runpy.run_path(str(ROOT / 'scripts/chess'))
        function = namespace['snapshot_inputs']
        function.__globals__['ROOT'] = temporary
        function.__globals__['LOCK'] = {'raylib': {'directory': 'raylib-test'}}
        for name in ('scripts/chess', 'locks/game-build-inputs.json',
                     'packages/tyrell-chess/src/main.cpp',
                     'packages/tyrell-game/src/runtime.cpp',
                     'build/game-deps/raylib-test/src/rcore.c'):
            path = temporary / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('original ' + name)
        return function

    def test_archive_hashes_use_identical_bytes_during_concurrent_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = self.snapshot(root)
            changing = root / 'packages/tyrell-chess/src/main.cpp'
            original = changing.read_bytes()
            real_read = Path.read_bytes
            def mutate_after_read(path):
                data = real_read(path)
                if path == changing:
                    path.write_bytes(b'concurrent edit with another length')
                return data
            archive = root / 'input.tar'
            with patch.object(Path, 'read_bytes', mutate_after_read):
                manifest = snapshot(archive)
            with tarfile.open(archive) as stream:
                for name, expected in manifest.items():
                    data = stream.extractfile(name).read()
                    self.assertEqual(hashlib.sha256(data).hexdigest(), expected)
                self.assertEqual(stream.extractfile('packages/tyrell-chess/src/main.cpp').read(), original)
            self.assertNotEqual(changing.read_bytes(), original)

    def test_snapshot_excludes_credentials_and_python_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = self.snapshot(root)
            (root / '.env').write_text('private')
            cache = root / 'packages/tyrell-chess/assets/__pycache__/secret.pyc'
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b'cache')
            manifest = snapshot(root / 'input.tar')
            self.assertFalse(any('.env' in name or '__pycache__' in name for name in manifest))

    def test_snapshot_rejects_symlink_to_outside_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = self.snapshot(root)
            (root / 'private').write_text('private')
            (root / 'packages/tyrell-chess/leak').symlink_to(root / 'private')
            with self.assertRaisesRegex(RuntimeError, 'symlink'):
                snapshot(root / 'input.tar')

    def test_partial_dpkg_failure_still_removes_own_test_package(self):
        namespace = runpy.run_path(str(ROOT / 'packages/tyrell-chess/assets/install_check.py'))
        calls = []
        def mocked_run(args, **kwargs):
            calls.append(args)
            if args[:3] == ['sudo', 'dpkg', '--install']:
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0, stdout='not-installed', stderr='')
        with patch.object(sys, 'argv', ['install_check.py', '/tmp/test-chess.deb']), patch.object(subprocess, 'run', mocked_run):
            with self.assertRaises(subprocess.CalledProcessError):
                namespace['main']()
        self.assertIn(['sudo', 'dpkg', '--remove', 'tyrell-chess'], calls)


if __name__ == '__main__':
    unittest.main()
