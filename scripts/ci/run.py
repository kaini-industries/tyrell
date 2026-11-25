#!/usr/bin/env python3
"""Scoped source checks; never starts Lima, builds an image, or opens a device."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
SUITES = ("docs", "python", "game", "chess", "chess-native", "status", "video", "zero-native")
SOURCE_ROOTS = {"packages", "scripts", "builder", "image", "locks", "research",
                "docs", "dist", "store", ".github"}
PYTHON_GROUPS = (
    ("builder/tests", "test*.py"), ("image/tests", "test*.py"),
    ("scripts/tests", "test*.py"), ("scripts/ci/tests", "test*.py"),
    ("packages/tyrell-zero-session/tests", "test*.py"),
    ("packages/tyrell-zero-session/wizard/tests", "test*.py"),
    ("packages/tyrell-zero-store/tests", "test*.py"),
    ("packages/tyrell-zero-power-policy/tests", "test*.py"),
    ("packages/tyrell-chess/tests", "*_test.py"),
    ("scripts/release/tests", "test*.py"),
    ("packages/tyrell-catalog/tests", "test*.py"),
    ("scripts/observe/tests", "test*.py"),
    ("scripts/power/tests", "test*.py"),
    ("scripts/panel/tests", "test*.py"),
    ("packages/tyrell-video-handoff/tests", "test*.py"),
)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def command(*args, cwd=ROOT, capture=False, env=None):
    print("+ " + " ".join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), cwd=cwd, check=True, text=True, env=env,
                          stdout=subprocess.PIPE if capture else None)


def source_identity(root=ROOT):
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    names = subprocess.check_output(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"])
    files = {}
    for name in sorted(set(os.fsdecode(names).split("\0")) - {""}):
        p = PurePosixPath(name)
        if p.parts[0] not in SOURCE_ROOTS and len(p.parts) != 1:
            continue
        if any(part.startswith(".env") for part in p.parts):
            continue
        path = root / name
        if path.is_symlink():
            files[name] = {"link": os.readlink(path)}
        elif path.is_file():
            files[name] = {"sha256": digest(path), "executable": bool(path.stat().st_mode & 0o111)}
    dirty = bool(subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=normal"]))
    return {"commit": commit, "dirty": dirty, "input_files": len(files),
            "worktree_sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}


def require_tools(*names):
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise RuntimeError("Missing required prerequisites: " + ", ".join(missing))


def tool_versions():
    versions = {}
    for name in ("cmake", "ctest", "c++", "dpkg", "ffmpeg", "gst-inspect-1.0", "pulseaudio"):
        if shutil.which(name):
            result = subprocess.run([name, "--version" if name != "ffmpeg" else "-version"],
                                    capture_output=True, text=True, timeout=10)
            versions[name] = (result.stdout or result.stderr).splitlines()[:3]
    return versions


def dependencies(work, names):
    """Use pinned bytes, but a fresh extraction for every run; never trust old trees."""
    locked = json.loads((ROOT / "locks/game-build-inputs.json").read_text())
    lvgl = json.loads((ROOT / "locks/build-inputs.json").read_text())["lvgl"]
    locked["lvgl"] = dict(lvgl, directory="lvgl-" + lvgl["commit"])
    cache = ROOT / "build/ci/downloads"
    cache.mkdir(parents=True, exist_ok=True)
    destination = work / "dependencies"
    destination.mkdir()
    out = {}
    for name in names:
        item = locked[name]
        filename = ("lvgl-" + item["commit"] if name == "lvgl" else name + "-" + item["version"]) + ".tar.gz"
        archive = cache / filename
        if not archive.exists():
            previous = [ROOT / "build/downloads" / filename, ROOT / "build/zero-vendor/downloads" / filename,
                        ROOT / "build/game-deps" / filename]
            existing = next((p for p in previous if p.is_file() and not p.is_symlink()
                             and digest(p) == item["sha256"]), None)
            if existing:
                partial = cache / (filename + "." + uuid.uuid4().hex + ".partial")
                try:
                    shutil.copyfile(existing, partial)
                    if digest(partial) != item["sha256"]:
                        raise RuntimeError("Dependency changed during cache copy: " + name)
                    partial.replace(archive)
                finally:
                    partial.unlink(missing_ok=True)
            else:
                require_tools("curl")
                partial = cache / (filename + "." + uuid.uuid4().hex + ".partial")
                try:
                    command("curl", "--fail", "--location", "--proto", "=https", "--proto-redir", "=https",
                            "--retry", "2", "--connect-timeout", "15", "--max-time", "300",
                            item["url"], "--output", partial)
                    if digest(partial) != item["sha256"]:
                        raise RuntimeError("Downloaded dependency checksum mismatch: " + name)
                    partial.replace(archive)
                finally:
                    partial.unlink(missing_ok=True)
        if archive.is_symlink() or not archive.is_file() or digest(archive) != item["sha256"]:
            raise RuntimeError("Dependency cache checksum mismatch: " + name)
        with tarfile.open(archive) as source:
            members = source.getmembers()
            if sum(m.size for m in members) > 1024**3:
                raise RuntimeError("Expanded dependency exceeds 1 GiB: " + name)
            if any(PurePosixPath(m.name).parts[0] != item["directory"] for m in members):
                raise RuntimeError("Unexpected dependency archive root: " + name)
            source.extractall(destination, filter="data")
        out[name] = destination / item["directory"]
    return out


def ctest(build, work, required, pattern=None, env=None, launcher=()):
    selection = ["--tests-regex", pattern] if pattern else []
    inventory = json.loads(command("ctest", "--test-dir", build, "--show-only=json-v1",
                                   *selection, capture=True, env=env).stdout)
    names = {test["name"] for test in inventory["tests"]}
    if not required <= names:
        raise RuntimeError("Required CTest groups omitted: " + ", ".join(sorted(required - names)))
    junit = work / (build.name + "-ctest.xml")
    command(*launcher, "ctest", "--test-dir", build, "--output-on-failure", "--no-tests=error",
            *selection, "--timeout", "120", "--output-junit", junit, env=env)
    tree = ET.parse(junit)
    cases = tree.findall(".//testcase")
    if not cases or any(case.find("skipped") is not None for case in cases):
        raise RuntimeError("Required CTest execution was empty or skipped")
    return {"kind": "ctest", "tests": len(cases), "status": "passed",
            "inventory": sorted(names), "selection": pattern or "all",
            "executed_tests": [case.get("name") for case in cases],
            "launcher": list(launcher),
            "junit": str(junit.relative_to(ROOT))}


def configure_build(source, build, *flags):
    command("cmake", "-S", source, "-B", build, "-DCMAKE_BUILD_TYPE=Release",
            "-DBUILD_TESTING=ON", *flags)
    command("cmake", "--build", build, "--parallel", "4")


def run_game(work):
    require_tools("cmake", "ctest", "c++")
    deps = dependencies(work, ("raylib", "enet"))
    flags = ["-DRAYLIB_SOURCE_DIR=" + str(deps["raylib"]), "-DENET_SOURCE_DIR=" + str(deps["enet"])]
    build = work / "game"
    configure_build(ROOT / "packages/tyrell-game", build, *flags, "-DTYRELL_GAME_BACKEND=HEADLESS",
                    "-DTYRELL_GAME_BUILD_TESTS=ON", "-DTYRELL_GAME_BUILD_EXAMPLES=ON")
    results = [ctest(build, work, {"tyrell-game-core", "tyrell-game-network", "tyrell-game-input",
                                  "tyrell-game-runtime", "tyrell-game-render", "tyrell-game-independent-example",
                                  "tyrell-game-runtime-lifecycle", "tyrell-game-native-lifecycle"})]
    build = work / "tanks"
    configure_build(ROOT / "packages/tyrell-tanks", build, *flags, "-DTANKS_BUILD_DESKTOP=OFF",
                    "-DTANKS_BUILD_HEADLESS=ON", "-DTANKS_BUILD_BENCHMARK=ON", "-DTYRELL_GAME_BUILD_TESTS=ON")
    results.append(ctest(build, work, {"tanks-world", "tanks-network", "tanks-cli-help",
                                      "tanks-framework-render", "tanks-cli-session"}))
    require_tools("xvfb-run")
    desktop = work / "game-desktop"
    command("cmake", "-S", ROOT / "packages/tyrell-game", "-B", desktop,
            "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_TESTING=ON", *flags,
            "-DTYRELL_GAME_BACKEND=DESKTOP", "-DTYRELL_GAME_BUILD_TESTS=ON",
            "-DTYRELL_GAME_BUILD_NETWORK=OFF", "-DTYRELL_GAME_BUILD_EXAMPLES=OFF")
    command("cmake", "--build", desktop, "--parallel", "4", "--target", "tyrell-game-desktop-lifecycle-test")
    results.append(ctest(desktop, work, {"tyrell-game-desktop-lifecycle"},
                         pattern="^tyrell-game-desktop-lifecycle$", launcher=("xvfb-run", "-a"),
                         env=dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1")))
    return results


def run_chess(work):
    require_tools("cmake", "ctest", "c++")
    deps = dependencies(work, ("raylib",))
    build = work / "chess"
    configure_build(ROOT / "packages/tyrell-chess", build, "-DCHESS_BACKEND=HEADLESS",
                    "-DRAYLIB_SOURCE_DIR=" + str(deps["raylib"]), "-DTYRELL_ENABLE_SHELL_LINK=OFF")
    results = [ctest(build, work, {"chess-lan", "chess-controller", "chess-model", "chess-render",
                               "chess-lifecycle", "chess-keyboard-navigation", "chess-input-repeat",
                               "tyrell-app-commands", "chess-command-context", "chess-ui-layout",
                               "chess-offline-lifecycle", "chess-ai-cancellation"})]
    require_tools("xvfb-run")
    desktop = work / "chess-desktop"
    command("cmake", "-S", ROOT / "packages/tyrell-chess", "-B", desktop,
            "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_TESTING=OFF", "-DCHESS_BACKEND=DESKTOP",
            "-DRAYLIB_SOURCE_DIR=" + str(deps["raylib"]), "-DTYRELL_ENABLE_SHELL_LINK=OFF")
    command("cmake", "--build", desktop, "--parallel", "4", "--target", "tyrell-chess")
    report = work / "a3-chess-desktop.json"
    command("xvfb-run", "-a", desktop / "tyrell-chess", "--frames", "2", "--demo",
            "--state-dir", work / "desktop-state", "--report", report,
            env=dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1"))
    smoke = json.loads(report.read_text())
    if smoke.get("frames") != 2:
        raise RuntimeError("Desktop Chess smoke did not render the required frames")
    results.append({"kind": "desktop-app-smoke", "status": "passed", "frames": smoke["frames"],
                    "shell_link_enabled": False, "report": str(report.relative_to(ROOT))})
    return results


def native_preflight():
    require_tools("dpkg", "dpkg-deb", "dpkg-query", "cmake", "ctest", "cpack", "c++", "file", "readelf")
    if sys.platform != "linux" or platform.machine() not in {"aarch64", "arm64"}:
        raise RuntimeError("Native suites require a disposable Debian 13 ARM64 environment")
    if command("dpkg", "--print-architecture", capture=True).stdout.strip() != "arm64":
        raise RuntimeError("Native suites require Debian arm64")
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    if release.get("ID", "").strip('"') != "debian" or release.get("VERSION_ID", "").strip('"') != "13":
        raise RuntimeError("Native suites require Debian 13; Ubuntu is not a substitute")


def run_chess_native(work, revision):
    native_preflight()
    with (work / "worker-packages.tsv").open("w") as output:
        subprocess.run(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${Architecture}\n"],
                       stdout=output, check=True)
    deps = dependencies(work, ("raylib",))
    source = ROOT / "packages/tyrell-chess"
    flags = ("-DCHESS_BACKEND=NATIVE", "-DRAYLIB_SOURCE_DIR=" + str(deps["raylib"]))
    build = work / "chess-native"
    configure_build(source, build, *flags, "-DTYRELL_ENABLE_SHELL_LINK=OFF")
    results = [ctest(build, work, {"chess-engine", "chess-lan", "chess-controller", "chess-model",
                                  "chess-render", "chess-lifecycle", "chess-offline-lifecycle",
                                  "chess-ai-cancellation", "chess-input-repeat", "chess-ui-layout"})]
    packages = work / "packages"
    packages.mkdir()
    command("cpack", "--config", build / "CPackConfig.cmake", "-G", "DEB", "-B", packages)
    debs = list(packages.glob("*.deb"))
    if len(debs) != 1:
        raise RuntimeError("Expected one default native Chess package")
    inspection = command(sys.executable, source / "assets/check_package.py", debs[0], capture=True).stdout.strip()
    results.append({"kind": "package-build", "status": "passed", "path": str(debs[0].relative_to(ROOT)),
                    "sha256": digest(debs[0]), "inspection": inspection, "installed": False,
                    "shell_link_enabled": False})
    experimental = work / "chess-native-shell"
    command("cmake", "-S", source, "-B", experimental, "-DCMAKE_BUILD_TYPE=Release",
            "-DBUILD_TESTING=ON", *flags, "-DTYRELL_ENABLE_SHELL_LINK=ON")
    command("cmake", "--build", experimental, "--parallel", "4", "--target",
            "tyrell-chess", "chess-shell-lifecycle-test", "chess-ai-cancellation-test")
    results.append(ctest(experimental, work, {"chess-shell-lifecycle", "chess-ai-cancellation"},
                         pattern="^(chess-shell-lifecycle|chess-ai-cancellation)$"))
    return results


def run_app(work, app, revision):
    native_preflight()
    with (work / "worker-packages.tsv").open("w") as output:
        subprocess.run(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${Architecture}\n"],
                       stdout=output, check=True)
    if app == "video":
        require_tools("pkg-config", "ffmpeg", "ffprobe", "gst-inspect-1.0")
        command("pkg-config", "--atleast-version=1.26", "gstreamer-1.0")
        command("pkg-config", "--exists", "gstreamer-app-1.0", "gstreamer-video-1.0", "libdrm", "libpulse")
        for encoder in ("libx264", "aac"):
            available = command("ffmpeg", "-hide_banner", "-encoders", capture=True).stdout
            if not re.search(r"\b" + encoder + r"\b", available):
                raise RuntimeError("Missing required FFmpeg encoder: " + encoder)
        for element in ("playbin", "appsink", "fakesink", "kmssink", "pulsesink", "matroskademux",
                        "qtdemux", "h264parse", "avdec_h264", "videoconvert", "audioconvert"):
            command("gst-inspect-1.0", element, capture=True)
    deps = dependencies(work, ("lvgl",))
    build = work / app
    configure_build(ROOT / ("packages/tyrell-" + app), build,
                    "-DLVGL_SOURCE_DIR=" + str(deps["lvgl"]), "-DTYRELL_ENABLE_SDL=OFF",
                    "-DTYRELL_BUILD_REVISION=" + revision,
                    *(("-DTYRELL_ENABLE_SHELL_LINK=OFF",) if app == "status" else ("-DTYRELL_EXPERIMENTAL_SHELL_GUARD=OFF",)))
    required = {"status-model", "status-input-edges", "status-event-wait", "status-idle-gui",
                "cli-version", "cli-help", "headless-render"} if app == "status" else {
        "video-media-fixture", "video-media-controls", "video-caption-clock", "video-lcd-geometry",
        "video-startup", "video-track-switch", "video-playback", "video-hdmi-lease", "video-hdmi-audio",
        "video-render-browser", "video-render-player", "video-cli-report-write-error",
        "tyrell-app-commands", "video-ui-commands", "video-checkpoint", "video-lifecycle-blocking-spike",
        "video-managed-hdmi", "video-managed-lcd", "video-managed-display-authority",
        "video-media-graph", "video-media-actor", "video-media-ui", "video-media-authority",
        "video-draft2-link", "video-draft2-integration"}
    results = [ctest(build, work, required)]
    packages = work / "packages"
    packages.mkdir()
    command("cpack", "--config", build / "CPackConfig.cmake", "-G", "DEB", "-B", packages)
    debs = list(packages.glob("*.deb"))
    if len(debs) != 1:
        raise RuntimeError("Expected one native app package")
    results.append({"kind": "package-build", "status": "passed", "path": str(debs[0].relative_to(ROOT)),
                    "sha256": digest(debs[0]), "installed": False})
    if app == "status":
        # The package retains the default M5 build. Exercise experimental IPC
        # separately with a fake shell and memory display, never device nodes.
        pilot = work / "status-shell"
        configure_build(ROOT / "packages/tyrell-status", pilot,
                        "-DLVGL_SOURCE_DIR=" + str(deps["lvgl"]), "-DTYRELL_ENABLE_SDL=OFF",
                        "-DTYRELL_ENABLE_SHELL_LINK=ON",
                        "-DTYRELL_BUILD_REVISION=" + revision)
        results.append(ctest(pilot, work, required | {
            "status-shell-lifecycle", "status-shell-gui", "tyrell-shell-link", "tyrell-app-commands"}))
    if app == "video":
        # Compile the fail-closed native integration boundary separately. It
        # refuses linked launches; this is not live media lifecycle support.
        guard = work / "video-shell-guard"
        command("cmake", "-S", ROOT / "packages/tyrell-video", "-B", guard,
                "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_TESTING=ON",
                "-DLVGL_SOURCE_DIR=" + str(deps["lvgl"]), "-DTYRELL_ENABLE_SDL=OFF",
                "-DTYRELL_EXPERIMENTAL_SHELL_GUARD=ON",
                "-DTYRELL_BUILD_REVISION=" + revision)
        command("cmake", "--build", guard, "--parallel", "4", "--target", "video-shell-admission-test")
        results.append(ctest(guard, work, {"video-shell-admission"}, pattern="^video-shell-admission$"))
        # Compile and exercise the raw SDL repeat adapter too. It is absent
        # from the shipped native build, so do not rerun media tests or package it.
        sdl = work / "video-sdl"
        command("cmake", "-S", ROOT / "packages/tyrell-video", "-B", sdl,
                "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_TESTING=ON",
                "-DLVGL_SOURCE_DIR=" + str(deps["lvgl"]), "-DTYRELL_ENABLE_SDL=ON",
                "-DTYRELL_BUILD_REVISION=" + revision)
        command("cmake", "--build", sdl, "--parallel", "4", "--target",
                "video-ui-commands-test", "tyrell-app-commands-test")
        results.append(ctest(sdl, work, {"video-ui-commands", "tyrell-app-commands"},
                             pattern="^(video-ui-commands|tyrell-app-commands)$",
                             env=dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")))
        results.append(run_video_candidate(build, work, revision))
    return results


def run_video_candidate(build, work, revision):
    """Test the exact inactive archive, with no system install or live grant."""
    peer = build / "video-draft2-app-peer"
    trial = json.loads((build / "a2-d5-draft2-integration.json").read_text())
    if trial.get("status") != "passed" or trial.get("native_peer_sha256") != digest(peer):
        raise RuntimeError("Candidate peer differs from the executed native integration")
    epoch = command("git", "show", "-s", "--format=%ct", revision, capture=True).stdout.strip()
    output = work / "candidate-packages"
    command(sys.executable, ROOT / "packages/tyrell-video-handoff/build-package.py",
            "--native-peer", peer, "--source-revision", revision, "--output-dir", output,
            env=dict(os.environ, SOURCE_DATE_EPOCH=epoch))
    report = json.loads((output / "a2-d5-candidate-package.json").read_text())
    if report["native_peer_sha256"] != trial["native_peer_sha256"] or report["source_revision"] != revision:
        raise RuntimeError("Packaged native candidate is not the exact tested source/binary")
    packages = list(output.glob("*.deb"))
    if len(packages) != 1 or report["package_sha256"] != digest(packages[0]):
        raise RuntimeError("Candidate archive binding is incomplete")
    # A normal-user subprocess must reach the actual extracted Python modules,
    # even when the build directory and its parents are root-only in CI.
    with tempfile.TemporaryDirectory(prefix="tyrell-d5-extracted-") as directory:
        temporary = Path(directory)
        temporary.chmod(0o755)
        extracted = temporary / "root"
        command("dpkg-deb", "--extract", packages[0], extracted)
        modules = extracted / "usr/lib/tyrell-video-handoff"
        installed_trial = work / "a2-d5-extracted-integration.json"
        command(sys.executable, ROOT / "packages/tyrell-video/tests/draft2_integration.py",
                modules / "qualification/native-peer", build / "video-managed-display-worker",
                build / "media-clock.mp4", "--report", installed_trial,
                env=dict(os.environ, TYRELL_D5_TEST_MODULE_ROOT=str(modules), PYTHONDONTWRITEBYTECODE="1"))
        installed = json.loads(installed_trial.read_text())
        if installed["status"] != "passed" or installed["native_peer_sha256"] != trial["native_peer_sha256"]:
            raise RuntimeError("Extracted candidate did not execute the exact tested peer")
    shutil.copyfile(output / "a2-d5-candidate-package.json", work / "a2-d5-candidate-package.json")
    return {"kind": "handoff-candidate-package", "status": "passed", "source_revision": revision,
            "package_sha256": report["package_sha256"], "native_peer_sha256": trial["native_peer_sha256"],
            "extracted_integration_cases": len(installed["cases"]), "installed": False,
            "hardware_qualified": False, "live_admission": False, "physical_testing": "pending"}


def run_catalog(work, revision):
    """Optional normal-user app, separate from the preserved Store packages."""
    native_preflight()
    require_tools("openssl")
    dependency_work = work / "catalog-deps"
    dependency_work.mkdir()
    deps = dependencies(dependency_work, ("lvgl",))
    source = ROOT / "packages/tyrell-catalog"
    build = work / "catalog"
    configure_build(source, build, "-DLVGL_SOURCE_DIR=" + str(deps["lvgl"]),
                    "-DTYRELL_BUILD_REVISION=" + revision)
    required = {"catalog-model", "catalog-artwork", "catalog-backend-process", "catalog-entry-guard",
                "catalog-cli-help", "catalog-cli-version", "catalog-ui-artwork", "catalog-ui-preview-no-transfer",
                "catalog-ui-fresh", "catalog-ui-expired", "catalog-ui-clock-unknown",
                "catalog-ui-incompatible", "catalog-ui-empty", "catalog-ui-error",
                "catalog-ui-details", "catalog-ui-help"}
    results = [ctest(build, work, required)]
    packages = work / "packages"
    packages.mkdir()
    command("cpack", "--config", build / "CPackConfig.cmake", "-G", "DEB", "-B", packages)
    debs = list(packages.glob("*.deb"))
    if len(debs) != 1:
        raise RuntimeError("Expected one optional native Catalog package")
    inspection = command(sys.executable, source / "tools/check_package.py", debs[0], capture=True).stdout.strip()
    results.append({"kind": "package-build", "status": "passed", "path": str(debs[0].relative_to(ROOT)),
                    "sha256": digest(debs[0]), "inspection": inspection, "installed": False,
                    "root_store_authorized": False, "image_included": False})
    return results


def run_python(work):
    require_tools("cmake", "ctest", "ffmpeg", "ffprobe", "dpkg", "dpkg-deb", "dpkg-divert")
    results = []
    failed = False
    for index, (directory, pattern) in enumerate(PYTHON_GROUPS):
        report = work / ("python-" + str(index) + ".json")
        proc = subprocess.run([sys.executable, str(ROOT / "scripts/ci/python_tests.py"),
                               "--directory", str(ROOT / directory), "--pattern", pattern,
                               "--report", str(report)], cwd=ROOT)
        data = json.loads(report.read_text()) if report.is_file() else {"status": "failed", "error": "No test result"}
        results.append(dict(data, directory=directory, pattern=pattern))
        failed |= proc.returncode != 0
    if failed:
        # Retain every group's results even when another group fails or skips.
        error = RuntimeError("Required Python tests failed/skipped; see per-group reports")
        error.results = results
        raise error
    return results


def run_docs(work):
    import ledger
    errors = ledger.validate_ledger(ROOT)
    files = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
    checked = 0
    for path in files:
        for target in re.findall(r"\[[^\]]*\]\(([^)\n]+)\)", path.read_text()):
            if "://" in target or target.startswith(("#", "mailto:")):
                continue
            relative = target.split("#", 1)[0].strip("<>")
            if relative and not (path.parent / relative).exists():
                errors.append(str(path.relative_to(ROOT)) + ": missing local link " + relative)
            checked += 1
    if errors:
        raise RuntimeError("\n".join(errors))
    return [{"kind": "documentation-and-ledger", "status": "passed", "local_links": checked,
             "hardware_qualification": False}]


def export_evidence(work, suite):
    destination = ROOT / "build/ci/evidence" / suite / work.name
    destination.mkdir(parents=True, exist_ok=True)
    selected = set(work.glob("*.json")) | set(work.glob("*-ctest.xml"))
    selected.update(work.glob("video*/a2-*.json"))
    for directory in ("game", "game-desktop", "tanks", "chess", "chess-native", "chess-native-shell"):
        selected.update((work / directory).rglob("a3-*.json"))
    for previews in work.glob("*/u1-previews"):
        selected.update(path for path in previews.rglob("*")
                        if path.suffix in {".png", ".ppm", ".json"})
    for previews in work.glob("*/c1-previews"):
        selected.update(path for path in previews.rglob("*")
                        if path.suffix in {".png", ".ppm", ".json"})
    selected.update(work.glob("*.tsv"))
    selected.update(work.glob("*/Testing/Temporary/LastTest.log"))
    selected.update(work.glob("packages/*.deb"))
    selected.update(work.glob("candidate-packages/*.deb"))
    foundations = work / "foundations"
    for path in foundations.rglob("*"):
        if path.name in {"native-foundation.json", "build.log", "tests.txt", "session-tests.txt",
                         "session-test-detail.txt", "store-tests.txt", "store-test-detail.txt",
                         "isolated-install.log", "builder-packages.tsv", "worker-packages.tsv"} or path.suffix == ".deb":
            selected.add(path)
    bindings = []
    for path in sorted(selected):
        if path.is_file() and not path.is_symlink():
            target = destination / path.relative_to(work)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            bindings.append({"original_path": str(path.relative_to(ROOT)),
                             "artifact_path": str(target.relative_to(ROOT / "build/ci")),
                             "sha256": digest(target)})
    return str(destination.relative_to(ROOT)), bindings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=SUITES, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--expected-revision", help="Required exact checkout SHA in CI")
    args = parser.parse_args(argv)
    ci = ROOT / "build/ci"
    ci.mkdir(parents=True, exist_ok=True)
    report = (args.report or ci / (args.suite + ".json")).absolute()
    if not report.resolve().is_relative_to(ci.resolve()) or report.is_symlink():
        parser.error("Report must be an unredirected path under build/ci")
    report.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=args.suite + "-", dir=ci))
    started = datetime.now(timezone.utc).isoformat()
    data = {"schema": 1, "suite": args.suite, "status": "failed", "started_at": started,
            "work_directory": str(work.relative_to(ROOT)), "results": [],
            "scope": "source-checks-only", "hardware_qualified": False, "image_built": False,
            "environment": {"python": sys.version, "system": platform.system(), "machine": platform.machine()}}
    try:
        before = source_identity()
        data["source"] = before
        data["environment"]["tools"] = tool_versions()
        if args.expected_revision:
            if not re.fullmatch(r"[0-9a-f]{40}", args.expected_revision) or before["commit"] != args.expected_revision:
                raise RuntimeError("Checkout differs from the required candidate revision")
            if before["dirty"]:
                raise RuntimeError("CI candidate checkout must be clean")
        if args.suite == "docs":
            data["results"] = run_docs(work)
        elif args.suite == "python":
            data["results"] = run_python(work)
        elif args.suite == "game":
            data["results"] = run_game(work)
        elif args.suite == "chess":
            data["results"] = run_chess(work)
        elif args.suite == "chess-native":
            data["results"] = run_chess_native(work, before["commit"])
        elif args.suite in {"status", "video"}:
            data["results"] = run_app(work, args.suite, before["commit"])
        else:
            import native
            native_work = work / "foundations"
            data["results"] = [native.run_zero_native(ROOT, native_work)]
            data["results"].extend(run_catalog(work, before["commit"]))
        after = source_identity()
        if after != before:
            raise RuntimeError("Source inputs changed during checks; result cannot certify this checkout")
        data["status"] = "passed"
    except (Exception, KeyboardInterrupt) as error:
        data["error"] = str(error) or type(error).__name__
        if hasattr(error, "results"):
            data["results"] = error.results
        print("ci: " + data["error"], file=sys.stderr)
    finally:
        data["finished_at"] = datetime.now(timezone.utc).isoformat()
        data["evidence_directory"], data["exported_evidence"] = export_evidence(work, args.suite)
        report.write_text(json.dumps(data, indent=2) + "\n")
        print("CI report: " + str(report))
    return 0 if data["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
