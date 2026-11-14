#!/usr/bin/env python3
"""Run on CardputerZero after opening Tyrell Tanks through stock APPLaunch.

Injects ordinary key events, captures the actual LCD, and exits through the game
menu. It never launches an application, signals a process, or changes services.
The caller is responsible for installing and selecting the launcher entry.
"""
import argparse
import ctypes
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time


class Bitfield(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in ('offset', 'length', 'msb_right')]


class VarInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        'xres', 'yres', 'xres_virtual', 'yres_virtual', 'xoffset', 'yoffset',
        'bits_per_pixel', 'grayscale')]
    _fields_ += [(name, Bitfield) for name in ('red', 'green', 'blue', 'transp')]
    _fields_ += [(name, ctypes.c_uint32) for name in (
        'nonstd', 'activate', 'height', 'width', 'accel_flags', 'pixclock',
        'left_margin', 'right_margin', 'upper_margin', 'lower_margin',
        'hsync_len', 'vsync_len', 'sync', 'vmode', 'rotate', 'colorspace')]
    _fields_ += [('reserved', ctypes.c_uint32 * 4)]


class FixInfo(ctypes.Structure):
    _fields_ = [('id', ctypes.c_char * 16), ('smem_start', ctypes.c_ulong),
                ('smem_len', ctypes.c_uint32), ('type', ctypes.c_uint32),
                ('type_aux', ctypes.c_uint32), ('visual', ctypes.c_uint32),
                ('xpanstep', ctypes.c_uint16), ('ypanstep', ctypes.c_uint16),
                ('ywrapstep', ctypes.c_uint16), ('line_length', ctypes.c_uint32),
                ('mmio_start', ctypes.c_ulong), ('mmio_len', ctypes.c_uint32),
                ('accel', ctypes.c_uint32), ('capabilities', ctypes.c_uint16),
                ('reserved', ctypes.c_uint16 * 2)]


def query(fd, request, cls):
    buffer = bytearray(ctypes.sizeof(cls))
    fcntl.ioctl(fd, request, buffer, True)
    return cls.from_buffer_copy(buffer)


