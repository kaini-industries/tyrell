#!/usr/bin/env python3
"""Verify bounded public Beacon server/host/client paths without device access."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time


def cleanup(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=2)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def wait_for_host(process, port):
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        require(process.poll() is None, 'Beacon host exited before joining')
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                return
        time.sleep(.01)
    raise RuntimeError('Beacon host did not bind its UDP port')


def lan_session(binary, root, env, server):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    host = peer = None
    label = 'server' if server else 'host'
    try:
        host = subprocess.Popen(
            [str(binary), *(['--server'] if server else ['--headless', '--host']),
             '--port', str(port), '--seconds', '2', '--autoplay',
             '--report', str(root / f'{label}.json')],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        wait_for_host(host, port)
        peer = subprocess.Popen(
            [str(binary), '--headless', '--join', '127.0.0.1', '--port', str(port),
             '--seconds', '1.4', '--autoplay',
             '--report', str(root / f'{label}-client.json')],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        peer_output, _ = peer.communicate(timeout=6)
        host_output, _ = host.communicate(timeout=6)
        require(host.returncode == 0 and peer.returncode == 0,
                f'Beacon {label}/client failed:\n{host_output}{peer_output}')
        authoritative = json.loads((root / f'{label}.json').read_text())
        client = json.loads((root / f'{label}-client.json').read_text())
        require(authoritative['frames'] == 0 if server else authoritative['frames'] > 0,
                f'Unexpected {label} renderer behavior: {authoritative}')
        require(authoritative['local_player'] == 0 and client['local_player'] > 0,
                f'Beacon did not assign distinct local players: {authoritative}, {client}')
        for report in (authoritative, client):
            require(report['sample'] == 'beacon-arena' and report['tick'] > 0 and
                    report['peak_players'] == 2 and report['maximum_local_movement'] > .1,
                    f'Beacon {label}/client simulation did not retain LAN activity: {report}')
        require(client['frames'] > 0 and client['connected'] and
                sum(actor['active'] for actor in client['actors']) == 2,
                f'Beacon client did not receive both actors: {client}')
    finally:
        cleanup(peer)
        cleanup(host)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    args = parser.parse_args()
    env = os.environ.copy()
    env.pop('GH0ST_SHELL_FD', None)
    # Headless and server paths must not use even deliberately unusable devices.
    env['LV_LINUX_FBDEV_DEVICE'] = '/unavailable-beacon-test/framebuffer'
    env['LV_LINUX_KEYBOARD_DEVICE'] = '/unavailable-beacon-test/keyboard'
    with tempfile.TemporaryDirectory(prefix='tyrell-beacon-cli-') as directory:
        root = Path(directory)
        lan_session(args.binary, root, env, server=True)
        lan_session(args.binary, root, env, server=False)
        for option in ('--frames', '--port'):
            result = subprocess.run([str(args.binary), '--headless', option, '1.5'],
                                    capture_output=True, text=True, env=env, timeout=3)
            require(result.returncode == 1 and 'requires an integer' in result.stderr,
                    f'Beacon accepted fractional {option}: {result.stderr}')
        result = subprocess.run(
            [str(args.binary), '--headless', '--frames', '1', '--report', str(root)],
            capture_output=True, text=True, env=env, timeout=3)
        require(result.returncode == 1 and 'Cannot open report' in result.stderr,
                f'Beacon did not report bounded output failure: {result.stderr}')
    print('PASS: public Beacon server, headless host/client, integer bounds and report failure')


if __name__ == '__main__':
    main()
