"""D4 journal for a disposable native media actor bound to a display borrower.

Command completion is an execution fact, not backend finality. Dispatched
permits remain held through positive graph drain and a parked epoch fence or
final actor destruction with exact native-worker fencing/reaping.
Suppressed-before-dispatch work is the exception.
"""
from __future__ import annotations
import copy
import hashlib
import json

KINDS={"load","play","pause","toggle-pause","seek","seek-relative","output","audio-track","subtitle-track","audio-sink","volume","muted","stop","regrant","drain","shutdown"}
OUTCOMES={"completed","suppressed","failed","unknown"}
GRAPH_FIELDS={"kind","epoch","resource","generation","operation","graph_serial","callbacks_started","callbacks_completed",
              "active_callbacks","callback_contexts","live_objects","pipeline_finalized","local_handles_released","legacy_helpers_unused","finality_receipt"}
LIMIT=32

def sha(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()

def validate_graph(record,media,graph):
    from handoff_authority import require,identifier,integer
    from handoff_display_state import SHA256
    require(type(graph) is dict and set(graph)==GRAPH_FIELDS and graph["kind"]=="graph-drain-v4", "invalid native graph fields")
    display=record["display"]
    require(integer(graph["epoch"]) and graph["epoch"]==display["epoch"] and graph["resource"]==display["resource"] and
        graph["generation"]==record["generation"] and graph["operation"]==media["drain"] and identifier(graph["operation"]) and
        all(integer(graph[key],0) for key in ("graph_serial","callbacks_started","callbacks_completed","active_callbacks","callback_contexts","live_objects")) and
        graph["callbacks_started"]==graph["callbacks_completed"] and
        all(graph[key]==0 for key in ("active_callbacks","callback_contexts","live_objects")) and
        all(graph[key] is True for key in ("pipeline_finalized","local_handles_released","legacy_helpers_unused")) and
        identifier(graph["finality_receipt"]), "native graph finality is incomplete or stale")

def validate_media(state,record,actor,borrower):
    from handoff_authority import require,identifier,integer
    from handoff_display_state import SHA256
    media=borrower["media"]
    if media is None:return
    require(borrower["kind"]=="graph" and type(media) is dict and set(media)=={
        "phase","pid","uid","identity_sha256","commands","last_request","drain","shutdown","graph","fence","fence_mode","reaped"}, "invalid media journal")
    require(media["phase"] in ("registered","running","sealed","unknown","fenced","released") and
        (media["pid"] is None or integer(media["pid"],1,2**31-1)) and integer(media["uid"],1,2**32-1) and media["uid"]==record["uid"] and
        (media["identity_sha256"] is None or (type(media["identity_sha256"]) is str and SHA256.fullmatch(media["identity_sha256"]))) and
        type(media["commands"]) is dict and len(media["commands"])<=LIMIT+2 and integer(media["last_request"],0) and type(media["reaped"]) is bool,
        "invalid media identity or quota")
    operations=state["leases"][record["lease"]]["operations"]
    for name,event in media["commands"].items():
        require(identifier(name) and name in operations and operations[name]["actor"]==actor and type(event) is dict and
            set(event)=={"kind","request","payload_sha256","outcome","native_sha256"} and event["kind"] in KINDS and integer(event["request"]) and
            type(event["payload_sha256"]) is str and SHA256.fullmatch(event["payload_sha256"]) and
            (event["outcome"] is None or event["outcome"] in OUTCOMES) and
            (event["native_sha256"] is None or (type(event["native_sha256"]) is str and SHA256.fullmatch(event["native_sha256"]))),"invalid media operation")
    for key in ("drain","shutdown"):
        require(media[key] is None or (media[key] in media["commands"] and media["commands"][media[key]]["kind"]==key),"missing media lifecycle permit")
    if media["graph"] is not None:validate_graph(record,media,media["graph"])
    if media["fence"] is not None:
        require(media["graph"] is not None and media["fence_mode"] in ("epoch","process") and
            (media["fence_mode"]=="epoch" or (media["shutdown"] is not None and media["commands"][media["shutdown"]]["outcome"]=="completed")) and
            type(media["fence"]) is str and SHA256.fullmatch(media["fence"]),"media fence without finality")
    else:require(media["fence_mode"] is None,"fence mode without proof")
    if borrower["state"]=="DONE":
        require(media["phase"]=="released" and media["fence"] is not None and (media["fence_mode"]=="epoch" or media["reaped"]) and
            all(event["outcome"] in ("completed","suppressed","failed") and operations[name]["state"]=="DONE" for name,event in media["commands"].items()) and
            borrower["proof"]["receipt"]==media["graph"]["finality_receipt"],"media released before actor/backend fence")

class MediaJournal:
    def __init__(self,authority):
        from handoff_participant_state import Participants
        self.owner,self.parts=authority,Participants(authority)

    def _entry(self,cap,borrower):
        from handoff_authority import require
        _,record=self.parts._record(cap)
        self.owner._actor(borrower.actor)
        require(borrower.participant==cap.actor.identifier and borrower.generation==cap.generation and record["display"] is not None,"foreign media borrower")
        entry=record["display"]["borrowers"].get(borrower.actor.identifier)
        require(entry is not None and entry["operation"]==borrower.operation and entry["kind"]=="graph","unregistered graph lifetime")
        return record,entry

    def _target(self,state,cap,borrower):return state["participants"][cap.actor.identifier]["display"]["borrowers"][borrower.actor.identifier]

    def attach(self,cap,borrower):
        from handoff_authority import require
        with self.owner._lock:
            self.owner._expire();record,entry=self._entry(cap,borrower)
            require(self.owner._state["leases"][record["lease"]]["state"]=="OWNED" and entry["state"]=="PENDING" and entry["media"] is None,"graph attachment after seal")
            state=copy.deepcopy(self.owner._state)
            self._target(state,cap,borrower)["media"]={"phase":"registered","pid":None,"uid":record["uid"],"identity_sha256":None,
                "commands":{},"last_request":0,"drain":None,"shutdown":None,"graph":None,"fence":None,"fence_mode":None,"reaped":False}
            self.owner._commit(state,"media-lifetime-before-launch",actor=borrower.actor.identifier)

    def bind(self,cap,borrower,*,pid,uid,identity):
        from handoff_authority import require,integer
        with self.owner._lock:
            self.owner._expire();record,entry=self._entry(cap,borrower)
            require(entry["media"] is not None and entry["media"]["phase"]=="registered" and integer(pid,1,2**31-1) and type(uid) is int and uid==record["uid"],"invalid media producer binding")
            expected={"kind":"media-fixture-v4","epoch":record["display"]["epoch"],"resource":record["display"]["resource"],
                "generation":cap.generation,"pid":pid,"uid":uid,"profile":"lcd-fake-audio"}
            require(type(identity) is dict and all(type(identity.get(key)) is int for key in ("epoch","pid","uid")) and identity==expected,"native media profile/identity mismatch")
            state=copy.deepcopy(self.owner._state);media=self._target(state,cap,borrower)["media"]
            media["pid"],media["identity_sha256"],media["phase"]=pid,sha(identity),"running"
            self.owner._commit(state,"media-producer-before-factory",actor=borrower.actor.identifier)

    def permit(self,cap,borrower,*,kind,request,payload):
        from handoff_authority import require,integer,fresh_id,MAX_OPERATIONS
        with self.owner._lock:
            self.owner._expire();record,entry=self._entry(cap,borrower);media=entry["media"]
            require(media is not None and media["phase"] in ("running","sealed") and kind in KINDS and integer(request) and type(payload) is dict,"invalid media permit")
            lease=self.owner._state["leases"][record["lease"]]
            lifecycle=kind in ("drain","shutdown")
            require((lifecycle and lease["state"] in ("DRAINING","QUARANTINED")) or
                    (not lifecycle and lease["state"]=="OWNED" and media["phase"]=="running" and request>media["last_request"]),"sealed media admission")
            require(len(lease["operations"])<MAX_OPERATIONS and (lifecycle or
                sum(event["kind"] not in ("drain","shutdown") for event in media["commands"].values())<LIMIT),"media operation quota reached")
            require(kind!="shutdown" or media["drain"] is not None,"seal/drain permit required before shutdown")
            if lifecycle and media[kind] is not None:
                old=media["commands"][media[kind]]
                require(old["request"]==request and old["payload_sha256"]==sha(payload),"conflicting lifecycle replay")
                return media[kind]
            operation=fresh_id(lease["operations"]);state=copy.deepcopy(self.owner._state)
            target=self._target(state,cap,borrower)["media"]
            target["commands"][operation]={"kind":kind,"request":request,"payload_sha256":sha(payload),"outcome":None,"native_sha256":None}
            if lifecycle:target[kind]=operation;target["phase"]="sealed"
            else:target["last_request"]=request
            state["leases"][record["lease"]]["operations"][operation]={"actor":borrower.actor.identifier,"kind":"cleanup" if lifecycle else "use","state":"PENDING"}
            self.owner._commit(state,"media-command-before-dispatch",actor=borrower.actor.identifier,operation=operation)
            return operation

    def event(self,cap,borrower,event):
        from handoff_authority import require
        with self.owner._lock:
            self.owner._expire();record,entry=self._entry(cap,borrower);media=entry["media"]
            require(type(event) is dict and set(event)=={"kind","epoch","resource","generation","request","operation","outcome","graph"},"invalid native media event")
            operation=event["operation"];expected=media["commands"].get(operation)
            require(expected is not None and event["kind"]==expected["kind"] and type(event["request"]) is int and event["request"]==expected["request"] and
                type(event["epoch"]) is int and event["epoch"]==record["display"]["epoch"] and event["resource"]==record["display"]["resource"] and
                event["generation"]==cap.generation and event["outcome"] in OUTCOMES,"stale/foreign media result")
            if expected["outcome"] is not None:
                require(expected["native_sha256"]==sha(event),"conflicting native event replay");return
            if event["graph"] is not None:
                require(event["kind"] in ("drain","shutdown") and event["outcome"]=="completed","graph evidence from non-drain event")
                validate_graph(record,media,event["graph"])
            require(event["kind"]!="drain" or event["outcome"]!="completed" or event["graph"] is not None,"drain event lacks graph finality")
            state=copy.deepcopy(self.owner._state);target=self._target(state,cap,borrower)["media"]
            target["commands"][operation]["outcome"],target["commands"][operation]["native_sha256"]=event["outcome"],sha(event)
            if event["graph"] is not None:target["graph"]=copy.deepcopy(event["graph"])
            if event["outcome"]=="suppressed":state["leases"][record["lease"]]["operations"][operation]["state"]="DONE"
            if event["outcome"]=="unknown":
                target["phase"]="unknown";state["leases"][record["lease"]]["operations"][operation]["state"]="UNKNOWN"
                self.owner._quarantine(state["leases"][record["lease"]],"unknown")
            self.owner._commit(state,"media-native-event-before-ack",actor=borrower.actor.identifier,operation=operation)

    def fence(self,cap,borrower,facts):
        from handoff_authority import require
        with self.owner._lock:
            self.owner._expire();record,entry=self._entry(cap,borrower);media=entry["media"]
            require(type(facts) is dict and set(facts)=={"finished","unknown","thread_joined"} and all(type(value) is bool for value in facts.values()) and
                media["phase"]=="sealed" and media["graph"] is not None and media["shutdown"] is not None and
                media["commands"][media["shutdown"]]["outcome"]=="completed" and
                facts=={"finished":True,"unknown":False,"thread_joined":True},"media thread/backend not positively fenced")
            state=copy.deepcopy(self.owner._state);target=self._target(state,cap,borrower)["media"]
            target["fence"],target["phase"],target["fence_mode"]=sha(facts),"fenced","process"
            self.owner._commit(state,"media-worker-fence",actor=borrower.actor.identifier)

    def reaped(self,cap,borrower):
        from handoff_authority import require
        with self.owner._lock:
            self.owner._expire();record,entry=self._entry(cap,borrower);media=entry["media"]
            require(media["phase"]=="fenced" and media["fence_mode"]=="process" and media["fence"] is not None and all(event["outcome"] in ("completed","suppressed","failed") for event in media["commands"].values()),
                "unconfirmed actor exit or pending events")
            state=copy.deepcopy(self.owner._state)
            self._retire(state,cap,borrower,record,media,reaped=True)
            self.owner._commit(state,"media-borrower-after-finality-and-reap",actor=borrower.actor.identifier)

    def epoch_fence(self,cap,borrower,facts):
        """Retire a positively parked old epoch while retaining CPU checkpoint.

        The actual actor guard requires acknowledged events, no active/queued
        calls, callbacks, handles or automatic polling. Only a fresh grant can
        reopen it. This is not worker death or an app's local-done assertion.
        """
        from handoff_authority import require
        with self.owner._lock:
            self.owner._expire();record,entry=self._entry(cap,borrower);media=entry["media"]
            expected={"epoch":record["display"]["epoch"],"resource":record["display"]["resource"],"generation":cap.generation,
                "lifetime_operation":borrower.operation,"epoch_fenced":True,"unknown":False,"active":False,"queued":0,"events_pending":0}
            require(type(facts) is dict and set(facts)==set(expected) and
                all(type(facts[key]) is bool for key in ("epoch_fenced","unknown","active")) and
                all(type(facts[key]) is int for key in ("epoch","queued","events_pending")) and facts==expected and
                media["phase"]=="sealed" and media["graph"] is not None and media["shutdown"] is None and
                all(event["outcome"] in ("completed","suppressed","failed") for event in media["commands"].values()),"old media epoch not positively parked")
            state=copy.deepcopy(self.owner._state);target=self._target(state,cap,borrower)["media"]
            target["fence"],target["fence_mode"]=sha(facts),"epoch"
            self._retire(state,cap,borrower,record,target,reaped=False)
            self.owner._commit(state,"media-borrower-after-parked-epoch-fence",actor=borrower.actor.identifier)

    def _retire(self,state,cap,borrower,record,media,*,reaped):
        from handoff_authority import require
        target=self._target(state,cap,borrower);target["media"]["reaped"]=reaped;target["media"]["phase"]="released"
        target["state"]="DONE"
        target["proof"]={"epoch":record["display"]["epoch"],"resource":record["display"]["resource"],"generation":cap.generation,
            "actor":borrower.actor.identifier,"operation":borrower.operation,"disposition":"drained","receipt":media["graph"]["finality_receipt"]}
        lease=state["leases"][record["lease"]]
        for operation in (borrower.operation,*media["commands"]):
            require(lease["operations"][operation]["state"]!="UNKNOWN","unknown accepted media work")
            lease["operations"][operation]["state"]="DONE"
        lease["actors"][borrower.actor.identifier]["released"]=True