def capture(device, destination):
    fd = os.open(device, os.O_RDONLY | os.O_CLOEXEC)
    try:
        var = query(fd, 0x4600, VarInfo)
        fix = query(fd, 0x4602, FixInfo)
        fields = [(getattr(var, c).offset, getattr(var, c).length,
                   getattr(var, c).msb_right) for c in ('red', 'green', 'blue')]
        if (var.xres, var.yres, var.bits_per_pixel) != (320, 170, 16):
            raise RuntimeError('Expected native 320x170 RGB565 LCD')
        if fields != [(11, 5, 0), (5, 6, 0), (0, 5, 0)] or fix.type != 0 or fix.visual != 2:
            raise RuntimeError('Framebuffer is not packed RGB565 true color')
        if var.grayscale or var.nonstd or var.transp.length:
            raise RuntimeError('Unsupported nonstandard framebuffer format')
        if (var.xoffset + var.xres > var.xres_virtual or
                var.yoffset + var.yres > var.yres_virtual or
                (var.xoffset + var.xres) * 2 > fix.line_length):
            raise RuntimeError('Framebuffer visible region exceeds virtual geometry')
        first = var.yoffset * fix.line_length + var.xoffset * 2
        end = first + (var.yres - 1) * fix.line_length + var.xres * 2
        if end > fix.smem_len:
            raise RuntimeError('Framebuffer visible region exceeds mapped memory')
        rgb = bytearray()
        for y in range(var.yres):
            raw = os.pread(fd, var.xres * 2, first + y * fix.line_length)
            if len(raw) != var.xres * 2:
                raise RuntimeError('Short LCD read')
            for offset in range(0, len(raw), 2):
                pixel = int.from_bytes(raw[offset:offset + 2], sys.byteorder)
                rgb.extend((((pixel >> 11) & 31) * 255 // 31,
                            ((pixel >> 5) & 63) * 255 // 63,
                            (pixel & 31) * 255 // 31))
        destination.write_bytes(b'P6\n320 170\n255\n' + rgb)
        return {'path': str(destination), 'sha256': hashlib.sha256(rgb).hexdigest(),
                'id': bytes(fix.id).decode(errors='replace'), 'stride': fix.line_length,
                'width': var.xres, 'height': var.yres, 'bits_per_pixel': var.bits_per_pixel}
    finally:
        os.close(fd)


KEYS = {'esc': 1, '1': 2, '2': 3, '3': 4, '4': 5, '5': 6, '6': 7,
        '7': 8, '8': 9, '9': 10, '0': 11, 'backspace': 14, 'tab': 15,
        'q': 16, 'w': 17, 'e': 18, 'enter': 28, 'a': 30, 's': 31,
        'd': 32, '.': 52, 'space': 57}


class Keys:
    def __init__(self, device):
        self.fd = os.open(device, os.O_WRONLY | os.O_CLOEXEC)
        self.pressed = set()
        self.events = []
        # The running Linux ABI determines the timeval fields. On aarch64 this
        # is the same 24-byte input_event used by the existing device harness.
        self.event = struct.Struct('@llHHi')

    def send(self, name, down):
        code = KEYS[name]
        os.write(self.fd, self.event.pack(0, 0, 1, code, int(down)))
        os.write(self.fd, self.event.pack(0, 0, 0, 0, 0))
        if down:
            self.pressed.add(name)
        else:
            self.pressed.discard(name)
        self.events.append({'key': name, 'pressed': down, 'time': time.monotonic()})

    def tap(self, name, seconds=0.08):
        self.hold((name,), seconds)
        time.sleep(0.12)

    def hold(self, names, seconds):
        try:
            for name in names:
                self.send(name, True)
            time.sleep(seconds)
        finally:
            for name in reversed(names):
                self.send(name, False)

    def close(self):
        errors = []
        try:
            for name in list(self.pressed):
                try:
                    self.send(name, False)
                except OSError as error:
                    errors.append(str(error))
        finally:
            os.close(self.fd)
        return errors


def launcher():
    result = subprocess.check_output(
        ['systemctl', '--user', 'show', 'APPLaunch.service', '-p', 'MainPID',
         '-p', 'ActiveState', '-p', 'SubState', '-p', 'NRestarts',
         '-p', 'ExecMainStartTimestampMonotonic'], text=True, timeout=4)
    values = dict(line.split('=', 1) for line in result.splitlines() if '=' in line)
    pid = int(values.get('MainPID', 0))
    if pid <= 0:
        raise RuntimeError('Original APPLaunch user service is not running')
    values['process_state'] = process(pid)['state']
    return values


def game_pids():
    result = subprocess.run(['pgrep', '-x', 'tyrell-tanks'], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
    if result.returncode not in (0, 1):
        raise RuntimeError('Cannot inspect game process: ' + result.stderr.strip())
    return [int(value) for value in result.stdout.split()]


def process(pid):
    root = Path('/proc') / str(pid)
    values = dict(line.split(':', 1) for line in (root / 'status').read_text().splitlines() if ':' in line)
    result = {'pid': pid, 'ppid': int(values['PPid']), 'state': values['State'].strip(),
              'rss': values.get('VmRSS', '').strip(), 'peak_rss': values.get('VmHWM', '').strip(),
              'executable': os.readlink(root / 'exe')}
    devices = []
    for entry in (root / 'fd').iterdir():
        try:
            target = os.readlink(entry)
            if target.startswith(('/dev/fb', '/dev/input/')):
                devices.append(target)
        except FileNotFoundError:
            pass
    result['device_fds'] = sorted(devices)
    return result


def ancestors(pid, stop_at):
    chain = []
    for _ in range(12):
        if pid <= 1:
            break
        item = process(pid)
        chain.append({'pid': item['pid'], 'ppid': item['ppid'], 'executable': item['executable']})
        if pid == stop_at:
            break
        pid = item['ppid']
    return chain


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='SD-backed evidence directory')
    parser.add_argument('--input', default='/dev/input/cardputer-zero-internal')
    parser.add_argument('--framebuffer', default='/dev/fb_lcd')
    parser.add_argument('--mode', choices=('practice', 'join', 'host'), default='practice')
    parser.add_argument('--host', help='IPv4 host for join mode, typed through the actual game menu')
    args = parser.parse_args()
    if args.mode == 'join':
        if not args.host:
            parser.error('--mode join requires --host')
        args.host = str(ipaddress.IPv4Address(args.host))
    args.output.mkdir(parents=True, exist_ok=True)
    result = {'synthetic_evdev_input': True, 'physical_keyboard_switches_tested': False,
              'mode': args.mode, 'host': args.host, 'captures': {}, 'passed': False}
    keys = None
    try:
        before = launcher()
        result['launcher_before'] = before
        pids = game_pids()
        if len(pids) != 1:
            raise RuntimeError('Open exactly one Tyrell Tanks instance through APPLaunch before running this test')
        pid = pids[0]
        result['game_before'] = process(pid)
        result['game_ancestors'] = ancestors(pid, int(before['MainPID']))
        result['launcher_is_ancestor'] = int(before['MainPID']) in [p['pid'] for p in result['game_ancestors']]
        if not result['launcher_is_ancestor']:
            raise RuntimeError('Game is not a descendant of the stock APPLaunch process; native handoff is unproven')

        def shot(name):
            result['captures'][name] = capture(args.framebuffer, args.output / (name + '.ppm'))

        shot('game-menu')
        keys = Keys(args.input)
        keys.tap({'practice': '1', 'host': '2', 'join': '3'}[args.mode])
        keys.tap('enter')
        if args.mode == 'join':
            # The address field starts with localhost. Clear its complete bounded
            # contents before entering a validated numeric IPv4 address.
            for _ in range(64):
                keys.tap('backspace', 0.005)
            for character in args.host:
                keys.tap(character, 0.035)
            keys.tap('enter')
            time.sleep(1.0)
        else:
            time.sleep(0.5)
        shot('playing-before-input')
        keys.hold(('w', 'd'), 1.0)
        keys.hold(('e',), 0.6)
        keys.hold(('space',), 0.8)
        shot('playing-after-input')
        keys.send('tab', True)
        time.sleep(0.2)
        shot('scoreboard')
        keys.send('tab', False)
        result['game_after_input'] = process(pid)
        keys.tap('esc')
        time.sleep(0.4)
        shot('game-menu-after-escape')
        keys.tap('esc')
        deadline = time.monotonic() + 5
        while pid in game_pids() and time.monotonic() < deadline:
            time.sleep(0.1)
        if pid in game_pids():
            raise RuntimeError('Game did not exit through Escape from its menu; process left running for inspection')
        time.sleep(0.5)
        shot('launcher-restored')
        after = launcher()
        result['launcher_after'] = after
        for field in ('MainPID', 'NRestarts', 'ExecMainStartTimestampMonotonic'):
            if after.get(field) != before.get(field):
                raise RuntimeError('Original launcher changed: ' + field)
        if after['ActiveState'] != 'active' or after['SubState'] != 'running' or after['process_state'].startswith(('T', 't', 'Z')):
            raise RuntimeError('Original launcher is not active and runnable after game exit')
        result['passed'] = True
    except BaseException as error:
        result['error'] = str(error) or type(error).__name__
    finally:
        if keys:
            errors = keys.close()
            if errors:
                result['passed'] = False
                result['key_release_errors'] = errors
            result['key_events'] = keys.events
        (args.output / 'acceptance.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Acceptance test interrupted; held keys will be released')
    signal.signal(signal.SIGTERM, interrupted)
    raise SystemExit(main())
