"""Direct, normal-user launcher supervisor inside greetd's PAM session."""
from collections import deque
import ctypes
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import termios
import time
import tty

import state

RECOVERY = "/usr/libexec/tyrell-zero-session-recovery"
KDGETMODE, KDSETMODE, KD_GRAPHICS = 0x4B3B, 0x4B3A, 1


def validate_logind(properties, uid):
    expected = {"User": str(uid), "Class": "user", "Seat": "seat0",
                "VTNr": "7", "Active": "yes", "Remote": "no"}
    if uid != 1000 or any(properties.get(k) != v for k, v in expected.items()):
        raise state.StateError("local-session-required")


def validate_boot(boot, uid, identity):
    account = boot.get("account")
    if boot.get("mode") != "native-ready" or not isinstance(account, dict) or type(account.get("uid")) is not int or account["uid"] != uid or boot.get("integration_sha256") != identity:
        raise state.StateError("native-boot-required")


def logind_session(uid):
    identifier = os.environ.get("XDG_SESSION_ID", "")
    if not identifier or len(identifier) > 64 or not all(c.isalnum() or c in "_-" for c in identifier):
        raise state.StateError("local-session-required")
    result = subprocess.run(["/usr/bin/loginctl", "show-session", identifier,
                             "-p", "User", "-p", "Class", "-p", "Seat", "-p", "VTNr",
                             "-p", "Active", "-p", "Remote"], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5)
    if result.returncode:
        raise state.StateError("local-session-required")
    properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    validate_logind(properties, uid)


def runtime_environment(uid):
    runtime = Path("/run/user") / str(uid)
    if os.environ.get("XDG_RUNTIME_DIR") != str(runtime):
        raise state.StateError("runtime-directory-mismatch")
    info = runtime.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
        raise state.StateError("runtime-directory-untrusted")
    env = os.environ.copy()
    env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + str(runtime / "bus")
    env["XDG_SESSION_TYPE"] = "tty"
    env["XDG_CURRENT_DESKTOP"] = "TyrellZero"
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    return runtime, env


def wait_for_audio(runtime, uid, deadline=30):
    expires = time.monotonic() + deadline
    paths = [runtime / "bus", runtime / "pulse/native"]
    while time.monotonic() < expires:
        try:
            values = [p.lstat() for p in paths]
            if all(stat.S_ISSOCK(v.st_mode) and v.st_uid == uid for v in values):
                return
        except FileNotFoundError:
            pass
        time.sleep(0.25)
    raise state.StateError("audio-bus-not-ready")


class RestartBudget:
    def __init__(self, maximum=3, interval=60):
        self.maximum, self.interval = maximum, interval
        self.starts = deque()

    def allow(self, now):
        while self.starts and now - self.starts[0] >= self.interval:
            self.starts.popleft()
        if len(self.starts) >= self.maximum:
            return False
        self.starts.append(now)
        return True


class Terminal:
    def __enter__(self):
        import array
        if not os.isatty(0) or os.ttyname(0) != "/dev/tty7":
            raise state.StateError("vt7-required")
        self.saved = termios.tcgetattr(0)
        mode = array.array("i", [0])
        fcntl.ioctl(0, KDGETMODE, mode, True)
        self.kd = mode[0]
        self.foreground_group = os.tcgetpgrp(0)
        try:
            tty.setraw(0, termios.TCSANOW)
            fcntl.ioctl(0, KDSETMODE, KD_GRAPHICS)
        except BaseException:
            try:
                fcntl.ioctl(0, KDSETMODE, self.kd)
            finally:
                termios.tcsetattr(0, termios.TCSANOW, self.saved)
            raise
        return self

    def foreground(self, group):
        previous = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        try:
            os.tcsetpgrp(0, group)
        finally:
            signal.signal(signal.SIGTTOU, previous)

    def __exit__(self, *_):
        try:
            self.foreground(self.foreground_group)
            fcntl.ioctl(0, KDSETMODE, self.kd)
        finally:
            termios.tcsetattr(0, termios.TCSANOW, self.saved)


class StopSession(Exception):
    pass


