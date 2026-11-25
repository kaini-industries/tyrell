// Fixed, uninstalled LCD/fake-audio coordinator fixture. It drives the same
// actor-backed control facade used by the App pilot. Only the main thread
// serializes replies; streaming callbacks publish flags and hold private gates.
#include "actor_player_control.h"
#include "json_string.h"
#include <atomic>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <fcntl.h>
#include <unistd.h>

using namespace tyrell::video;
namespace {
void require(bool value,const std::string& message) { if(!value)throw std::runtime_error(message); }
bool id(const std::string& text) { return text.size()==32&&text.find_first_not_of("0123456789abcdef")==std::string::npos; }
void emit(const std::string& text) { std::cout<<text<<'\n'<<std::flush; }
const char* kind(MediaCommandKind value) {
    switch(value) {
        case MediaCommandKind::Load:return "load";case MediaCommandKind::Play:return "play";case MediaCommandKind::Pause:return "pause";
        case MediaCommandKind::TogglePause:return "toggle-pause";case MediaCommandKind::Seek:return "seek";case MediaCommandKind::SeekRelative:return "seek-relative";
        case MediaCommandKind::Output:return "output";case MediaCommandKind::AudioTrack:return "audio-track";case MediaCommandKind::SubtitleTrack:return "subtitle-track";
        case MediaCommandKind::AudioSink:return "audio-sink";case MediaCommandKind::Volume:return "volume";case MediaCommandKind::Muted:return "muted";
        case MediaCommandKind::Stop:return "stop";case MediaCommandKind::Regrant:return "regrant";case MediaCommandKind::Drain:return "drain";case MediaCommandKind::Shutdown:return "shutdown";
    }return "unknown";
}
const char* phase(MediaActorPhase value) {
    switch(value) {
        case MediaActorPhase::Starting:return "starting";case MediaActorPhase::Foreground:return "foreground";case MediaActorPhase::Sealed:return "sealed";
        case MediaActorPhase::Drained:return "drained";case MediaActorPhase::Unknown:return "unknown";case MediaActorPhase::Stopping:return "stopping";case MediaActorPhase::Finished:return "finished";
    }return "unknown";
}
const char* outcome(MediaEventOutcome value) {
    switch(value) {case MediaEventOutcome::Completed:return "completed";case MediaEventOutcome::Suppressed:return "suppressed";
        case MediaEventOutcome::Failed:return "failed";case MediaEventOutcome::Unknown:return "unknown";}return "unknown";
}
std::string binding(const MediaActorBinding& b) {
    return "\"epoch\":"+std::to_string(b.epoch)+",\"resource\":"+json_string(b.resource)+",\"generation\":"+json_string(b.generation);
}
std::string identity(const MediaActorBinding& b) {
    return "{\"kind\":\"media-fixture-v4\","+binding(b)+",\"pid\":"+std::to_string(getpid())+",\"uid\":"+std::to_string(getuid())+",\"profile\":\"lcd-fake-audio\"}";
}
std::string descriptor(const MediaCommand& c) {
    std::ostringstream out;out<<"{\"kind\":"<<json_string(kind(c.kind))<<','<<binding(c.binding)<<",\"request\":"<<c.request
        <<",\"path\":"<<json_string(c.path)<<",\"subtitle\":"<<json_string(c.external_subtitle)<<",\"number\":"<<std::setprecision(17)<<c.number
        <<",\"index\":"<<c.index<<",\"output\":"<<json_string(c.output==Output::Lcd?"lcd":"hdmi")<<",\"flag\":"<<(c.flag?"true":"false")<<'}';return out.str();
}
std::string event(const MediaActorEvent& e) {
    return "{\"kind\":"+json_string(kind(e.kind))+","+binding(e.binding)+",\"request\":"+std::to_string(e.request)+
        ",\"operation\":"+json_string(e.operation)+",\"outcome\":"+json_string(outcome(e.outcome))+
        ",\"graph\":"+(e.outcome==MediaEventOutcome::Completed&&e.graph?e.graph->json():"null")+'}';
}
}
int main(int argc,char** argv) {
    try {
        require(argc==8,"Explicit media fixture bootstrap required");
        MediaActorBinding current{std::stoull(argv[1]),argv[2],argv[3]};std::string lifetime=argv[4],mode=argv[7];
        const int sample_gate=std::stoi(argv[5]),finality_gate=std::stoi(argv[6]);
        for(int fd:{sample_gate,finality_gate})if(fd>=0) {
            const int flags=fcntl(fd,F_GETFD);
            require(flags>=0&&fcntl(fd,F_SETFD,flags|FD_CLOEXEC)==0,"Cannot secure private streaming gate");
        }
        require(current.epoch&&id(current.resource)&&id(current.generation)&&id(lifetime)&&(mode=="ok"||mode=="unqualified"),"Invalid fixed media profile");
        std::atomic<bool> hold_next{false},sample_entered{false},finality_entered{false},finality_used{false};
        std::unique_ptr<MediaActor> actor;
        std::unique_ptr<ActorPlayerControl> control;
        std::map<std::uint64_t,std::pair<std::string,std::string>> permits;
        std::vector<std::pair<MediaCommand,bool>> submissions;
        std::optional<MediaActorBinding> next_binding;std::string next_lifetime;
        emit(identity(current)); // No GStreamer/actor factory exists yet.
        char bytes[8193];
        while(std::cin.getline(bytes,sizeof(bytes))) {
            std::istringstream in(bytes);std::string command;in>>command;
            if(command=="start"&&!actor) {
                const auto initial=current;
                actor=std::make_unique<MediaActor>(initial,lifetime,[&,initial] {
                    PlaybackOptions options;options.fake_audio=true;options.desktop_handoff=false;options.automatic_hdmi_audio=false;options.managed_graph=initial;
                    options.managed_sample_hook=[&] {
                        if(hold_next.exchange(false)) {sample_entered=true;char byte;require(sample_gate>2&&read(sample_gate,&byte,1)==1&&byte=='G',"Sample gate lost");}
                    };
                    options.graph_finality=[&](const GraphDrainEvidence& facts) {
                        finality_entered=true;
                        if(!finality_used.exchange(true)&&finality_gate>2) {char byte;require(read(finality_gate,&byte,1)==1&&byte=='G',"Graph finality gate lost");}
                        return mode=="ok"?facts.binding.generation:std::string("unqualified");
                    };
                    return make_playback_actor_backend(std::move(options));
                });
                control=std::make_unique<ActorPlayerControl>(*actor,current,
                    [&](const MediaCommand& proposed)->std::optional<std::string> {
                        const auto found=permits.find(proposed.request);if(found==permits.end())return {};
                        require(found->second.second==descriptor(proposed),"Permit payload changed before dispatch");
                        auto value=found->second.first;permits.erase(found);return value;
                    },[&](const MediaCommand& c,bool accepted) {require(submissions.size()<64,"Submission mailbox full");submissions.push_back({c,accepted});});
                emit("{\"started\":true}");continue;
            }
            require(bool(actor)&&bool(control),"Media fixture not started");
            if(command=="load") {std::string path,subtitle;in>>std::quoted(path)>>std::quoted(subtitle);emit(std::string("{\"accepted\":")+(control->load(path,subtitle)?"true}":"false}"));}
            else if(command=="pause")emit(std::string("{\"accepted\":")+(control->toggle_pause()?"true}":"false}"));
            else if(command=="seek") {double value;in>>value;emit(std::string("{\"accepted\":")+(control->seek_relative(value)?"true}":"false}"));}
            else if(command=="output") {std::string value;in>>value;require(value=="lcd"||value=="hdmi","Fixed output required");emit(std::string("{\"accepted\":")+(control->set_output(value=="lcd"?Output::Lcd:Output::Hdmi)?"true}":"false}"));}
            else if(command=="volume") {double value;in>>value;control->set_volume(value);emit("{\"accepted\":true}");}
            else if(command=="mute") {int value;in>>value;require(value==0||value==1,"Fixed mute flag required");control->set_muted(value);emit("{\"accepted\":true}");}
            else if(command=="intents") {
                std::string result="[";bool first=true;for(const auto& c:control->awaiting_permits()) {if(!first)result+=',';first=false;result+=descriptor(c);}emit(result+"]");
            } else if(command=="intent-token") {
                std::uint64_t request;in>>request;std::string payload;
                for(const auto& c:control->awaiting_permits())if(c.request==request)payload=descriptor(c);
                require(!payload.empty(),"No matching proposed command");emit("{\"payload\":"+json_string(payload)+'}');
            } else if(command=="permit") {
                std::uint64_t request;std::string operation,payload;in>>request>>operation>>std::quoted(payload);
                require(request&&id(operation)&&!permits.count(request),"Invalid trusted media permit");
                bool matches=false;for(const auto& c:control->awaiting_permits())if(c.request==request&&descriptor(c)==payload)matches=true;
                if(!matches){emit("{\"permit_rejected\":true}");continue;}
                permits[request]={operation,payload};control->poll();
                std::string result="[";bool first=true;for(const auto& entry:submissions) {
                    if(!first)result+=',';first=false;auto data=descriptor(entry.first);data.pop_back();
                    result+=data+",\"operation\":"+json_string(entry.first.operation)+",\"accepted\":"+(entry.second?"true}":"false}");
                }submissions.clear();emit(result+"]");
            } else if(command=="events") {
                control->poll();std::string result="[";bool first=true;for(const auto& e:control->peek_audit()) {if(!first)result+=',';first=false;result+=event(e);}emit(result+"]");
            } else if(command=="ack") {std::size_t count;in>>count;emit(std::string("{\"acknowledged\":")+(control->acknowledge_audit(count)?"true}":"false}"));}
            else if(command=="hold") {require(sample_gate>2,"No sample gate registered");sample_entered=false;hold_next=true;emit("{\"armed\":true}");}
            else if(command=="seal") {std::uint64_t request;std::string operation;in>>request>>operation;emit(std::string("{\"accepted\":")+(control->seal(request,operation)?"true}":"false}"));}
            else if(command=="cancel") {std::uint64_t request;in>>request;emit(std::string("{\"cancelled\":")+(control->cancel(request)?"true}":"false}"));}
            else if(command=="park") {
                const auto s=actor->snapshot();require(actor->epoch_fenced(current),"Old media epoch not positively fenced");
                emit("{"+binding(current)+",\"lifetime_operation\":"+json_string(s->lifetime_operation)+
                    ",\"epoch_fenced\":true,\"unknown\":false,\"active\":false,\"queued\":0,\"events_pending\":0}");
            } else if(command=="prepare-regrant") {
                MediaActorBinding next;in>>next.epoch>>next.resource>>next.generation>>next_lifetime;
                require(next.epoch>current.epoch&&id(next.resource)&&id(next.generation)&&id(next_lifetime),"Invalid fresh fixture binding");next_binding=next;emit(identity(next));
            } else if(command=="regrant") {
                std::uint64_t request;std::string operation;in>>request>>operation;require(bool(next_binding),"Fresh native binding not prepared");
                const bool accepted=control->regrant(*next_binding,next_lifetime,request,operation);
                if(accepted) {current=*next_binding;lifetime=next_lifetime;next_binding.reset();}
                emit(std::string("{\"accepted\":")+(accepted?"true}":"false}"));
            } else if(command=="shutdown") {std::uint64_t request;std::string operation;in>>request>>operation;emit(std::string("{\"accepted\":")+(actor->request_shutdown(current,request,operation)?"true}":"false}"));}
            else if(command=="fence") {
                require(actor->finished()&&!actor->snapshot()->unknown&&actor->join_finished(),"Media actor/backend still outstanding");
                control.reset();actor.reset();emit("{\"finished\":true,\"unknown\":false,\"thread_joined\":true}");return 0;
            } else if(command=="status") {
                control->poll();const auto s=actor->snapshot();const auto& m=s->media;std::ostringstream out;
                out<<"{\"phase\":"<<json_string(phase(s->phase))<<",\"active_operation\":"<<json_string(s->active_operation)
                    <<",\"sealed\":"<<(s->sealed?"true":"false")<<",\"cancelled\":"<<(s->cancelled?"true":"false")
                    <<",\"unknown\":"<<(s->unknown?"true":"false")<<",\"queued\":"<<s->queued<<",\"events_pending\":"<<s->unacknowledged
                    <<",\"sample_entered\":"<<(sample_entered?"true":"false")<<",\"finality_entered\":"<<(finality_entered?"true":"false")
                    <<",\"loaded\":"<<(m.status.loaded?"true":"false")<<",\"playing\":"<<(m.status.playing?"true":"false")
                    <<",\"paused\":"<<(m.status.paused?"true":"false")<<",\"buffering\":"<<(m.status.buffering?"true":"false")
                    <<",\"position\":"<<std::setprecision(17)<<m.status.position<<",\"volume\":"<<m.status.volume
                    <<",\"muted\":"<<(m.status.muted?"true":"false")<<",\"frame\":"<<(m.frame?"true":"false")
                    <<",\"audio_track\":"<<m.status.audio_track<<",\"subtitle_track\":"<<m.status.subtitle_track
                    <<",\"fallbacks\":"<<m.status.video_decode_fallbacks<<",\"checkpoint\":";
                if(m.checkpoint) {const auto& c=*m.checkpoint;out<<"{\"position\":"<<c.position<<",\"playing\":"<<(c.playing?"true":"false")
                    <<",\"volume\":"<<c.volume<<",\"muted\":"<<(c.muted?"true":"false")<<",\"audio_track\":"<<c.audio_track
                    <<",\"subtitle_track\":"<<c.subtitle_track<<",\"fallbacks\":"<<c.video_decode_fallbacks<<'}';}else out<<"null";
                out<<'}';emit(out.str());
            } else throw std::runtime_error("Unknown fixed media fixture command");
        }
        // EOF/destruction can block while accepted work drains. It remains an
        // abandoned lifetime, never a positive ownership receipt.
        return 3;
    } catch(const std::exception& error) {std::cerr<<"Media fixture: "<<error.what()<<'\n';emit("{\"failed\":true}");return 2;}
}
