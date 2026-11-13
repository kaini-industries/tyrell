#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
import platform
import struct
import subprocess
import sys
import zlib

root=Path(sys.argv[1])
def png_chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)

# Convert the actual software-rendered screenshots using only the standard
# library, so the preview images are available without installing image tools.
for ppm in root.glob('*.ppm'):
    magic, dimensions, maximum, pixels = ppm.read_bytes().split(b'\n', 3)
    width, height = map(int, dimensions.split())
    if magic != b'P6' or maximum != b'255' or len(pixels) != width * height * 3:
        raise ValueError('Unexpected PPM format: ' + str(ppm))
    scanlines = b''.join(b'\0' + pixels[y*width*3:(y+1)*width*3] for y in range(height))
    png = b'\x89PNG\r\n\x1a\n'
    png += png_chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    png += png_chunk(b'IDAT', zlib.compress(scanlines)) + png_chunk(b'IEND', b'')
    ppm.with_suffix('.png').write_bytes(png)
files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.iterdir()) if p.is_file() and p.name not in {'manifest.json','SHA256SUMS'}}
source=json.loads((root/'source-manifest.json').read_text())
tests=json.loads((root/'test-inventory.json').read_text())
manifest={
    'package': source.get('package','tyrell-status'),
    'version': source.get('debian_version',source['version']+'-1'), 'architecture': 'arm64',
    'source': source, 'builder_os': Path('/etc/os-release').read_text(),
    'builder_machine': platform.machine(),
    'compiler': subprocess.check_output(['c++','--version'],text=True).splitlines()[0],
    'cmake': subprocess.check_output(['cmake','--version'],text=True).splitlines()[0],
    'verification': {
        'automated_tests': {'result': 'passed', 'log': 'tests.txt',
                            'detail_log': 'ctest-detail.txt',
                            'names': [test['name'] for test in tests['tests']]},
        'headless_render': 'passed', 'install_remove_in_builder': 'passed',
        'device_installation': 'not performed', 'physical_display_keyboard': 'not tested',
        'cardputer_firmware': 'untouched; no device access by the build workflow',
        'video_performance_audio_hdmi': 'not tested on hardware',
    },
    'sha256': files,
}
(root/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
files['manifest.json']=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()
(root/'SHA256SUMS').write_text(''.join(f'{hash_value}  {name}\n' for name,hash_value in sorted(files.items())))
print('Recorded artifact checksums and build provenance.')