def stop_process(child, grace=1):
    if child is None:
        return
    group = child.pid
    if group == os.getpgrp():
        raise state.StateError("refusing-parent-process-group")
    try:
        # A reaped leader can leave live descendants in its owned group.
        os.killpg(group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    expires = time.monotonic() + grace
    while time.monotonic() < expires:
        child.poll()
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    try:
        os.killpg(group, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=3)


def enable_subreaper():
    """Adopt app descendants even when the vendor gives them another PGID.

    Linux PR_SET_CHILD_SUBREAPER is process-local and preserves the existing
    PAM/POSIX session. pidfds bind signals to identities rather than reused PIDs.
    """
    if sys.platform != "linux" or not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise state.StateError("descendant-tracking-unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise state.StateError("descendant-tracking-unavailable")
    probe = os.pidfd_open(os.getpid())
    try:
        signal.pidfd_send_signal(probe, 0)
    finally:
        os.close(probe)
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    adopted_children()  # Prove procfs is available before starting the launcher.


def adopted_children():
    path = Path("/proc/self/task") / str(os.getpid()) / "children"
    try:
        return [int(value) for value in path.read_text().split()]
    except FileNotFoundError:
        # The optional proc children file need not be compiled in the retained
        # kernel. Standard proc stat still identifies our direct children.
        Path("/proc/self/stat").read_text()
        children = []
        for process in Path("/proc").glob("[0-9]*"):
            try:
                text = (process / "stat").read_text()
                fields = text[text.rindex(")") + 2:].split()
                if int(fields[1]) == os.getpid():
                    children.append(int(process.name))
            except (OSError, ValueError, IndexError):
                pass
        return children


def stop_adopted_children(grace=1):
    """Bounded cleanup of this supervisor's children, not other user processes."""
    for sig in [signal.SIGTERM, signal.SIGKILL]:
        deadline = time.monotonic() + grace
        signaled = set()
        while time.monotonic() < deadline:
            alive = []
            for pid in adopted_children():
                try:
                    reaped, _status = os.waitpid(pid, os.WNOHANG)
                    if reaped:
                        continue
                    alive.append(pid)
                    if pid not in signaled:
                        fd = os.pidfd_open(pid)
                        try:
                            signal.pidfd_send_signal(fd, sig)
                        finally:
                            os.close(fd)
                        signaled.add(pid)
                except (ChildProcessError, ProcessLookupError):
                    continue
            if not alive:
                return
            time.sleep(0.02)
    # SIGKILL recipients may need a final scheduling turn before waitpid.
    for pid in adopted_children():
        try:
            if os.waitpid(pid, os.WNOHANG)[0] == 0:
                raise state.StateError("descendant-cleanup-incomplete")
        except ChildProcessError:
            pass


def stop_application(child):
    stop_process(child)
    stop_adopted_children()


def foreground_child(terminal, child):
    try:
        terminal.foreground(child.pid)
        # Exec can touch its tty before the parent transfers foreground and
        # stop on SIGTTIN/SIGTTOU. Only this newly owned group is resumed.
        os.killpg(child.pid, signal.SIGCONT)
    except ProcessLookupError:
        pass  # A very short-lived child may already have exited.


def supervise(launcher, env):
    def stop(_signum, _frame):
        raise StopSession()
    for sig in [signal.SIGTERM, signal.SIGHUP, signal.SIGINT]:
        signal.signal(sig, stop)
    child = None
    budget = RestartBudget()
    with Terminal() as terminal:
        try:
            while budget.allow(time.monotonic()):
                child = subprocess.Popen([launcher], cwd="/usr/share/APPLaunch", env=env,
                                         process_group=0)
                foreground_child(terminal, child)
                code = child.wait()
                stop_application(child)
                child = None
                terminal.foreground(terminal.foreground_group)
                if code == 0:
                    return 0  # Deliberate exit: let greetd close PAM normally.
                time.sleep(1)
            # Recovery stays in this existing PAM session and raw/graphics VT.
            child = subprocess.Popen([RECOVERY], env=env, process_group=0)
            foreground_child(terminal, child)
            return child.wait()
        except StopSession:
            return 0
        finally:
            # Terminate the owned group before restoring text/echo state.
            for sig in [signal.SIGTERM, signal.SIGHUP, signal.SIGINT]:
                signal.signal(sig, signal.SIG_IGN)
            stop_application(child)


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv == ["--version"]:
        print(state.VERSION)
        return 0
    if argv:
        print("No session command arguments are accepted", file=sys.stderr)
        return 64
    lock_fd = None
    try:
        uid = os.getuid()
        if uid != 1000 or os.geteuid() != uid:
            raise state.StateError("normal-user-required")
        files = state.Files()
        identity = state.integration(files)
        boot = files.json(state.RUNTIME + "/boot-state.json")
        validate_boot(boot, uid, identity)
        logind_session(uid)
        runtime, env = runtime_environment(uid)
        lock_fd = os.open(runtime / "tyrell-zero-session.lock",
                          os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        info = os.fstat(lock_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
            raise state.StateError("session-lock-untrusted")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        wait_for_audio(runtime, uid)
        enable_subreaper()
        return supervise(state.ASSETS["launcher"], env)
    except (state.StateError, OSError, subprocess.SubprocessError) as error:
        code = str(error) if isinstance(error, state.StateError) else "session-start-failed"
        print("Tyrell Zero: " + code, file=sys.stderr)
        # No shell/root fallback and no new auth/session. greetd's default fixed
        # recovery handles full session failures.
        return 78
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


if __name__ == "__main__":
    raise SystemExit(main())
