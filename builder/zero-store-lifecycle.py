#!/usr/bin/env python3
"""Exercise real Store/dpkg operations in a disposable, isolated Debian root.

Invoke as root inside fresh mount AND network namespaces.  This harness models
the pinned vendor package with its exact two executable bytes atop the builder's
installed runtime.  It does not qualify a full Zero image or physical hardware.
No command operates on the builder's package database, services, or devices.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import types

PUBLIC = "/usr/share/APPLaunch/bin/M5CardputerZero-AppStore"
MAIN = "/usr/share/APPLaunch/bin/M5CardputerZero-APPLaunch"
STORE_SHA = "e5fe24b1dd26cd224c64ef4df2e7a61e0fce3fb63b3b04ac90d38c5092ccc05e"
MAIN_SHA = "9f26df9329160385c68794d26e0c7153271c6a69bb048938580e1495418c968c"
CAPS = "-all,+chown,+dac_override,+fowner,+fsetid,+setgid,+setuid"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tree_identity(path):
    """Hash the live database only for before/after proof; never copy secrets."""
    rows = []
    for p in sorted(Path(path).rglob("*")):
        if p.is_symlink():
            rows.append([str(p.relative_to(path)), "link", os.readlink(p)])
        elif p.is_file():
            rows.append([str(p.relative_to(path)), "file", sha(p)])
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def canonical(value):
    path = Path(value)
    if not path.is_absolute() or path != path.resolve() or any(c in str(path) for c in ",:\n\r"):
        raise RuntimeError("canonical absolute paths without overlay separators are required")
    return path


class Lifecycle:
    def __init__(self, source, output):
        self.source, self.output = source, output
        self.work = output.parent / "store-lifecycle-work"
        self.root = self.work / "root"
        # Lima commonly inherits the macOS user's UID501. Runtime authorization
        # is tested against this chroot-only UID1000 account, never host identity.
        self.user = types.SimpleNamespace(pw_name="native", pw_uid=1000,
                                          pw_gid=1000, pw_dir="/home/native")
        self.pending = Path(self.user.pw_dir) / ".local/share/cardputerzero-appstore/pending-package.json"
        self.checks = []
        self.log = None
        self.mounted = False

    def at(self, value):
        p = Path(value)
        if ".." in p.parts:
            raise RuntimeError("test path escapes root")
        target = self.root / str(p).lstrip("/")
        if not target.resolve().is_relative_to(self.root):
            raise RuntimeError("test root path follows an external symlink")
        return target

    def run(self, args, *, expected=0, env=None):
        result = subprocess.run([str(a) for a in args], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, env=env,
                                # The production helper has its own 600s child
                                # process-group timeout. Let it reap dpkg before
                                # this outer watchdog can terminate the helper.
                                timeout=900, close_fds=True)
        self.log.write("$ " + " ".join(map(str, args)) + "\n" + result.stdout + "\n")
        self.log.flush()
        if expected is not None and result.returncode != expected:
            raise RuntimeError("isolated command returned " + str(result.returncode) + ": " + result.stdout[-5000:])
        return result

    def chroot(self, args, *, expected=0, sudo=False):
        env = ["PATH=/usr/sbin:/usr/bin:/sbin:/bin", "HOME=/root", "LC_ALL=C.UTF-8",
               "DEBIAN_FRONTEND=noninteractive"]
        if sudo:
            env.append("SUDO_UID=1000")
        # Drop every capability except the narrow file/identity changes needed
        # by dpkg and the exact qualified Video maintainer scripts.  In
        # particular these children cannot mount, chroot again, create devices,
        # load modules, reboot, or access host bus/process endpoints.
        return self.run(["/usr/sbin/chroot", self.root, "/usr/bin/setpriv",
                         "--bounding-set=" + CAPS, "--", "/usr/bin/env", "-i",
                         *env, *args], expected=expected)

    def check(self, name, detail=None):
        self.checks.append({"name": name, "status": "passed", "detail": detail})

    def build_root(self):
        if self.work.exists() or self.work.is_symlink():
            raise RuntimeError("refusing to reuse a previous lifecycle root")
        self.root.mkdir(parents=True, mode=0o755)
        for name in ["upper", "overlay-work"]:
            (self.work / name).mkdir(mode=0o755)
        (self.root / "usr").mkdir()
        self.run(["mount", "-t", "overlay", "overlay", "-o",
                  "lowerdir=/usr,upperdir=" + str(self.work / "upper") +
                  ",workdir=" + str(self.work / "overlay-work"), self.root / "usr"])
        self.mounted = True
        for name in ["bin", "sbin", "lib"]:
            (self.root / name).symlink_to("usr/" + name)
        for name in ["etc", "dev", "proc", "sys", "run", "tmp", "root", "home",
                     "var/log", "var/cache", "var/lib/apt/lists/partial", "var/cache/apt/archives/partial"]:
            self.at(name).mkdir(parents=True, exist_ok=True)
        self.at("tmp").chmod(0o1777)
        shutil.copytree("/var/lib/dpkg", self.at("var/lib/dpkg"), symlinks=True)
        # A removed package's harmless residual-config entry is not part of the
        # modeled installed base. Any genuinely unfinished host package aborts.
        stanzas = self.at("var/lib/dpkg/status").read_text().strip().split("\n\n")
        installed = []
        for stanza in stanzas:
            status = re.search(r"^Status: (.+)$", stanza, re.M)
            if not status:
                raise RuntimeError("invalid copied package status")
            if status[1] == "install ok installed":
                installed.append(stanza)
            elif not status[1].endswith(("config-files", "not-installed")):
                raise RuntimeError("builder has an unfinished package; do not model around it")
        self.at("var/lib/dpkg/status").write_text("\n\n".join(installed) + "\n\n")
        for name in ["passwd", "group", "login.defs", "adduser.conf", "shells", "ld.so.cache",
                     "ld.so.conf", "debian_version"]:
            if Path("/etc", name).is_file():
                shutil.copyfile(Path("/etc", name), self.at("etc/" + name))
        for name in ["ld.so.conf.d", "alternatives", "fonts"]:
            if Path("/etc", name).is_dir():
                shutil.copytree(Path("/etc", name), self.at("etc/" + name), symlinks=True)
        passwd = [row for row in self.at("etc/passwd").read_text().splitlines()
                  if row.split(":")[0] != "native" and row.split(":")[2] != "1000"]
        passwd.append("native:x:1000:1000:Tyrell isolated fixture:/home/native:/bin/sh")
        self.at("etc/passwd").write_text("\n".join(passwd) + "\n")
        groups = [row for row in self.at("etc/group").read_text().splitlines()
                  if row.split(":")[0] != "native" and row.split(":")[2] != "1000"]
        groups.append("native:x:1000:")
        self.at("etc/group").write_text("\n".join(groups) + "\n")
        # Never copy the builder's password hashes. All fixture accounts lock.
        names = [row.split(":")[0] for row in self.at("etc/passwd").read_text().splitlines()]
        self.at("etc/shadow").write_text("".join(name + ":!:20000:0:99999:7:::\n" for name in names))
        self.at("etc/shadow").chmod(0o600)
        self.at("etc/gshadow").write_text("".join(row.split(":")[0] + ":!::\n" for row in self.at("etc/group").read_text().splitlines()))
        self.at("etc/gshadow").chmod(0o600)
        self.at("etc/nsswitch.conf").write_text("passwd: files\ngroup: files\nshadow: files\nhosts: files\n")
        self.at("usr/sbin/policy-rc.d").write_text("#!/bin/sh\nexit 101\n")
        self.at("usr/sbin/policy-rc.d").chmod(0o755)
        # No service manager sockets and no mounted host devices enter chroot.
        for name, major, minor in [("null", 1, 3), ("zero", 1, 5), ("random", 1, 8), ("urandom", 1, 9)]:
            os.mknod(self.at("dev/" + name), stat.S_IFCHR | 0o666, os.makedev(major, minor))
        # Exact vendor originals, modeled as installed applaunch baseline.
        files = []; md5sums = []
        for name, path, identity in [("M5CardputerZero-APPLaunch", MAIN, MAIN_SHA),
                                     ("M5CardputerZero-AppStore", PUBLIC, STORE_SHA)]:
            original = self.source / "vendor" / name
            if original.is_symlink() or sha(original) != identity:
                raise RuntimeError("vendor executable differs from pinned .105 baseline")
            target = self.at(path); target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target); target.chmod(0o755)
            files.append(path)
            md5sums.append(hashlib.md5(original.read_bytes()).hexdigest() + "  " + path.lstrip("/"))
        with self.at("var/lib/dpkg/status").open("a") as stream:
            stream.write("Package: applaunch\nStatus: install ok installed\nPriority: optional\nSection: misc\nInstalled-Size: 1\nMaintainer: Tyrell isolated fixture\nArchitecture: arm64\nVersion: 0.6.105\nDescription: modeled pinned vendor baseline, exact main and Store bytes\n\n")
        self.at("var/lib/dpkg/info/applaunch.list").write_text("\n".join(files) + "\n")
        self.at("var/lib/dpkg/info/applaunch.md5sums").write_text("\n".join(md5sums) + "\n")
        home = self.at(self.user.pw_dir)
        home.mkdir(parents=True, exist_ok=True); home.chmod(0o755); os.chown(home, 1000, self.user.pw_gid)
        self.at(self.pending.parent).mkdir(parents=True)
        for folder in [home / ".local", home / ".local/share", self.at(self.pending.parent)]:
            os.chown(folder, 1000, self.user.pw_gid); folder.chmod(0o700)
        downloads = home / "Downloads"; downloads.mkdir(); os.chown(downloads, 1000, self.user.pw_gid)
        self.apps = {}
        for name in ["status", "video-old", "video-new"]:
            src = self.source / "apps" / (name + ".deb")
            dst = downloads / (name + ".deb")
            shutil.copyfile(src, dst); os.chown(dst, 1000, self.user.pw_gid); dst.chmod(0o600)
            self.apps[name] = "/" + str(dst.relative_to(self.root))
        self.check("isolated-root", "Overlay /usr, copied db, locked synthetic passwords, empty runtime endpoints")

    def installed(self, package):
        result = self.chroot(["/usr/bin/dpkg-query", "-W", "-f=${db:Status-Abbrev}\\t${Version}", package], expected=None)
        if result.returncode == 1:
            return {"installed": False, "version": ""}
        if result.returncode:
            raise RuntimeError("cannot inspect disposable package status")
        fields = result.stdout.split("\t")
        if len(fields) != 2:
            raise RuntimeError("unexpected dpkg query format")
        return {"installed": fields[0][1:2] == "i", "version": fields[1] if fields[0][1:2] not in {"n", "c"} else ""}

    def save_pending(self, value):
        self.at(self.pending).write_text(json.dumps(value, indent=2) + "\n")
        self.at(self.pending).chmod(0o600); os.chown(self.at(self.pending), 1000, self.user.pw_gid)

    def request(self, label, app, action="install", *, expected=0, mutate=None):
        record = self.records[app]
        state = self.installed(record["package"])
        pending = {"schema_version": 2, "transaction_id": "lifecycle-" + label,
                   "action": action, "package": record["package"], "helper_completed": False,
                   "previously_installed": state["installed"], "previous_version": state["version"],
                   "expected_package_version": record["version"], "expected_md5": record["md5"],
                   "deb_path": self.apps[app], "rollback_deb_path": self.apps.get("video-old", "")}
        if mutate:
            mutate(pending)
        self.save_pending(pending)
        self.execute(pending, expected=expected)
        return json.loads(self.at(self.pending).read_text())

    def execute(self, pending, *, expected=0, repair=False):
        action = "repair" if repair else "uninstall" if pending["action"] == "uninstall" else "install"
        value = pending["package"] if action != "install" else pending["deb_path"]
        command = [PUBLIC, "--package-helper", action, "--package-value", value,
                   "--package-transaction", pending["transaction_id"],
                   "--package-pending-path", str(self.pending)]
        if pending["action"] == "reinstall" and not repair:
            command.append("--package-reinstall")
        return self.chroot(command, sudo=True, expected=expected)

    def repair(self, pending):
        pending.update(repair_requested=True, helper_completed=False)
        self.save_pending(pending)
        self.execute(pending, repair=True)
        value = json.loads(self.at(self.pending).read_text())
        if value.get("helper_completed") is not True or value.get("repair_completed") is not True:
            raise RuntimeError("repair did not record completion")
        return value

    def package_paths(self):
        found = {}
        # CPack retains its own staging/package copy below output. Only the
        # explicit exported top-level artifacts are integration candidates.
        for path in self.output.glob("*.deb"):
            fields = self.run(["dpkg-deb", "-f", path, "Package", "Version", "Architecture"]).stdout
            parsed = dict(row.split(": ", 1) for row in fields.splitlines() if ": " in row)
            key = parsed.get("Package")
            if key in {"tyrell-zero-session", "tyrell-zero-store"}:
                version = {"tyrell-zero-session": "0.2.0-1", "tyrell-zero-store": "0.1.0-1"}[key]
                if key in found or parsed.get("Version") != version or parsed.get("Architecture") != "arm64":
                    raise RuntimeError("ambiguous integration DEB identity")
                found[key] = path
        if set(found) != {"tyrell-zero-session", "tyrell-zero-store"}:
            raise RuntimeError("native integration DEBs are missing")
        return found

    def run_tests(self):
        activate = self.source / "packages/tyrell-zero-store/activate-image.py"
        self.run(["python3", activate, "prepare", "--root", self.root])
        packages = self.package_paths()
        # Files-only packages are installed offline; this deliberately does not
        # claim greetd/vendor dependency closure for this modeled test root.
        for package in ["tyrell-zero-session", "tyrell-zero-store"]:
            target = self.at("tmp/" + packages[package].name)
            shutil.copyfile(packages[package], target)
            self.chroot(["/usr/bin/dpkg", "--force-depends", "--no-triggers", "--install", "/tmp/" + target.name])
            self.chroot(["/usr/bin/dpkg", "--force-depends", "--no-triggers", "--install", "/tmp/" + target.name])
        self.run(["python3", activate, "capture", "--root", self.root,
                  "--protect-file", MAIN])
        self.check("integration-install-reinstall-diversion")
        catalog = json.loads(self.at("usr/share/tyrell-zero/store-catalog.json").read_text())
        by_identity = {(r["package"], r["version"]): r for r in catalog["packages"]}
        self.records = {"status": by_identity[("tyrell-status", "0.1.0-1")],
                        "video-old": by_identity[("tyrell-video", "0.1.6-1")],
                        "video-new": by_identity[("tyrell-video", "0.1.7-1")]}
        installed = self.request("status-install", "status")
        if installed.get("helper_completed") is not True or not self.installed("tyrell-status")["installed"]:
            raise RuntimeError("real Status installation failed")
        installed["helper_completed"] = False
        self.save_pending(installed); self.execute(installed, expected=1)
        self.repair(installed)
        self.request("status-reinstall", "status", "reinstall")
        self.request("status-uninstall", "status", "uninstall")
        self.check("status-install-reinstall-uninstall-and-completed-replay-denial")
        # An unqualified cache mutation fails before dpkg; its authenticated
        # transaction can still reconcile the already-absent app safely.
        status_path = self.at(self.apps["status"])
        original = status_path.read_bytes(); status_path.write_bytes(original + b"unqualified")
        failed = self.request("status-tampered", "status", expected=1)
        self.repair(failed); status_path.write_bytes(original)
        self.check("tampered-input-denial-and-absent-app-repair")
        self.request("video-install", "video-old")
        self.request("video-upgrade", "video-new", "upgrade")
        if self.installed("tyrell-video")["version"] != "0.1.7-1":
            raise RuntimeError("real Video upgrade did not retain expected version")
        self.check("video-install-and-upgrade-exact-maintainer-scripts")
        # Deterministic dpkg failure, followed by an explicitly modeled durable
        # interruption state. The subsequent repair uses the production helper
        # and real dpkg; no alternate runtime CLI or patched script is used.
        lock = self.at("var/lib/dpkg/lock").open("a+")
        try:
            fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            failed = self.request("video-interrupted-upgrade", "video-old", "upgrade", expected=1,
                                  mutate=lambda value: value.update(rollback_deb_path=self.apps["video-new"]))
        finally:
            lock.close()
        journal = self.at("var/lib/tyrell-zero/store/lifecycle-video-interrupted-upgrade.json")
        if not journal.is_file():
            raise RuntimeError("failure occurred before root transaction authorization")
        # Force a half-installed target in this disposable *copied* database,
        # a state dpkg --configure cannot repair; rollback must reinstall .7.
        status = self.at("var/lib/dpkg/status")
        stanzas = status.read_text().strip().split("\n\n")
        for index, stanza in enumerate(stanzas):
            if stanza.startswith("Package: tyrell-video\n"):
                stanza = re.sub(r"^Status: .+$", "Status: install reinstreq half-installed", stanza, flags=re.M)
                stanza = re.sub(r"^Version: .+$", "Version: 0.1.6-1", stanza, flags=re.M)
                stanzas[index] = stanza
                break
        else:
            raise RuntimeError("Video stanza missing for interruption fixture")
        status.write_text("\n\n".join(stanzas) + "\n\n")
        for app in ["video-old", "video-new"]:
            self.at(self.apps[app]).unlink()
        repaired = self.repair(failed)
        if repaired.get("repair_outcome") != "restored" or self.installed("tyrell-video")["version"] != "0.1.7-1":
            raise RuntimeError("root-staged previous Video bytes were not restored")
        self.check("video-root-staged-rollback", "Actual dpkg lock failure, modeled half-installed target status, deleted user cache; real dpkg restored previous qualified version")
        self.request("video-uninstall", "video-new", "uninstall")
        self.check("video-uninstall")
        # The baseline must still agree before removing the integration itself.
        self.chroot(["/usr/bin/python3", "-I", "-c",
                     "import sys;sys.path.insert(0,'/usr/lib/tyrell-zero/store');import guard;guard.Guard().invariants()"])
        self.check("base-invariants-after-app-lifecycle")
        for package in ["tyrell-zero-store", "tyrell-zero-session"]:
            self.chroot(["/usr/bin/dpkg", "--force-depends", "--no-triggers", "--remove", package])
            self.chroot(["/usr/bin/dpkg", "--force-depends", "--no-triggers", "--purge", package])
        self.run(["python3", activate, "restore", "--root", self.root])
        if sha(self.at(PUBLIC)) != STORE_SHA or sha(self.at(MAIN)) != MAIN_SHA:
            raise RuntimeError("removal did not restore exact vendor executables")
        self.check("integration-remove-purge-restores-pinned-vendor-store")

    def main(self):
        self.output.mkdir(parents=True, exist_ok=True)
        if (self.output / "store-lifecycle.json").exists():
            raise RuntimeError("refusing to overwrite a lifecycle report")
        baseline = tree_identity("/var/lib/dpkg")
        report = {"schema": 1, "scope": "isolated-real-dpkg-native-store-lifecycle",
                  "base": "builder-derived installed runtime with modeled exact applaunch executable baseline",
                  "full_zero_apt_closure_qualified": False, "physical_hardware_qualified": False,
                  "device_access": False, "external_network": False, "checks": self.checks,
                  "host_dpkg_before": baseline, "status": "failed"}
        self.log = (self.output / "store-lifecycle.log").open("w")
        try:
            self.build_root(); self.run_tests()
            report["status"] = "passed"
        except Exception as error:
            report["error"] = str(error)
            raise
        finally:
            if self.mounted:
                self.run(["umount", self.root / "usr"])
                self.mounted = False
            report["host_dpkg_after"] = tree_identity("/var/lib/dpkg")
            report["host_dpkg_unchanged"] = report["host_dpkg_before"] == report["host_dpkg_after"]
            if not report["host_dpkg_unchanged"]:
                report["status"] = "failed"
                report["error"] = "live builder dpkg database changed"
            (self.output / "store-lifecycle.json").write_text(json.dumps(report, indent=2) + "\n")
            self.log.close()
        if report["status"] != "passed":
            raise RuntimeError(report["error"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=canonical)
    parser.add_argument("output", type=canonical)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("root inside fresh mount/network namespaces is required")
    if os.readlink("/proc/self/ns/mnt") == os.readlink("/proc/1/ns/mnt"):
        parser.error("refusing the builder's live mount namespace")
    # The inherited sysfs mount can still expose the builder's old network view;
    # interface enumeration is a syscall against our actual network namespace.
    if sorted(name for _, name in socket.if_nameindex()) != ["lo"]:
        parser.error("fresh network namespace with only loopback is required")
    # A new mount namespace can inherit shared mounts. Privatize before adding
    # the disposable overlay so it cannot propagate back into the builder.
    subprocess.run(["mount", "--make-rprivate", "/"], check=True)
    Lifecycle(args.source, args.output).main()


if __name__ == "__main__":
    main()
