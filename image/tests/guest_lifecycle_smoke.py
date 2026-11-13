#!/usr/bin/env python3
"""Exercise the real job wrapper in the already-running builder using dummy work.

No image is built and the production image unit/work tree is not changed.
Run explicitly from the host, after other builder work has finished.
"""
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
B = runpy.run_path(str(ROOT / 'scripts/builder'))
identifier = 'tyrell-lifecycle-smoke-' + uuid.uuid4().hex
scratch = '/var/tmp/' + identifier
lock = '/var/tmp/tyrell-locks/builder.lock'
units = []
results = {}


def clear_unit(unit):
    for action in ('stop', 'reset-failed'):
        try:
            B['guest']('sudo', '-n', 'systemctl', action, unit,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            pass  # Successful transient units may already have been collected.


with B['shared_lock']():
    B['assert_guest_idle']()
    B['guest']('mkdir', '-p', scratch)
    try:
        B['lima']('copy', '--backend=scp', str(ROOT / 'image/run-job.sh'),
                  B['INSTANCE'] + ':' + scratch + '/run-job.sh')
        for case, code, tampered in [('success', 0, False), ('failure', 7, False), ('tampered', 0, True)]:
            directory = scratch + '/' + case
            content = 'fixed smoke archive\n'
            checksum = hashlib.sha256(content.encode()).hexdigest()
            B['guest']('python3', '-c',
                      "from pathlib import Path; import sys; p=Path(sys.argv[1]); "
                      "(p/'input/builder').mkdir(parents=True); "
                      "(p/'image-input.tar').write_text(sys.argv[2]); "
                      "(p/'input/builder/build-image.sh').write_text(sys.argv[3])",
                      directory, content + ('changed' if tampered else ''),
                      '#!/bin/bash\nsleep 2\ntouch "$1/ran"\nexit ' + str(code) + '\n')
            unit = identifier + '-' + case + '.service'
            units.append(unit)
            B['guest']('sudo', '-n', 'systemd-run', '--unit=' + unit, '--no-block',
                      '--service-type=oneshot', '--property=RemainAfterExit=yes',
                      '--property=StandardOutput=append:' + directory + '/build.log',
                      '--property=StandardError=inherit',
                      '/usr/bin/flock', '--nonblock', lock, '/bin/bash', scratch + '/run-job.sh',
                      case, directory + '/input', 'fresh', checksum,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if case == 'success':
                time.sleep(.5)
                try:
                    B['assert_guest_idle']()
                except subprocess.CalledProcessError:
                    results['busy_guest_exclusion'] = 'passed'
                else:
                    raise AssertionError('Busy root job did not exclude package/stop guard')
            for _ in range(50):
                value = B['guest']('python3', '-c',
                                   "from pathlib import Path; import sys; p=Path(sys.argv[1]); print(p.read_text() if p.exists() else 'null')",
                                   directory + '/result.json', stdout=subprocess.PIPE, text=True)
                outcome = json.loads(value.stdout)
                if outcome is not None:
                    break
                time.sleep(.2)
            else:
                raise AssertionError('Timed out waiting for durable result: ' + case)
            expected = 1 if tampered else code
            assert outcome == {'job_id': case, 'exit_status': expected, 'bundle_sha256': checksum}, outcome
            ran = B['guest']('python3', '-c',
                             "from pathlib import Path; import sys; print((Path(sys.argv[1])/'input/ran').exists())",
                             directory, stdout=subprocess.PIPE, text=True).stdout.strip()
            assert (ran == 'True') != tampered, (case, ran)
            clear_unit(unit)
            B['guest']('test', '-s', directory + '/result.json')
            results[case] = 'passed; durable result retained after unit cleanup'
        destination = ROOT / 'build/hardening-validation'
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'guest-lifecycle.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps(results, indent=2))
    finally:
        for unit in units:
            clear_unit(unit)
        B['guest']('sudo', '-n', 'rm', '-rf', '--', scratch)
