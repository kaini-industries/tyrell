#!/usr/bin/env python3
"""Actual native display adapters through the durable authority, no devices.

The registered test producer is a fixed CMake-built fixture executable. Private
inherited pipes plus its pidfd bind this test's native facts; this does not
qualify an installed native bootstrap or a target driver completion provider.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
import stat

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"packages/tyrell-video-handoff"))
from handoff_authority import Authority, AuthorityError, identifier
from handoff_display_state import Displays,SCOPES
from handoff_participant_state import Participants
from handoff_participants import ParticipantWorker
from handoff_protocol import Message,CAPABILITIES
from handoff_vt import VtBackend,VT_GETSTATE,VT_ACTIVATE,VT_WAITACTIVE


def check(value,message):
    if not value: raise AssertionError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


class Clock:
    now=0
    def __call__(self): return self.now


class Native:
    def __init__(self,parts,cap,executable,*,gate=False,mode="ok"):
        self.parts,self.cap=parts,cap
        self.record=parts.snapshot(cap)
        self.domain=self.record["domain"]
        display=self.record["display"]
        self.gate_read,self.gate_write=os.pipe() if gate else (-1,-1)
        self.buffer=b""
        self.pidfd=-1
        self.terminal=False
        self.raw=[]
        arguments=[str(executable),self.domain,str(display["epoch"]),display["resource"],cap.generation,str(self.gate_read),mode]
        try:
            self.process=subprocess.Popen(arguments,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                pass_fds=(self.gate_read,) if gate else ())
            self.pidfd=os.pidfd_open(self.process.pid,0)
            self.initial=self.read()
            self.check_scope(self.initial)
            self.prepared={"kind":"display-fixture-v3","domain":self.domain,"epoch":display["epoch"],
                "resource":display["resource"],"generation":cap.generation,"scope":SCOPES[self.domain],
                "backend_pid":self.process.pid,"backend_uid":os.getuid(),"native_identity_sha256":digest(self.initial)}
            operation=parts.bind(cap,pid=self.process.pid,uid=os.getuid(),prepared=self.prepared)
            acquired=self.command("acquire "+operation)
            self.check_scope(acquired)
            if self.domain=="kms":
                check(acquired.get("verified") is True and acquired.get("acquire_operation")==operation,"KMS acquire not bound")
            else:
                check(acquired.get("acquired") is True and acquired.get("operation")==operation,"LCD acquire not bound")
            parts.completed(cap,phase="acquire",evidence={**self.prepared,"operation":operation,"admitted":True})
        except BaseException:
            try:parts.lost(cap)
            finally:
                if hasattr(self,"process"): self.close()
            raise
        finally:
            if self.gate_read>=0: os.close(self.gate_read);self.gate_read=-1

    def check_scope(self,facts):
        display=self.record["display"]
        check(type(facts.get("epoch")) is int and facts.get("epoch")==display["epoch"] and facts.get("resource")==display["resource"] and
              facts.get("generation")==self.cap.generation,"Native display binding changed")
        if self.domain=="kms":
            identity={"kind","epoch","resource","generation","connector","crtc","planes","device","inode","rdev"}
            allowed=(identity,identity|{"acquire_operation","verified"},identity|{"acquire_operation","cleanup_operation",
                "borrowers","atomic_sequence","fence","neutral","terminal_proof","future_writes_suppressed"})
            check(set(facts) in allowed and type(facts.get("connector")) is int and type(facts.get("crtc")) is int and
                  facts.get("kind")=="kms-v2" and facts.get("connector")==11 and facts.get("crtc")==12 and
                  facts.get("planes")=="14,15" and all(type(facts.get(key)) is int and facts[key]>=0 for key in ("device","inode","rdev")),
                  "Native KMS scope changed")
            if hasattr(self,"initial"):
                check(all(facts[key]==self.initial[key] for key in ("device","inode","rdev")),"Native DRM descriptor changed")
        else:
            check(set(facts)=={"kind","epoch","resource","generation","backend","width","height","format","stage","operation",
                "acquired","sealed","queued","inflight","driver_finality","future_callbacks_disabled","backend_receipt"} and
                  type(facts.get("width")) is int and type(facts.get("height")) is int and
                  facts.get("kind")=="lcd-v3" and facts.get("backend")=="controlled-memory-v3" and
                  facts.get("width")==320 and facts.get("height")==170 and facts.get("format")=="RGB565","Native LCD scope changed")

    def send(self,value):
        check(self.process.poll() is None,"Native worker has exited")
        self.process.stdin.write((value+"\n").encode());self.process.stdin.flush()

    def read(self,timeout=5):
        deadline=time.monotonic()+timeout
        while b"\n" not in self.buffer:
            remaining=deadline-time.monotonic()
            check(remaining>0 and select.select([self.process.stdout],[],[],max(0,remaining))[0],"Native reply timed out")
            data=os.read(self.process.stdout.fileno(),4096)
            check(bool(data),"Native worker exited without completion")
            self.buffer+=data
            check(len(self.buffer)<=8192,"Native message bound exceeded")
        raw,self.buffer=self.buffer.split(b"\n",1)
        check(len(raw)<=4096,"Native packet bound exceeded")
        value=json.loads(raw)
        check(type(value) is dict and not value.get("failed"),"Native backend refused completion")
        self.raw.append(value)
        return value

    def command(self,value): self.send(value);return self.read()

    def start_cleanup(self):
        operation=self.parts.drain(self.cap)
        check(identifier(operation),"No durable display cleanup permit")
        self.send("release "+operation)
        return operation

    def accept_cleanup(self,operation):
        facts=self.read()
        self.check_scope(facts)
        if self.domain=="kms":
            check(facts.get("cleanup_operation")==operation and facts.get("acquire_operation")==self.parts.snapshot(self.cap)["acquire"] and
                  type(facts.get("borrowers")) is int and facts.get("borrowers")==0 and
                  type(facts.get("atomic_sequence")) is int and facts.get("atomic_sequence")==1 and facts.get("fence") is True and
                  facts.get("neutral") is True and facts.get("future_writes_suppressed") is True,"Native KMS work not terminal")
            receipt=facts.get("terminal_proof")
        else:
            check(facts.get("stage")=="released" and facts.get("operation")==operation and facts.get("sealed") is True and
                  type(facts.get("queued")) is int and type(facts.get("inflight")) is int and
                  facts.get("queued")==0 and facts.get("inflight")==0 and facts.get("driver_finality") is True and
                  facts.get("future_callbacks_disabled") is True,"Native LCD work not terminal")
            receipt=facts.get("backend_receipt")
        check(identifier(receipt),"Native backend finality proof missing")
        self.parts.completed(self.cap,phase="cleanup",evidence={**self.prepared,"operation":operation,"sealed":True,
            "borrowers":0,"queued":0,"inflight":0,"driver_finality":True,"callbacks_suppressed":True,
            "native_receipt":receipt,"native_sha256":digest(facts)})
        return facts

    def fence(self):
        check(self.command("fence").get("fenced") is True,"Native destructor fence missing")
        self.parts.fence_ack(self.cap)
        check(select.select([self.pidfd],[],[],5)[0],"Native process did not exit after fence")
        check(self.process.wait(timeout=1)==0,"Native destructor/process failed")
        self.parts.reaped(self.cap)
        self.terminal=True

    def close(self):
        try:
            if not self.terminal:self.parts.lost(self.cap)
        finally:
            if self.process.poll() is None:
                if self.pidfd>=0:signal.pidfd_send_signal(self.pidfd,signal.SIGKILL)
                else:self.process.kill()
            self.process.communicate(timeout=5)
            if hasattr(self,"pidfd") and self.pidfd>=0:os.close(self.pidfd);self.pidfd=-1
            if self.gate_write>=0:os.close(self.gate_write);self.gate_write=-1


class Scenario:
    def __init__(self,executable):
        self.temp=tempfile.TemporaryDirectory()
        self.clock=Clock()
        self.owner=Authority.create(Path(self.temp.name)/"ledger",disposable=True,clock=self.clock)
        self.app=self.owner.bootstrap_app("a"*32,uid=os.getuid(),pid=os.getpid())
        self.owner.negotiate(self.app,Message("hello",(self.app.instance,CAPABILITIES)))
        self.lease=self.owner.grant_disposable(self.app).fields[4]
        self.parts=Participants(self.owner);self.displays=Displays(self.owner)
        self.kms=self.parts.register(self.app,self.lease,domain="kms",profile="kms-fixture-v3",uid=os.getuid())
        self.lcd=self.parts.register(self.app,self.lease,domain="lcd",profile="lcd-fixture-v3",uid=os.getuid())
        self.vt=self.parts.register(self.app,self.lease,domain="vt-display",profile="vt-display-fixture-v3",uid=os.getuid(),
            required_displays=(self.kms.actor.identifier,self.lcd.actor.identifier))
        self.workers=[];self.executable=executable
        self.vt_worker=None;self.vt_file=None;self.vt_trace=(-1,-1)

    def launch(self,cap,**kwargs):
        worker=Native(self.parts,cap,self.executable,**kwargs);self.workers.append(worker);return worker

    def seal(self,budget=10000):
        request=self.owner.quiesce(self.app,self.lease,budget_ms=budget)
        self.owner.local_done(self.app,Message("local-done",(*request.fields[:4],"none")))
        for worker in self.workers:check(worker.command("seal").get("sealed") is True,"Native epoch not sealed")

    def bind_vt(self):
        self.vt_file=tempfile.TemporaryFile()
        self.vt_trace=os.pipe()
        def factory():
            state={"active":1,"target":None}
            def ioctl(_fd,operation,value,mutate=False):
                if operation==VT_GETSTATE:value[0]=state["active"]
                elif operation==VT_ACTIVATE:
                    state["target"]=value;os.write(self.vt_trace[1],b"A" if value==2 else b"R")
                elif operation==VT_WAITACTIVE:state["active"]=state["target"]
                else:raise AssertionError("Unqualified VT fixture operation")
                return 0
            info=SimpleNamespace(st_mode=stat.S_IFCHR|0o600,st_rdev=os.makedev(4,0))
            with patch("handoff_vt.os.fstat",return_value=info):
                backend=VtBackend.adopt(self.vt_file.fileno(),managed_vt=2,neutral_vt=1,ioctl=ioctl)
            backend.disposable=True;backend.profile="vt-fixture"
            return backend
        self.vt_worker=ParticipantWorker(self.owner,self.vt,factory,inherited_fds=(self.vt_file.fileno(),self.vt_trace[1]))
        self.pump_vt(lambda:self.parts.snapshot(self.vt)["phase"]=="acquired")
        check(select.select([self.vt_trace[0]],[],[],1)[0] and os.read(self.vt_trace[0],1)==b"A","Actual VT acquire did not run")

    def pump_vt(self,condition):
        deadline=time.monotonic()+5
        while not condition() and time.monotonic()<deadline:
            self.vt_worker.pump();time.sleep(.002)
        check(condition(),"VT worker did not reach expected state")

    def finish_vt(self):
        self.vt_worker.request_drain()
        self.pump_vt(lambda:self.vt_worker.closed)
        check(self.vt_worker.stop_post()=="suppressed","VT stop-post not suppressed")
        check(select.select([self.vt_trace[0]],[],[],1)[0] and os.read(self.vt_trace[0],1)==b"R","Actual VT neutral transition did not run")

    def must_hold_vt(self):
        try:self.vt_worker.request_drain()
        except AuthorityError:return
        raise AssertionError("VT cleanup bypassed a display barrier")

    def close(self):
        try:
            if self.vt_worker is not None:
                self.vt_worker.close()
                self.pump_vt(lambda:self.vt_worker.reaped)
            for worker in self.workers:worker.close()
        finally:
            for fd in self.vt_trace:
                if fd>=0:os.close(fd)
            if self.vt_file is not None:self.vt_file.close()
            self.owner.close();self.temp.cleanup()


def composite(executable,*,expire=False):
    scenario=Scenario(executable)
    try:
        graph=scenario.displays.register_borrower(scenario.kms,kind="graph")
        active=scenario.displays.register_borrower(scenario.lcd,kind="flush")
        queued=scenario.displays.register_borrower(scenario.lcd,kind="flush")
        kms=scenario.launch(scenario.kms,gate=True);lcd=scenario.launch(scenario.lcd)
        scenario.bind_vt()
        check(kms.command("borrow "+graph.actor.identifier+" "+graph.operation)["admitted"] is True,"Native graph loan failed")
        for borrower in (active,queued):lcd.command("submit "+borrower.operation)
        check(lcd.command("refresh")["inflight"]==1,"Real LVGL flush was not pending")
        scenario.seal(1 if expire else 10000)
        scenario.must_hold_vt()
        for worker in (kms,lcd):
            try:worker.start_cleanup()
            except AuthorityError:pass
            else:raise AssertionError("Native cleanup ignored an independent borrower")
        proof="d"*32
        drained=kms.command("drain "+graph.actor.identifier+" "+graph.operation+" "+proof)
        check(drained.get("drained") is True,"Graph did not independently drain")
        def borrower_done(cap,borrower,disposition,receipt):
            record=scenario.parts.snapshot(cap)
            scenario.displays.borrower_done(cap,borrower,{"epoch":record["display"]["epoch"],"resource":record["display"]["resource"],
                "generation":cap.generation,"actor":borrower.actor.identifier,"operation":borrower.operation,"disposition":disposition,"receipt":receipt})
        borrower_done(scenario.kms,graph,"drained",drained["receipt"])
        suppressed=lcd.command("operation "+queued.operation)
        check(suppressed["state"]=="suppressed-before-start","Queued flush not positively suppressed")
        borrower_done(scenario.lcd,queued,"suppressed-before-start",queued.operation)
        lcd.command("complete")
        finished=lcd.command("operation "+active.operation)
        check(finished["state"]=="complete" and identifier(finished["receipt"]),"Driver callback did not complete actual flush")
        borrower_done(scenario.lcd,active,"drained",finished["receipt"])
        operation=kms.start_cleanup()
        check(kms.read().get("event")=="terminal-pending","Terminal backend gate not held")
        check(scenario.parts.snapshot(scenario.kms)["phase"]=="acquired","Fence alone retired the permit")
        scenario.must_hold_vt()
        if expire:scenario.clock.now=1000000;scenario.owner.tick()
        os.write(kms.gate_write,b"G")
        kms_facts=kms.accept_cleanup(operation)
        lcd_facts=lcd.accept_cleanup(lcd.start_cleanup())
        scenario.must_hold_vt()
        for worker in (kms,lcd):worker.fence()
        scenario.must_hold_vt()
        for cap in (scenario.kms,scenario.lcd):scenario.parts.stop_post(cap)
        scenario.finish_vt()
        if expire:
            try:scenario.owner.release_receipt(scenario.app,scenario.lease)
            except AuthorityError:pass
            else:raise AssertionError("Late display result resurrected expired lease")
        else:scenario.owner.release_receipt(scenario.app,scenario.lease)
        return {"case":"expiry" if expire else "composite","status":"passed","kms":kms_facts,"lcd":lcd_facts,
                "vt_dependencies":scenario.parts.snapshot(scenario.vt)["display_proofs"],"actual_vt_worker":True,"hardware_qualified":False}
    finally:scenario.close()


def failure(executable,*,kill=False):
    scenario=Scenario(executable)
    try:
        kms=scenario.launch(scenario.kms,gate=kill,mode="ok" if kill else "readback-fail")
        scenario.bind_vt();scenario.seal()
        operation=kms.start_cleanup()
        if kill:
            check(kms.read().get("event")=="terminal-pending","Kill fixture not held")
            signal.pidfd_send_signal(kms.pidfd,signal.SIGKILL)
        try:kms.accept_cleanup(operation)
        except AssertionError:scenario.parts.lost(scenario.kms)
        else:raise AssertionError("Unknown native result certified cleanup")
        scenario.must_hold_vt()
        record=scenario.parts.snapshot(scenario.kms)
        check(record["phase"]=="unknown" and scenario.owner.snapshot()["leases"][scenario.lease]["operations"][record["cleanup"]]["state"]=="UNKNOWN",
              "Native loss failed to preserve original cleanup barrier")
        return {"case":"worker-death" if kill else "readback-failure","status":"passed","hardware_qualified":False}
    finally:scenario.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("worker",type=Path);parser.add_argument("--report",type=Path,required=True)
    arguments=parser.parse_args()
    if os.getuid()==0:
        # Native CI's container builds as root. The actual authority/worker
        # trial runs as nobody; only its completed report is copied back.
        with tempfile.TemporaryDirectory() as directory:
            os.chown(directory,65534,65534)
            report=Path(directory)/"report.json"
            worker=Path(directory)/"managed-display-worker"
            shutil.copyfile(arguments.worker,worker)
            worker.chmod(0o755)
            def normal_user():
                os.setgroups([]);os.setgid(65534);os.setuid(65534)
            subprocess.run([sys.executable,"-I",str(Path(__file__).resolve()),str(worker),"--report",str(report)],
                           cwd=directory,preexec_fn=normal_user,check=True)
            arguments.report.write_bytes(report.read_bytes())
        return
    check(sys.platform.startswith("linux") and os.getuid()>0 and hasattr(os,"pidfd_open"),"Linux normal-user native integration required")
    results=[composite(arguments.worker),composite(arguments.worker,expire=True),failure(arguments.worker),failure(arguments.worker,kill=True)]
    arguments.report.write_text(json.dumps({"schema":1,"kind":"a2-d3-native-display","status":"passed","hardware_qualified":False,
        "live_admission":False,"cases":results},indent=2)+"\n")
    print("PASS: actual native KMS/LVGL adapters, independent borrowers, durable display-to-VT barriers and unknown-work containment")


if __name__=="__main__":main()
