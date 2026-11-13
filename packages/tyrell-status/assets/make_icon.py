#!/usr/bin/env python3
"""Generate the original geometric 80x80 app icon using only Python stdlib."""
from pathlib import Path
import struct
import zlib

size = 80
pixels = []
for y in range(size):
    row = bytearray()
    for x in range(size):
        color = (16, 28, 36)
        if 6 <= x < 74 and 6 <= y < 74:
            color = (26, 42, 53)
        if (17 <= x < 27 and 44 <= y < 61 or
                35 <= x < 45 and 32 <= y < 61 or
                53 <= x < 63 and 20 <= y < 61):
            color = (105, 220, 200)
        row.extend(color)
    pixels.append(b'\x00' + row)

def chunk(kind, data):
    return (struct.pack('>I', len(data)) + kind + data +
            struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))

png = b'\x89PNG\r\n\x1a\n'
png += chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0))
png += chunk(b'IDAT', zlib.compress(b''.join(pixels), 9))
png += chunk(b'IEND', b'')
Path(__file__).with_name('tyrell-status.png').write_bytes(png)
