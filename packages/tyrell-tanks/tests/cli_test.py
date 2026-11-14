#!/usr/bin/env python3
"""Exercise server reports and a stalled graphical client without device access."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time


def cleanup(process):
    if process is None or process.poll() is not None:
        return
    # A stopped process cannot handle termination until it is resumed.
    os.kill(process.pid, signal.SIGCONT)
    process.terminate()
    try:
        process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    args = parser.parse_args()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(('127.0.0.1', 0))
        port = str(probe.getsockname()[1])
    server = client = None
    with tempfile.TemporaryDirectory(prefix='tyrell-session-') as directory:
        root = Path(directory)
        try:
            server = subprocess.Popen(
                [str(args.binary), '--server', '--port', port, '--seconds', '2',
                 '--report', str(root / 'server.json')],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            client = subprocess.Popen(
                [str(args.binary), '--headless', '--join', '127.0.0.1', '--port', port,
                 '--seconds', '6', '--report', str(root / 'client.json')],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            time.sleep(1.2)
            if server.poll() is not None or client.poll() is not None:
                raise RuntimeError('Session process exited before the stall check')
            # Drain any final host snapshots before stopping the client's loop.
            os.kill(server.pid, signal.SIGSTOP)
            time.sleep(.25)
            os.kill(client.pid, signal.SIGSTOP)
            time.sleep(3.25)
            os.kill(client.pid, signal.SIGCONT)
            client_output, _ = client.communicate(timeout=7)
            os.kill(server.pid, signal.SIGCONT)
            server_output, _ = server.communicate(timeout=3)
            if client.returncode or server.returncode:
                raise RuntimeError('Session process failed:\n' + client_output + server_output)
            host = json.loads((root / 'server.json').read_text())
            peer = json.loads((root / 'client.json').read_text())
            if host['frames'] != 0 or host['tick'] <= 0:
                raise RuntimeError('Server did not write its simulation report')
            if sum(player['active'] for player in host['players']) != 2:
                raise RuntimeError('Client never joined the host before it was paused')
            if peer['player'] != -1 or not any(
                    word in peer['status'].lower() for word in ('timed out', 'disconnected')):
                raise RuntimeError('Stalled client kept a stale connection: ' + str(peer))
            print('CLI server report and stalled-client cleanup passed')
        finally:
            cleanup(client)
            cleanup(server)


if __name__ == '__main__':
    main()
