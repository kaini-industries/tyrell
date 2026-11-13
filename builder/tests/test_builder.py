"""Host-only checks for app selection and independent artifact publication."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
from collections import namedtuple
import tempfile
import unittest
from unittest.mock import patch


loader = importlib.machinery.SourceFileLoader(
    'tyrell_builder', str(Path(__file__).resolve().parents[2] / 'scripts/builder'))
spec = importlib.util.spec_from_loader(loader.name, loader)
builder = importlib.util.module_from_spec(spec)
loader.exec_module(builder)


class BuilderTests(unittest.TestCase):
    def test_video_bundle_includes_only_allowlisted_validation_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'packages/tyrell-video').mkdir(parents=True)
            (root / 'packages/tyrell-video/CMakeLists.txt').write_text('project(test)')
            (root / 'builder').mkdir()
            (root / 'locks').mkdir()
            (root / 'scripts').mkdir()
            for name in ['builder', 'video-fixtures.py', 'video-matrix.py', 'video-inventory.py', 'unrelated.py']:
                (root / 'scripts' / name).write_text('# test script')
            (root / '.env').write_text('secret')
            state = root / 'build'
            state.mkdir()
            archive = state / 'lvgl.tar.gz'
            archive.write_bytes(b'archive')
            locked = {'package': 'tyrell-video', 'version': '0.1.0', 'source_date_epoch': 1}
            with patch.object(builder, 'ROOT', root), patch.object(builder, 'STATE', state), \
                    patch.object(builder, 'fetch', return_value=archive), \
                    patch.object(builder, 'inputs', return_value=locked), \
                    patch.object(builder, 'require_space'):
                bundle = builder.bundle('video')
            with builder.tarfile.open(bundle) as tar:
                names = tar.getnames()
            source = json.loads((state / 'source-manifest.json').read_text())['source_files']
            for name in ['video-fixtures.py', 'video-matrix.py', 'video-inventory.py']:
                self.assertIn('scripts/' + name, names)
                self.assertIn('scripts/' + name, source)
            self.assertNotIn('scripts/unrelated.py', names)
            self.assertNotIn('.env', names)

    def test_space_guard_retains_host_reserve(self):
        usage = namedtuple('usage', 'total used free')
        with patch.object(builder.shutil, 'disk_usage', return_value=usage(1000, 900, 100)):
            with self.assertRaisesRegex(RuntimeError, 'source transfer needs'):
                builder.require_space(Path('/tmp'), 1, 'source transfer')
        with patch.object(builder.shutil, 'disk_usage', return_value=usage(2**30, 0, 2**30)):
            builder.require_space(Path('/tmp'), 1024, 'source transfer')

    def test_failed_source_bundle_removes_partial_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'packages/tyrell-status').mkdir(parents=True)
            (root / 'packages/tyrell-status/CMakeLists.txt').write_text('project(test)')
            (root / 'builder').mkdir()
            (root / 'locks').mkdir()
            (root / 'scripts').mkdir()
            (root / 'scripts/builder').write_text('# test builder')
            state = root / 'build'
            state.mkdir()
            archive = state / 'lvgl.tar.gz'
            archive.write_bytes(b'cache remains untouched')
            locked = {'package': 'tyrell-status', 'version': '0.1.0', 'source_date_epoch': 1}
            with patch.object(builder, 'ROOT', root), patch.object(builder, 'STATE', state), \
                    patch.object(builder, 'fetch', return_value=archive), \
                    patch.object(builder, 'inputs', return_value=locked), \
                    patch.object(builder, 'require_space'), \
                    patch.object(builder.tarfile.TarFile, 'add', side_effect=OSError('No space')):
                with self.assertRaises(OSError):
                    builder.bundle()
            self.assertFalse((state / 'builder-input.tar').exists())
            self.assertEqual(archive.read_bytes(), b'cache remains untouched')

    def test_app_selection_and_command_forwarding(self):
        self.assertEqual(builder.parse_args(['build']).app, 'status')
        self.assertEqual(builder.parse_args(['build', '--app', 'video']).app, 'video')
        self.assertEqual(builder.parse_args(['image', '--storage', '/tmp/image']).command,
                         ['--storage', '/tmp/image'])
        self.assertEqual(builder.parse_args(['shell', 'bash', '-lc', 'true']).command,
                         ['bash', '-lc', 'true'])

    def test_status_export_retains_video_and_backs_up_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'artifacts'
            (output / 'video').mkdir(parents=True)
            (output / 'video/movie.deb').write_text('video')
            (output / 'status.deb').write_text('old')
            stage = root / 'stage'
            stage.mkdir()
            (stage / 'status.deb').write_text('new')
            state = root / 'build'
            state.mkdir()
            with patch.object(builder, 'STATE', state):
                builder.publish_artifacts(stage, output, 'status')
            self.assertEqual((output / 'status.deb').read_text(), 'new')
            self.assertEqual((output / 'video/movie.deb').read_text(), 'video')
            backup = next(state.glob('previous-status-artifacts-*'))
            self.assertEqual((backup / 'status.deb').read_text(), 'old')

    def test_video_export_retains_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'artifacts'
            output.mkdir()
            (output / 'status.deb').write_text('status')
            stage = root / 'stage'
            stage.mkdir()
            (stage / 'movie.deb').write_text('video')
            state = root / 'build'
            state.mkdir()
            with patch.object(builder, 'STATE', state):
                builder.publish_artifacts(stage, output / 'video', 'video')
            self.assertEqual((output / 'status.deb').read_text(), 'status')
            self.assertEqual((output / 'video/movie.deb').read_text(), 'video')

    def test_failed_status_export_restores_both_apps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'artifacts'
            (output / 'video').mkdir(parents=True)
            (output / 'status.deb').write_text('old')
            (output / 'video/movie.deb').write_text('video')
            stage = root / 'stage'
            stage.mkdir()
            state = root / 'build'
            state.mkdir()
            original = Path.rename

            def fail_publish(path, target):
                if path == stage:
                    raise OSError('simulated export failure')
                return original(path, target)

            with patch.object(builder, 'STATE', state), patch.object(Path, 'rename', fail_publish):
                with self.assertRaises(OSError):
                    builder.publish_artifacts(stage, output, 'status')
            self.assertEqual((output / 'status.deb').read_text(), 'old')
            self.assertEqual((output / 'video/movie.deb').read_text(), 'video')

    def test_refuses_symlinked_other_app(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'artifacts'
            output.mkdir()
            (output / 'video').symlink_to(root / 'elsewhere', target_is_directory=True)
            stage = root / 'stage'
            stage.mkdir()
            with self.assertRaises(RuntimeError):
                builder.publish_artifacts(stage, output, 'status')


if __name__ == '__main__':
    unittest.main()
