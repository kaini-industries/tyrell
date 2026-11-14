import hashlib
from pathlib import Path
import runpy
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]

class GameToolsTests(unittest.TestCase):
    def setUp(self):
        self.api=runpy.run_path(str(ROOT/'scripts/game'))

    def test_small_backends_do_not_require_graphics_downloads(self):
        select=self.api['selected_dependencies']
        self.assertEqual(select('core'),{})
        self.assertEqual(set(select('network')),{'enet'})
        self.assertEqual(set(select('headless')),{'raylib','enet'})

    def test_modified_extracted_source_fails_even_with_matching_archive(self):
        with tempfile.TemporaryDirectory(prefix='tyrell-game-pins-') as directory:
            root=Path(directory);tree=root/'sample-1';tree.mkdir()
            source=tree/'code.c';source.write_text('int sample = 1;\n')
            archive=root/'sample-1.tar.gz'
            with tarfile.open(archive,'w:gz') as out:out.add(tree,arcname='sample-1')
            item={'version':'1','directory':tree.name,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
            verify=self.api['verify_source'];verify.__globals__['DEPS']=root
            verify('sample',item)
            source.write_text('int sample = 2;\n')
            with self.assertRaisesRegex(RuntimeError,'modified source'):verify('sample',item)

    def test_added_header_cannot_shadow_verified_dependency_includes(self):
        with tempfile.TemporaryDirectory(prefix='tyrell-game-injected-') as directory:
            root=Path(directory);tree=root/'sample-1';tree.mkdir()
            (tree/'code.c').write_text('#include <stdlib.h>\n')
            archive=root/'sample-1.tar.gz'
            with tarfile.open(archive,'w:gz') as out:out.add(tree,arcname=tree.name)
            item={'version':'1','directory':tree.name,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
            verify=self.api['verify_source'];verify.__globals__['DEPS']=root
            verify('sample',item)
            (tree/'stdlib.h').write_text('#error unpinned injected header\n')
            with self.assertRaisesRegex(RuntimeError,'unexpected source'):verify('sample',item)

    def test_verified_source_directory_cannot_be_replaced_with_symlink(self):
        with tempfile.TemporaryDirectory(prefix='tyrell-game-link-') as directory:
            root=Path(directory);tree=root/'sample-1';tree.mkdir()
            (tree/'code.c').write_text('int sample;\n')
            archive=root/'sample-1.tar.gz'
            with tarfile.open(archive,'w:gz') as out:out.add(tree,arcname=tree.name)
            item={'version':'1','directory':tree.name,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
            verify=self.api['verify_source'];verify.__globals__['DEPS']=root
            target=root/'replacement';tree.rename(target);tree.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError,'replaced source directory'):verify('sample',item)

    def test_core_configuration_runs_without_dependency_cache(self):
        configure=self.api['configure']
        with tempfile.TemporaryDirectory(prefix='tyrell-game-core-') as directory:
            configure.__globals__['DEPS']=Path(directory)/'absent-cache'
            with mock.patch.dict(configure.__globals__,run=mock.Mock()) as globals_:
                configure('framework','core')
                arguments=globals_['run'].call_args.args
        self.assertIn('-DTYRELL_GAME_BUILD_RUNTIME=OFF',arguments)
        self.assertIn('-DTYRELL_GAME_BUILD_NETWORK=OFF',arguments)

    def test_legacy_tanks_entry_preserves_separator_and_game_arguments(self):
        result=mock.Mock(returncode=0)
        with mock.patch('sys.argv',['scripts/tanks','run','--','--host']),mock.patch('subprocess.run',return_value=result) as run:
            with self.assertRaises(SystemExit) as exit_:
                runpy.run_path(str(ROOT/'scripts/tanks'),run_name='__main__')
        self.assertEqual(exit_.exception.code,0)
        self.assertEqual(run.call_args.args[0][2:],['--project','tanks','run','--','--host'])

    @unittest.skipUnless(shutil.which('cmake') and shutil.which('ctest'), 'CMake tools unavailable')
    def test_explicit_framework_tests_are_registered_when_parent_tests_are_off(self):
        with tempfile.TemporaryDirectory(prefix='tyrell-game-ctest-') as directory:
            subprocess.run(['cmake','-S',str(ROOT/'packages/tyrell-game'),'-B',directory,
                            '-DBUILD_TESTING=OFF','-DTYRELL_GAME_BUILD_TESTS=ON',
                            '-DTYRELL_GAME_BUILD_RUNTIME=OFF','-DTYRELL_GAME_BUILD_NETWORK=OFF',
                            '-DTYRELL_GAME_BUILD_EXAMPLES=OFF'],check=True,capture_output=True,text=True)
            listed=subprocess.run(['ctest','--test-dir',directory,'-N'],check=True,capture_output=True,text=True)
            self.assertIn('Test #1: tyrell-game-core',listed.stdout)
            self.assertIn('Total Tests: 1',listed.stdout)

    @unittest.skipUnless(shutil.which('cmake'), 'CMake unavailable')
    def test_benchmark_only_configuration_omits_desktop_runtime(self):
        with tempfile.TemporaryDirectory(prefix='tyrell-game-benchmark-') as directory:
            root=Path(directory);raylib=root/'raylib';enet=root/'enet';build=root/'build'
            (raylib/'src').mkdir(parents=True);(raylib/'src/rcore.c').write_text('int core;\n')
            (enet/'include/enet').mkdir(parents=True);(enet/'include/enet/enet.h').touch()
            (enet/'empty.c').write_text('int enet;\n')
            (enet/'CMakeLists.txt').write_text('add_library(enet STATIC empty.c)\n')
            subprocess.run(['cmake','-S',str(ROOT/'packages/tyrell-tanks'),'-B',str(build),
                            '-DBUILD_TESTING=OFF','-DTYRELL_GAME_BUILD_TESTS=OFF',
                            '-DTANKS_BUILD_DESKTOP=OFF',f'-DRAYLIB_SOURCE_DIR={raylib}',
                            f'-DENET_SOURCE_DIR={enet}'],check=True,capture_output=True,text=True)
            targets=(build/'CMakeFiles/TargetDirectories.txt').read_text()
            self.assertIn('tanks-display-bench-rlsw.dir',targets)
            self.assertNotIn('tyrell_game_runtime.dir',targets)
            self.assertNotIn('tyrell_game_graphics.dir',targets)

if __name__=='__main__':unittest.main()
