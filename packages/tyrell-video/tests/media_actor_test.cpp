// Exercise the production actor, including real Playback/GStreamer. No display,
// audio server, shell helper or handheld device is opened by these profiles.
#include "media_actor.h"
#include "json_string.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <limits>
#include <mutex>
#include <poll.h>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <vector>
#include <fcntl.h>
#include <unistd.h>

using namespace tyrell::video;
using Clock=std::chrono::steady_clock;
namespace {
void require(bool condition,const std::string& message) { if(!condition)throw std::runtime_error(message); }
std::string op(unsigned value) { std::ostringstream out;out<<std::hex<<std::setfill('0')<<std::setw(32)<<value;return out.str(); }
MediaActorBinding binding(unsigned epoch=1) { return {epoch,op(10000+epoch),op(20000+epoch)}; }
MediaCommand command(MediaCommandKind kind,unsigned request,unsigned operation,MediaActorBinding scope=binding()) {
    MediaCommand c;c.kind=kind;c.binding=std::move(scope);c.request=request;c.operation=op(operation);return c;
}
void until(const std::function<bool()>& predicate,const char* message,std::chrono::seconds timeout=std::chrono::seconds(8)) {
    const auto deadline=Clock::now()+timeout;
    while(!predicate()&&Clock::now()<deadline)std::this_thread::sleep_for(std::chrono::milliseconds(1));
    require(predicate(),message);
}
struct Gate {
    std::mutex mutex;std::condition_variable condition;
    bool enabled=false,entered=false,released=false;
    void wait() {
        std::unique_lock<std::mutex> lock(mutex);
        if(!enabled)return;
        entered=true;condition.notify_all();condition.wait(lock,[&]{return released;});
    }
    bool started() { std::lock_guard<std::mutex> lock(mutex);return entered; }
    void release() { std::lock_guard<std::mutex> lock(mutex);released=true;condition.notify_all(); }
};
struct State {
    Gate execute,poll,drain;
    std::mutex mutex;
    std::vector<std::string> calls;
    std::thread::id owner;
    std::atomic<unsigned> poll_calls{0},destructed{0};
    bool invalid_drain=false,throw_execute=false,wrong_drain_operation=false,stale_drain_binding=false,large_metadata=false,external_after_stop=false;
    void call(std::string name) {
        std::lock_guard<std::mutex> lock(mutex);
        if(owner==std::thread::id{})owner=std::this_thread::get_id();
        require(owner==std::this_thread::get_id(),"Backend escaped its single owner");
        calls.push_back(std::move(name));
    }
    std::size_t count(const std::string& name) {
        std::lock_guard<std::mutex> lock(mutex);return std::count(calls.begin(),calls.end(),name);
    }
    void release() {execute.release();poll.release();drain.release();}
};
struct Release {
    std::function<void()> run;
    ~Release(){run();}
};
class FakeBackend final:public MediaActorBackend {
public:
    explicit FakeBackend(std::shared_ptr<State> state,MediaActorBinding scope):state_(std::move(state)),binding_(std::move(scope)) {
        state_->call("construct");
    }
    ~FakeBackend()override {state_->call("destroy");++state_->destructed;}
    bool execute(const MediaCommand& c,std::string&)override {
        state_->call("execute");
        state_->execute.wait();
        if(state_->throw_execute)throw std::runtime_error("Controlled graph command uncertainty");
        if(c.kind==MediaCommandKind::Load){media_.status.loaded=true;media_.status.path=c.path;media_.resources.pipeline_present=true;}
        if(c.kind==MediaCommandKind::Seek)media_.status.position=c.number;
        if(c.kind==MediaCommandKind::Volume)media_.status.volume=c.number;
        return true;
    }
    void poll()override {state_->call("poll");++state_->poll_calls;state_->poll.wait();}
    MediaBackendSnapshot snapshot()const override {
        state_->call("snapshot");auto value=media_;
        if(state_->large_metadata) {
            value.status.audio_tracks.resize(200);
            for(auto& track:value.status.audio_tracks){track.title=std::string(4096,'T');track.language=std::string(512,'L');}
            value.status.error=std::string(32768,'E');
        }
        return value;
    }
    std::optional<GraphDrainEvidence> drain(const std::string& operation,std::string&)override {
        state_->call("drain");state_->drain.wait();
        media_.status.suspended=true;media_.resources={};media_.frame.reset();
        GraphDrainEvidence e;e.binding=binding_;e.operation=operation;e.graph_serial=1;
        e.callbacks_started=e.callbacks_completed=4;e.pipeline_finalized=e.local_handles_released=e.legacy_helpers_unused=true;
        e.finality_receipt=op(999);if(state_->invalid_drain)e.live_objects=1;
        if(state_->wrong_drain_operation)e.operation=op(9999);
        if(state_->stale_drain_binding)e.binding=binding(99);
        return e;
    }
    bool regrant(const MediaActorBinding& scope,std::string&)override {
        state_->call("regrant");binding_=scope;media_.status.suspended=false;return true;
    }
    void stop()override {state_->call("stop");media_.status.loaded=false;media_.resources={};media_.resources.external_cleanup_unconfirmed=state_->external_after_stop;}
private:
    std::shared_ptr<State> state_;
    MediaActorBinding binding_;
    mutable MediaBackendSnapshot media_;
};
MediaActor::Factory fake(std::shared_ptr<State> state) {
    return [state]{return std::make_unique<FakeBackend>(state,binding());};
}
void ready(MediaActor& actor) {until([&]{return actor.snapshot()->phase==MediaActorPhase::Foreground;},"Actor did not initialize");}
void shutdown(MediaActor& actor,unsigned operation=9000) {
    const auto scope=actor.snapshot()->binding;
    require(actor.request_shutdown(scope,operation,op(operation)),"Actor shutdown refused reserved permit");
    until([&]{return actor.finished();},"Actor did not finish shutdown");
    require(actor.join_finished(),"Finished actor could not be joined");
}

void held_command_seal_and_regrant() {
    auto state=std::make_shared<State>();state->execute.enabled=state->drain.enabled=true;
    MediaActor actor(binding(),op(100),fake(state));
    Release release{[&]{state->release();}};
    ready(actor);
    require(fcntl(actor.wake_fd(),F_GETFD)&FD_CLOEXEC,"Wake descriptor can leak into helpers");
    auto load=command(MediaCommandKind::Load,1,1);load.path="accepted-before-seal";
    require(actor.submit(load),"Load not queued");
    until([&]{return state->execute.started();},"Controlled load did not block");
    for(unsigned n=2;n<=32;++n)require(actor.submit(command(MediaCommandKind::Pause,n,n)),"Queue filled before declared bound");
    require(!actor.submit(command(MediaCommandKind::Pause,33,33)),"Bounded queue accepted overflow");
    const auto before=Clock::now();
    require(actor.seal(binding(),50,op(200))&&actor.cancel(binding(),50),"Seal/cancel refused");
    require(actor.seal(binding(),50,op(200))&&actor.cancel(binding(),50),"Matching replay repeated/refused drain");
    require(!actor.submit(command(MediaCommandKind::Seek,34,34)),"Sealed graph accepted controls");
    const auto held=actor.snapshot();
    require(held->sealed&&held->cancelled&&held->active_operation==op(1)&&!held->media.frame&&!actor.join_finished(),
            "Control query retired or waited on held command");
    require(Clock::now()-before<std::chrono::milliseconds(500),"Control path waited for blocked media");
    pollfd wake{actor.wake_fd(),POLLIN,0};require(poll(&wake,1,0)==1,"Accepted work did not wake control");actor.consume_wake();
    const auto suppressed=actor.peek_events();require(suppressed.size()==31,"Queued controls lost suppression evidence");
    for(const auto& e:suppressed)require(e.outcome==MediaEventOutcome::Suppressed,"Unstarted control was executed");
    state->execute.release();
    until([&]{return state->drain.started();},"Drain did not follow active command");
    require(actor.snapshot()->active_operation==op(200)&&actor.snapshot()->phase==MediaActorPhase::Sealed,"Drain permit retired while backend blocked");
    require(state->count("execute")==1,"Queued commands escaped seal");
    const auto terminal=actor.peek_events();require(terminal.size()==32,"Active result or suppression disappeared");
    require(actor.acknowledge_events(terminal.size()),"Terminal prefix acknowledgment failed");
    state->drain.release();
    until([&]{return actor.snapshot()->phase==MediaActorPhase::Drained;},"Positive graph did not drain");
    require(actor.snapshot()->graph&&actor.snapshot()->graph->operation==op(200),"Drain lost actual operation binding");
    require(!actor.epoch_fenced(binding()),"Unacknowledged drain became a parked epoch fence");
    require(!actor.regrant(binding(2),op(300),1,op(301)),"New grant discarded unacknowledged old receipt");
    require(actor.take_events().size()==1,"Drain event was not retained");
    require(actor.epoch_fenced(binding())&&!actor.epoch_fenced(binding(2)),"Parked epoch fence lost exact binding or remains active");
    require(actor.regrant(binding(2),op(300),1,op(301)),"Fresh graph grant refused");
    until([&]{return actor.snapshot()->phase==MediaActorPhase::Foreground;},"Fresh grant did not rebuild");
    require(!actor.submit(command(MediaCommandKind::Play,100,2)),"Stale epoch was accepted");
    shutdown(actor);
    require(state->destructed==1&&state->owner!=std::this_thread::get_id(),"Factory/destructor did not belong to media thread");
    require(state->count("drain")==2&&state->count("regrant")==1,"Cancellation resumed/repeated an old graph");
}

void automatic_work_is_held_and_polling_stops_after_seal() {
    auto state=std::make_shared<State>();state->poll.enabled=true;
    MediaActor actor(binding(),op(100),fake(state));Release release{[&]{state->release();}};
    until([&]{return state->poll.started();},"Automatic backend poll did not start");
    require(actor.snapshot()->active_operation==op(100),"Automatic rebuild/poll lost lifetime permit");
    require(actor.submit(command(MediaCommandKind::Seek,1,1)),"Control enqueue during poll failed");
    require(actor.seal(binding(),2,op(2)),"Seal raced automatic poll");
    require(actor.snapshot()->sealed&&!actor.snapshot()->graph,"Poll completion was invented");
    state->poll.release();
    until([&]{return actor.snapshot()->phase==MediaActorPhase::Drained;},"Held automatic work did not drain");
    const unsigned polls=state->poll_calls;
    std::this_thread::sleep_for(std::chrono::milliseconds(30));
    require(state->poll_calls==polls&&state->count("execute")==0,"Sealed actor polled/rebuilt or executed stale seek");
    shutdown(actor);
}

void malformed_completion_and_exceptions_remain_unknown() {
    for(bool throwing:{false,true}) {
        auto state=std::make_shared<State>();state->invalid_drain=!throwing;state->throw_execute=throwing;
        MediaActor actor(binding(),op(100),fake(state));Release release{[&]{state->release();}};ready(actor);
        if(throwing)require(actor.submit(command(MediaCommandKind::Play,1,1)),"Exception fixture not accepted");
        else require(actor.seal(binding(),1,op(1)),"Malformed proof fixture not accepted");
        until([&]{return actor.snapshot()->phase==MediaActorPhase::Unknown;},"Incomplete callback/object proof was accepted");
        require(actor.snapshot()->unknown&&!actor.regrant(binding(2),op(20),2,op(21)),"Unknown epoch regained rights");
        shutdown(actor);
        bool found=false;for(const auto& e:actor.peek_events())if(e.kind==MediaCommandKind::Shutdown)found=e.outcome==MediaEventOutcome::Unknown;
        require(found,"Shutdown or destruction erased prior uncertainty");
    }
}

void finite_history_preserves_cleanup_and_validates_commands() {
    auto state=std::make_shared<State>();
    MediaActor actor(binding(),op(2000),fake(state));Release release{[&]{state->release();}};ready(actor);
    for(auto kind:{MediaCommandKind::Drain,MediaCommandKind::Regrant,MediaCommandKind::Shutdown,static_cast<MediaCommandKind>(999)})
        require(!actor.submit(command(kind,1,1)),"Generic submit bypassed lifecycle gate");
    auto invalid=command(MediaCommandKind::Seek,1,1);invalid.number=std::numeric_limits<double>::quiet_NaN();
    require(!actor.submit(invalid),"NaN entered graph");
    invalid=command(MediaCommandKind::Pause,0,1);require(!actor.submit(invalid),"Zero request entered graph");
    invalid=command(MediaCommandKind::Pause,1,1);invalid.operation=std::string(32,'A');require(!actor.submit(invalid),"Malformed operation entered graph");
    unsigned accepted=0;
    for(unsigned n=1;n<=1100;++n) {
        if(!actor.submit(command(MediaCommandKind::Pause,n,n)))break;
        ++accepted;until([&]{return !actor.peek_events().empty();},"History fixture did not finish accepted operation");
        const auto events=actor.peek_events();require(actor.acknowledge_events(events.size()),"History prefix ack failed");
    }
    require(accepted>=900&&accepted<MediaActor::operation_history_limit,"History was unbounded or discarded old operation IDs");
    require(actor.seal(binding(),5000,op(5000)),"History exhaustion consumed cleanup reserve");
    until([&]{return actor.snapshot()->phase==MediaActorPhase::Drained;},"Reserved drain did not complete");
    shutdown(actor,6000);
}

void shutdown_never_joins_an_active_backend_call() {
    auto state=std::make_shared<State>();state->drain.enabled=true;
    MediaActor actor(binding(),op(100),fake(state));Release release{[&]{state->release();}};ready(actor);
    require(actor.request_shutdown(binding(),1,op(1)),"Shutdown request blocked/refused");
    until([&]{return state->drain.started();},"Shutdown did not retain graph drain");
    require(!actor.finished()&&!actor.join_finished()&&actor.snapshot()->active_operation==op(1)&&state->destructed==0,
            "Shutdown joined or destroyed a held graph on control thread");
    state->drain.release();until([&]{return actor.finished();},"Shutdown did not finish after backend proof");
    require(state->destructed==1&&actor.join_finished(),"Finished was published before owner-thread backend destruction");
}

void shutdown_refuses_unrelated_graph_receipts() {
    for(bool stale:{false,true}) {
        auto state=std::make_shared<State>();state->wrong_drain_operation=!stale;state->stale_drain_binding=stale;
        MediaActor actor(binding(),op(100),fake(state));Release release{[&]{state->release();}};ready(actor);
        shutdown(actor);
        require(actor.snapshot()->unknown&&!actor.snapshot()->graph,"Shutdown certified or cached a foreign operation/binding");
        const auto events=actor.peek_events();
        require(events.size()==1&&events[0].kind==MediaCommandKind::Shutdown&&events[0].outcome==MediaEventOutcome::Unknown,
                "Malformed shutdown proof became positive resource completion");
    }
}

void publication_metadata_is_bounded_before_control_lock() {
    auto state=std::make_shared<State>();state->large_metadata=true;
    MediaActor actor(binding(),op(100),fake(state));Release release{[&]{state->release();}};ready(actor);
    const auto snapshot=actor.snapshot();
    require(snapshot->media.status.audio_tracks.size()==64&&snapshot->media.status.audio_tracks[0].title.size()==256&&
        snapshot->media.status.audio_tracks[0].language.size()==64&&snapshot->media.status.error.size()==4096,
        "Unbounded backend metadata entered control publication");
    require(actor.seal(binding(),1,op(1)),"Metadata publication blocked control seal");
    until([&]{return actor.snapshot()->phase==MediaActorPhase::Drained;},"Metadata fixture did not drain");shutdown(actor);
}

void shutdown_does_not_erase_new_external_cleanup_uncertainty() {
    auto state=std::make_shared<State>();state->external_after_stop=true;
    MediaActor actor(binding(),op(100),fake(state));Release release{[&]{state->release();}};ready(actor);
    shutdown(actor);
    const auto snapshot=actor.snapshot();
    require(snapshot->unknown&&snapshot->media.resources.external_cleanup_unconfirmed,
            "Backend shutdown discarded unconfirmed external work");
    require(actor.peek_events().back().outcome==MediaEventOutcome::Unknown,"Unconfirmed external cleanup became completion");
}

void sealed_pending_regrant_cannot_reuse_the_old_graph_receipt() {
    auto state=std::make_shared<State>();
    MediaActor actor(binding(),op(100),fake(state));Release release{[&]{state->release();}};ready(actor);
    require(actor.seal(binding(),1,op(1)),"Old graph seal refused");
    until([&]{return actor.snapshot()->phase==MediaActorPhase::Drained;},"Old graph did not drain");actor.take_events();
    require(actor.epoch_fenced(binding()),"Old parked graph is not fenced");
    require(actor.regrant(binding(2),op(200),1,op(201)),"Fresh pending rebuild refused");
    require(!actor.epoch_fenced(binding()),"Fresh grant retained old epoch rights");
    require(actor.seal(binding(2),2,op(202)),"Pending rebuild could not be sealed");
    require(actor.request_shutdown(binding(2),3,op(203)),"Pending rebuild shutdown refused");
    until([&]{return actor.finished();},"Pending rebuild did not finish containment");require(actor.join_finished(),"Cannot join contained rebuild");
    const auto snapshot=actor.snapshot();
    require(!snapshot->graph||snapshot->graph->binding.epoch==2,"Old graph receipt escaped as a new-epoch proof");
    for(const auto& event:actor.peek_events())if(event.outcome==MediaEventOutcome::Completed&&event.graph)
        require(event.graph->binding.epoch==2&&event.graph->binding.resource==binding(2).resource,
                "Suppressed pending rebuild retired its new lifetime with old proof");
    // If the worker selected Regrant before seal, it may finish under its held
    // permit. If it was still queued, suppression leaves the backend parked in
    // the old epoch and the new epoch conservatively stays unknown.
    require(snapshot->unknown||state->count("regrant")==1,"Unexecuted rebuild manufactured fresh completion");
}

PlaybackOptions playback_options(MediaActorBinding scope) {
    PlaybackOptions options;options.fake_audio=true;options.desktop_handoff=false;options.automatic_hdmi_audio=false;
    options.managed_graph=scope;options.graph_finality=[](const GraphDrainEvidence&){return op(999);};return options;
}
void actual_ready(MediaActor& actor,const std::function<bool(const MediaActorSnapshot&)>& predicate,const char* message) {
    until([&]{const auto snapshot=actor.snapshot();return snapshot->phase==MediaActorPhase::Unknown||predicate(*snapshot);},message,std::chrono::seconds(15));
    require(!actor.snapshot()->unknown,message+std::string(": ")+actor.snapshot()->error);
}
GraphDrainEvidence actual_checkpoint_regrant(const std::string& fixture) {
    char pattern[]="/tmp/tyrell-actor-media-XXXXXX";const char* directory=mkdtemp(pattern);require(directory,"Cannot create actor fixture directory");
    const std::filesystem::path root(directory),movie=root/"same-stem.mp4",subtitle=root/"same-stem.SRT";
    Release remove{[&]{std::error_code error;std::filesystem::remove_all(root,error);}};
    std::filesystem::copy_file(fixture,movie);
    std::ofstream(subtitle)<<"1\n00:00:00,000 --> 00:00:03,500\nActor subtitle\n\n";
    auto options=playback_options(binding());
    MediaActor actor(binding(),op(100),[options]{return make_playback_actor_backend(options);});
    auto load=command(MediaCommandKind::Load,1,1);load.path=movie.string();load.flag=true;
    require(actor.submit(load),"Actual media load enqueue failed");
    actual_ready(actor,[](const auto& s){return s.media.status.playing&&s.media.frame&&s.media.status.position>.1;},"Actual actor never played");
    auto volume=command(MediaCommandKind::Volume,2,2);volume.number=.35;require(actor.submit(volume),"Actual volume enqueue failed");
    auto mute=command(MediaCommandKind::Muted,3,3);mute.flag=true;require(actor.submit(mute),"Actual mute enqueue failed");
    require(actor.submit(command(MediaCommandKind::Pause,4,4)),"Actual pause enqueue failed");
    auto seek=command(MediaCommandKind::Seek,5,5);seek.number=1.1;require(actor.submit(seek),"Actual seek enqueue failed");
    actual_ready(actor,[](const auto& s){return s.media.status.paused&&!s.media.status.buffering&&s.media.frame&&
        std::abs(s.media.frame->position-1.1)<.1&&s.media.status.muted;},"Actual actor did not apply checkpoint controls");
    require(actor.seal(binding(),6,op(6)),"Actual graph seal refused");
    actual_ready(actor,[](const auto& s){return s.phase==MediaActorPhase::Drained;},"Actual graph drain did not finish");
    auto snapshot=actor.snapshot();
    require(snapshot->graph&&snapshot->media.checkpoint&&snapshot->graph->callbacks_started==snapshot->graph->callbacks_completed&&
        snapshot->graph->pipeline_finalized&&snapshot->graph->live_objects==0&&snapshot->graph->callback_contexts==0,
        "Actual actor did not retain final graph facts");
    const auto saved=*snapshot->media.checkpoint;
    require(saved.external_subtitle&&saved.external_subtitle->path==subtitle.string()&&!saved.playing&&saved.muted&&std::abs(saved.volume-.35)<.001,
            "Worker subtitle discovery/checkpoint controls were lost");
    actor.take_events();
    require(actor.epoch_fenced(binding()),"Actual drained graph did not positively fence old epoch");
    require(actor.regrant(binding(2),op(200),1,op(201)),"Fresh actual graph grant refused");
    require(!actor.epoch_fenced(binding()),"Actual fresh grant retained old epoch fence");
    actual_ready(actor,[&](const auto& s){return s.media.status.paused&&!s.media.status.buffering&&s.media.frame&&
        std::abs(s.media.frame->position-saved.position)<.1;},"Checkpoint did not rebuild after fresh grant");
    require(actor.snapshot()->media.status.video_decode_fallbacks==saved.video_decode_fallbacks&&actor.snapshot()->media.status.muted&&
        std::abs(actor.snapshot()->media.status.volume-saved.volume)<.001,"Fresh grant reset retry/volume/mute intent");
    require(!actor.submit(command(MediaCommandKind::Play,100,500)),"Old actor epoch resumed after new grant");
    shutdown(actor,900);
    require(!actor.snapshot()->unknown,"Actual graph shutdown retained unexplained uncertainty");
    return *actor.snapshot()->graph;
}

GraphDrainEvidence actual_blocked_callback(const std::string& fixture) {
    auto held=std::make_shared<Gate>();held->enabled=true;
    auto options=playback_options(binding());options.managed_sample_hook=[held]{held->wait();};
    MediaActor actor(binding(),op(100),[options]{return make_playback_actor_backend(options);});
    Release release{[held]{held->release();}};
    auto load=command(MediaCommandKind::Load,1,1);load.path=fixture;require(actor.submit(load),"Held real load enqueue failed");
    until([&]{return held->started();},"Real appsink callback did not block",std::chrono::seconds(15));
    require(actor.seal(binding(),2,op(2))&&actor.cancel(binding(),2),"Real callback seal/cancel refused");
    require(actor.request_shutdown(binding(),3,op(3)),"Shutdown waited on real callback");
    const auto visible=actor.snapshot();
    require(visible->sealed&&!visible->graph&&!actor.finished()&&!actor.join_finished()&&!actor.epoch_fenced(binding()),"Blocked streaming task was declared released");
    for(const auto& event:actor.peek_events())require(event.kind!=MediaCommandKind::Drain,"Drain escaped held callback");
    held->release();until([&]{return actor.finished();},"Real callback did not drain before shutdown",std::chrono::seconds(15));
    require(actor.join_finished()&&!actor.snapshot()->unknown&&actor.snapshot()->graph,"Real callback finality was lost");
    const auto proof=*actor.snapshot()->graph;
    require(proof.active_callbacks==0&&proof.callback_contexts==0&&proof.live_objects==0&&proof.pipeline_finalized&&
        proof.callbacks_started==proof.callbacks_completed,"Actor retired a live GStreamer task/context/object");
    return proof;
}
} // namespace

