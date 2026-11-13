#!/usr/bin/env python3
import hashlib,json,lzma,struct,sys,zlib
from pathlib import Path

root=Path(sys.argv[1])
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda:f.read(1024*1024),b''):h.update(data)
    return h.hexdigest()
report=json.loads((root/'image-verification.json').read_text())
compressed=list(root.glob('*.img.xz'))
if len(compressed)!=1:
    raise ValueError('Expected one compressed image artifact')
digest=hashlib.sha256()
with lzma.open(compressed[0],'rb') as stream:
    for data in iter(lambda:stream.read(1024*1024),b''):digest.update(data)
payload_sha=digest.hexdigest()
if payload_sha!=report['image']['sha256_after']:
    raise ValueError('Compressed image payload differs from the verified raw image')
for ppm in root.glob('*.ppm'):
    magic,dimensions,maximum,pixels=ppm.read_bytes().split(b'\n',3)
    width,height=map(int,dimensions.split())
    if magic!=b'P6' or maximum!=b'255' or len(pixels)!=width*height*3:
        raise ValueError('Unexpected preview format')
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    scanlines=b''.join(b'\0'+pixels[y*width*3:(y+1)*width*3] for y in range(height))
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))
    png+=chunk(b'IDAT',zlib.compress(scanlines))+chunk(b'IEND',b'')
    ppm.with_suffix('.png').write_bytes(png)
files={str(p.relative_to(root)):sha(p) for p in sorted(root.rglob('*')) if p.is_file() and p.name not in {'image-manifest.json','SHA256SUMS'}}
manifest={'kind':'Tyrell Debian image','inputs':json.loads((root/'build-inputs.json').read_text()),
          'verification':report,'physical_boot':'not tested','deployment':'not performed; device and SD untouched',
          'compressed_payload_sha256':payload_sha,
          'sha256':files}
(root/'image-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
files['image-manifest.json']=sha(root/'image-manifest.json')
(root/'SHA256SUMS').write_text(''.join(f'{value}  {name}\n' for name,value in sorted(files.items())))
print('Recorded image checksums and build provenance.')
