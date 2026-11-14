#!/usr/bin/env python3
"""Inspect a built native .deb without installing or executing it.

Usage: python3 package_test.py tyrell-tanks_0.1.0-1_arm64.deb
       [--enet-source PATH] [--raylib-source PATH]
"""
import argparse
import configparser
import io
from pathlib import Path, PurePosixPath
import runpy
import struct
import subprocess
import tarfile
import zlib


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def archive(package, option):
    result = subprocess.check_output(['dpkg-deb', option, str(package)])
    with tarfile.open(fileobj=io.BytesIO(result), mode='r:') as source:
        files = {}
        for entry in source:
            name = str(PurePosixPath(entry.name))
            if entry.isdir():
                continue
            require(entry.isfile(), f'Unexpected non-regular package member: {name}')
            require(name not in files, f'Duplicate package member: {name}')
            files[name] = (entry, source.extractfile(entry).read())
        return files


def desktop(data, icon):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(data.decode())
    entry = parser['Desktop Entry']
    require(entry.get('Type') == 'Application', 'Desktop entry must launch an application')
    require(entry.get('Name') == 'Sandline Tanks', 'Launcher name must be Sandline Tanks')
    require(entry.get('Exec') == '/usr/bin/tyrell-tanks', 'Desktop entry points at the wrong executable')
    require(entry.get('Icon') == icon, 'Desktop icon path does not match its destination')
    require(entry.get('Terminal') == 'false', 'Game must not open an extra terminal')
    require('Game;' in entry.get('Categories', ''), 'Standard desktop needs a game category')


def png(data):
    require(data.startswith(b'\x89PNG\r\n\x1a\n'), 'Icon is not PNG')
    at, image, header, ended = 8, bytearray(), None, False
    while at < len(data):
        require(at+12 <= len(data), 'Truncated PNG chunk')
        length = struct.unpack('>I', data[at:at+4])[0]
        kind, content = data[at+4:at+8], data[at+8:at+8+length]
        require(at+12+length <= len(data), 'Truncated PNG data')
        expected = struct.unpack('>I', data[at+8+length:at+12+length])[0]
        require(zlib.crc32(kind+content) & 0xffffffff == expected, 'Corrupt icon CRC')
        if kind == b'IHDR':
            header = struct.unpack('>IIBBBBB', content)
        elif kind == b'IDAT':
            image.extend(content)
        elif kind == b'IEND':
            ended = True
        at += 12+length
    require(header == (80, 80, 8, 2, 0, 0, 0), 'Icon must be native 80x80 RGB PNG')
    require(ended and len(zlib.decompress(image)) == 80*(1+80*3), 'Icon pixel payload is incomplete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--enet-source', type=Path)
    parser.add_argument('--raylib-source', type=Path)
    args = parser.parse_args()
    fields = subprocess.check_output(['dpkg-deb', '--field', str(args.package)], text=True)
    control = {}
    for line in fields.splitlines():
        if line and not line[0].isspace() and ': ' in line:
            key, value = line.split(': ', 1)
            control[key] = value
    require(control.get('Package') == 'tyrell-tanks', 'Wrong Debian package name')
    require(control.get('Version') == '0.1.0-1', 'Wrong Debian package version')
    require(control.get('Architecture') == 'arm64', 'Device package must be ARM64')
    require(bool(control.get('Depends')), 'SHLIBDEPS did not emit runtime dependencies')
    dependencies = [item.strip().split()[0] for item in control['Depends'].split(',')]
    forbidden = ('libx11', 'libgl1', 'libegl', 'libgles', 'libglfw', 'libwayland', 'libvulkan', 'libgbm')
    require(not any(name.startswith(forbidden) for name in dependencies),
            'Native software renderer unexpectedly depends on a desktop/GPU stack')

    files = archive(args.package, '--fsys-tarfile')
    expected = {
        'usr/bin/tyrell-tanks',
        'usr/share/APPLaunch/applications/tyrell-tanks.desktop',
        'usr/share/applications/tyrell-tanks.desktop',
        'usr/share/APPLaunch/share/images/tyrell-tanks.png',
        'usr/share/icons/hicolor/80x80/apps/tyrell-tanks.png',
        'usr/share/doc/tyrell-tanks/LICENSE',
        'usr/share/doc/tyrell-tanks/enet-LICENSE.txt',
        'usr/share/doc/tyrell-tanks/raylib-LICENSE.txt',
        'usr/share/doc/tyrell-tanks/raylib-third-party-notices.txt',
    }
    require(set(files) == expected, f'Unexpected package payload: {set(files) ^ expected}')
    binary_info, binary = files['usr/bin/tyrell-tanks']
    require(binary_info.mode & 0o111, 'Game binary is not executable')
    require(binary[:6] == b'\x7fELF\x02\x01' and len(binary) > 20 and
            struct.unpack('<H', binary[18:20])[0] == 183, 'Game is not a little-endian AArch64 ELF')
    desktop(files['usr/share/APPLaunch/applications/tyrell-tanks.desktop'][1], 'share/images/tyrell-tanks.png')
    desktop(files['usr/share/applications/tyrell-tanks.desktop'][1], 'tyrell-tanks')
    icon = files['usr/share/APPLaunch/share/images/tyrell-tanks.png'][1]
    png(icon)
    require(icon == files['usr/share/icons/hicolor/80x80/apps/tyrell-tanks.png'][1],
            'Launcher and standard desktop icons must match')
    package_source = Path(__file__).resolve().parents[1]
    for installed, source in [
        ('LICENSE', package_source/'LICENSE'),
        ('enet-LICENSE.txt', args.enet_source/'LICENSE' if args.enet_source else None),
        ('raylib-LICENSE.txt', args.raylib_source/'LICENSE' if args.raylib_source else None),
    ]:
        content = files[f'usr/share/doc/tyrell-tanks/{installed}'][1]
        require(content, f'Empty license: {installed}')
        if source:
            require(content == source.read_bytes(), f'License differs from its source: {installed}')
    notices = files['usr/share/doc/tyrell-tanks/raylib-third-party-notices.txt'][1]
    require(b'Le Juez Victor' in notices and b'src/external/rlsw.h' in notices,
            'The software renderer needs its own upstream MIT notice')
    if args.raylib_source:
        generator = runpy.run_path(str(package_source/'assets/make_notices.py'))['generate']
        require(notices == generator(args.raylib_source), 'Third-party notice bundle differs from its upstream sources')
    controls = archive(args.package, '--ctrl-tarfile')
    require(not {'preinst', 'postinst', 'prerm', 'postrm', 'triggers', 'conffiles'} & set(controls),
            'Game package must not contain maintainer scripts, triggers, or configuration files')
    print('Package passed: ARM64 metadata, exact payload, launcher/icons, licenses, no service/config changes')


if __name__ == '__main__':
    main()
