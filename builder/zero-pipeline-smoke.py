#!/usr/bin/env python3
"""Exercise the real Zero guest wrapper with isolated, deliberately failing jobs.

Only generated root-owned fixture jobs are touched. The build script in each
capture is a failure fixture: pi-gen, services, image qualification and device
access are never invoked. Preserve every generated job and result for review.
"""
import argparse
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import runpy
import signal
import stat
import subprocess
import sys
import tarfile
import time
import uuid

BASE = Path("/var/tmp/tyrell-zero-image-jobs")
FAIL_SCRIPT = b'''#!/bin/bash
set -euo pipefail
printf 'Deliberate runner failure fixture; no image build.\\n' > "$3/fixture-started.txt"
exit 73
'''
WAIT_SCRIPT = b'''#!/bin/bash
set -euo pipefail
printf 'Deliberate interrupted failure fixture; no image build.\\n' > "$3/fixture-started.txt"
exec /bin/sleep 30
'''


def sha(value):
    return hashlib.sha256(value).hexdigest()


def require(condition, message):
    if not condition: raise RuntimeError(message)


def trusted_directory(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
            "fixture directory is not root-owned and immutable to ordinary users: " + str(path))


class Smoke:
    def __init__(self, source):
        self.source = source
        self.wrapper = (source / "image/run-zero-job.py").read_bytes()
        self.pipeline = (source / "image/zero_pipeline.py").read_bytes()
        self.tools = runpy.run_path(str(source / "image/zero_pipeline.py"))
        require(self.tools["GUEST_BASE"] == str(BASE), "guest base changed; review smoke fixtures")
        if not BASE.exists(): BASE.mkdir(mode=0o700)
        trusted_directory(BASE)
        self.jobs = []
        self.checks = []

    def check(self, name, passed, detail=None):
        record = {"name": name, "passed": bool(passed)}
        if detail is not None: record["detail"] = detail
        self.checks.append(record)
        require(passed, name)

    def fixture(self, label, script=FAIL_SCRIPT):
        identifier = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-") + uuid.uuid4().hex[:8]
        root = BASE / identifier
        root.mkdir(mode=0o700)
        input_root = root / "input";input_root.mkdir(mode=0o700)
        attempt = root / "attempts/0001";attempt.mkdir(parents=True, mode=0o700)
        (root / "attempts").chmod(0o700)
        for path in (root, input_root, attempt): trusted_directory(path)
        lock_bytes = (json.dumps({"schema": 1, "profile": "tyrell-zero", "complete": False,
                                 "scope": "runner-failure-fixture-only"}, sort_keys=True) + "\n").encode()
        profile = b'{"schema":1,"id":"tyrell-zero","scope":"runner-failure-fixture-only"}\n'
        payloads = {"image/run-zero-job.py": (self.wrapper, 0o644),
                    "image/zero_pipeline.py": (self.pipeline, 0o644),
                    "builder/build-zero-image.sh": (script, 0o755),
                    "image/profiles/tyrell-zero.json": (profile, 0o644),
                    "apt-repository/apt-lock.json": (lock_bytes, 0o644)}
        for name in self.tools["LOCKS"]: payloads["locks/" + name] = (lock_bytes, 0o644)
        files = {name: {"sha256": sha(data), "bytes": len(data), "mode": mode}
                 for name, (data, mode) in sorted(payloads.items())}
        metadata = {"schema": 1, "profile_id": "tyrell-zero", "build_mode": "zero-native",
                    "job_id": identifier, "files": files,
                    "capture_sha256": sha(json.dumps(files, sort_keys=True).encode()),
                    "locks_sha256": {name: sha(lock_bytes) for name in self.tools["LOCKS"]},
                    "profile_sha256": sha(profile), "scope": "runner-failure-fixture-only",
                    "image_build": False, "image_qualified": False, "hardware_qualified": False}
        metadata_bytes = (json.dumps(metadata, sort_keys=True, indent=2) + "\n").encode()
        with tarfile.open(root / "image-input.tar", "w") as archive:
            for name, (data, mode) in sorted({**payloads, "image-metadata.json": (metadata_bytes, 0o644)}.items()):
                target = input_root / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                # Intermediate mkdir(parents=True) modes also need to be fixed
                # under the builder user's ordinary collaborative umask.
                parent = target.parent
                while parent != input_root:
                    parent.chmod(0o700);parent = parent.parent
                target.write_bytes(data);target.chmod(mode)
                member = tarfile.TarInfo(name)
                member.size = len(data);member.mode = mode;member.uid = member.gid = 0;member.mtime = 1794528000
                archive.addfile(member, io.BytesIO(data))
        bundle = self.tools["digest"](root / "image-input.tar")
        require(self.tools["verify_bundle"](root / "image-input.tar") == metadata,
                "generated fixture did not pass the actual capture verifier")
        job = {"label": label, "job_id": identifier, "root": str(root), "bundle_sha256": bundle,
               "result_path": str(attempt / "result.json")}
        self.jobs.append(job)
        return job

    def invocation(self, job, overrides=None):
        root = Path(job["root"])
        command = [sys.executable, str(root / "input/image/run-zero-job.py"),
                   "--job", job["job_id"], "--attempt", "1", "--mode", "fresh",
                   "--bundle-sha256", job["bundle_sha256"]]
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TYRELL_IMAGE_JOB_ID=job["job_id"],
                   TYRELL_IMAGE_ATTEMPT_ID="1", TYRELL_IMAGE_INPUT_SHA256=job["bundle_sha256"])
        env.update(overrides or {})
        return command, env

    def run(self, job, overrides=None):
        command, env = self.invocation(job, overrides)
        result = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=15)
        job.setdefault("invocations", []).append({"returncode": result.returncode,
            "stdout": result.stdout[-4096:], "stderr": result.stderr[-4096:]})
        return result

    def failure(self, job, output):
        path = Path(job["result_path"])
        result = json.loads(path.read_text())
        expected = {"job_id": job["job_id"], "attempt_id": 1, "bundle_sha256": job["bundle_sha256"],
                    "exit_status": 1, "manifest_sha256": None}
        self.check(job["label"] + "_durable_bound_failure", result == expected)
        info = path.lstat()
        self.check(job["label"] + "_result_root_owned", stat.S_ISREG(info.st_mode) and info.st_uid == 0
                   and not info.st_mode & 0o022)
        attempt = path.parent / "output"
        self.check(job["label"] + "_expected_execution_boundary", attempt.exists() == output)
        if output:
            identity = {name: expected[name] for name in ("job_id", "attempt_id", "bundle_sha256")}
            self.check(job["label"] + "_output_attempt_identity",
                       json.loads((attempt / "attempt.json").read_text()) == identity)
        self.check(job["label"] + "_no_image_success", not (attempt / "image-manifest.json").exists()
                   and not list(attempt.glob("*.img*")))

    def exercise(self):
        failed = self.fixture("deliberate_failure")
        first = self.run(failed)
        self.check("fixture_child_failure_observed", first.returncode != 0 and "73" in first.stderr)
        self.failure(failed, True)
        result_path = Path(failed["result_path"])
        original = result_path.read_bytes();before = result_path.stat()
        second = self.run(failed)
        self.check("completed_attempt_replay_refused", second.returncode != 0 and "Preserving completed" in second.stderr)
        after = result_path.stat()
        self.check("completed_result_preserved", result_path.read_bytes() == original
                   and (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns))

        changed = self.fixture("changed_extracted_recipe")
        recipe = Path(changed["root"]) / "input/builder/build-zero-image.sh"
        recipe.write_bytes(recipe.read_bytes() + b"\n# deliberate tamper fixture\n")
        execution = self.run(changed)
        self.check("changed_recipe_refused", execution.returncode != 0 and "Root-staged input changed" in execution.stderr)
        self.failure(changed, False)

        changed = self.fixture("changed_archive")
        with (Path(changed["root"]) / "image-input.tar").open("ab") as stream: stream.write(b"deliberate archive tamper")
        execution = self.run(changed)
        self.check("changed_archive_refused", execution.returncode != 0 and "Root-staged archive changed" in execution.stderr)
        self.failure(changed, False)

        identity = self.fixture("wrong_environment")
        execution = self.run(identity, {"TYRELL_IMAGE_ATTEMPT_ID": "2"})
        self.check("environment_identity_refused_before_work", execution.returncode != 0
                   and "execution identity differs" in execution.stderr and not Path(identity["result_path"]).exists())
        self.run(identity);self.failure(identity, True)

        interrupted = self.fixture("interrupted_execution", WAIT_SCRIPT)
        command, env = self.invocation(interrupted)
        child = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, start_new_session=True)
        try:
            marker = Path(interrupted["result_path"]).parent / "output/fixture-started.txt"
            deadline = time.monotonic() + 8
            while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            require(marker.exists() and child.poll() is None, "interruption fixture did not reach its bounded child")
            child.send_signal(signal.SIGTERM)
            stdout, stderr = child.communicate(timeout=8)
            interrupted["invocations"] = [{"returncode": child.returncode, "stdout": stdout[-4096:], "stderr": stderr[-4096:]}]
            self.check("sigterm_failure_recorded", child.returncode != 0 and "interrupted" in stderr)
            self.failure(interrupted, True)
        finally:
            # Own child session only; never target a system service or another
            # job. subprocess.run kills its bounded sleep child on interruption.
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.communicate(timeout=5)


