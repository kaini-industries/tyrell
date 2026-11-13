"""Canonical first-boot dependencies and explicit guarded provisioning."""
from contextlib import nullcontext
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).parents[2]
loader = importlib.machinery.SourceFileLoader('image_deps_builder', str(REPO / 'scripts/builder'))
spec = importlib.util.spec_from_loader(loader.name, loader)
builder = importlib.util.module_from_spec(spec)
loader.exec_module(builder)


class ImageDependencyTests(unittest.TestCase):
    def test_first_boot_template_uses_canonical_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(builder, 'STATE', Path(directory)):
                template = builder.render_lima_template().read_text()
        expected = builder.image_dependencies()
        line = next(line for line in template.splitlines() if 'apt-get install' in line and 'debootstrap' in line)
        self.assertEqual(line.split('--no-install-recommends ', 1)[1].split(), expected)
        self.assertNotIn('# TYRELL_IMAGE_DEPENDENCIES', template)
        self.assertIn('quilt', expected)
        self.assertIn('bmap-tools', expected)

    def test_missing_or_duplicate_marker_fails(self):
        for content in ['# absent\n', '      # TYRELL_IMAGE_DEPENDENCIES\n' * 2]:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'builder').mkdir()
                (root / 'builder/lima.yaml').write_text(content)
                with patch.object(builder, 'ROOT', root), patch.object(builder, 'STATE', root):
                    with self.assertRaisesRegex(RuntimeError, 'exactly one'):
                        builder.render_lima_template()
                self.assertFalse((root / 'builder-template.yaml').exists())

    def test_invalid_dependency_cannot_become_shell_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'builder').mkdir()
            (root / 'builder/image-dependencies.txt').write_text('quilt\n$(touch bad)\n')
            with patch.object(builder, 'ROOT', root), self.assertRaisesRegex(RuntimeError, 'Invalid canonical'):
                builder.image_dependencies()

    def test_busy_builder_refuses_before_provisioning_transfer(self):
        with patch.object(builder, 'shared_lock', return_value=nullcontext()), \
                patch.object(builder, 'up'), \
                patch.object(builder, 'assert_guest_idle', side_effect=RuntimeError('busy')), \
                patch.object(builder, 'lima') as lima, patch.object(builder, 'guest') as guest:
            with self.assertRaisesRegex(RuntimeError, 'busy'):
                builder.image_deps()
            lima.assert_not_called()
            guest.assert_not_called()

    def test_check_cli_holds_guest_lock_without_install(self):
        with patch.object(builder, 'shared_lock', return_value=nullcontext()), \
                patch.object(builder, 'up'), patch.object(builder, 'assert_guest_idle'), \
                patch.object(builder, 'lima'), patch.object(builder, 'guest') as guest:
            builder.image_deps(check_only=True)
        final = guest.call_args.args
        self.assertEqual(final[:5], ('sudo', '-n', 'flock', '-n', '/var/tmp/tyrell-locks/builder.lock'))
        self.assertEqual(final[-1], '--check')
        self.assertEqual(builder.parse_args(['image-deps', '--check']).check, True)

    def test_check_script_detects_missing_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(REPO / 'builder/ensure-image-deps.sh', root)
            shutil.copy(REPO / 'builder/image-dependencies.txt', root)
            tools = root / 'bin'
            tools.mkdir()
            stubs = {'dpkg': '#!/bin/sh\nprintf "arm64\\n"\n',
                     'uname': '#!/bin/sh\nprintf "aarch64\\n"\n',
                     'dpkg-query': '#!/bin/sh\ncase "$*" in *quilt) printf "not-installed\\n";; *) printf "installed\\n";; esac\n'}
            for name, text in stubs.items():
                path = tools / name
                path.write_text(text)
                path.chmod(0o755)
            result = subprocess.run(['bash', str(root / 'ensure-image-deps.sh'), '--check'],
                                    env={**os.environ, 'PATH': str(tools) + ':' + os.environ['PATH']},
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Missing builder image tool: quilt', result.stderr)


if __name__ == '__main__':
    unittest.main()
