#!/usr/bin/env python3
"""Exercise generated fixtures with the uninstalled playback probe. Prints JSON.

Measures decoder delivery, not physical display smoothness or audible sound.
"""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    parser.add_argument('fixtures', type=Path)
    parser.add_argument('--fake-audio', action='store_true')
    parser.add_argument('--seconds', type=float, default=5)
    parser.add_argument('--output', type=Path, help='Save incremental evidence on the test machine')
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0 or args.seconds > 86400:
        parser.error('seconds must be positive, finite and at most 86400')
    if not args.fixtures.is_dir():
        parser.error('fixtures must be an existing directory')
    if not args.probe.is_file() or not os.access(args.probe, os.X_OK):
        parser.error('probe must be an existing executable file')
    if args.output:
        try:
            # Reserve a new report; incremental saves below only update the
            # report created by this run, never previous validation evidence.
            with args.output.open('x') as stream:
                stream.write('{"complete":false,"results":[]}\n')
        except FileExistsError:
            parser.error('output already exists; select a new evidence path')
    results = []
    def save(complete=False):
        report = {'measurement': 'decoder delivery only; no physical display or audio verification',
                  'complete': complete, 'results': results}
        if args.output:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', dir=args.output.parent,
                                                 prefix=args.output.name + '.', suffix='.tmp',
                                                 delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(json.dumps(report, indent=2) + '\n')
                temporary.replace(args.output)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return report
    save()
    for file in sorted(args.fixtures.iterdir()):
        if file.suffix.lower() not in {'.mp4', '.mkv', '.webm', '.avi'}:
            continue
        cmd = [str(args.probe.resolve()), str(file.resolve()), '--seconds', str(args.seconds)]
        if args.fake_audio:
            cmd.append('--fake-audio')
        if file.name == 'multi-track-subtitles.mkv':
            cmd.append('--exercise')
        matching = file.with_suffix('.srt')
        if matching.exists():
            cmd.extend(['--subtitle', str(matching.resolve())])
        print('Testing ' + file.name, file=sys.stderr, flush=True)
        try:
            process = subprocess.run(cmd, capture_output=True, text=True, timeout=args.seconds + 20)
            metrics = None
            for line in reversed(process.stdout.splitlines()):
                try:
                    candidate = json.loads(line)
                    if isinstance(candidate, dict) and isinstance(candidate.get('ok'), bool):
                        metrics = candidate
                        break
                except ValueError:
                    continue
            expected_failure = file.name == 'corrupt.mp4'
            results.append({'file': file.name, 'returncode': process.returncode,
                            'expected_failure': expected_failure,
                            'passed': (process.returncode == 1 and bool(metrics) and metrics.get('ok') is False
                                       if expected_failure else
                                       process.returncode == 0 and bool(metrics and metrics.get('ok'))),
                            'metrics': metrics, 'stderr': process.stderr[-4000:]})
        except subprocess.TimeoutExpired:
            results.append({'file': file.name, 'passed': False, 'error': 'Probe timed out'})
        save()
    print(json.dumps(save(True), indent=2))
    return 0 if results and all(row['passed'] for row in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
