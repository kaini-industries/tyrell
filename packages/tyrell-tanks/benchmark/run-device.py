#!/usr/bin/env python3
"""Run beside the ARM64 binaries on an idle Zero, preserving APPLaunch and LCD.

No installs, driver settings, or service configuration changes. Requires the
same user that owns APPLaunch. Results and temporary screen backup stay here.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parent
SPI = Path('/sys/bus/spi/devices/spi0.0/statistics')
child = None

def stop(signum, _frame):
    if child is not None and child.poll() is None:
        child.terminate()
    raise SystemExit(128 + signum)

def launcher_state():
    data = subprocess.check_output(['systemctl', '--user', 'show', 'APPLaunch.service',
                                    '-p', 'MainPID', '-p', 'NRestarts', '-p', 'ActiveState'], text=True)
    return dict(line.split('=', 1) for line in data.splitlines() if '=' in line)

def spi_stats():
    return {key: int((SPI / key).read_text()) for key in ['bytes_tx', 'errors', 'timedout']}

def main():
    global child
    cases = {'rlsw-unpaced': ('rlsw', 0, True), 'gles-unpaced': ('gles', 0, True),
             'rlsw-30': ('rlsw', 30, True), 'gles-30': ('gles', 30, True),
             'deferred-30': ('rlsw', 30, False)}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['all', *cases], default='all')
    options = parser.parse_args()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGHUP, stop)
    if subprocess.run(['pgrep', '-x', 'tyrell-video'], stdout=subprocess.DEVNULL).returncode == 0:
        raise SystemExit('Close Tyrell Video before benchmarking the display.')
    before = launcher_state()
    pid = int(before['MainPID'])
    if pid < 2 or before['ActiveState'] != 'active':
        raise SystemExit('Expected the existing active APPLaunch process.')
    if Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()[0] in ('T', 't'):
        raise SystemExit('APPLaunch is already suspended; close the foreground app first.')
    children = Path(f'/proc/{pid}/task/{pid}/children').read_text().strip()
    if children:
        raise SystemExit('APPLaunch has child processes; close the foreground app first.')
    summary = {'launcher_before': before, 'runs': []}
    original = b''
    fd = os.open('/dev/fb_lcd', os.O_RDWR | os.O_CLOEXEC)
    paused = False
    try:
        paused = True
        os.kill(pid, signal.SIGSTOP)
        time.sleep(.1)
        original = os.pread(fd, 108800, 0)
        if len(original) != 108800:
            raise RuntimeError('Unexpected native framebuffer length')
        selected = cases.values() if options.case == 'all' else [cases[options.case]]
        for backend, fps, commit in selected:
            name = f'{backend}-{fps}-' + ('commit' if commit else 'deferred')
            args = [str(ROOT / f'tanks-display-bench-{backend}'), '--seconds', '8',
                    '--warmup', '1', '--fps', str(fps), '--fb', '/dev/fb_lcd',
                    '--output', str(ROOT / f'{name}.json'),
                    '--frame-output', str(ROOT / f'{name}.ppm')]
            if not commit:
                args.append('--no-commit')
            with (ROOT / f'{name}.log').open('w') as log:
                child = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
                # Wait for the measured phase rather than guessing how long
                # EGL initialization takes. Sample its central five seconds.
                ready_deadline = time.monotonic() + 15
                while 'TANKS_MEASUREMENT_STARTED' not in (ROOT / f'{name}.log').read_text():
                    if child.poll() is not None or time.monotonic() >= ready_deadline:
                        raise RuntimeError(f'{name} did not enter measurement; see its log')
                    time.sleep(.02)
                time.sleep(.5)
                start_stats = spi_stats()
                start = time.monotonic()
                deadline = start + 5
                while time.monotonic() < deadline and child.poll() is None:
                    time.sleep(.01)
                elapsed = time.monotonic() - start
                end_stats = spi_stats()
                if elapsed < 4.9 or child.poll() is not None:
                    raise RuntimeError(f'{name} did not complete a steady five-second SPI window')
                try:
                    code = child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                    raise RuntimeError(f'{name} exceeded the bounded run')
            item = {'name': name, 'exit_code': code, 'spi_window_seconds': elapsed,
                    'spi_deltas': {k: end_stats[k] - start_stats[k] for k in start_stats},
                    'spi_full_frame_equivalents_per_second':
                        (end_stats['bytes_tx'] - start_stats['bytes_tx']) / 108800 / elapsed}
            report = ROOT / f'{name}.json'
            if report.exists():
                item['application'] = json.loads(report.read_text())
            summary['runs'].append(item)
            print(name, 'exit', code, 'SPI frames/s', round(item['spi_full_frame_equivalents_per_second'], 2), flush=True)
            if code:
                raise RuntimeError(f'{name} failed; see its log')
    finally:
        if child is not None and child.poll() is None:
            try:
                child.kill()
                child.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
        try:
            if original:
                os.pwrite(fd, original, 0)
                os.fsync(fd)
                summary['framebuffer_restored'] = hashlib.sha256(os.pread(fd, 108800, 0)).digest() == hashlib.sha256(original).digest()
        except OSError as error:
            summary['restore_error'] = str(error)
        finally:
            os.close(fd)
            if paused:
                try:
                    os.kill(pid, signal.SIGCONT)
                except ProcessLookupError:
                    summary['launcher_restore_error'] = 'Original APPLaunch process no longer exists'
        summary['launcher_after'] = launcher_state()
        after = summary['launcher_after']
        summary['launcher_preserved'] = all(before[k] == after[k] for k in ('MainPID', 'NRestarts', 'ActiveState'))
        try:
            summary['launcher_resumed'] = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()[0] not in ('T', 't')
        except OSError:
            summary['launcher_resumed'] = False
        (ROOT / 'device-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    if not all(summary.get(k) for k in ('framebuffer_restored', 'launcher_preserved', 'launcher_resumed')):
        raise RuntimeError('Device restoration check failed; inspect device-summary.json')

if __name__ == '__main__':
    main()
