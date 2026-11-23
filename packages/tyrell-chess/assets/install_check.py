#!/usr/bin/env python3
"""Install/run/remove the exact app in the isolated build VM, never a handheld."""
import argparse
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    args = parser.parse_args()
    current = subprocess.run(['dpkg-query', '-W', '-f=${db:Status-Status}', 'tyrell-chess'],
                             capture_output=True, text=True)
    if current.stdout.strip() not in ('', 'not-installed', 'config-files'):
        raise RuntimeError('A chess package is already installed in this builder; preserve it for inspection')
    attempted = False
    try:
        attempted = True
        subprocess.run(['sudo', 'dpkg', '--install', str(args.package.resolve())], check=True)
        subprocess.run(['/usr/bin/tyrell-chess', '--version'], check=True)
        subprocess.run(['/usr/bin/tyrell-chess', '--help'], check=True)
        verification = subprocess.run(['dpkg', '--verify', 'tyrell-chess'], check=True, capture_output=True, text=True)
        if verification.stdout.strip():
            raise RuntimeError('Installed package integrity mismatch: ' + verification.stdout.strip())
    finally:
        if attempted:
            # dpkg can fail after unpacking. Remove this test's package even
            # when installation did not reach the configured state.
            subprocess.run(['sudo', 'dpkg', '--remove', 'tyrell-chess'], check=True)
    assert not Path('/usr/bin/tyrell-chess').exists(), 'Installed executable remains after removal'
    assert not Path('/usr/share/APPLaunch/applications/tyrell-chess.desktop').exists(), 'Launcher entry remains after removal'
    print('PASS: exact package installs, runs CLI, verifies integrity, and removes cleanly in isolated builder')


if __name__ == '__main__':
    main()
