#!/usr/bin/env python3
"""Draw the native 80px cartridge-style pixel icon without external libraries."""
from pathlib import Path
import struct
import zlib
SIZE=80
pixels=[[(54,72,133) for _ in range(SIZE)] for _ in range(SIZE)]
def rect(x,y,w,h,color):
    for row in range(max(0,y),min(SIZE,y+h)):
        for col in range(max(0,x),min(SIZE,x+w)):
            pixels[row][col]=color
for r in range(5):
    for c in range(5):
        rect(c*16,r*16,16,16,(67,103,151) if (r+c)%2 else (64,119,148))
ink=(25,33,61);cream=(249,237,200);gold=(250,197,85)
def block(x,y,w,h,color=ink):rect(10+x*4,5+y*4,w*4,h*4,color)
block(7,0,1,5);block(5,2,5,1)
block(4,5,7,4);block(5,6,5,2,cream)
block(5,9,5,3);block(6,9,3,3,cream)
block(2,12,11,3);block(3,12,9,1,cream);block(4,13,7,1,gold)
rect(10,71,60,4,gold)
for x,y in ((7,13),(68,53)):
    rect(x,y+2,7,3,gold);rect(x+2,y,3,7,gold)
raw=b''.join(b'\0'+bytes(c for pixel in row for c in pixel) for row in pixels)
def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
image=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',SIZE,SIZE,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw,9))+chunk(b'IEND',b'')
Path(__file__).with_name('tyrell-chess.png').write_bytes(image)
