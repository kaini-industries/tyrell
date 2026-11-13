"""Configuration and mutual exclusion regressions without starting a builder."""
import importlib.machinery
import importlib.util
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

loader = importlib.machinery.SourceFileLoader('builder_resources', str(Path(__file__).parents[2] / 'scripts/builder'))
spec = importlib.util.spec_from_loader(loader.name, loader)
builder = importlib.util.module_from_spec(spec)
loader.exec_module(builder)


class BuilderResourceTests(unittest.TestCase):
    def setup_vm(self, root):
        config = root / 'build/lima/tyrell-build/lima.yaml'
        config.parent.mkdir(parents=True)
        config.write_text('memory: 6GiB\n')
        return config

    def resource_api(self):
        return {'host_preflight': Mock(), 'memory_mib': Mock(return_value=4096),
                'check_memory_capacity': Mock()}

    def test_explicit_ram_change_refuses_running_vm(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.setup_vm(root)
            with patch.object(builder, 'ROOT', root), patch.object(builder, 'STATE', root / 'build'), \
                    patch.dict(builder.os.environ, {'TYRELL_BUILDER_MEMORY': '4GiB'}), \
                    patch.object(builder.runpy, 'run_path', return_value=self.resource_api()), \
                    patch.object(builder, 'instance_state', return_value=('Running', 6 * 1024**3)), \
                    patch.object(builder, 'lima') as lima:
                with self.assertRaisesRegex(RuntimeError, 'Stop the builder'):
                    builder.up()
                lima.assert_not_called()

    def test_stopped_vm_explicit_override_edits_before_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.setup_vm(root)
            with patch.object(builder, 'ROOT', root), patch.object(builder, 'STATE', root / 'build'), \
                    patch.dict(builder.os.environ, {'TYRELL_BUILDER_MEMORY': '4GiB'}), \
                    patch.object(builder.runpy, 'run_path', return_value=self.resource_api()), \
                    patch.object(builder, 'instance_state', return_value=('Stopped', 6 * 1024**3)), \
                    patch.object(builder, 'lima') as lima:
                builder.up()
                self.assertEqual(lima.call_args_list[0].args,
                                 ('edit', '--tty=false', '--memory=4.0', 'tyrell-build'))
                self.assertEqual(lima.call_args_list[1].args, ('start', '--tty=false', 'tyrell-build'))

    def test_no_override_preserves_existing_vm_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.setup_vm(root)
            with patch.object(builder, 'ROOT', root), patch.object(builder, 'STATE', root / 'build'), \
                    patch.dict(builder.os.environ, {}, clear=True), \
                    patch.object(builder.runpy, 'run_path', return_value=self.resource_api()), \
                    patch.object(builder, 'lima') as lima:
                builder.up()
                lima.assert_called_once_with('start', '--tty=false', 'tyrell-build')

    def test_new_vm_template_defaults_to_four_gib(self):
        template = Path(__file__).parents[1] / 'lima.yaml'
        self.assertIn('\nmemory: 4GiB\n', template.read_text())

    def test_active_image_rejected_before_package_inputs(self):
        active = SimpleNamespace(stdout='LoadState=loaded\nActiveState=active\nSubState=running\n')
        with patch.object(builder, 'shared_lock', return_value=nullcontext()), \
                patch.object(builder, 'up'), patch.object(builder, 'guest', return_value=active) as guest, \
                patch.object(builder, 'bundle') as bundle:
            with self.assertRaisesRegex(RuntimeError, 'image build is running'):
                builder.build('video')
            bundle.assert_not_called()
            self.assertEqual(guest.call_count, 1)

    def test_unknown_guest_state_fails_closed(self):
        with patch.object(builder, 'guest', return_value=SimpleNamespace(stdout='')) as guest:
            with self.assertRaisesRegex(RuntimeError, 'Cannot determine'):
                builder.assert_guest_idle()
            self.assertEqual(guest.call_count, 1)


if __name__ == '__main__':
    unittest.main()