def write_report(path, value):
    require(path.is_absolute() and path == path.resolve() and not os.path.lexists(path),
            "a new canonical report filename is required")
    trusted_directory(path.parent)
    encoded = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encoded);stream.flush();os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if (os.geteuid() != 0 or platform.system() != "Linux" or platform.machine() not in {"aarch64", "arm64"}
            or subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip() != "arm64"):
        parser.error("run as root in the existing Linux ARM64 builder only")
    source = args.source_root
    require(source.is_absolute() and source == source.resolve(), "canonical source root required")
    smoke = Smoke(source)
    error = None
    try: smoke.exercise()
    except Exception as failure: error = str(failure)
    report = {"schema": 1, "scope": "actual-zero-wrapper-failure-fixtures-only",
              "status": "passed" if error is None else "failed", "error": error,
              "runner_sha256": sha(smoke.wrapper), "pipeline_sha256": sha(smoke.pipeline),
              "smoke_helper_sha256": sha(Path(__file__).read_bytes()),
              "checks": smoke.checks, "jobs": smoke.jobs,
              "full_image_build": False, "runtime_image_qualification": False,
              "service_activation": False, "device_access": False, "physical_hardware_qualified": False}
    write_report(args.output, report)
    print(json.dumps({"status": report["status"], "checks": len(smoke.checks), "jobs": len(smoke.jobs),
                      "report": str(args.output), "full_image_build": False}, sort_keys=True))
    return 0 if error is None else 1


if __name__ == "__main__": sys.exit(main())
