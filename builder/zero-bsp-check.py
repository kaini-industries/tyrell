#!/usr/bin/env python3
"""Compare two fixed-path BSP rebuilds in private disposable overlay roots.

The retained stage2 filesystem is a read-only overlay lowerdir. Builds use the
pinned archive and its original install target, never config_setup, APT, module
loading, services, an image build or device/SD access. Preserve logs and payloads.
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import uuid

BASE = Path("/var/tmp/tyrell-zero-bsp-checks")
KERNEL = "6.18.39+rpt-rpi-v8"
ARCHIVE_SHA = "bb671341f2419b2cd20607fb7e00fde4a35a338a8de7036632430375a66514d0"
MODULES = "/usr/lib/modules/" + KERNEL + "/extra"
OVERLAYS = "/boot/firmware/overlays"
BSP_OVERLAYS = ("cardputerzero-v5-overlay", "cardputerzero-v3-overlay", "spk-gpio24-high-overlay",
    "spi0-spidev2-gpio22-overlay", "camera-gpio16-high-overlay", "camera-py12-high-overlay",
    "mic-pyg4-high-overlay", "bq27220_v5", "bq27220_v3", "es8389_m5stack", "m5ioe1-overlay",
    "st7789v_m5stack", "tca8418_m5stack", "bmi270_bmm150_overlay", "bmi270_overlay")
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "ARCH": "arm64", "LANG": "C.UTF-8",
       "LC_ALL": "C.UTF-8", "TZ": "UTC", "DEBIAN_FRONTEND": "noninteractive"}
COMMAND = ("cd /tmp/dtoverlays/modules/CardputerZero && exec make "
           "KERNELDIR=/lib/modules/" + KERNEL + "/build "
           "EXTRADIR=/lib/modules/" + KERNEL + "/extra CONFIG_CARDPUTERO_V0_5=y install")


def require(value, message):
    if not value: raise RuntimeError(message)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    path = Path(path)
    require(not os.path.lexists(path), "preserving existing report: " + str(path))
    with path.open("x") as stream:
        json.dump(value, stream, sort_keys=True, indent=2);stream.write("\n")
        stream.flush();os.fsync(stream.fileno())


def trusted_directory(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
            "root-owned immutable directory required: " + str(path))


def canonical(path):
    require(path.is_absolute() and path == path.resolve() and path != Path("/")
            and re.fullmatch(r"/[A-Za-z0-9_./+\-]+", str(path)), "canonical safe build path required")
    return path


def mountpoints():
    points = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        parts = line.split();require(len(parts) >= 6, "invalid mount inventory")
        points.append(Path(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), parts[4])))
    return points


def image_path(root, name, follow=True):
    pending = collections.deque(PurePosixPath(name).parts[1:]);parts = [];links = 0
    while pending:
        part = pending.popleft()
        if part in {"", "."}: continue
        if part == "..":
            require(bool(parts), "dependency link escapes root");parts.pop();continue
        path = root.joinpath(*parts, part)
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) and (follow or pending):
            links += 1;require(links <= 40, "cyclic image link")
            target = PurePosixPath(os.readlink(path))
            if target.is_absolute(): parts = []
            pending.extendleft(reversed(target.parts[1:] if target.is_absolute() else target.parts))
        else: parts.append(part)
    return root.joinpath(*parts)


def snapshot(root):
    result = {}
    for name, pattern in ((MODULES, "*.ko*"), (OVERLAYS, "*.dtbo")):
        directory = image_path(root, name)
        require(directory.is_dir(), "missing BSP output directory")
        for path in sorted(directory.rglob(pattern)):
            logical = name + "/" + str(path.relative_to(directory))
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o6022,
                    "unsafe BSP payload: " + logical)
            result[logical] = {"type": "file", "sha256": digest(path),
                "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}
    return result


def differences(actual, expected):
    return {"missing": sorted(expected.keys() - actual.keys()), "extra": sorted(actual.keys() - expected.keys()),
            "changed": [{"path": key, "expected": expected[key], "actual": actual[key]}
                        for key in sorted(actual.keys() & expected.keys()) if actual[key] != expected[key]]}


def extract(archive_path, target):
    require(not os.path.lexists(target), "fresh BSP source extraction required")
    target.mkdir(mode=0o755)
    with tarfile.open(archive_path) as archive:
        total = 0;names = set()
        for member in archive:
            parts = PurePosixPath(member.name).parts
            require(parts and not member.name.startswith("/") and ".." not in parts,
                    "unsafe pinned source member")
            if len(parts) == 1:
                require(member.isdir(), "unexpected BSP archive root file");continue
            relative = PurePosixPath(*parts[1:]);name = str(relative)
            require(name not in names and (member.isfile() or member.isdir()), "unsafe or duplicate BSP source member")
            names.add(name);total += member.size
            require(total <= 256 * 1024 * 1024, "BSP source exceeds extraction budget")
            path = target.joinpath(*relative.parts)
            if member.isdir(): path.mkdir(parents=True, exist_ok=True, mode=0o755)
            else:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with archive.extractfile(member) as source, path.open("xb") as output:
                    shutil.copyfileobj(source, output)
                path.chmod(member.mode & 0o777)
            os.utime(path, (member.mtime, member.mtime), follow_symlinks=False)


def empty_copy_runtime(root):
    # These are overlay paths only. Unlink links, never follow them into the
    # lower root or host. No host runtime filesystem is bound into this root.
    for name in ("proc", "sys", "dev", "run"):
        folder = root / name
        require(not folder.is_symlink() and folder.is_dir(), "unsafe overlay runtime parent")
        for path in folder.iterdir():
            if path.is_symlink() or not path.is_dir(): path.unlink()
            else: shutil.rmtree(path)
    os.mknod(root / "dev/null", stat.S_IFCHR | 0o666, os.makedev(1, 3))


def build(root, log):
    with log.open("xb") as stream:
        child = subprocess.Popen(["chroot", str(root), "/bin/sh", "-c", COMMAND], env=ENV,
                                 stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        try: return child.wait(timeout=600)
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL);child.wait(timeout=10)


def worker(job):
    canonical(job);require(job.parent == BASE, "worker job outside fixed namespace");trusted_directory(job)
    config = json.loads((job / "config.json").read_text())
    lower = canonical(Path(config["retained_root"]));trusted_directory(lower)
    require(digest(job / "bsp.tar.gz") == ARCHIVE_SHA, "staged pinned archive changed")
    require(not any(lower == point or lower in point.parents for point in mountpoints()), "retained root is mounted")
    results = []
    for number in (1, 2):
        directory = job / ("run-" + str(number));directory.mkdir(mode=0o700)
        for name in ("upper", "work", "root", "payload"): (directory / name).mkdir(mode=0o700)
        root = directory / "root"
        options = "lowerdir=" + str(lower) + ",upperdir=" + str(directory / "upper") + ",workdir=" + str(directory / "work")
        subprocess.run(["mount", "-t", "overlay", "overlay", "-o", options, str(root)], check=True)
        print("BSP replay " + str(number) + ": fresh fixed-path overlay build", flush=True)
        started = time.monotonic();record = {"run": number, "status": "failed"}
        try:
            empty_copy_runtime(root)
            require(not (root / "tmp").is_symlink() and (root / "tmp").is_dir(),
                    "fixed source parent must be a real overlay directory")
            source = root / "tmp/dtoverlays"
            if os.path.lexists(source):
                if source.is_symlink() or not source.is_dir(): source.unlink()
                else: shutil.rmtree(source)
            extract(job / "bsp.tar.gz", source)
            # Remove every expected fresh output in the overlay so a partial
            # Makefile cannot pass by retaining old lowerdir bytes.
            for name in config["expected"]:
                if name.startswith(MODULES + "/") or PurePosixPath(name).name in {v + ".dtbo" for v in BSP_OVERLAYS}:
                    image_path(root, name).unlink()
            record["build_returncode"] = build(root, directory / "build.log")
            actual = snapshot(root)
            record["outputs"] = actual
            record["difference_from_retained"] = differences(actual, config["expected"])
            record["status"] = "passed" if record["build_returncode"] == 0 and actual == config["expected"] else "failed"
            for name in actual:
                path = directory / "payload" / name.lstrip("/")
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(image_path(root, name), path)
        except Exception as error: record["error"] = str(error)
        finally:
            record["seconds"] = round(time.monotonic() - started, 3)
            subprocess.run(["umount", str(root)], check=True)
            write(directory / "report.json", record)
        results.append(record)
        print("BSP replay " + str(number) + ": " + record["status"], flush=True)
    identical = bool(results[0].get("outputs")) and results[0].get("outputs") == results[1].get("outputs")
    report = {"runs": results, "two_rebuilds_identical": identical,
              "status": "passed" if identical and all(r["status"] == "passed" for r in results) else "failed"}
    write(job / "worker-report.json", report)
    return 0 if report["status"] == "passed" else 1


def parent(args):
    lower = canonical(args.retained_root);trusted_directory(lower)
    require(not os.path.samefile(lower, "/"), "live builder root is not a disposable input")
    require(not any(lower == p or lower in p.parents for p in mountpoints()), "detach retained source runtime mounts")
    require(not (lower / "usr/share/tyrell-zero/image-activation.json").exists(), "use the unactivated retained stage2 lower root")
    require(not args.archive.is_symlink() and args.archive.is_file() and digest(args.archive) == ARCHIVE_SHA,
            "exact pinned BSP source archive required")
    require(not args.native_compat.is_symlink() and args.native_compat.is_file(), "regular native compatibility lock required")
    compat = json.loads(args.native_compat.read_text())
    require(compat.get("profile") == "tyrell-zero" and compat.get("complete") is True, "complete native compatibility lock required")
    expected = {name: record for name, record in compat["protected_files"].items()
                if (name.startswith(MODULES + "/") and ".ko" in name)
                or (name.startswith(OVERLAYS + "/") and name.endswith(".dtbo"))}
    require(sum(name.startswith(MODULES + "/") for name in expected) == 16
            and all(OVERLAYS + "/" + name + ".dtbo" in expected for name in BSP_OVERLAYS),
            "pinned BSP module/overlay inventory is incomplete")
    before = snapshot(lower)
    require(before == expected, "retained BSP bytes already differ from native compatibility lock")
    rows = subprocess.check_output(["/usr/bin/dpkg-query", "--admindir=" + str(lower / "var/lib/dpkg"),
        "-W", "-f=${Package}\\t${db:Status-Abbrev}\\t${Version}\\n"], text=True, env=ENV)
    installed = {}
    for row in rows.splitlines():
        name, state, version = row.split("\t")
        if state[1:2] in {"c", "n"}: continue
        require(state[1:2] == "i" and state[2:3] != "R" and name not in installed,
                "retained root has incomplete or ambiguous packages")
        installed[name] = version
    require(all(compat["required_packages"].get(name) == version for name, version in installed.items()),
            "retained build toolchain differs from frozen runtime package versions")
    build_packages = {name: version for name, version in installed.items()
                      if name in {"build-essential", "make", "device-tree-compiler", "gcc", "g++", "cpp"}
                      or name.startswith(("gcc-", "g++-", "binutils", "linux-headers", "libc6-dev"))}
    require({"build-essential", "make", "device-tree-compiler", "linux-headers-rpi-v8"}.issubset(build_packages),
            "frozen BSP build prerequisites are missing")
    headers = image_path(lower, "/lib/modules/" + KERNEL + "/build")
    require(headers.is_dir(), "pinned kernel header tree is missing")
    header_files = {name: digest(image_path(lower, "/lib/modules/" + KERNEL + "/build/" + name))
                    for name in ("Makefile", ".config", "Module.symvers", "include/generated/utsrelease.h")}
    retained_database_before = digest(lower / "var/lib/dpkg/status")
    if not BASE.exists(): BASE.mkdir(mode=0o700)
    trusted_directory(BASE)
    require(shutil.disk_usage(BASE).free >= 2 * 1024**3, "BSP overlay proof needs 2 GiB guest free space")
    require(args.output.is_absolute() and args.output == args.output.resolve()
            and not os.path.lexists(args.output), "new canonical report filename required")
    trusted_directory(args.output.parent)
    identifier = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-") + uuid.uuid4().hex[:8]
    job = BASE / identifier;job.mkdir(mode=0o700)
    shutil.copyfile(args.archive, job / "bsp.tar.gz")
    shutil.copyfile(Path(__file__), job / "helper.py")
    write(job / "config.json", {"retained_root": str(lower), "expected": expected})
    database_before = digest(Path("/var/lib/dpkg/status"))
    command = ["unshare", "--mount", "--net", "--fork", "--propagation", "private", "--",
               sys.executable, str(job / "helper.py"), "--worker", "--job", str(job)]
    execution = subprocess.run(command, check=False)
    report_path = job / "worker-report.json"
    record = json.loads(report_path.read_text()) if report_path.is_file() else {"status": "failed", "error": "worker exited before durable report"}
    after = snapshot(lower)
    database_after = digest(Path("/var/lib/dpkg/status"))
    retained_database_unchanged = retained_database_before == digest(lower / "var/lib/dpkg/status")
    retained_headers_unchanged = all(digest(image_path(lower, "/lib/modules/" + KERNEL + "/build/" + name)) == identity
                                     for name, identity in header_files.items())
    retained_unchanged = after == before and retained_database_unchanged and retained_headers_unchanged
    result = {"schema": 1, "scope": "two-fixed-path-bsp-builds-in-private-disposable-overlay-roots",
              "status": "passed" if execution.returncode == 0 and record["status"] == "passed" and retained_unchanged and database_before == database_after else "failed",
              "job_directory": str(job), "helper_sha256": digest(Path(__file__)),
              "source_archive_sha256": ARCHIVE_SHA, "native_compat_sha256": digest(args.native_compat),
              "kernel_release": KERNEL, "build_command": COMMAND, "build_environment": ENV,
              "frozen_build_packages": build_packages, "kernel_header_files_sha256": header_files,
              "timestamp_overrides": {key: None for key in ("SOURCE_DATE_EPOCH", "KBUILD_BUILD_TIMESTAMP", "KBUILD_BUILD_USER", "KBUILD_BUILD_HOST")},
              "module_count": sum(name.startswith(MODULES + "/") for name in expected),
              "overlay_count": sum(name.startswith(OVERLAYS + "/") for name in expected),
              "retained_bsp_payload_unchanged": after == before,
              "retained_package_database_unchanged": retained_database_unchanged,
              "retained_sampled_headers_unchanged": retained_headers_unchanged,
              "live_builder_dpkg_unchanged": database_before == database_after,
              "builder_dpkg_before": database_before, "builder_dpkg_after": database_after,
              "worker": record, "full_image_build": False, "service_activation": False,
              "device_access": False, "physical_hardware_qualified": False}
    require(args.output.is_absolute() and args.output == args.output.resolve(), "canonical report filename required")
    trusted_directory(args.output.parent)
    write(args.output, result)
    print(json.dumps({"status": result["status"], "module_count": result["module_count"],
                      "overlay_count": result["overlay_count"], "report": str(args.output)}, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--retained-root", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--native-compat", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--job", type=Path)
    args = parser.parse_args(argv)
    if os.geteuid() != 0 or platform.system() != "Linux" or platform.machine() not in {"aarch64", "arm64"}:
        parser.error("run only as root inside the existing ARM64 Linux builder")
    os.umask(0o022)
    if args.worker:
        require(args.job is not None, "worker job is required");return worker(args.job)
    if not all((args.retained_root, args.archive, args.native_compat, args.output)):
        parser.error("--retained-root, --archive, --native-compat and --output are required")
    return parent(args)


if __name__ == "__main__": sys.exit(main())
