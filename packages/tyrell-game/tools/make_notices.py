#!/usr/bin/env python3
"""Preserve complete upstream license comment blocks for raylib's C helpers.

The native build includes the single-file helpers directly under src/external.
Keep complete comments, including alternate licenses and bundled subcomponent
notices, rather than attempting to rewrite individual license texts.
"""
import argparse
from pathlib import Path
import re

# Skip quoted C strings/characters so apparent comment delimiters inside source
# literals cannot truncate a real copyright or license block.
TOKENS = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|/\*.*?\*/|//[^\n]*(?:\n[ \t]*//[^\n]*)*''', re.S)
LICENSE = re.compile(r'copyright|\blicen[cs]e\b|permission is hereby|public domain|\bCC0\b|software is provided', re.I)


def generate(raylib):
    external = raylib/'src/external'
    required = {'rlsw.h', 'stb_image.h', 'stb_image_resize2.h', 'stb_truetype.h',
                'stb_image_write.h', 'stb_perlin.h', 'stb_rect_pack.h',
                'tinyobj_loader_c.h', 'cgltf.h', 'cgltf_write.h', 'm3d.h', 'par_shapes.h',
                'pep.h', 'qoi.h', 'rltexgpu.h', 'rprand.h', 'sinfl.h', 'sdefl.h', 'vox_loader.h'}
    covered = set()
    output = [
        'RAYLIB THIRD-PARTY NOTICES\n',
        'Complete license-bearing comment blocks from the pinned raylib source\n'
        'files directly under src/external. This collection also preserves notices\n'
        'for optional upstream components. See raylib-LICENSE.txt for raylib itself.\n',
    ]
    for source in sorted(external.iterdir()):
        if source.suffix not in {'.h', '.c'} or not source.is_file():
            continue
        comments = [token.group(0) for token in TOKENS.finditer(source.read_text(encoding='utf-8'))
                    if token.group(0).startswith(('/*', '//')) and LICENSE.search(token.group(0))]
        if not comments:
            continue
        covered.add(source.name)
        output.append(f'\n{"="*72}\nsrc/external/{source.name}\n{"="*72}\n')
        output.extend(comment+'\n\n' for comment in comments)
    if required-covered:
        raise RuntimeError(f'Missing native dependency notices: {sorted(required-covered)}')
    return ''.join(output).encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raylib_source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.write_bytes(generate(args.raylib_source))


if __name__ == '__main__':
    main()
