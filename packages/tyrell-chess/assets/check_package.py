#!/usr/bin/env python3
"""Validate the installable native app's payload without installing anything."""
import argparse
import io
from pathlib import Path
import struct
import subprocess
import tarfile


def output(*args):
    return subprocess.check_output(args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    args = parser.parse_args()
    fields = output('dpkg-deb', '--field', str(args.package)).decode()
    values = dict(line.split(': ', 1) for line in fields.splitlines() if ': ' in line and not line.startswith(' '))
    assert values['Package'] == 'tyrell-chess', values
    assert values['Architecture'] == 'arm64', values
    dependencies = values.get('Depends', '')
    assert all(value in dependencies for value in ('libc6', 'libstdc++6')), dependencies
    assert not any(value in dependencies.lower() for value in ('x11', 'glfw', 'mesa', 'libegl', 'libgles')), dependencies
    control = output('dpkg-deb', '--ctrl-tarfile', str(args.package))
    with tarfile.open(fileobj=io.BytesIO(control)) as archive:
        assert not ({Path(member.name).name for member in archive} &
                    {'preinst', 'postinst', 'prerm', 'postrm', 'triggers', 'conffiles'}), 'Unexpected maintainer/configuration scripts'
    expected = {
        'usr/bin/tyrell-chess',
        'usr/share/APPLaunch/applications/tyrell-chess.desktop',
        'usr/share/APPLaunch/share/images/tyrell-chess.png',
        'usr/share/applications/tyrell-chess.desktop',
        'usr/share/icons/hicolor/80x80/apps/tyrell-chess.png',
        'usr/share/doc/tyrell-chess/LICENSE',
        'usr/share/doc/tyrell-chess/cardputer-chess-LICENSE.txt',
        'usr/share/doc/tyrell-chess/UPSTREAM.json',
        'usr/share/doc/tyrell-chess/raylib-LICENSE.txt',
        'usr/share/doc/tyrell-chess/raylib-third-party-notices.txt',
    }
    payload = output('dpkg-deb', '--fsys-tarfile', str(args.package))
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        members = {member.name.removeprefix('./'): member for member in archive if not member.isdir()}
        assert set(members) == expected, {'missing': sorted(expected - set(members)), 'extra': sorted(set(members) - expected)}
        assert all(member.isfile() and not member.mode & 0o6000 for member in members.values()), 'Unexpected symlink or privileged file'
        def read(name):
            return archive.extractfile(members[name]).read()
        executable = read('usr/bin/tyrell-chess')
        assert executable[:5] == b'\x7fELF\x02' and executable[5] == 1, 'Expected little-endian ELF64'
        assert struct.unpack_from('<H', executable, 18)[0] == 183, 'Expected AArch64 ELF'
        assert members['usr/bin/tyrell-chess'].mode & 0o111, 'App is not executable'
        entry = read('usr/share/APPLaunch/applications/tyrell-chess.desktop').decode()
        assert 'Exec=/usr/bin/tyrell-chess\n' in entry
        assert 'Icon=share/images/tyrell-chess.png\n' in entry
        png = read('usr/share/APPLaunch/share/images/tyrell-chess.png')
        assert png[:8] == b'\x89PNG\r\n\x1a\n' and struct.unpack_from('>II', png, 16) == (80, 80)
        assert read('usr/share/icons/hicolor/80x80/apps/tyrell-chess.png') == png
        assert b'Permission is hereby granted' in read('usr/share/doc/tyrell-chess/cardputer-chess-LICENSE.txt')
    print('PASS: exact app payload, ARM64 ELF, dependencies, launcher/icon, licenses, and absence of system modifications')


if __name__ == '__main__':
    main()
