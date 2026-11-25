#!/usr/bin/env python3
"""Real Playback/actor/control events through the SQLite display authority.

Fixed native fixture producers use private pipes and pidfds. This is not a
deployed shell/service bootstrap and cannot authorize physical device access.
"""
import argparse
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(HERE));sys.path.insert(0,str(ROOT/"packages/tyrell-video-handoff"))
import managed_display_integration as display_fixture
from handoff_authority import AuthorityError
from handoff_media_state import MediaJournal,sha

check=display_fixture.check


class MediaPeer:
    def __init__(self,scenario,executable,*,mode="ok",gates=True):
        self.scenario,self.cap,self.borrower=scenario,scenario.lcd,scenario.displays.register_borrower(scenario.lcd,kind="graph")
        self.journal=MediaJournal(scenario.owner);self.journal.attach(self.cap,self.borrower)
        self.buffer=b"";self.terminal=False;self.events=[]
        self.sample=os.pipe() if gates else (-1,-1)
        self.finality=os.pipe() if gates else (-1,-1)
        record=scenario.parts.snapshot(self.cap);binding=record["display"]
        self.process=subprocess.Popen([str(executable),str(binding["epoch"]),binding["resource"],self.cap.generation,
            self.borrower.operation,str(self.sample[0]),str(self.finality[0]),mode],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            pass_fds=tuple(fd for fd in (self.sample[0],self.finality[0]) if fd>=0))
        self.pidfd=os.pidfd_open(self.process.pid,0)
        for fd in (self.sample[0],self.finality[0]):
            if fd>=0:os.close(fd)
        self.sample=(-1,self.sample[1]);self.finality=(-1,self.finality[1])
        try:
            identity=self.read()
            self.journal.bind(self.cap,self.borrower,pid=self.process.pid,uid=os.getuid(),identity=identity)
            check(self.command("start")=={"started":True},"Actor factory startup failed")
        except BaseException:
            self.close();raise

    def read(self,timeout=5):
        deadline=time.monotonic()+timeout
        while b"\n" not in self.buffer:
            remaining=deadline-time.monotonic()
            check(remaining>0 and select.select([self.process.stdout],[],[],max(0,remaining))[0],"Media control reply timed out while graph was held")
            data=os.read(self.process.stdout.fileno(),16384)
            check(bool(data),"Native media worker exited without a terminal result")
            self.buffer+=data;check(len(self.buffer)<=65536,"Media fixture reply exceeds bound")
        raw,self.buffer=self.buffer.split(b"\n",1)
        check(len(raw)<=32768,"Native event batch exceeds bound")
        value=json.loads(raw)
        check(not(type(value) is dict and value.get("failed")),"Native media fixture rejected command")
        return value

    def command(self,value):
        check(self.process.poll() is None,"Media producer is no longer registered")
        self.process.stdin.write((value+"\n").encode());self.process.stdin.flush();return self.read()

    def events_before_ack(self):
        events=self.command("events")
        check(type(events) is list and len(events)<=34,"Invalid native event prefix")
        for event in events:self.journal.event(self.cap,self.borrower,event)
        if events:
            # The prefix is acknowledged only after every ledger commit above.
            check(self.command("ack "+str(len(events)))=={"acknowledged":True},"Durable event prefix was not acknowledged")
            self.events.extend(events)
        return events

    def wait(self,predicate,timeout=10):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            status=self.command("status");self.events_before_ack()
            if predicate(status):return status
            time.sleep(.003)
        raise AssertionError("Native actor did not reach expected state")

    def propose(self,command):
        check(self.command(command)=={"accepted":True},"UI intent queue refused a controlled command")
        intents=self.command("intents")
        check(type(intents) is list and bool(intents),"No exact unpermitted UI intent")
        operations=[]
        for intent in intents:
            token=self.command("intent-token "+str(intent["request"]))["payload"]
            check(json.loads(token)==intent,"Permit token differs from proposed payload")
            operation=self.journal.permit(self.cap,self.borrower,kind=intent["kind"],request=intent["request"],payload=intent)
            check(self.command("permit "+str(intent["request"])+" "+operation+" \"wrong-payload\"")=={"permit_rejected":True},
                "Wrong payload permit entered the actor")
            facts=self.command("permit "+str(intent["request"])+" "+operation+" "+json.dumps(token,ensure_ascii=False))
            check(len(facts)==1,"Unexpected submission prefix")
            actual=facts[0];descriptor={key:value for key,value in actual.items() if key not in ("operation","accepted")}
            check(descriptor==intent and actual["operation"]==operation and type(actual["accepted"]) is bool,"Dispatched payload differs from prejournaled intent")
            if not actual["accepted"]:
                event={key:intent[key] for key in ("kind","epoch","resource","generation","request")}
                self.journal.event(self.cap,self.borrower,{**event,"operation":operation,"outcome":"suppressed","graph":None})
            operations.append(operation)
        return operations

    def seal(self,request):
        operation=self.journal.permit(self.cap,self.borrower,kind="drain",request=request,payload={"sealed":True})
        check(self.command("seal "+str(request)+" "+operation)=={"accepted":True},"Actor did not seal its acceptance queue")
        return operation

    def park(self):
        self.events_before_ack()
        facts=self.command("park")
        self.journal.epoch_fence(self.cap,self.borrower,facts)
        return self.scenario.parts.snapshot(self.cap)["display"]["borrowers"][self.borrower.actor.identifier]["media"]["graph"]

    def fresh(self,scenario):
        self.scenario,self.cap=scenario,scenario.lcd
        self.borrower=scenario.displays.register_borrower(self.cap,kind="graph")
        self.journal.attach(self.cap,self.borrower)
        record=scenario.parts.snapshot(self.cap);binding=record["display"]
        identity=self.command("prepare-regrant "+str(binding["epoch"])+" "+binding["resource"]+" "+self.cap.generation+" "+self.borrower.operation)
        self.journal.bind(self.cap,self.borrower,pid=self.process.pid,uid=os.getuid(),identity=identity)
        operation=self.journal.permit(self.cap,self.borrower,kind="regrant",request=100,payload={"fresh_epoch":binding["epoch"]})
        check(self.command("regrant 100 "+operation)=={"accepted":True},"Fresh authority grant did not rebuild the saved checkpoint")

    def finish(self,request):
        operation=self.journal.permit(self.cap,self.borrower,kind="shutdown",request=request,payload={"shutdown":True})
        check(self.command("shutdown "+str(request)+" "+operation)=={"accepted":True},"Nonblocking shutdown refused")
        self.wait(lambda status:status["phase"]=="finished")
        facts=self.command("fence")
        self.journal.fence(self.cap,self.borrower,facts)
        check(select.select([self.pidfd],[],[],5)[0] and self.process.wait(timeout=1)==0,"Exact native media producer did not exit")
        self.journal.reaped(self.cap,self.borrower);self.terminal=True

    def close(self):
        try:
            if not self.terminal:self.scenario.displays.borrower_lost(self.cap,self.borrower)
        finally:
            if self.process.poll() is None:signal.pidfd_send_signal(self.pidfd,signal.SIGKILL)
            self.process.communicate(timeout=5)
            if self.pidfd>=0:os.close(self.pidfd);self.pidfd=-1
            for pair in (self.sample,self.finality):
                for fd in pair:
                    if fd>=0:os.close(fd)
            self.sample=self.finality=(-1,-1)


