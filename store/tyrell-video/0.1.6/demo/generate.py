#!/usr/bin/env python3
"""Create a synthetic local-playback fixture without downloading or replacing media.

Run on a computer with FFmpeg/ffprobe, never on the Cardputer Zero.
This creates demonstration content, not the Store's real-device phone recording.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


NAME = 'TYRELL-DEMO'
CAPTIONS = [
    ('00:00:00,250', '00:00:06,000', 'Tyrell Video synthetic demo'),
    ('00:00:06,000', '00:00:12,000', 'Local files, keyboard controls'),
    ('00:00:12,000', '00:00:18,000', 'Captions keep playback time'),
    ('00:00:18,000', '00:00:24,000', 'Pause and resume at this point'),
    ('00:00:24,000', '00:00:30,000', 'Left / Right: seek 10 seconds'),
    ('00:00:30,000', '00:00:36,000', 'O: select LCD or HDMI'),
    ('00:00:36,000', '00:00:42,000', 'T: choose captions or audio'),
    ('00:00:42,000', '00:00:45,000', 'End of synthetic test clip'),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent,
                        help='Directory for new files; existing outputs are never replaced')
    args = parser.parse_args()
    for tool in ('ffmpeg', 'ffprobe'):
        if not shutil.which(tool):
            parser.error(tool + ' is required on the computer')
    args.output.mkdir(parents=True, exist_ok=True)
    media = args.output / (NAME + '.mp4')
    captions = args.output / (NAME + '.srt')
    poster = args.output / (NAME + '-poster.png')
    manifest = args.output / 'manifest.json'
    outputs = (media, captions, poster, manifest)
    for path in outputs:
        if path.exists() or path.is_symlink():
            parser.error('Refusing to replace existing output: ' + str(path))
    # Encode privately, then publish with hard links (atomic no-replace). A
    # failed encode or concurrent destination writer cannot erase originals.
    working = tempfile.TemporaryDirectory(prefix='.generate-', dir=args.output)
    work = Path(working.name)
    media, captions, poster, manifest = (work / path.name for path in outputs)
    published = []

    # Every second starts with two white frames and an 80 ms, -20 dBFS beep.
    # testsrc2 supplies original geometric motion; no film or user media is read.
    encode = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
        '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24:duration=45',
        '-f', 'lavfi', '-i',
        "aevalsrc='if(lt(mod(t,1),0.08),0.10*sin(2*PI*880*t),0)':s=48000:d=45",
        '-map', '0:v:0', '-map', '1:a:0',
        '-vf', "drawbox=color=white:t=fill:enable='lt(mod(t,1),0.08)',setsar=1",
        '-t', '45', '-c:v', 'libx264', '-preset', 'medium',
        '-profile:v', 'baseline', '-level:v', '3.0', '-pix_fmt', 'yuv420p',
        '-b:v', '400k', '-maxrate', '500k', '-bufsize', '1000k', '-g', '48',
        '-c:a', 'aac', '-ac', '1', '-b:a', '48k', '-movflags', '+faststart',
        '-metadata', 'title=Tyrell Video synthetic Store demonstration fixture',
        '-metadata', 'comment=Generated geometric pattern and quiet beeps; no film or user media',
        media.name,
    ]
    extract = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
        '-ss', '1.5', '-i', media.name, '-frames:v', '1', '-update', '1', poster.name,
    ]
    decode = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
              '-i', media.name, '-f', 'null', '-']
    try:
        subprocess.run(encode, check=True, cwd=work)
        if media.stat().st_size >= 4 * 1024 * 1024:
            raise RuntimeError('Generated fixture exceeds the 4 MiB size budget')
        with captions.open('x', encoding='utf-8') as out:
            out.write('\n\n'.join(f'{number}\n{start} --> {end}\n{text}'
                                 for number, (start, end, text) in enumerate(CAPTIONS, 1)) + '\n')
        subprocess.run(extract, check=True, cwd=work)
        subprocess.run(decode, check=True, cwd=work)
        probe_command = ['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                         '-of', 'json', media.name]
        probe = json.loads(subprocess.check_output(probe_command, text=True, cwd=work))
        video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
        audio = next(s for s in probe['streams'] if s['codec_type'] == 'audio')
        assert (video['codec_name'], video['width'], video['height'], video['r_frame_rate']) == (
            'h264', 640, 360, '24/1')
        assert audio['codec_name'] == 'aac' and audio['channels'] == 1
        assert abs(float(probe['format']['duration']) - 45) < .1
        data = {
            'purpose': 'Synthetic input for filming a real-device Store demonstration; not that recording',
            'media_license': 'MIT',
            'generation_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'command_working_directory': 'Private temporary directory; output filenames remain portable',
            'ffmpeg_version': subprocess.check_output(['ffmpeg', '-version'], text=True).splitlines()[0],
            'generation_argv': encode,
            'poster_argv': extract,
            'decode_check_argv': decode,
            'probe_argv': probe_command,
            'probe': probe,
            'files': {p.name: {'bytes': p.stat().st_size,
                                  'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                      for p in (media, captions, poster)},
        }
        with manifest.open('x', encoding='utf-8') as out:
            json.dump(data, out, indent=2)
            out.write('\n')
        for original, target in zip((media, captions, poster, manifest), outputs):
            os.link(original, target)
            published.append((original, target))
    except BaseException:
        # Delete only this invocation's published hard links, preserving any
        # independently replaced file even if a concurrent writer won a race.
        for original, target in published:
            try:
                if original.samefile(target):
                    target.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        working.cleanup()
    print(json.dumps(data['files'], indent=2))


if __name__ == '__main__':
    main()
