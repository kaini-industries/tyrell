#!/usr/bin/env python3
"""Read-only device package/kernel/launcher inventory for chess installation.

Output belongs in an SD-backed evidence directory, outside installed files.
"""
import argparse
import datetime
import hashlib
import json
import platform
from pathlib import Path
import subprocess

PROTECTED = (
    '/boot/firmware/config.txt', '/boot/firmware/cmdline.txt',
    '/boot/firmware/start.elf', '/boot/firmware/fixup.dat',
    '/boot/firmware/overlays/cardputerzero-v5-overlay.dtbo',
    '/boot/firmware/overlays/bq27220_v5.dtbo',
    '/boot/firmware/overlays/bmi270_bmm150_overlay.dtbo',
    '/usr/share/APPLaunch/bin/M5CardputerZero-APPLaunch',
    '/usr/lib/systemd/user/APPLaunch.service',
    '/etc/cardputerzero-dtoverlays.commit', '/etc/rpi-issue',
    '/etc/apt/preferences.d/cardputerzero-kernel',
)


def text(*args):
    return subprocess.check_output(args, text=True, timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--compare', type=Path, help='Pre-install inventory; require only tyrell-chess package changes')
    args = parser.parse_args()
    packages = {}
    for line in text('dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${db:Status-Status}\n').splitlines():
        name, version, status = line.split('\t')
        packages[name] = {'version': version, 'status': status}
    launcher = dict(line.split('=', 1) for line in text('systemctl', '--user', 'show', 'APPLaunch.service',
        '-p', 'MainPID', '-p', 'NRestarts', '-p', 'ActiveState', '-p', 'SubState',
        '-p', 'ExecMainStartTimestampMonotonic').splitlines() if '=' in line)
    inventory = {'timestamp_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 'architecture': platform.machine(), 'kernel': platform.release(),
                 'sha256': {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in PROTECTED},
                 'packages': packages, 'launcher': launcher}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, indent=2) + '\n')
    summary = {'inventory': str(args.output), 'packages': len(packages), 'protected_files': len(PROTECTED)}
    if args.compare:
        before = json.loads(args.compare.read_text())
        changed = [name for name in sorted(set(before['packages']) | set(packages))
                   if before['packages'].get(name) != packages.get(name)]
        preservation = {'changed_packages': changed,
                        'only_chess_package_changed': changed == ['tyrell-chess'],
                        'protected_files_unchanged': before['sha256'] == inventory['sha256'],
                        'kernel_unchanged': before['kernel'] == inventory['kernel'],
                        'launcher_unchanged': all(before['launcher'][key] == launcher[key] for key in
                            ('MainPID', 'NRestarts', 'ExecMainStartTimestampMonotonic'))}
        preservation['passed'] = all(value for key, value in preservation.items() if key != 'changed_packages')
        args.output.with_name('preservation.json').write_text(json.dumps(preservation, indent=2) + '\n')
        summary.update(preservation)
    print(json.dumps(summary, indent=2))
    return 0 if summary.get('passed', True) else 1


if __name__ == '__main__':
    raise SystemExit(main())
