#!/usr/bin/env python3
"""Read-only CardputerZero video inventory; run on the target, without sudo.

Prints JSON. Does not play audio, acquire DRM master, stop services, or change
configuration. Command failures are evidence, not reasons to install software.
"""
import datetime
import glob
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess


def command(args):
    if not shutil.which(args[0]):
        return {"available": False}
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=12)
        return {"available": True, "returncode": p.returncode,
                "stdout": p.stdout, "stderr": p.stderr}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": True, "error": str(error)}


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(64 * 1024), b''):
            checksum.update(data)
    return checksum.hexdigest()


def inventory():
    connectors = []
    for entry in sorted(Path('/sys/class/drm').glob('card*-*')):
        if not (entry / 'status').exists():
            continue
        connectors.append({"name": entry.name,
                           "status": read(entry / 'status'),
                           "enabled": read(entry / 'enabled'),
                           "modes": (read(entry / 'modes') or '').splitlines()})
    devices = []
    for path in sorted(set(glob.glob('/dev/dri/*') + glob.glob('/dev/video*') +
                           glob.glob('/dev/snd/pcm*') + ['/dev/fb_lcd',
                           '/dev/input/cardputer-zero-internal'])):
        p = Path(path)
        if p.exists():
            devices.append({"path": path, "target": str(p.resolve()),
                            "readable": os.access(p, os.R_OK),
                            "writable": os.access(p, os.W_OK)})
    protected = ['/boot/firmware/config.txt', '/boot/firmware/cmdline.txt',
                 '/usr/share/APPLaunch/bin/M5CardputerZero-APPLaunch',
                 '/usr/lib/systemd/user/APPLaunch.service']
    hashes = {}
    for path in protected:
        try:
            hashes[path] = digest(path)
        except OSError:
            hashes[path] = None
    checks = {
        "gstreamer_version": ['gst-inspect-1.0', '--version'],
        "gstreamer_registry": ['gst-inspect-1.0'],
        "ffmpeg_decoders": ['ffmpeg', '-hide_banner', '-decoders'],
        "pulse_sinks": ['pactl', '--format=json', 'list', 'sinks'],
        "alsa_cards": ['aplay', '-l'],
        "display_services": ['systemctl', 'show', 'factory_precheck.service',
                             'display-manager.service', '-p', 'Id', '-p',
                             'ActiveState', '-p', 'SubState', '-p', 'MainPID'],
        "launcher": ['systemctl', '--user', 'show', 'APPLaunch.service', '-p',
                     'ActiveState', '-p', 'MainPID', '-p', 'NRestarts'],
        "packages": ['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\n',
                     'gstreamer1.0-*', 'libgstreamer*', 'libavcodec*',
                     'pipewire*', 'factorytest', 'applaunch'],
    }
    return {"timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "architecture": platform.machine(), "kernel": platform.release(),
            "memory": read('/proc/meminfo'), "connectors": connectors,
            "devices": devices, "video_devices": {
                p.name: read(p / 'name') for p in Path('/sys/class/video4linux').glob('video*')},
            "protected_sha256": hashes,
            "checks": {name: command(args) for name, args in checks.items()},
            "limitations": ["Inventory does not prove decoding or display ownership.",
                            "No physical sound, display or key checks are performed."]}


if __name__ == '__main__':
    print(json.dumps(inventory(), indent=2))