def seal_displays(scenario,peer,budget=10000):
    packet=scenario.owner.quiesce(scenario.app,scenario.lease,budget_ms=budget)
    for worker in scenario.workers:check(worker.command("seal")=={"sealed":True},"Native display did not seal")
    peer.seal(int(packet.fields[3]))
    # App local-done intentionally arrives before graph completion; it cannot
    # substitute for the independent graph producer/driver borrower.
    from handoff_protocol import Message
    scenario.owner.local_done(scenario.app,Message("local-done",(*packet.fields[:4],"none")))
    return int(packet.fields[3])

def must_hold(scenario):
    for cap in (scenario.lcd,scenario.vt):
        try:scenario.parts.drain(cap)
        except AuthorityError:pass
        else:raise AssertionError("Display/VT cleanup bypassed outstanding graph work")

def launch_displays(scenario):
    scenario.d4_flush=scenario.displays.register_borrower(scenario.lcd,kind="flush")
    scenario.launch(scenario.kms)
    lcd=scenario.launch(scenario.lcd)
    check(lcd.command("submit "+scenario.d4_flush.operation)=={"submitted":True},"LCD flush admission failed")
    check(lcd.command("refresh")["inflight"]==1,"Actual LVGL flush was not held independently")
    scenario.bind_vt()

