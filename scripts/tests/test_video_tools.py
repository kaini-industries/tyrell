"""Host checks for conversion correctness and preservation of evidence files."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VideoToolsTests(unittest.TestCase):
    def test_interrupted_encode_removes_only_new_partial_files(self):
        fixtures = load_script('video-fixtures')
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'partial.mp4'

            def interrupt(*args, **kwargs):
                destination.write_bytes(b'partial output')
                raise KeyboardInterrupt()

            with patch.object(fixtures.subprocess, 'run', side_effect=interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    fixtures.ffmpeg([str(destination)])
            self.assertFalse(destination.exists())
            destination.symlink_to(Path(directory) / 'missing-target')
            with patch.object(fixtures.subprocess, 'run', side_effect=KeyboardInterrupt()):
                with self.assertRaises(KeyboardInterrupt):
                    fixtures.ffmpeg([str(destination)])
            self.assertTrue(destination.is_symlink())

    def test_invalid_duration_creates_no_fixture_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'fixtures'
            process = subprocess.run([sys.executable, str(SCRIPTS / 'video-fixtures.py'),
                                      'generate', str(destination), '--duration', 'nan'],
                                     capture_output=True, text=True)
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse(destination.exists())

    def test_matrix_preserves_existing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / 'fixtures'
            fixtures.mkdir()
            (fixtures / 'corrupt.mp4').write_bytes(b'invalid')
            probe = root / 'probe'
            probe.write_text('#!/usr/bin/env python3\nraise SystemExit(1)\n')
            probe.chmod(0o755)
            report = root / 'evidence.json'
            report.write_text('Previous verified evidence\n')
            process = subprocess.run([sys.executable, str(SCRIPTS / 'video-matrix.py'),
                                      str(probe), str(fixtures), '--output', str(report)],
                                     capture_output=True, text=True)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(report.read_text(), 'Previous verified evidence\n')

    def test_corrupt_fixture_requires_a_structured_media_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / 'fixtures'
            fixtures.mkdir()
            (fixtures / 'corrupt.mp4').write_bytes(b'invalid')
            probe = root / 'probe'
            for label, code, payload, expected in [('media', 1, '{"ok":false}', True),
                                                   ('cli', 2, '{"ok":false}', False),
                                                   ('crash', 1, '123', False)]:
                with self.subTest(label=label):
                    probe.write_text('#!/usr/bin/env python3\nimport sys\n'
                                     + 'print(' + repr(payload) + ')\nsys.exit(' + str(code) + ')\n')
                    probe.chmod(0o755)
                    report = root / (label + '.json')
                    process = subprocess.run([sys.executable, str(SCRIPTS / 'video-matrix.py'),
                                              str(probe), str(fixtures), '--output', str(report)],
                                             capture_output=True, text=True)
                    result = json.loads(report.read_text())
                    self.assertEqual(result['results'][0]['passed'], expected)
                    self.assertEqual(process.returncode == 0, expected)

    def test_inventory_digest_matches_sha256(self):
        inventory = load_script('video-inventory')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'large-file'
            data = b'chunked inventory digest\n' * 10000
            path.write_bytes(data)
            self.assertEqual(inventory.digest(path), hashlib.sha256(data).hexdigest())

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg and ffprobe required')
    def test_conversion_preserves_anamorphic_display_aspect_and_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'anamorphic.mp4'
            destination = root / 'handheld.mp4'
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
                            '-f', 'lavfi', '-i', 'testsrc2=size=720x576:rate=24', '-t', '0.5',
                            '-vf', 'setsar=16/15', '-c:v', 'libx264', '-preset', 'ultrafast', str(source)],
                           check=True, capture_output=True)
            command = [sys.executable, str(SCRIPTS / 'video-fixtures.py'), 'convert', str(source), str(destination)]
            subprocess.run(command, check=True, capture_output=True)
            probe = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                             '-show_entries', 'stream=width,height,sample_aspect_ratio,pix_fmt,r_frame_rate',
                                             '-of', 'json', str(destination)], text=True)
            video = json.loads(probe)['streams'][0]
            self.assertLessEqual(video['width'], 320)
            self.assertLessEqual(video['height'], 170)
            self.assertEqual(video['width'] % 2, 0)
            self.assertEqual(video['height'] % 2, 0)
            self.assertAlmostEqual(video['width'] / video['height'], 4 / 3, delta=0.01)
            self.assertEqual(video['sample_aspect_ratio'], '1:1')
            self.assertEqual(video['pix_fmt'], 'yuv420p')
            self.assertEqual(video['r_frame_rate'], '24/1')
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            process = subprocess.run(command, capture_output=True)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(hashlib.sha256(destination.read_bytes()).hexdigest(), digest)


if __name__ == '__main__':
    unittest.main()
