#!/usr/bin/env python3
"""Generate synthetic video compatibility fixtures or an FFmpeg conversion preset.

Requires a computer with FFmpeg. Never downloads media or overwrites originals.
"""
import argparse
import math
from pathlib import Path
import shutil
import subprocess


def ffmpeg(args):
    destination = Path(args[-1])
    existed = destination.exists() or destination.is_symlink()
    try:
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                        '-n', *args], check=True)
    except BaseException:
        # A failed encode must not look like a completed fixture on the next
        # run. Preserve any file that already existed before this invocation.
        if not existed:
            destination.unlink(missing_ok=True)
        raise


def conversion_filter(width, height):
    # Include sample aspect ratio before producing square pixels. Combining
    # force_original_aspect_ratio with a later setsar=1 distorts anamorphic
    # sources. Expressions also support FFmpeg versions before reset_sar.
    return (f"scale=w='max(2,trunc(min({width},{height}*dar)/2)*2)':"
            f"h='max(2,trunc(min({height},{width}/dar)/2)*2)',setsar=1")


def generate(destination, duration, baseline_only=False, size='640x360'):
    destination.mkdir(parents=True, exist_ok=True)
    subtitles = destination / 'captions.srt'
    if not subtitles.exists():
        subtitles.write_text('1\n00:00:00,200 --> 00:00:01,500\nTyrell Video: café\n'
                             'Two lines of captions\n\n2\n00:00:01,600 --> 00:00:03,000\n'
                             'Seeking clears old captions.\n', encoding='utf-8')
    # Short sources keep codec probes cheap. The baseline can be generated at
    # 1800 seconds for a real soak rather than looping a decoder reset.
    source = ['-f', 'lavfi', '-i', f'testsrc2=size={size}:rate=24',
              '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
              '-t', str(duration), '-pix_fmt', 'yuv420p']
    fixtures = [
        ('baseline-h264-aac.mp4', ['-c:v', 'libx264', '-preset', 'ultrafast',
                                  '-crf', '25', '-c:a', 'aac', '-movflags', '+faststart']),
        ('mpeg4-mp3.avi', ['-c:v', 'mpeg4', '-q:v', '5', '-c:a', 'libmp3lame']),
        ('vp8-vorbis.webm', ['-c:v', 'libvpx', '-deadline', 'realtime',
                             '-cpu-used', '8', '-b:v', '400k', '-c:a', 'libvorbis']),
        ('vp9-opus.webm', ['-c:v', 'libvpx-vp9', '-deadline', 'realtime',
                           '-cpu-used', '8', '-b:v', '400k', '-c:a', 'libopus']),
        ('hevc-aac.mkv', ['-c:v', 'libx265', '-preset', 'ultrafast',
                          '-x265-params', 'log-level=error:pools=2', '-c:a', 'aac']),
        ('av1-opus.mkv', ['-c:v', 'libaom-av1', '-cpu-used', '8', '-threads', '2',
                          '-crf', '45', '-b:v', '0', '-c:a', 'libopus']),
    ]
    if baseline_only:
        fixtures = fixtures[:1]
    encoders = subprocess.check_output(['ffmpeg', '-hide_banner', '-encoders'],
                                      text=True, stderr=subprocess.DEVNULL)
    for name, options in fixtures:
        path = destination / name
        if path.exists():
            continue
        if options[1] not in encoders:
            print('SKIP unavailable encoder: ' + options[1])
            continue
        ffmpeg([*source, *options, str(path)])
        print('Generated ' + str(path))
    baseline = destination / 'baseline-h264-aac.mp4'
    if baseline.exists():
        if not (destination / 'baseline-h264-aac.srt').exists():
            shutil.copyfile(subtitles, destination / 'baseline-h264-aac.srt')
        if baseline_only:
            return
        multi = destination / 'multi-track-subtitles.mkv'
        if not multi.exists():
            ffmpeg(['-i', str(baseline), '-i', str(subtitles), '-map', '0:v',
                    '-map', '0:a', '-map', '0:a', '-map', '1:0', '-c', 'copy',
                    '-c:s', 'srt', '-metadata:s:a:0', 'language=eng',
                    '-metadata:s:a:1', 'language=fra', str(multi)])
        silent = destination / 'silent-h264.mp4'
        if not silent.exists():
            ffmpeg(['-i', str(baseline), '-map', '0:v', '-c', 'copy', str(silent)])
    corrupt = destination / 'corrupt.mp4'
    if not corrupt.exists():
        corrupt.write_bytes(b'Tyrell intentional invalid media fixture\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    fixtures = sub.add_parser('generate')
    fixtures.add_argument('directory', type=Path)
    fixtures.add_argument('--duration', type=float, default=4)
    fixtures.add_argument('--baseline-only', action='store_true')
    fixtures.add_argument('--size', choices=['320x170', '640x360', '1280x720', '1920x1080'], default='640x360')
    convert = sub.add_parser('convert')
    convert.add_argument('source', type=Path)
    convert.add_argument('destination', type=Path)
    convert.add_argument('--profile', choices=['handheld', 'hdmi'], default='handheld')
    args = parser.parse_args()
    if not shutil.which('ffmpeg'):
        parser.error('FFmpeg is required on the computer running this tool')
    if args.action == 'generate':
        if not math.isfinite(args.duration) or args.duration <= 0:
            parser.error('duration must be positive and finite')
        generate(args.directory, args.duration, args.baseline_only, args.size)
    else:
        width, height = (320, 170) if args.profile == 'handheld' else (640, 360)
        ffmpeg(['-i', str(args.source), '-map', '0:v:0', '-map', '0:a:0?',
                '-vf', conversion_filter(width, height),
                '-r', '24', '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ac', '2', '-b:a', '96k',
                '-movflags', '+faststart', str(args.destination)])


if __name__ == '__main__':
    main()