def finish_displays(scenario):
    lcd=next(worker for worker in scenario.workers if worker.domain=="lcd")
    lcd.command("complete")
    flush=scenario.d4_flush
    facts=lcd.command("operation "+flush.operation)
    check(facts["state"]=="complete","Driver did not complete the independent LCD flush")
    record=scenario.parts.snapshot(scenario.lcd);binding=record["display"]
    scenario.displays.borrower_done(scenario.lcd,flush,{"epoch":binding["epoch"],"resource":binding["resource"],
        "generation":scenario.lcd.generation,"actor":flush.actor.identifier,"operation":flush.operation,
        "disposition":"drained","receipt":facts["receipt"]})
    native={}
    for worker in scenario.workers:
        native[worker.domain]=worker.accept_cleanup(worker.start_cleanup())
        worker.fence();scenario.parts.stop_post(worker.cap)
    scenario.finish_vt()
    scenario.owner.release_receipt(scenario.app,scenario.lease)
    return {"native_displays":native,"flush":facts,"vt_dependencies":scenario.parts.snapshot(scenario.vt)["display_proofs"],
        "actual_vt_worker":True,"composite_released":True}

def next_epoch(scenario):
    for worker in scenario.workers:worker.close()
    scenario.workers=[]
    scenario.vt_worker.close();scenario.pump_vt(lambda:scenario.vt_worker.reaped);scenario.vt_worker=None
    for fd in scenario.vt_trace:os.close(fd)
    scenario.vt_trace=(-1,-1);scenario.vt_file.close();scenario.vt_file=None
    scenario.lease=scenario.owner.grant_disposable(scenario.app).fields[4]
    uid=os.getuid()
    scenario.kms=scenario.parts.register(scenario.app,scenario.lease,domain="kms",profile="kms-fixture-v3",uid=uid)
    scenario.lcd=scenario.parts.register(scenario.app,scenario.lease,domain="lcd",profile="lcd-fixture-v3",uid=uid)
    scenario.vt=scenario.parts.register(scenario.app,scenario.lease,domain="vt-display",profile="vt-display-fixture-v3",uid=uid,
        required_displays=(scenario.kms.actor.identifier,scenario.lcd.actor.identifier))


def lifecycle(media_worker,display_worker,fixture):
    scenario=display_fixture.Scenario(display_worker);peer=None
    try:
        peer=MediaPeer(scenario,media_worker)
        launch_displays(scenario)
        peer.propose("load "+json.dumps(str(fixture))+" \"\"")
        peer.wait(lambda s:s["playing"] and s["frame"] and s["position"]>=.15)
        peer.propose("volume 0.42");peer.propose("mute 1")
        peer.wait(lambda s:abs(s["volume"]-.42)<.001 and s["muted"])
        check(peer.command("hold")=={"armed":True},"Real streaming gate was not armed")
        peer.wait(lambda s:s["sample_entered"])
        pause=peer.propose("pause")[0]
        peer.wait(lambda s:s["active_operation"]==pause)
        stale=peer.propose("seek 1")+peer.propose("output hdmi")
        request=seal_displays(scenario,peer)
        check(peer.command("cancel "+str(request))=={"cancelled":True},"Cancellation intent was not retained")
        blocked=peer.command("status")
        check(blocked["sample_entered"] and blocked["sealed"] and blocked["active_operation"]==pause,"In-flight call escaped its held callback")
        peer.events_before_ack();must_hold(scenario)
        check(all(next(e for e in peer.events if e["operation"]==op)["outcome"]=="suppressed" for op in stale),"Queued old-epoch controls executed after seal")
        os.write(peer.sample[1],b"G")
        peer.wait(lambda s:s["finality_entered"])
        must_hold(scenario)
        check(scenario.parts.snapshot(scenario.lcd)["display"]["borrowers"][peer.borrower.actor.identifier]["state"]=="PENDING","NULL/callback completion falsely retired graph")
        os.write(peer.finality[1],b"G")
        old=peer.wait(lambda s:s["phase"]=="drained")
        checkpoint=old["checkpoint"];check(checkpoint is not None and checkpoint["playing"] is False,"User pause intent was not checkpointed")
        first_graph=peer.park()
        must_hold(scenario) # Positive graph finality does not finish the panel flush.
        first_displays=finish_displays(scenario)
        next_epoch(scenario)
        peer.fresh(scenario)
        launch_displays(scenario)
        restored=peer.wait(lambda s:s["paused"] and s["frame"] and not s["buffering"])
        check(abs(restored["position"]-checkpoint["position"])<.15 and abs(restored["volume"]-checkpoint["volume"])<.001 and
            restored["muted"]==checkpoint["muted"] and restored["audio_track"]==checkpoint["audio_track"] and
            restored["subtitle_track"]==checkpoint["subtitle_track"] and restored["fallbacks"]==checkpoint["fallbacks"],"Fresh grant reset checkpoint state or retry accounting")
        request=seal_displays(scenario,peer)
        peer.wait(lambda s:s["phase"]=="drained")
        peer.finish(request+1)
        second_graph=scenario.parts.snapshot(scenario.lcd)["display"]["borrowers"][peer.borrower.actor.identifier]["media"]["graph"]
        second_displays=finish_displays(scenario)
        return {"case":"parked-checkpoint-fresh-grant-and-final-process-fence","status":"passed","initial_graph":first_graph,
            "fresh_graph":second_graph,"initial_displays":first_displays,"fresh_displays":second_displays,
            "checkpoint":checkpoint,"restored":restored,"suppressed_controls":len(stale),
            "wrong_payload_refused_before_dispatch":True,"events_committed_before_ack":True,"hardware_qualified":False}
    finally:
        if peer is not None:peer.close()
        scenario.close()

