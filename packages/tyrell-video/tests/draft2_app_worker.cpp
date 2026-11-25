// Fixed qualification-kit peer: actual App/LVGL with production LCD capture
// and fake audio. No framebuffer, keyboard, HDMI, VT or user Pulse endpoint.
#define main legacy_video_main
#include "../src/main.cpp"
#undef main
#include "draft2_link.h"
#include <gst/gst.h>
#include <thread>
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
namespace {
std::uint64_t ui_flushes = 0;
const std::thread::id ui_thread = std::this_thread::get_id();
const char* link_state(Draft2State value) {
    switch(value) {case Draft2State::AwaitWelcome:return "await-welcome";case Draft2State::AwaitGrant:return "await-grant";
        case Draft2State::AwaitReady:return "await-ready";case Draft2State::Foreground:return "foreground";
        case Draft2State::Draining:return "draining";case Draft2State::Hidden:return "hidden";case Draft2State::Lost:return "lost";}
    return "lost";
}
const char* lifecycle_kind(Draft2EventKind value) {
    switch(value) {case Draft2EventKind::Welcome:return "welcome";case Draft2EventKind::Grant:return "grant";
        case Draft2EventKind::Quiesce:return "quiesce";case Draft2EventKind::Cancel:return "cancel";
        case Draft2EventKind::Commit:return "commit";case Draft2EventKind::Failed:return "failed";case Draft2EventKind::Lost:return "lost";}
    return "lost";
}
}
int main(int argc,char** argv) {
    try {
        if(argc==2&&std::string(argv[1])=="--qualification-info") {
            emit("{\"schema\":1,\"kind\":\"draft2-app-v5\",\"profile\":\"memory-lvgl-lcd-fake-audio\",\"live_admission\":false,\"hardware_qualified\":false}");return 0;
        }
        require(argc==9,"Explicit qualification bootstrap required");
        Draft2Bootstrap bootstrap{std::stoi(argv[1]),static_cast<std::uint32_t>(std::stoul(argv[2])),std::stoi(argv[3]),
            static_cast<std::uint32_t>(std::stoul(argv[4])),argv[5]};
        const int sample_gate=std::stoi(argv[6]),finality_gate=std::stoi(argv[7]);const std::string mode=argv[8];
        require(getuid()>0&&bootstrap.expected_uid==getuid()&&bootstrap.creator_uid==getuid()&&id(bootstrap.app_instance)&&
            (mode=="ok"||mode=="unqualified"),"Normal-user fixed qualification profile required");
        require(bootstrap.descriptor>2&&(sample_gate==-1||sample_gate>2)&&(finality_gate==-1||finality_gate>2)&&
            bootstrap.descriptor!=sample_gate&&bootstrap.descriptor!=finality_gate&&(sample_gate<0||sample_gate!=finality_gate),
            "Exclusive private qualification descriptors required");
        for(int fd:{sample_gate,finality_gate})if(fd>=0) {
            const int flags=fcntl(fd,F_GETFD);require(flags>=0&&fcntl(fd,F_SETFD,flags|FD_CLOEXEC)==0,"Cannot secure private streaming gate");
        }
        std::atomic<bool> hold_next{false},sample_entered{false},finality_entered{false},finality_used{false};
        std::unique_ptr<Draft2Link> link;
        std::unique_ptr<MediaActor> actor;
        std::unique_ptr<App> app;
        ActorPlayerControl* control=nullptr;
        lv_display_t* display=nullptr;
        std::vector<std::uint32_t> pixels(width*height);
        MediaActorBinding current;std::string lifetime;
        std::optional<MediaActorBinding> next_binding;std::string next_lifetime;
        std::map<std::uint64_t,std::pair<std::string,std::string>> permits;
        std::vector<std::pair<MediaCommand,bool>> submissions;
        std::deque<Draft2Event> lifecycle_events;
        emit("{\"kind\":\"draft2-app-v5\",\"pid\":"+std::to_string(getpid())+",\"uid\":"+std::to_string(getuid())+
            ",\"profile\":\"memory-lvgl-lcd-fake-audio\",\"live_admission\":false,\"hardware_qualified\":false}");
        auto service_lifecycle=[&] {
            if(!link)return;
            for(const auto& e:link->pump()) {
                require(lifecycle_events.size()<64,"Bounded lifecycle event mailbox exhausted");
                lifecycle_events.push_back(e);
                if(control&&(e.kind==Draft2EventKind::Quiesce||e.kind==Draft2EventKind::Lost||e.kind==Draft2EventKind::Failed))control->revoke_admission();
                if(control&&e.kind==Draft2EventKind::Cancel)control->cancel(e.request);
            }
        };
        auto tick=[&] {
            service_lifecycle();
            if(!app)return;
            app->poll();
            service_lifecycle();
            if(link->render_allowed()&&control->render_allowed()) {app->render();lv_refr_now(display);}
        };
        auto dispose_ui=[&] {
            if(app){app->playback.reset();control=nullptr;app->frame_image.clear(app->image);app.reset();}
            if(display){lv_display_delete(display);display=nullptr;lv_deinit();}
        };
        char bytes[16385];
        while(std::cin.getline(bytes,sizeof(bytes))) {
            std::istringstream in(bytes);std::string command;in>>command;
            if(command=="start"&&!link) {
                std::string error;link=Draft2Link::adopt(bootstrap,error);require(bool(link),error);emit("{\"started\":true}");continue;
            }
            require(bool(link),"Lifecycle bootstrap not adopted");
            service_lifecycle(); // Lifecycle revocation always wins over a queued UI decision.
            if(command=="pump") {
                std::string result="[";bool first=true;
                for(const auto& e:lifecycle_events) {
                    if(!first)result+=',';first=false;
                    result+="{\"kind\":"+json_string(lifecycle_kind(e.kind))+",\"epoch\":"+std::to_string(e.grant.epoch)+
                        ",\"request\":"+std::to_string(e.request)+",\"receipt\":"+json_string(e.receipt)+",\"error\":"+json_string(e.error)+'}';
                }
                lifecycle_events.clear();emit(result+"]");continue;
            }
            if(command=="prepare"||command=="prepare-regrant") {
                MediaActorBinding next;in>>next.epoch>>next.resource>>next.generation>>next_lifetime;
                require(link->state()==Draft2State::AwaitReady&&link->grant()&&link->grant()->epoch==next.epoch&&next.epoch>current.epoch&&
                    id(next.resource)&&id(next.generation)&&id(next_lifetime),"Fresh authenticated lifecycle grant required");
                next_binding=next;emit(identity(next));continue;
            }
            if(command=="bind"&&!actor) {
                require(bool(next_binding)&&link->state()==Draft2State::AwaitReady,"Durable media binding not prepared");
                current=*next_binding;lifetime=next_lifetime;next_binding.reset();
                lv_init();lv_tick_set_cb(tick_ms);display=lv_display_create(width,height);
                require(display!=nullptr,"Cannot create qualification memory display");
                lv_display_set_color_format(display,LV_COLOR_FORMAT_XRGB8888);
                lv_display_set_buffers(display,pixels.data(),nullptr,pixels.size()*sizeof(pixels[0]),LV_DISPLAY_RENDER_MODE_DIRECT);
                lv_display_set_flush_cb(display,[](lv_display_t* value,const lv_area_t*,std::uint8_t*) {
                    require(std::this_thread::get_id()==ui_thread,"Media worker entered LVGL");++ui_flushes;lv_display_flush_ready(value);
                });
                lv_timer_pause(lv_display_get_refr_timer(display));
                const auto initial=current;
                actor=std::make_unique<MediaActor>(initial,lifetime,[&,initial] {
                    PlaybackOptions options;options.fake_audio=true;options.desktop_handoff=false;options.automatic_hdmi_audio=false;options.managed_graph=initial;
                    options.managed_sample_hook=[&] {if(hold_next.exchange(false)) {sample_entered=true;char byte;require(sample_gate>2&&read(sample_gate,&byte,1)==1&&byte=='G',"Sample gate lost");}};
                    options.graph_finality=[&](const GraphDrainEvidence& facts) {
                        finality_entered=true;
                        if(!finality_used.exchange(true)&&finality_gate>2) {char byte;require(read(finality_gate,&byte,1)==1&&byte=='G',"Graph finality gate lost");}
                        return mode=="ok"?facts.binding.generation:std::string("unqualified");
                    };
                    return make_playback_actor_backend(std::move(options));
                });
                Options options;options.media_root=std::filesystem::current_path();
                app=std::make_unique<App>(options);
                app->foreground_allowed=[&]{return link->input_allowed();};
                auto facade=std::make_unique<ActorPlayerControl>(*actor,current,
                    [&](const MediaCommand& proposed)->std::optional<std::string> {
                        const auto found=permits.find(proposed.request);if(found==permits.end())return {};
                        require(found->second.second==descriptor(proposed),"Permit payload changed before dispatch");
                        auto value=found->second.first;permits.erase(found);return value;
                    },[&](const MediaCommand& c,bool accepted){require(submissions.size()<64,"Submission mailbox full");submissions.push_back({c,accepted});});
                control=facade.get();app->playback=std::move(facade);emit("{\"bound\":true}");continue;
            }
            if(command=="link-status") {
                emit("{\"state\":"+json_string(link_state(link->state()))+",\"input_allowed\":"+(link->input_allowed()?"true":"false")+
                    ",\"render_allowed\":"+(link->render_allowed()?"true":"false")+",\"worker_exists\":"+(actor?"true":"false")+
                    ",\"gst_initialized\":"+(gst_is_initialized()?"true":"false")+",\"ui_flushes\":"+std::to_string(ui_flushes)+'}');continue;
            }
            require(bool(actor)&&bool(control),"Media factory not admitted");
            if(command=="ready") {
                const auto state=actor->snapshot();
                require(state->phase==MediaActorPhase::Foreground&&!state->unknown&&!state->media.status.buffering&&
                    state->media.status.error.empty()&&(!state->media.status.loaded||state->media.frame),"Rebuild/preroll not complete");
                const bool accepted=link->ready();if(accepted){++app->input_epoch;app->rebuild=true;tick();}
                emit(std::string("{\"ready\":")+(accepted?"true}":"false}"));
            } else if(command=="load") {
                std::string path,subtitle;in>>std::quoted(path)>>std::quoted(subtitle);
                bool accepted=link->input_allowed()&&control->input_allowed();
                const auto waiting=control->awaiting_permits().size();
                if(accepted){app->start_file(path);accepted=control->awaiting_permits().size()>waiting;}
                emit(std::string("{\"accepted\":")+(accepted?"true}":"false}"));
            } else if(command=="pause") {
                bool accepted=link->input_allowed()&&control->input_allowed();const auto waiting=control->awaiting_permits().size();
                if(accepted){app->key(' ');accepted=control->awaiting_permits().size()>waiting;}
                emit(std::string("{\"accepted\":")+(accepted?"true}":"false}"));
            } else if(command=="seek") {
                double value;in>>value;emit(std::string("{\"accepted\":")+(link->input_allowed()&&control->seek_relative(value)?"true}":"false}"));
            } else if(command=="volume") {
                double value;in>>value;require(link->input_allowed(),"UI foreground revoked");control->set_volume(value);emit("{\"accepted\":true}");
            } else if(command=="mute") {
                int value;in>>value;require((value==0||value==1)&&link->input_allowed(),"Fixed foreground mute required");control->set_muted(value);emit("{\"accepted\":true}");
            } else if(command=="intents") {
                std::string result="[";bool first=true;for(const auto& c:control->awaiting_permits()){if(!first)result+=',';first=false;result+=descriptor(c);}emit(result+"]");
            } else if(command=="intent-token") {
                std::uint64_t request;in>>request;std::string payload;for(const auto& c:control->awaiting_permits())if(c.request==request)payload=descriptor(c);
                require(!payload.empty(),"No matching proposed command");emit("{\"payload\":"+json_string(payload)+'}');
            } else if(command=="permit") {
                std::uint64_t request;std::string operation,payload;in>>request>>operation>>std::quoted(payload);
                require(request&&id(operation)&&!permits.count(request),"Invalid trusted media permit");
                bool matches=false;for(const auto& c:control->awaiting_permits())if(c.request==request&&descriptor(c)==payload)matches=true;
                if(!matches){emit("{\"permit_rejected\":true}");continue;}
                permits[request]={operation,payload};tick();std::string result="[";bool first=true;
                for(const auto& entry:submissions){if(!first)result+=',';first=false;auto data=descriptor(entry.first);data.pop_back();
                    result+=data+",\"operation\":"+json_string(entry.first.operation)+",\"accepted\":"+(entry.second?"true}":"false}");}
                submissions.clear();emit(result+"]");
            } else if(command=="events") {
                tick();std::string result="[";bool first=true;for(const auto& e:control->peek_audit()){if(!first)result+=',';first=false;result+=event(e);}emit(result+"]");
            } else if(command=="ack") {
                std::size_t count;in>>count;emit(std::string("{\"acknowledged\":")+(control->acknowledge_audit(count)?"true}":"false}"));
            } else if(command=="hold") {
                require(sample_gate>2,"No sample gate registered");sample_entered=false;hold_next=true;emit("{\"armed\":true}");
            } else if(command=="seal") {
                std::uint64_t request;std::string operation;in>>request>>operation;
                const bool accepted=link->state()==Draft2State::Draining&&link->drain_request()==request&&control->seal(request,operation)&&link->local_done();
                emit(std::string("{\"accepted\":")+(accepted?"true}":"false}"));
            } else if(command=="park") {
                const auto s=actor->snapshot();require(actor->epoch_fenced(current),"Old media epoch not positively fenced");
                emit("{"+binding(current)+",\"lifetime_operation\":"+json_string(s->lifetime_operation)+
                    ",\"epoch_fenced\":true,\"unknown\":false,\"active\":false,\"queued\":0,\"events_pending\":0}");
            } else if(command=="regrant") {
                std::uint64_t request;std::string operation;in>>request>>operation;require(bool(next_binding)&&link->state()==Draft2State::AwaitReady,"Fresh lifecycle binding not prepared");
                const bool accepted=control->regrant(*next_binding,next_lifetime,request,operation);
                if(accepted){current=*next_binding;lifetime=next_lifetime;next_binding.reset();}
                emit(std::string("{\"accepted\":")+(accepted?"true}":"false}"));
            } else if(command=="shutdown") {
                std::uint64_t request;std::string operation;in>>request>>operation;emit(std::string("{\"accepted\":")+(actor->request_shutdown(current,request,operation)?"true}":"false}"));
            } else if(command=="fence") {
                require(actor->finished()&&!actor->snapshot()->unknown&&actor->join_finished(),"Media actor/backend still outstanding");
                dispose_ui();actor.reset();emit("{\"finished\":true,\"unknown\":false,\"thread_joined\":true}");return 0;
            } else if(command=="exit-parked") {
                require(link->state()==Draft2State::Hidden&&actor->epoch_fenced(current),"Composite commit and parked actor required before exit");
                dispose_ui();actor.reset();emit("{\"parked_exit\":true}");return 0;
            } else if(command=="status") {
                tick();const auto s=actor->snapshot();const auto& m=s->media;std::ostringstream out;
                out<<"{\"phase\":"<<json_string(phase(s->phase))<<",\"link_state\":"<<json_string(link_state(link->state()))
                    <<",\"input_allowed\":"<<(link->input_allowed()&&control->input_allowed()?"true":"false")
                    <<",\"render_allowed\":"<<(link->render_allowed()&&control->render_allowed()?"true":"false")
                    <<",\"ui_flushes\":"<<ui_flushes<<",\"frame_assignments\":"<<app->lcd_frame_assignments<<",\"input_epoch\":"<<app->input_epoch
                    <<",\"active_operation\":"<<json_string(s->active_operation)<<",\"sealed\":"<<(s->sealed?"true":"false")
                    <<",\"cancelled\":"<<(s->cancelled?"true":"false")<<",\"unknown\":"<<(s->unknown?"true":"false")
                    <<",\"queued\":"<<s->queued<<",\"events_pending\":"<<s->unacknowledged
                    <<",\"sample_entered\":"<<(sample_entered?"true":"false")<<",\"finality_entered\":"<<(finality_entered?"true":"false")
                    <<",\"loaded\":"<<(m.status.loaded?"true":"false")<<",\"playing\":"<<(m.status.playing?"true":"false")
                    <<",\"paused\":"<<(m.status.paused?"true":"false")<<",\"buffering\":"<<(m.status.buffering?"true":"false")
                    <<",\"position\":"<<std::setprecision(17)<<m.status.position<<",\"volume\":"<<m.status.volume
                    <<",\"muted\":"<<(m.status.muted?"true":"false")<<",\"frame\":"<<(m.frame?"true":"false")
                    <<",\"audio_track\":"<<m.status.audio_track<<",\"subtitle_track\":"<<m.status.subtitle_track
                    <<",\"fallbacks\":"<<m.status.video_decode_fallbacks<<",\"checkpoint\":";
                if(m.checkpoint){const auto& c=*m.checkpoint;out<<"{\"position\":"<<c.position<<",\"playing\":"<<(c.playing?"true":"false")
                    <<",\"volume\":"<<c.volume<<",\"muted\":"<<(c.muted?"true":"false")<<",\"audio_track\":"<<c.audio_track
                    <<",\"subtitle_track\":"<<c.subtitle_track<<",\"fallbacks\":"<<c.video_decode_fallbacks<<'}';}else out<<"null";
                out<<'}';emit(out.str());
            } else throw std::runtime_error("Unknown fixed qualification command");
        }
        // EOF is abandonment. No last redraw, synthetic release or detached
        // worker; a supervisor retains unknown work and may terminate this peer.
        dispose_ui();return 3;
    } catch(const std::exception& error){std::cerr<<"Draft-2 qualification peer: "<<error.what()<<'\n';emit("{\"failed\":true}");return 2;}
}
