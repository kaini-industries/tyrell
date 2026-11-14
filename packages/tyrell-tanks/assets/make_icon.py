#!/usr/bin/env python3
"""Reproduce the original 80x80 tank icon with Python's standard library.

Artwork and generator are MIT licensed with Tyrell Tanks. SVG is the editable
reference; this matching shape renderer avoids additional build dependencies.
"""
from pathlib import Path
import struct
import zlib

SIZE = 80
SAMPLES = 4


def rounded(x, y, left, top, width, height, radius):
    if not (left <= x < left + width and top <= y < top + height):
        return False
    nearest_x = min(max(x, left + radius), left + width - radius)
    nearest_y = min(max(y, top + radius), top + height - radius)
    return (x - nearest_x) ** 2 + (y - nearest_y) ** 2 <= radius ** 2


def color_at(x, y):
    ink = (36, 43, 40)
    color = ink
    if rounded(x, y, 3, 3, 74, 74, 13):
        color = (198, 158, 104)
    if 13 <= x < 67 and any(abs(y - row) < 0.5 for row in (21, 40, 59)):
        color = (185, 142, 89)
    if rounded(x, y, 21, 29, 42, 36, 4):
        color = (154, 121, 77)
    if rounded(x, y, 18, 25, 11, 37, 3) or rounded(x, y, 51, 25, 11, 37, 3):
        color = ink
    if (20 <= x < 27 or 53 <= x < 60) and any(abs(y-row) < 1 for row in (32, 39, 46, 53)):
        color = (115, 117, 97)
    if rounded(x, y, 26, 25, 28, 37, 4):
        color = (80, 117, 86)
    if 29 <= x < 51 and 54 <= y < 58:
        color = (234, 213, 162)
    if (x-40)**2 + (y-40)**2 <= 12**2:
        color = (57, 90, 66)
    if (x-40)**2 + (y-38)**2 <= 10**2:
        color = (106, 140, 99)
    if 37 <= x < 43 and 12 <= y < 39:
        color = ink
    if rounded(x, y, 35, 10, 10, 5, 1):
        color = (57, 67, 54)
    if rounded(x, y, 34, 37, 7, 5, 1):
        color = (212, 188, 130)
    return color


def chunk(kind, data):
    return (struct.pack('>I', len(data)) + kind + data +
            struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))


def main():
    rows = []
    for y in range(SIZE):
        row = bytearray([0])
        for x in range(SIZE):
            colors = [color_at(x + (sx + .5)/SAMPLES, y + (sy + .5)/SAMPLES)
                      for sy in range(SAMPLES) for sx in range(SAMPLES)]
            row.extend(round(sum(color[channel] for color in colors)/(SAMPLES*SAMPLES))
                       for channel in range(3))
        rows.append(row)
    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', SIZE, SIZE, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(b''.join(rows), 9))
    png += chunk(b'IEND', b'')
    Path(__file__).with_name('tyrell-tanks.png').write_bytes(png)


if __name__ == '__main__':
    main()
