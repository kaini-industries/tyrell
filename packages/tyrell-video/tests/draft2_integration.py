#!/usr/bin/env python3
"""Exact TVH2 shell/App/actor integration, with no device admission.

The fixed peer renders the real App into private memory and owns the production
LCD/fake-audio graph. The same trial runs again from the extracted candidate.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
# An extracted candidate supplies the exact modules under test, not a fallback
# to checkout modules when its closure is missing or changed.
module_root = os.environ.get("TYRELL_D5_TEST_MODULE_ROOT")
sys.path.insert(0, module_root or str(ROOT / "packages/tyrell-video-handoff"))
import media_actor_integration as media
import managed_display_integration as displays
from handoff_authority import Authority, AuthorityError
from handoff_display_state import Displays
from handoff_media_state import MediaJournal
from handoff_participant_state import Participants
from handoff_shell import ShellSession

check = displays.check

class Scenario(displays.Scenario):
    def __init__(self, executable):
        self.temp = tempfile.TemporaryDirectory(); self.clock = displays.Clock()
        self.owner = Authority.create(Path(self.temp.name) / "ledger", disposable=True, clock=self.clock)
        self.parts = Participants(self.owner); self.displays = Displays(self.owner)
        self.workers = []; self.executable = executable
        self.vt_worker = None; self.vt_file = None; self.vt_trace = (-1, -1)
        self.shell = None

    def register_displays(self):
        self.app = self.shell.app; self.lease = self.shell.lease
        self.kms = self.parts.register(self.app, self.lease, domain="kms", profile="kms-fixture-v3", uid=os.getuid())
        self.lcd = self.parts.register(self.app, self.lease, domain="lcd", profile="lcd-fixture-v3", uid=os.getuid())
        self.vt = self.parts.register(self.app, self.lease, domain="vt-display", profile="vt-display-fixture-v3", uid=os.getuid(),
            required_displays=(self.kms.actor.identifier, self.lcd.actor.identifier))

class Peer(media.MediaPeer):
    def __init__(self, scenario, executable, mode="ok"):
        self.scenario = scenario; self.buffer = b""; self.terminal = False; self.events = []
        self.cap = self.borrower = None
        self.sample = os.pipe(); self.finality = os.pipe()
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.process = subprocess.Popen([str(executable), str(right.fileno()), str(os.getuid()), str(os.getpid()), str(os.getuid()),
            "a" * 32, str(self.sample[0]), str(self.finality[0]), mode], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, pass_fds=(right.fileno(), self.sample[0], self.finality[0]))
        right.close(); self.pidfd = os.pidfd_open(self.process.pid, 0)
        os.close(self.sample[0]); os.close(self.finality[0])
        self.sample = (-1, self.sample[1]); self.finality = (-1, self.finality[1])
        try:
            identity = self.read()
            check(identity == {"kind": "draft2-app-v5", "pid": self.process.pid, "uid": os.getuid(),
                "profile": "memory-lvgl-lcd-fake-audio", "live_admission": False, "hardware_qualified": False}, "Unexpected native App identity")
            scenario.shell = ShellSession(scenario.owner, left.detach(), profile="disposable-v2", app_instance="a" * 32,
                expected_uid=os.getuid(), expected_pid=self.process.pid, clock=scenario.clock)
            check(self.command("start") == {"started": True}, "Native TVH2 bootstrap failed")
            self.link_wait("await-grant")
            scenario.shell.grant(); self.link_wait("await-ready"); scenario.register_displays()
            self.bind_media("prepare")
            check(self.command("bind") == {"bound": True}, "Factory was not admitted after durable lifetime binding")
            self.wait(lambda s: s["phase"] == "foreground")
            check(self.command("ready") == {"ready": True}, "Native App readiness refused")
            scenario.shell.pump()
        except BaseException:
            left.close(); self.close(); raise

    def link_wait(self, expected):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.scenario.shell.pump(); self.command("pump")
            status = self.command("link-status")
            if status["state"] == expected: return status
            time.sleep(.002)
        raise AssertionError("TVH2 App did not reach " + expected)

    def bind_media(self, verb):
        s = self.scenario; self.cap = s.lcd
        self.borrower = s.displays.register_borrower(self.cap, kind="graph")
        self.journal = MediaJournal(s.owner); self.journal.attach(self.cap, self.borrower)
        binding = s.parts.snapshot(self.cap)["display"]
        identity = self.command(verb + " " + str(binding["epoch"]) + " " + binding["resource"] + " " + self.cap.generation + " " + self.borrower.operation)
        self.journal.bind(self.cap, self.borrower, pid=self.process.pid, uid=os.getuid(), identity=identity)

    def quiesce(self, budget=10000):
        s = self.scenario; packet = s.shell.quiesce(budget_ms=budget)
        # No explicit pump command: a queued lifecycle packet must gate the
        # very next real App tick/input decision, before a durable seal arrives.
        gated = self.command("status")
        check(gated["link_state"] == "draining" and not gated["input_allowed"] and not gated["render_allowed"],
            "Queued quiesce did not precede App work")
        check(self.command("load \"/not-admitted.mp4\" \"\"") == {"accepted": False}, "Quiescing App accepted new UI input")
        for worker in s.workers: check(worker.command("seal") == {"sealed": True}, "Display seal failed")
        self.seal(int(packet.fields[3])); s.shell.pump()
        check(s.shell.status()["state"] == "DRAINING", "Local-done bypassed graph/display barriers")
        return gated

    def commit(self):
        shell = self.scenario.shell
        receipt = shell.release_receipt(); shell.commit_hidden(receipt.fields[4])
        self.link_wait("hidden")
        return receipt.fields[4]

    def fresh(self):
        s = self.scenario
        for worker in s.workers: worker.close()
        s.workers = []; s.vt_worker.close(); s.pump_vt(lambda: s.vt_worker.reaped); s.vt_worker = None
        for fd in s.vt_trace: os.close(fd)
        s.vt_trace = (-1, -1); s.vt_file.close(); s.vt_file = None
        s.shell.grant(); self.link_wait("await-ready"); s.register_displays(); self.bind_media("prepare-regrant")
        operation = self.journal.permit(self.cap, self.borrower, kind="regrant", request=100, payload={"fresh_epoch": s.parts.snapshot(self.cap)["display"]["epoch"]})
        check(self.command("regrant 100 " + operation) == {"accepted": True}, "Fresh TVH2 grant did not rebuild checkpoint")
        self.wait(lambda status: status["phase"] == "foreground" and not status["buffering"] and
            (not status["loaded"] or status["frame"]))
        check(self.command("ready") == {"ready": True}, "Fresh rebuilt App not ready")
        s.shell.pump()

    def exit_parked(self):
        check(self.command("exit-parked") == {"parked_exit": True}, "Native exit was not gated by the composite commit")
        check(select.select([self.pidfd], [], [], 5)[0] and self.process.wait(timeout=1) == 0, "Parked App failed to exit")
        self.terminal = True

    def close(self):
        if self.cap is None: self.terminal = True
        try:
            if self.scenario.shell is not None: self.scenario.shell.close()
        finally:
            super().close()

def lifecycle(executable, display_worker, fixture):
    s = Scenario(display_worker); peer = None
    try:
        peer = Peer(s, executable); media.launch_displays(s)
        peer.propose("load " + json.dumps(str(fixture)) + " \"\"")
        peer.wait(lambda status: status["playing"] and status["frame"] and status["position"] > .15)
        peer.propose("volume 0.43"); peer.propose("mute 1")
        peer.wait(lambda status: abs(status["volume"] - .43) < .001 and status["muted"])
        check(peer.command("hold") == {"armed": True}, "Streaming hold failed")
        peer.wait(lambda status: status["sample_entered"])
        pause = peer.propose("pause")[0]; peer.wait(lambda status: status["active_operation"] == pause)
        before = peer.quiesce(); s.shell.cancel(); peer.command("pump")
        held = peer.command("status")
        check(held["ui_flushes"] == before["ui_flushes"] and held["frame_assignments"] == before["frame_assignments"], "Revoked UI rendered while graph was blocked")
        media.must_hold(s)
        os.write(peer.sample[1], b"G"); peer.wait(lambda status: status["finality_entered"]); media.must_hold(s)
        os.write(peer.finality[1], b"G"); old = peer.wait(lambda status: status["phase"] == "drained")
        graph = peer.park(); media.must_hold(s); display_facts = media.finish_displays(s); receipt = peer.commit()
        checkpoint = old["checkpoint"]; input_epoch = old["input_epoch"]
        peer.fresh(); media.launch_displays(s)
        restored = peer.wait(lambda status: status["paused"] and status["frame"] and not status["buffering"])
        check(abs(restored["position"] - checkpoint["position"]) < .15 and restored["muted"] == checkpoint["muted"] and
            abs(restored["volume"] - checkpoint["volume"]) < .001 and restored["input_epoch"] > input_epoch, "Fresh TVH2 grant lost checkpoint or input epoch")
        peer.quiesce(); peer.wait(lambda status: status["phase"] == "drained")
        fresh_graph = peer.park(); fresh_displays = media.finish_displays(s); fresh_receipt = peer.commit(); peer.exit_parked()
        return {"case": "actual-app-shell-two-epoch-checkpoint", "status": "passed", "initial_graph": graph, "fresh_graph": fresh_graph,
            "initial_displays": display_facts, "fresh_displays": fresh_displays, "receipt": receipt, "fresh_receipt": fresh_receipt,
            "checkpoint": checkpoint, "restored": restored, "queued_quiesce_gated_ui": True, "live_admission": False}
    finally:
        if peer is not None: peer.close()
        s.close()

def loss(executable, display_worker, fixture):
    s = Scenario(display_worker); peer = None
    try:
        peer = Peer(s, executable); media.launch_displays(s)
        peer.propose("load " + json.dumps(str(fixture)) + " \"\"")
        peer.wait(lambda status: status["playing"] and status["frame"])
        before = peer.command("status"); s.shell.stop("peer-loss")
        after = peer.command("status")
        check(after["link_state"] == "lost" and not after["input_allowed"] and not after["render_allowed"] and
            before["ui_flushes"] == after["ui_flushes"], "Channel loss did not gate App before its next tick")
        check(s.owner.snapshot()["leases"][s.lease]["state"] == "QUARANTINED", "Shell loss discarded accepted work")
        try: s.owner.release_receipt(s.app, s.lease)
        except AuthorityError: pass
        else: raise AssertionError("Channel loss manufactured release")
        return {"case": "shell-loss-before-next-app-tick", "status": "passed", "quarantined": True, "live_admission": False}
    finally:
        if peer is not None: peer.close()
        s.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("peer", "display_worker", "fixture"): parser.add_argument(name, type=Path)
    parser.add_argument("--report", type=Path, required=True); args = parser.parse_args()
    if os.getuid() == 0:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); os.chown(base, 65534, 65534); copies = []
            for source, name, mode in ((args.peer, "peer", 0o755), (args.display_worker, "display-worker", 0o755), (args.fixture, "media.mp4", 0o644)):
                path = base / name; shutil.copyfile(source, path); path.chmod(mode); copies.append(path)
            report = base / "report.json"
            def normal_user(): os.setgroups([]); os.setgid(65534); os.setuid(65534)
            subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), *map(str, copies), "--report", str(report)],
                cwd=directory, preexec_fn=normal_user, check=True)
            args.report.write_bytes(report.read_bytes())
        return
    check(sys.platform.startswith("linux") and hasattr(os, "pidfd_open"), "Normal-user Linux required")
    if module_root:
        for name, module in tuple(sys.modules.items()):
            if name.startswith("handoff_"):
                check(Path(module.__file__).resolve().is_relative_to(Path(module_root).resolve()), "Checkout module fallback: " + name)
    cases = [lifecycle(args.peer, args.display_worker, args.fixture), loss(args.peer, args.display_worker, args.fixture)]
    args.report.write_text(json.dumps({"schema": 1, "kind": "a2-d5-draft2-integration", "status": "passed",
        "native_peer_sha256": hashlib.sha256(args.peer.read_bytes()).hexdigest(), "actual_app": True,
        "hardware_qualified": False, "live_admission": False, "physical_testing": "pending", "cases": cases}, indent=2) + "\n")
    print("PASS: authenticated TVH2 shell, actual App/actor, durable composite commits and fresh checkpoint; live admission closed")

if __name__ == "__main__": main()
