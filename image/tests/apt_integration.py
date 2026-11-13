#!/usr/bin/env python3
"""Explicit Linux-only APT/debootstrap smoke test in a new scratch directory."""
import argparse
import fcntl
import functools
import http.server
import importlib.util
import json
from pathlib import Path
import subprocess
import threading
import urllib.request
import hashlib


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('repository', type=Path)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
repository = args.repository.resolve()
output = args.output.resolve()
if output.exists():
    raise ValueError('Preserving existing scratch results; choose a new output')
# Existing project lock is created by the normal builder user. Opening read-only
# allows root to share the same inode under Linux protected_regular semantics.
with Path('/var/tmp/tyrell-locks/builder.lock').open('rb') as lease:
    fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    output.mkdir(parents=True)
    handler = functools.partial(QuietHandler, directory=str(repository))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = 'http://127.0.0.1:' + str(server.server_port)
    try:
        lock = json.loads((repository / 'apt-lock.json').read_text())
        kernel = next(item for item in lock['packages'] if item['Package'] == 'linux-base-rpi-v8')
        with urllib.request.urlopen(base + '/' + kernel['file']) as stream:
            if hashlib.sha256(stream.read()).hexdigest() != kernel['sha256']:
                raise ValueError('Pinned kernel pool URL did not return locked bytes')
        with (output / 'debootstrap.log').open('w') as log:
            # Preserve the vendor's actual argument ordering, including its
            # repeated --include option (the final occurrence wins).
            subprocess.run(['debootstrap', '--no-check-gpg', '--arch=arm64', '--include', 'gnupg',
                            '--components=main,contrib,non-free', '--exclude=info,ifupdown',
                            '--include=ca-certificates', '--download-only', 'trixie',
                            str(output / 'bootstrap'), base + '/'], stdout=log, stderr=log, check=True)
        etc = output / 'etc/apt'
        etc.mkdir(parents=True)
        (etc / 'sources.list').write_text('deb [trusted=yes] ' + base + ' stage2 main\n')
        state = output / 'state'
        (state / 'lists/partial').mkdir(parents=True)
        (state / 'status').write_text('')
        cache = output / 'cache'
        (cache / 'archives/partial').mkdir(parents=True)
        apt = ['apt-get', '-o', 'Dir::Etc=' + str(etc), '-o', 'Dir::Etc::sourceparts=-',
               '-o', 'Dir::Etc::preferences=-', '-o', 'Dir::Etc::preferencesparts=-',
               '-o', 'Dir::State=' + str(state), '-o', 'Dir::State::status=' + str(state / 'status'),
               '-o', 'Dir::Cache=' + str(cache), '-o', 'APT::Architecture=arm64',
               '-o', 'Debug::NoLocking=1']
        with (output / 'apt-update.log').open('w') as log:
            subprocess.run(apt + ['update'], stdout=log, stderr=log, check=True)
        resolution = subprocess.check_output(apt + ['--simulate', '--no-install-recommends', 'install',
                                                    'factorytest=1.0.0-m5stack1',
                                                    'libcamera0.7=0.7.2+rpt20260817-1'],
                                             text=True, stderr=subprocess.STDOUT)
        (output / 'apt-resolution.log').write_text(resolution)
        if 'Inst libcamera0.7 ' not in resolution or 'Inst libcamera0.8 ' in resolution:
            raise ValueError('Frozen camera ABI resolution differs from baseline')
        (output / 'result.json').write_text(json.dumps({'debootstrap': 'passed',
                'pinned_kernel_pool_url': 'passed',
                'factorytest_camera_resolution': 'passed', 'sources': 'guest loopback only',
                'device_access': 'none', 'package_installation': 'none; downloads and simulation only'}, indent=2) + '\n')
        print('Frozen APT integration passed: debootstrap downloads and FactoryTest/camera dependency resolution.')
    finally:
        server.shutdown()
        server.server_close()
