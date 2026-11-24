#!/usr/bin/env python3
"""Explicit, bounded, read-only Linux observations; no tuning or acceptance."""

import argparse
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import select
import signal
import stat
import sys
import time


FILE_BYTES = 128 * 1024
READ_BYTES = 2 * 1024 * 1024
STATUS_BYTES = 16 * 1024 * 1024
SNAPSHOT_BYTES = 768 * 1024
OUTPUT_BYTES = 32 * 1024 * 1024
RESULT_BYTES = 1024 * 1024
SNAPSHOT_SECONDS = 2.0
SCHEDULE_GRACE_SECONDS = 0.05
MEMBERS = 16
NAME = re.compile(r"[A-Za-z0-9_.:+-]{1,128}\Z")
PROC = ("/proc/meminfo", "/proc/stat", "/proc/vmstat", "/proc/diskstats",
        "/proc/interrupts", "/proc/pressure/cpu", "/proc/pressure/memory",
        "/proc/pressure/io")
IDENTITY = ("/proc/sys/kernel/osrelease", "/proc/version", "/usr/lib/os-release",
            "/etc/debian_version", "/sys/firmware/devicetree/base/model")
GROUPS = (
    ("/sys/class/backlight", ("brightness", "actual_brightness", "max_brightness", "bl_power", "type")),
    ("/sys/class/power_supply", ("type", "status", "online", "present", "capacity", "voltage_now",
       "current_now", "current_instant", "voltage_instant", "power_now", "power_avg", "energy_now",
       "charge_now", "charge_full", "charge_full_design", "temp")),
    ("/sys/class/thermal", ("type", "temp")),
    ("/sys/class/net", ("operstate", "carrier", "power/control", "power/runtime_status",
       "statistics/rx_bytes", "statistics/tx_bytes", "statistics/rx_packets", "statistics/tx_packets")),
    ("/sys/bus/usb/devices", ("idVendor", "idProduct", "busnum", "devnum", "devpath", "product", "uevent", "power/control",
       "power/runtime_status", "power/autosuspend_delay_ms", "power/runtime_active_time",
       "power/runtime_suspended_time", "power/wakeup")),
    ("/sys/devices/system/cpu/cpufreq", ("scaling_governor", "scaling_min_freq", "scaling_max_freq",
       "scaling_cur_freq", "affected_cpus", "stats/time_in_state", "ondemand/sampling_rate",
       "ondemand/sampling_down_factor", "ondemand/up_threshold", "ondemand/io_is_busy",
       "ondemand/ignore_nice_load", "ondemand/powersave_bias")),
    ("/sys/bus/spi/devices", ("statistics/messages", "statistics/transfers", "statistics/bytes",
       "statistics/bytes_rx", "statistics/bytes_tx", "statistics/errors", "statistics/timedout")),
)
FIXED_SYS = ("/sys/devices/system/cpu/cpufreq/ondemand/sampling_rate",
             "/sys/devices/system/cpu/cpufreq/ondemand/sampling_down_factor",
             "/sys/devices/system/cpu/cpufreq/ondemand/up_threshold",
             "/sys/devices/system/cpu/cpufreq/ondemand/io_is_busy",
             "/sys/devices/system/cpu/cpufreq/ondemand/ignore_nice_load",
             "/sys/devices/system/cpu/cpufreq/ondemand/powersave_bias",
             "/sys/devices/platform/soc/soc:firmware/get_throttled")


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def tool_hashes():
    return {name: sha256(Path(__file__).with_name(name).read_bytes())
            for name in ("collect.py", "records.py") if Path(__file__).with_name(name).is_file()}