int main(int argc,char** argv) {
    try {
        std::string fixture,report;
        for(int i=1;i<argc;++i) {
            const std::string argument=argv[i];require(i+1<argc,"Expected actor test option value");
            if(argument=="--fixture")fixture=argv[++i];else if(argument=="--report")report=argv[++i];else throw std::runtime_error("Unknown actor test option");
        }
        require(!fixture.empty(),"Actual Playback fixture is required; this test must not skip graph execution");
        held_command_seal_and_regrant();automatic_work_is_held_and_polling_stops_after_seal();
        malformed_completion_and_exceptions_remain_unknown();finite_history_preserves_cleanup_and_validates_commands();
        shutdown_never_joins_an_active_backend_call();shutdown_refuses_unrelated_graph_receipts();publication_metadata_is_bounded_before_control_lock();
        shutdown_does_not_erase_new_external_cleanup_uncertainty();
        sealed_pending_regrant_cannot_reuse_the_old_graph_receipt();
        const auto checkpoint=actual_checkpoint_regrant(fixture),blocked=actual_blocked_callback(fixture);
        const std::string evidence="{\"schema\":1,\"kind\":\"a2-d4-media-actor\",\"status\":\"passed\",\"hardware_qualified\":false,"
            "\"live_admission\":false,\"single_owner\":true,\"bounded_queue\":true,\"control_does_not_join\":true,"
            "\"checkpoint_regrant\":"+checkpoint.json()+",\"blocked_callback\":"+blocked.json()+"}\n";
        if(!report.empty()){std::ofstream output(report);require(bool(output<<evidence),"Cannot write actor evidence");}
        std::cout<<"PASS: single-owner media commands, blocked calls, immutable control snapshots, graph drain, fresh regrant and owner-thread destruction\n";
        return 0;
    }catch(const std::exception& error){std::cerr<<"Media actor: "<<error.what()<<'\n';return 1;}
}