def refusal(media_worker,display_worker,fixture,*,kill=False,expire=False):
    scenario=display_fixture.Scenario(display_worker);peer=None
    try:
        peer=MediaPeer(scenario,media_worker,mode="unqualified" if not kill and not expire else "ok")
        launch_displays(scenario)
        peer.propose("load "+json.dumps(str(fixture))+" \"\"")
        peer.wait(lambda s:s["playing"] and s["frame"])
        request=seal_displays(scenario,peer,1 if expire else 10000)
        peer.wait(lambda s:s["finality_entered"])
        must_hold(scenario)
        if kill:
            signal.pidfd_send_signal(peer.pidfd,signal.SIGKILL)
            check(select.select([peer.pidfd],[],[],5)[0],"Killed media producer remained live")
            scenario.displays.borrower_lost(scenario.lcd,peer.borrower)
        else:
            if expire:scenario.clock.now=1000000;scenario.owner.tick()
            os.write(peer.finality[1],b"G")
            if expire:
                peer.wait(lambda s:s["phase"]=="drained");peer.finish(request+1)
                try:finish_displays(scenario)
                except AuthorityError:pass
                else:raise AssertionError("Late graph/fence result resurrected expired composite")
            else:
                peer.wait(lambda s:s["phase"]=="unknown")
        try:scenario.owner.release_receipt(scenario.app,scenario.lease)
        except AuthorityError:pass
        else:raise AssertionError("Uncertain graph work manufactured a composite receipt")
        return {"case":"worker-loss" if kill else "expiry" if expire else "unqualified-finality","status":"passed","hardware_qualified":False}
    finally:
        if peer is not None:peer.close()
        scenario.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media_worker",type=Path);parser.add_argument("display_worker",type=Path);parser.add_argument("fixture",type=Path)
    parser.add_argument("--report",type=Path,required=True);args=parser.parse_args()
    if os.getuid()==0:
        with tempfile.TemporaryDirectory() as directory:
            os.chown(directory,65534,65534);base=Path(directory)
            copies=[]
            for source,name,executable in ((args.media_worker,"media-worker",True),(args.display_worker,"display-worker",True),(args.fixture,"media.mp4",False)):
                target=base/name;shutil.copyfile(source,target);target.chmod(0o755 if executable else 0o644);copies.append(target)
            report=base/"result.json"
            def normal_user():os.setgroups([]);os.setgid(65534);os.setuid(65534)
            subprocess.run([sys.executable,"-I",str(Path(__file__).resolve()),*map(str,copies),"--report",str(report)],cwd=directory,preexec_fn=normal_user,check=True)
            args.report.write_bytes(report.read_bytes())
        return
    check(sys.platform.startswith("linux") and hasattr(os,"pidfd_open"),"Linux normal-user media integration required")
    cases=[lifecycle(args.media_worker,args.display_worker,args.fixture),refusal(args.media_worker,args.display_worker,args.fixture),
        refusal(args.media_worker,args.display_worker,args.fixture,kill=True),refusal(args.media_worker,args.display_worker,args.fixture,expire=True)]
    args.report.write_text(json.dumps({"schema":1,"kind":"a2-d4-media-authority","status":"passed","hardware_qualified":False,"live_admission":False,"cases":cases},indent=2)+"\n")
    print("PASS: actual Playback/actor/UI control permits, graph borrowers, checkpoint fresh grant, display/VT dependencies and uncertain-work containment")

if __name__=="__main__":main()