class Reader:
    """Resolve sysfs aliases inside devices, then walk no-follow directory FDs."""

    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        self.remaining = READ_BYTES
        self.opened_paths = {}

    def resolve(self, name):
        relative = Path(name.lstrip("/"))
        if not name.startswith("/") or ".." in relative.parts:
            raise ValueError("unsafe path")
        candidate = self.root / relative
        if name.startswith("/sys/"):
            # Linux class and bus entries are aliases. No other filesystem's
            # symlinks are followed, and no sysfs alias may escape devices.
            resolved = candidate.resolve(strict=True)
            if resolved != candidate:
                resolved.relative_to(self.root / "sys/devices")
            relative = resolved.relative_to(self.root)
        return relative

    def open(self, name, directory=False):
        relative = self.resolve(name)
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in relative.parts[:-1]:
                new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = new
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if directory:
                flags |= os.O_DIRECTORY
            info = os.stat(relative.parts[-1], dir_fd=fd, follow_symlinks=False)
            if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
                raise ValueError("not a regular file/directory")
            result = os.open(relative.parts[-1], flags, dir_fd=fd)
        finally:
            os.close(fd)
        self.opened_paths[result] = relative
        return result

    @staticmethod
    def failure(exc):
        if isinstance(exc, (ValueError, RuntimeError)) or getattr(exc, "errno", None) in (errno.ELOOP, errno.ENOTDIR):
            return {"status": "unsafe"}
        if isinstance(exc, FileNotFoundError):
            return {"status": "missing"}
        if isinstance(exc, PermissionError):
            return {"status": "denied"}
        return {"status": "error", "errno": getattr(exc, "errno", None)}

    def read(self, name, limit=FILE_BYTES, *, identity=False):
        if not identity and self.remaining <= 0:
            return {"status": "oversized", "reason": "snapshot read budget"}
        try:
            fd = self.open(name)
            opened_relative = self.opened_paths.pop(fd)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    return {"status": "unsafe", "reason": "not a regular file"}
                allowed = limit if identity else min(limit, self.remaining)
                data = bytearray()
                while len(data) <= allowed:
                    chunk = os.read(fd, min(65536, allowed + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                if not identity:
                    self.remaining -= len(data)
                if len(data) > allowed:
                    return {"status": "oversized", "limit_bytes": allowed}
                result = {"status": "ok", "raw": bytes(data).decode("utf-8", errors="replace")}
                if name.startswith("/sys/"):
                    result["resolved_path"] = "/" + opened_relative.as_posix()
                if identity:
                    result.update(sha256=sha256(data), bytes=len(data))
                return result
            finally:
                os.close(fd)
        except (OSError, ValueError, RuntimeError) as exc:
            return self.failure(exc)

    def members(self, name):
        try:
            fd = self.open(name, directory=True)
            self.opened_paths.pop(fd)
            try:
                names = []
                with os.scandir(fd) as entries:
                    for item in entries:
                        if len(names) >= MEMBERS:
                            return sorted(names), {"status": "oversized", "reason": "enumeration limit", "limit": MEMBERS}
                        if NAME.fullmatch(item.name):
                            names.append(item.name)
                return sorted(names), {"status": "ok", "count": len(names)}
            finally:
                os.close(fd)
        except (OSError, ValueError, RuntimeError) as exc:
            return [], self.failure(exc)


def value(readings, name):
    reading = readings.get(name, {})
    return reading.get("raw") if reading.get("status") == "ok" else None


def process_start(text):
    # comm may contain whitespace and ')'. The last ')' terminates it;
    # starttime is field22, hence index19 after fields1 and2.
    suffix = text.rsplit(")", 1)[-1].split() if text and ")" in text else []
    return int(suffix[19]) if len(suffix) > 19 and suffix[19].isdigit() else None


def metrics(readings, pids):
    result = {"pss_kib": {}, "pss_status": {}, "process_identity": {}}
    mem = value(readings, "/proc/meminfo")
    available = re.findall(r"^MemAvailable:\s+([0-9]+) kB$", mem or "", re.MULTILINE)
    if len(available) == 1:
        result["mem_available_kib"] = int(available[0])
    for pid in pids:
        name = "/proc/" + str(pid) + "/smaps_rollup"
        start = process_start(value(readings, "/proc/" + str(pid) + "/stat"))
        if start is not None:
            result["process_identity"][str(pid)] = {"start_ticks": start}
        pss = re.findall(r"^Pss:\s+([0-9]+) kB$", value(readings, name) or "", re.MULTILINE)
        result["pss_status"][str(pid)] = "ok" if len(pss) == 1 else readings.get(name, {}).get("status", "missing")
        if len(pss) == 1:
            result["pss_kib"][str(pid)] = int(pss[0])
        elif result["pss_status"][str(pid)] == "ok":
            result["pss_status"][str(pid)] = "malformed"
    text = value(readings, "/proc/stat") or ""
    aggregate = re.findall(r"^cpu\s+((?:[0-9]+\s+){3,9}[0-9]+)\s*$", text, re.MULTILINE)
    cpu = {}
    if len(aggregate) == 1:
        ticks = list(map(int, aggregate[0].split()))
        cpu.update(total_ticks=sum(ticks[:8]), idle_ticks=ticks[3] + (ticks[4] if len(ticks) > 4 else 0))
    for key in ("intr", "ctxt"):
        match = re.findall(r"^" + key + r" ([0-9]+)(?: .*|)$", text, re.MULTILINE)
        if len(match) == 1:
            cpu[key] = int(match[0])
    if cpu:
        result["cpu"] = cpu
    vmstat = {}
    for key in ("pgmajfault", "pswpin", "pswpout", "pgpgin", "pgpgout", "oom_kill"):
        match = re.findall(r"^" + key + r" ([0-9]+)$", value(readings, "/proc/vmstat") or "", re.MULTILINE)
        if len(match) == 1:
            vmstat[key] = int(match[0])
    if vmstat:
        result["vmstat"] = vmstat
    return result


def installed_identity(reader, packages):
    reading = reader.read("/var/lib/dpkg/status", STATUS_BYTES, identity=True)
    result = {"dpkg_status": {key: val for key, val in reading.items() if key != "raw"}, "app_packages": {}}
    if reading["status"] != "ok":
        return result
    raw = reading["raw"]
    result["dpkg_status_sha256"] = reading["sha256"]
    wanted = {row["name"] for row in packages}
    for paragraph in raw.split("\n\n"):
        fields = {}
        for line in paragraph.splitlines():
            if not line.startswith((" ", "\t")) and ": " in line:
                key, val = line.split(": ", 1)
                if key in ("Package", "Version", "Status"):
                    fields[key] = val
        if fields.get("Package") in wanted:
            name = fields["Package"]
            if name in result["app_packages"]:
                result["app_packages"][name] = {"status": "ambiguous"}
            else:
                result["app_packages"][name] = {"version": fields.get("Version"), "status": fields.get("Status")}
    for name in wanted - result["app_packages"].keys():
        result["app_packages"][name] = {"status": "missing"}
    return result


def snapshot(root, pids, packages, include_identity):
    start = time.monotonic()
    reader = Reader(root)
    readings = {name: reader.read(name) for name in (*PROC, *FIXED_SYS)}
    for pid in pids:
        for suffix in ("stat", "smaps_rollup"):
            name = "/proc/" + str(pid) + "/" + suffix
            readings[name] = reader.read(name)
        stat_name = "/proc/" + str(pid) + "/stat"
        pss_name = "/proc/" + str(pid) + "/smaps_rollup"
        before = process_start(value(readings, stat_name))
        after_reading = reader.read(stat_name)
        after = process_start(value({stat_name: after_reading}, stat_name))
        if before is None or before != after:
            readings[pss_name] = {"status": "unsafe", "reason": "process identity unavailable or changed during snapshot"}
    for directory, attributes in GROUPS:
        members, status = reader.members(directory)
        readings[directory + "/"] = status
        for member in members:
            if directory.endswith("thermal") and not member.startswith("thermal_zone"):
                continue
            if directory.endswith("cpufreq") and not re.fullmatch(r"policy[0-9]+", member):
                continue
            for attribute in attributes:
                name = directory + "/" + member + "/" + attribute
                readings[name] = reader.read(name, 8192)
    result = {"readings": readings, "metrics": metrics(readings, pids), "read_seconds": time.monotonic() - start}
    if include_identity:
        files = {name: reader.read(name) for name in IDENTITY}
        result["identity"] = {"observed_files": files, "kernel_release": files[IDENTITY[0]],
                              **installed_identity(reader, packages), "reference_proven": False}
    return result


def supervised_snapshot(root, pids, packages, include_identity, *, timeout=SNAPSHOT_SECONDS):
    """A stalled kernel attribute cannot hold the supervisor's read loop open."""
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        try:
            os.close(read_fd)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
            signal.setitimer(signal.ITIMER_REAL, timeout)
            # Retain only the result pipe. In particular do not extend the life
            # of the parent's output files, input session or unrelated sockets.
            os.closerange(0, write_fd)
            descriptor_limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
            os.closerange(write_fd + 1, descriptor_limit if descriptor_limit != resource.RLIM_INFINITY else 1048576)
            payload = encoded(snapshot(root, pids, packages, include_identity))
            if len(payload) > SNAPSHOT_BYTES:
                os._exit(2)
            offset = 0
            while offset < len(payload):
                offset += os.write(write_fd, payload[offset:])
            os._exit(0)
        except BaseException:
            os._exit(2)
    os.close(write_fd)
    data = bytearray()
    deadline = time.monotonic() + timeout
    outcome = "error"
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome = "timeout"
                break
            if not select.select([read_fd], [], [], remaining)[0]:
                outcome = "timeout"
                break
            chunk = os.read(read_fd, min(65536, SNAPSHOT_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > SNAPSHOT_BYTES:
                outcome = "oversized"
                break
        os.waitpid(child, os.WNOHANG)
        # EOF can precede the child's final exit by a scheduling quantum. The
        # payload is sufficient; never block waitpid on a kernel-stalled child.
        if data and outcome == "error":
            try:
                result = json.loads(data)
                if isinstance(result, dict) and isinstance(result.get("readings"), dict):
                    return result
            except (UnicodeError, ValueError):
                pass
        return {"readings": {"collector": {"status": outcome}}, "metrics": {}, "read_seconds": timeout,
                "snapshot_failed": True}
    finally:
        os.close(read_fd)
        try:
            pid, _ = os.waitpid(child, os.WNOHANG)
            if pid == 0:
                os.kill(child, signal.SIGKILL)
                reap_deadline = time.monotonic() + 0.05
                while os.waitpid(child, os.WNOHANG)[0] == 0 and time.monotonic() < reap_deadline:
                    time.sleep(0.001)
        except (ChildProcessError, ProcessLookupError):
            pass


def write_file(directory_fd, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def collect(plan, output, root=Path("/"), *, clock=time.monotonic, sleep=time.sleep,
            take_snapshot=supervised_snapshot, cancelled=lambda: False):
    """Collect a validated plan into a new private directory. Fixtures use root."""
    from records import validate_plan
    validate_plan(plan)
    output = Path(output)
    output.mkdir(mode=0o700)
    directory_fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    config = plan["collection"]
    start = clock()
    cpu_start = time.process_time()
    child_start = os.times().children_user + os.times().children_system
    status, error = "failed", None
    samples_hash = hashlib.sha256()
    sample_count = missed = 0
    identity = {"reference_proven": False, "app_packages": {}}
    hashes = tool_hashes()
    plan_bytes = encoded(plan)
    deadline = start + config["settle_seconds"] + config["duration_seconds"]
    observed_start = start + config["settle_seconds"]
    total_bytes = len(plan_bytes)
    sample_fd = None

    def wait_until(target):
        while clock() < target:
            if cancelled():
                raise InterruptedError("cancelled")
            sleep(min(0.1, target - clock()))
        if cancelled():
            raise InterruptedError("cancelled")

    try:
        write_file(directory_fd, "plan.json", plan_bytes)
        sample_fd = os.open("samples.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600, dir_fd=directory_fd)
        with os.fdopen(sample_fd, "wb") as samples:
            sample_fd = None
            wait_until(observed_start)
            count = config["duration_seconds"] // config["interval_seconds"] + 1
            for index in range(count):
                scheduled = observed_start + index * config["interval_seconds"]
                wait_until(scheduled)
                if clock() - scheduled >= config["interval_seconds"]:
                    missed += 1
                # Real waits return slightly after a scheduled boundary. Allow
                # a bounded scheduling margin for the final sample, while
                # missed full intervals still fail the completed run below.
                if clock() > deadline + SCHEDULE_GRACE_SECONDS:
                    raise TimeoutError("collection deadline")
                before = clock()
                sample = take_snapshot(root, config["pids"], plan["reference"]["app_packages"], index == 0)
                after = clock()
                identity = sample.pop("identity", identity)
                sample.update(schema=1, index=index, monotonic_seconds=before,
                              elapsed_seconds=before - observed_start,
                              capture_started_seconds=before - observed_start,
                              capture_finished_seconds=after - observed_start,
                              wall_time_utc=datetime.now(timezone.utc).isoformat(),
                              observer_cpu_seconds=time.process_time() - cpu_start)
                blob = encoded(sample)
                if total_bytes + len(blob) > OUTPUT_BYTES - RESULT_BYTES:
                    raise ValueError("output budget exceeded")
                samples.write(blob)
                samples.flush()
                samples_hash.update(blob)
                total_bytes += len(blob)
                sample_count += 1
                if sample.get("snapshot_failed"):
                    raise RuntimeError("bounded snapshot failed")
            wait_until(deadline)
            samples.flush()
            os.fsync(samples.fileno())
        status = "completed"
        if missed:
            status, error = "failed", "sampling schedule missed"
        if tool_hashes() != hashes:
            status, error = "failed", "collector source changed during observation"
    except (KeyboardInterrupt, InterruptedError):
        status, error = "interrupted", "cancelled; partial observations are not completion"
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        status, error = "failed", str(exc)[:256]
    finally:
        if sample_fd is not None:
            os.close(sample_fd)
        result = {
            "schema": 1, "kind": "tyrell-observation-result", "status": status, "error": error,
            "plan_sha256": sha256(plan_bytes), "samples_sha256": samples_hash.hexdigest(),
            "sample_count": sample_count, "tool_sha256": hashes, "identity": identity,
            "claims_release_approval": False, "hardware_acceptance": False,
            "timing": {"settle_seconds": config["settle_seconds"],
                       "requested_duration_seconds": config["duration_seconds"],
                       "elapsed_seconds_including_settle": max(0, clock() - start),
                       "observed_seconds": max(0, clock() - observed_start),
                       "observer_cpu_seconds": time.process_time() - cpu_start,
                       "snapshot_child_cpu_seconds": max(0, os.times().children_user + os.times().children_system - child_start),
                       "missed_samples": missed},
            "limits": {"file_bytes": FILE_BYTES, "snapshot_read_bytes": READ_BYTES,
                       "dpkg_status_bytes": STATUS_BYTES, "snapshot_output_bytes": SNAPSHOT_BYTES,
                       "output_bytes": OUTPUT_BYTES, "enumerated_members_per_group": MEMBERS,
                       "result_reserved_bytes": RESULT_BYTES,
                       "snapshot_seconds": SNAPSHOT_SECONDS,
                       "final_sample_grace_seconds": SCHEDULE_GRACE_SECONDS,
                       "maximum_samples": 181},
            "limitations": [
                "Declared export/image/BSP identities are references, not proof of this running image.",
                "Package versions/status do not authenticate installed files or DEB bytes.",
                "Missing, denied, unsafe, oversized and malformed data are not zero or a pass.",
                "Read-only observations include observer overhead and do not exercise physical hardware.",
                "Interrupt occurrences are not wakeups; SPI counters are not rendered frames or optical scanout.",
                "Parent capture windows include snapshot startup/IPC; child read_seconds alone is not a capture-time bound.",
                "Raw gauge/PWM/backlight state is not validated electrical energy or proof of darkness.",
                "No service/timer inventory or Wi-Fi power-save command is executed; record independent evidence.",
                "A kernel-stalled child is killed without an unbounded reap wait; the kernel may delay its exit.",
            ],
        }
        result_bytes = encoded(result)
        if len(result_bytes) > RESULT_BYTES:
            result.update(status="failed", error="result budget exceeded",
                          identity={"reference_proven": False, "app_packages": {}, "status": "oversized"})
            result_bytes = encoded(result)
        if len(result_bytes) > RESULT_BYTES or total_bytes + len(result_bytes) > OUTPUT_BYTES:
            os.close(directory_fd)
            raise ValueError("result exceeds reserved output budget")
        try:
            write_file(directory_fd, "result.json", result_bytes)
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory; never overwritten")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("Live observation requires Linux; fixtures use the Python API")
    from records import load_json
    stopped = [False]
    def stop(_signal, _frame):
        stopped[0] = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        result = collect(load_json(args.plan), args.output, cancelled=lambda: stopped[0])
    except (OSError, ValueError) as exc:
        parser.exit(2, str(exc) + "\n")
    print(json.dumps({"status": result["status"], "sample_count": result["sample_count"],
                      "hardware_acceptance": False, "claims_release_approval": False}))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
