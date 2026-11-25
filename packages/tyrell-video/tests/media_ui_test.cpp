// Actual App controller/LVGL tree with a borrowed actor control. Only the media
// backend is controlled here; real Playback/GStreamer is covered separately.
#define main video_application_main
#include "../src/main.cpp"
#undef main
#include "actor_player_control.h"

#include <atomic>
#include <condition_variable>
#include <map>
#include <mutex>
#include <iomanip>
#include <limits>

namespace {
void require(bool value,const std::string& message) {if(!value)throw std::runtime_error(message);}
std::string operation(unsigned number) {std::ostringstream out;out<<std::hex<<std::setfill('0')<<std::setw(32)<<number;return out.str();}
MediaActorBinding scope(unsigned epoch=1) {return {epoch,operation(10000+epoch),operation(20000+epoch)};}
bool same(const MediaActorBinding& a,const MediaActorBinding& b) {return a.epoch==b.epoch&&a.resource==b.resource&&a.generation==b.generation;}
std::uint32_t fake_time=100;
std::uint32_t ui_tick(){return fake_time;}
std::vector<std::uint32_t> ui_pixels(width*height);
lv_display_t* ui_display=nullptr;
unsigned ui_flushes=0;
const std::thread::id ui_owner=std::this_thread::get_id();

struct Gate {
    std::mutex mutex;std::condition_variable condition;
    bool enabled=false,entered=false,released=false;
    void wait(){std::unique_lock<std::mutex> lock(mutex);if(!enabled)return;entered=true;condition.notify_all();condition.wait(lock,[&]{return released;});}
    bool started(){std::lock_guard<std::mutex> lock(mutex);return entered;}
    void release(){std::lock_guard<std::mutex> lock(mutex);released=true;condition.notify_all();}
};
struct BackendState {
    Gate old_load,stop,regrant;
    std::mutex mutex;
    std::vector<MediaCommand> executed;
    std::thread::id owner;
    bool old_fails=false;
    std::atomic<unsigned> destroyed{0};
    void on_owner(){std::lock_guard<std::mutex> lock(mutex);if(owner==std::thread::id{})owner=std::this_thread::get_id();require(owner==std::this_thread::get_id()&&owner!=ui_owner,"UI called a graph backend");}
    void record(const MediaCommand& command){on_owner();std::lock_guard<std::mutex> lock(mutex);executed.push_back(command);}
    std::size_t count(MediaCommandKind kind){std::lock_guard<std::mutex> lock(mutex);return std::count_if(executed.begin(),executed.end(),[&](const auto& c){return c.kind==kind;});}
    void release(){old_load.release();stop.release();regrant.release();}
};
class Backend final:public MediaActorBackend {
public:
    Backend(std::shared_ptr<BackendState> state,MediaActorBinding binding):state_(std::move(state)),binding_(std::move(binding)){state_->on_owner();}
    ~Backend()override{state_->on_owner();++state_->destroyed;}
    bool execute(const MediaCommand& command,std::string& error)override {
        state_->record(command);
        if(command.kind==MediaCommandKind::Load) {
            if(fs::path(command.path).filename()=="old.mp4") {
                state_->old_load.wait();
                if(state_->old_fails){media_.status.error=error="Late old load failure";media_.status.path=command.path;return false;}
            }
            media_.status={};media_.status.loaded=media_.status.playing=true;media_.status.path=command.path;
            media_.status.duration=120;media_.resources.pipeline_present=true;saved_path_=command.path;publish_frame();
        } else if(command.kind==MediaCommandKind::Stop) {
            state_->stop.wait();media_={};
        } else if(command.kind==MediaCommandKind::TogglePause) {
            media_.status.playing=!media_.status.playing;media_.status.paused=!media_.status.playing;
        } else if(command.kind==MediaCommandKind::Volume)media_.status.volume=command.number;
        else if(command.kind==MediaCommandKind::Muted)media_.status.muted=command.flag;
        else if(command.kind==MediaCommandKind::SeekRelative)media_.status.position+=command.number;
        return true;
    }
    void poll()override{state_->on_owner();}
    MediaBackendSnapshot snapshot()const override{state_->on_owner();return media_;}
    std::optional<GraphDrainEvidence> drain(const std::string& operation_id,std::string&)override {
        state_->on_owner();media_.status.suspended=true;media_.resources={};media_.frame.reset();
        PlaybackCheckpoint checkpoint;checkpoint.media.path=saved_path_;checkpoint.position=media_.status.position;
        checkpoint.volume=media_.status.volume;checkpoint.muted=media_.status.muted;media_.checkpoint=checkpoint;
        GraphDrainEvidence result;result.binding=binding_;result.operation=operation_id;
        result.pipeline_finalized=result.local_handles_released=result.legacy_helpers_unused=true;result.finality_receipt=operation(999);
        return result;
    }
    bool regrant(const MediaActorBinding& binding,std::string&)override {
        state_->on_owner();state_->regrant.wait();binding_=binding;media_.status.suspended=false;media_.status.loaded=media_.status.playing=true;
        media_.status.path=saved_path_;media_.status.error.clear();publish_frame();return true;
    }
    void stop()override{state_->on_owner();media_.resources={};media_.frame.reset();}
private:
    void publish_frame(){auto frame=std::make_shared<Frame>();frame->serial=++serial_;frame->pixels.resize(width*height*2,0x35);media_.frame=std::move(frame);}
    std::shared_ptr<BackendState> state_;MediaActorBinding binding_;MediaBackendSnapshot media_;std::string saved_path_;unsigned serial_=0;
};

bool same_payload(const MediaCommand& a,const MediaCommand& b) {
    return same(a.binding,b.binding)&&a.request==b.request&&a.kind==b.kind&&a.path==b.path&&a.external_subtitle==b.external_subtitle&&
        a.number==b.number&&a.index==b.index&&a.output==b.output&&a.flag==b.flag;
}
struct Mailbox {
    struct Reserved{MediaCommand command;std::string operation;};
    struct Decision{MediaCommand command;bool accepted;};
    unsigned next=1000;
    std::map<std::string,Reserved> ready,issued;
    std::vector<Decision> decisions;
    std::vector<MediaActorEvent> journaled;
    static std::string key(const MediaCommand& value){return std::to_string(value.binding.epoch)+":"+value.binding.generation+":"+std::to_string(value.request);}
    void grant(const MediaCommand& command){require(command.operation.empty(),"UI intent minted an operation before journal reservation");const auto id=operation(next++);ready[key(command)]={command,id};issued[id]={command,id};}
    void grant_all(const ActorPlayerControl& control){for(const auto& command:control.awaiting_permits())if(!ready.count(key(command)))grant(command);}
    std::optional<std::string> permit(const MediaCommand& value){const auto found=ready.find(key(value));if(found==ready.end())return{};require(same_payload(value,found->second.command),"Mailbox matched a different payload");const auto id=found->second.operation;ready.erase(found);return id;}
    void submitted(const MediaCommand& command,bool accepted){const auto found=issued.find(command.operation);require(found!=issued.end()&&same_payload(command,found->second.command),"Submission used an unjournaled or mismatched permit");require(std::none_of(decisions.begin(),decisions.end(),[&](const auto& old){return old.command.operation==command.operation;}),"Submission result repeated");decisions.push_back({command,accepted});}
    void acknowledge(ActorPlayerControl& control){const auto batch=control.peek_audit();journaled.insert(journaled.end(),batch.begin(),batch.end());require(control.acknowledge_audit(batch.size()),"Journaled event prefix could not be acknowledged");}
};

void wait(const std::function<bool()>& predicate,const char* message){const auto until=std::chrono::steady_clock::now()+std::chrono::seconds(5);while(!predicate()&&std::chrono::steady_clock::now()<until)std::this_thread::sleep_for(std::chrono::milliseconds(1));require(predicate(),message);}
void render(App& app){app.render();if(app.root)lv_obj_update_layout(app.root);lv_refr_now(ui_display);}
void dispose(App& app){app.frame_image.clear(app.image);if(app.root)lv_obj_delete(app.root);app.root=nullptr;}
void finish(MediaActor& actor){const auto binding=actor.snapshot()->binding;require(actor.request_shutdown(binding,9000,operation(9000)),"Supervisor shutdown refused");wait([&]{return actor.finished();},"Supervisor failed to retain/finish actor");require(actor.join_finished(),"Supervisor could not join finished actor");}

struct Fixture {
    std::shared_ptr<BackendState> state=std::make_shared<BackendState>();
    Mailbox mailbox;
    std::unique_ptr<MediaActor> actor;
    explicit Fixture(bool hold=false,bool fail=false,bool stop_hold=false){state->old_load.enabled=hold;state->old_fails=fail;state->stop.enabled=stop_hold;
        actor=std::make_unique<MediaActor>(scope(),operation(100),[state=state]{return std::make_unique<Backend>(state,scope());});
        wait([&]{return actor->snapshot()->phase==MediaActorPhase::Foreground;},"UI actor did not initialize");}
    ~Fixture(){state->release();if(actor&&!actor->finished())actor->request_shutdown(actor->snapshot()->binding,9000,operation(9000));if(actor)actor->join_finished();}
    std::unique_ptr<ActorPlayerControl> control(){return std::make_unique<ActorPlayerControl>(*actor,scope(),[&](const auto& command){return mailbox.permit(command);},[&](const auto& command,bool accepted){mailbox.submitted(command,accepted);});}
};

void late_load_does_not_replace_new_intent(const fs::path& directory) {
    Fixture fixture(true,true,true);
    Options options;options.media_root=directory;App app(options,ui_tick);
    auto control=fixture.control();auto* actor_control=control.get();app.playback=std::move(control);
    app.start_file(directory/"old.mp4");
    require(actor_control->awaiting_permits().size()==1&&fixture.state->count(MediaCommandKind::Load)==0,"UI load executed without a permit/poll");
    const auto proposed=actor_control->awaiting_permits().front();
    require(proposed.flag&&proposed.external_subtitle.empty()&&proposed.path==fs::absolute(directory/"old.mp4"),"SRT discovery or exact load payload escaped worker");
    app.poll();require(fixture.state->count(MediaCommandKind::Load)==0,"Missing permit dispatched media work");
    fixture.mailbox.grant_all(*actor_control);app.poll();wait([&]{return fixture.state->old_load.started();},"Old load did not remain accepted/held");
    app.key('h');require(app.page==Page::Menu&&app.title=="KEY GUIDE","Help waited for media backend");render(app);
    app.key(LV_KEY_ESC);require(app.page==Page::Player,"Help back did not return to player");
    app.key(LV_KEY_ESC);require(app.page==Page::Browser,"Back waited for held load");
    app.start_file(directory/"new.mp4");fixture.mailbox.grant_all(*actor_control);app.poll();render(app);
    require(app.page==Page::Player&&app.status.path==fs::absolute(directory/"new.mp4")&&app.status.buffering,"New UI intent was not presented while old work held");
    fixture.state->old_load.release();wait([&]{return fixture.state->stop.started();},"Queued Stop did not follow old operation");
    app.poll();render(app);
    require(app.page==Page::Player&&app.status.path==fs::absolute(directory/"new.mp4")&&app.error.empty(),"Late old error changed new page/path");
    fixture.state->stop.release();
    wait([&]{app.poll();return app.status.loaded&&!app.status.buffering&&app.status.path==fs::absolute(directory/"new.mp4");},"New load never reached UI");
    render(app);require(app.page==Page::Player&&app.error.empty()&&app.lcd_frame_assignments>0,"New successful frame retained old failure");
    require(fixture.mailbox.decisions.size()==3&&std::all_of(fixture.mailbox.decisions.begin(),fixture.mailbox.decisions.end(),[](const auto& d){return d.accepted;}),"Accepted old/Stop/new reservations lost their submission results");
    const auto audit=actor_control->peek_audit();require(audit.size()==3&&std::any_of(audit.begin(),audit.end(),[](const auto& event){return event.outcome==MediaEventOutcome::Failed;}),"UI discarded accepted old operation evidence");
    fixture.mailbox.acknowledge(*actor_control);dispose(app);app.playback.reset();
    require(fixture.state->destroyed==0,"UI facade destruction destroyed/joined graph owner");finish(*fixture.actor);
}

void bounds_and_no_dispatch_on_seal(const fs::path& directory) {
    Fixture fixture(true,false);
    Options options;options.media_root=directory;App app(options,ui_tick);
    auto control=fixture.control();auto* actor_control=control.get();app.playback=std::move(control);
    app.start_file(directory/"old.mp4");fixture.mailbox.grant_all(*actor_control);app.poll();
    wait([&]{return fixture.state->old_load.started();},"Bounded UI load did not block");
    const auto intents_before=actor_control->awaiting_permits().size();
    require(!actor_control->load(std::string(4097,'p'))&&
            !actor_control->load((directory/"new.mp4").string(),std::string(4097,'s'))&&
            !actor_control->seek_relative(std::numeric_limits<double>::quiet_NaN())&&
            !actor_control->seek_relative(std::numeric_limits<double>::infinity()),
            "Invalid path/subtitle or nonfinite seek entered the UI intent queue");
    require(actor_control->awaiting_permits().size()==intents_before&&fixture.mailbox.decisions.size()==1&&
            fixture.state->count(MediaCommandKind::Load)==1&&fixture.state->count(MediaCommandKind::SeekRelative)==0,
            "Rejected UI arguments consumed a permit or dispatched backend work");
    for(std::size_t n=1;n<MediaActor::command_capacity;++n)require(actor_control->toggle_pause(),"UI intent bound filled early");
    require(!actor_control->toggle_pause(),"Unbounded UI intent queue accepted overflow");
    const auto waiting=actor_control->awaiting_permits();require(waiting.size()==31&&waiting.front().request==2,"Bounded local UI queue lost intents or rejected arguments consumed request IDs");
    fixture.mailbox.grant(waiting.front());
    render(app);const auto pixels_before=ui_pixels;const auto flush_before=ui_flushes;const auto input_before=app.input_epoch;
    require(actor_control->seal(8000,operation(8000))&&actor_control->cancel(8000),"UI lifecycle seal/cancel failed");
    require(!actor_control->input_allowed()&&!actor_control->render_allowed()&&actor_control->awaiting_permits().empty(),"Seal/cancel reopened UI submissions");
    require(std::count_if(fixture.mailbox.decisions.begin(),fixture.mailbox.decisions.end(),[](const auto& d){return !d.accepted;})==1,"Reserved unused operation lost positive no-dispatch result");
    app.key('h');app.key(' ');app.render();lv_refr_now(ui_display);
    require(app.input_epoch==input_before&&ui_pixels==pixels_before&&ui_flushes==flush_before,"Sealed App accepted a key/render submission");
    require(fixture.state->count(MediaCommandKind::Load)==1&&fixture.state->count(MediaCommandKind::TogglePause)==0,"Unpermitted UI work reached actor backend");
    fixture.state->old_load.release();wait([&]{return fixture.actor->snapshot()->phase==MediaActorPhase::Drained;},"Sealed UI actor did not drain");
    app.poll();require(!actor_control->input_allowed()&&!actor_control->render_allowed(),"Cancellation unfreezed a drained old epoch");
    fixture.mailbox.acknowledge(*actor_control);require(fixture.actor->epoch_fenced(scope()),"Journaled UI/graph prefix did not permit old epoch fence");
    dispose(app);app.playback.reset();require(fixture.state->destroyed==0,"Borrowed UI control joined retained parked actor");finish(*fixture.actor);
}

void durable_ack_before_ui_and_fresh_request_sequence(const fs::path& directory) {
    Fixture fixture;
    Options options;options.media_root=directory;App app(options,ui_tick);
    auto control=fixture.control();auto* actor_control=control.get();app.playback=std::move(control);
    app.start_file(directory/"new.mp4");fixture.mailbox.grant_all(*actor_control);app.poll();
    wait([&]{return !actor_control->peek_audit().empty();},"Accepted load did not complete");
    actor_control->set_volume(.7);fixture.mailbox.grant_all(*actor_control);
    // Durable coordinator acknowledgment can precede the next GUI tick. It
    // must not erase the presentation result or leave Loading forever.
    fixture.mailbox.acknowledge(*actor_control);
    require(actor_control->awaiting_permits().size()==1&&fixture.mailbox.decisions.size()==1,
            "Acknowledging old evidence dispatched an unrelated ready UI intent");
    app.poll();require(app.status.loaded&&!app.status.buffering&&app.status.path==fs::absolute(directory/"new.mp4"),"Early durable ack erased load presentation completion");
    wait([&]{app.poll();return std::abs(app.status.volume-.7)<.001;},"Prepared volume intent did not dispatch on App poll");
    fixture.mailbox.acknowledge(*actor_control);
    render(app);const auto input_before=app.input_epoch;
    require(actor_control->seal(8000,operation(8000)),"UI graph seal refused");
    wait([&]{return fixture.actor->snapshot()->phase==MediaActorPhase::Drained;},"UI graph did not park");
    fixture.mailbox.acknowledge(*actor_control);require(fixture.actor->epoch_fenced(scope()),"Old UI epoch not fenced");
    require(actor_control->regrant(scope(2),operation(8100),100,operation(8101)),"Fresh UI grant refused");
    wait([&]{return fixture.actor->snapshot()->phase==MediaActorPhase::Foreground;},"Fresh UI graph did not rebuild");
    fixture.mailbox.acknowledge(*actor_control); // Before App.poll, deliberately.
    app.poll();render(app);
    require(actor_control->input_allowed()&&actor_control->render_allowed()&&app.input_epoch>input_before&&
            app.status.loaded&&app.status.path==fs::absolute(directory/"new.mp4"),"Fresh grant stayed presentation-pending or kept old input epoch");
    app.key(' ');
    const auto waiting=actor_control->awaiting_permits();
    require(waiting.size()==1&&waiting.front().request==101&&same(waiting.front().binding,scope(2)),"Fresh UI request stream did not advance beyond resume request100");
    fixture.mailbox.grant_all(*actor_control);app.poll();
    wait([&]{app.poll();return fixture.state->count(MediaCommandKind::TogglePause)==1&&app.status.paused;},"Post-regrant UI request101 did not execute");
    fixture.mailbox.acknowledge(*actor_control);
    dispose(app);app.playback.reset();require(fixture.state->destroyed==0,"UI destruction joined live graph owner");finish(*fixture.actor);
}

void late_regrant_completion_cannot_undo_revocation(const fs::path& directory) {
    for(bool seal_while_held:{false,true}) {
        Fixture fixture;
        Options options;options.media_root=directory;App app(options,ui_tick);
        auto control=fixture.control();auto* actor_control=control.get();app.playback=std::move(control);
        app.start_file(directory/"new.mp4");fixture.mailbox.grant_all(*actor_control);app.poll();
        wait([&]{app.poll();return app.status.loaded&&!app.status.buffering;},"Revocation fixture did not load");
        fixture.mailbox.acknowledge(*actor_control);render(app);
        require(actor_control->seal(8000,operation(8000)),"First epoch seal refused");
        wait([&]{return fixture.actor->snapshot()->phase==MediaActorPhase::Drained;},"First epoch did not drain");
        fixture.mailbox.acknowledge(*actor_control);require(fixture.actor->epoch_fenced(scope()),"First epoch was not fenced");
        fixture.state->regrant.enabled=true;
        require(actor_control->regrant(scope(2),operation(8100),100,operation(8101)),"Held fresh regrant refused");
        wait([&]{return fixture.state->regrant.started();},"Fresh rebuild did not remain in flight");
        actor_control->revoke_admission();
        if(seal_while_held)require(actor_control->seal(8200,operation(8200)),"Fresh epoch seal during rebuild refused");
        const auto flush_before=ui_flushes;const auto input_before=app.input_epoch;
        fixture.state->regrant.release();
        wait([&]{return fixture.actor->snapshot()->phase==(seal_while_held?MediaActorPhase::Drained:MediaActorPhase::Foreground);},"Held rebuild did not finish under revoked admission");
        // Commit/ack may precede the next App tick. A late successful resume
        // event must not clear the newer revocation of that same fresh epoch.
        fixture.mailbox.acknowledge(*actor_control);app.poll();app.key(' ');app.render();lv_refr_now(ui_display);
        require(!actor_control->input_allowed()&&!actor_control->render_allowed()&&!actor_control->latest_frame()&&
            actor_control->awaiting_permits().empty()&&ui_flushes==flush_before&&app.input_epoch==input_before,
            "Late RegrantCompleted reopened revoked input/render or dispatched UI work");
        if(!seal_while_held) {
            require(actor_control->seal(8200,operation(8200)),"Revoked fresh epoch could not receive its durable drain");
            wait([&]{return fixture.actor->snapshot()->phase==MediaActorPhase::Drained;},"Revoked fresh epoch did not drain");
            fixture.mailbox.acknowledge(*actor_control);
        }
        require(fixture.actor->epoch_fenced(scope(2)),"Revoked fresh epoch lost its terminal evidence");
        require(actor_control->regrant(scope(3),operation(8300),200,operation(8301)),"Genuinely fresh third grant refused");
        wait([&]{return fixture.actor->snapshot()->phase==MediaActorPhase::Foreground;},"Third epoch rebuild did not complete");
        fixture.mailbox.acknowledge(*actor_control);app.poll();render(app);
        require(actor_control->input_allowed()&&actor_control->render_allowed()&&app.input_epoch>input_before,
            "A distinct fresh authority binding did not reset prior revocation");
        dispose(app);app.playback.reset();finish(*fixture.actor);
    }
}

void current_failure_and_borrowed_destruction(const fs::path& directory) {
    {
        Fixture fixture(false,true);
        Options options;options.media_root=directory;App app(options,ui_tick);
        auto control=fixture.control();auto* actor_control=control.get();app.playback=std::move(control);
        app.start_file(directory/"old.mp4");fixture.mailbox.grant_all(*actor_control);app.poll();
        wait([&]{app.poll();return app.page==Page::Error;},"Current asynchronous load failure never reached App Error");
        require(app.error.find("Late old load failure")!=std::string::npos,"Current load diagnostic was lost");
        require(actor_control->peek_audit().size()==1&&actor_control->peek_audit().front().outcome==MediaEventOutcome::Failed,
                "Current load failure discarded its terminal audit fact");
        fixture.mailbox.acknowledge(*actor_control);render(app);dispose(app);app.playback.reset();finish(*fixture.actor);
    }
    {
        Fixture fixture(true);
        Options options;options.media_root=directory;App app(options,ui_tick);
        auto control=fixture.control();auto* actor_control=control.get();app.playback=std::move(control);
        app.start_file(directory/"old.mp4");fixture.mailbox.grant_all(*actor_control);app.poll();
        wait([&]{return fixture.state->old_load.started();},"Borrowed lifetime fixture did not hold active Load");
        const auto held=fixture.actor->snapshot()->active_operation;
        dispose(app);app.playback.reset();
        require(fixture.state->destroyed==0&&!fixture.actor->finished()&&fixture.actor->snapshot()->active_operation==held,
                "Destroying borrowed UI facade joined or invalidated accepted held work");
        require(fixture.actor->request_shutdown(scope(),9000,operation(9000))&&!fixture.actor->finished(),
                "Supervisor shutdown waited for a held operation or claimed early completion");
        fixture.state->old_load.release();wait([&]{return fixture.actor->finished();},"Retained worker never completed after UI destruction");
        require(fixture.actor->join_finished()&&fixture.state->destroyed==1,"Supervisor did not own final backend destruction/join");
    }
}
} // namespace

int main(int argc,char** argv) {
    char pattern[]="/tmp/tyrell-media-ui-XXXXXX";const char* name=mkdtemp(pattern);
    if(!name){std::cerr<<"Cannot create media UI fixture directory\n";return 1;}
    const fs::path directory(name);
    std::string report;
    try {
        for(int n=1;n<argc;++n){require(std::string(argv[n])=="--report"&&n+1<argc,"Unknown media UI test option");report=argv[++n];}
        std::ofstream(directory/"old.mp4")<<"UI fixture only";std::ofstream(directory/"new.mp4")<<"UI fixture only";
        lv_init();lv_tick_set_cb(ui_tick);ui_display=lv_display_create(width,height);
        lv_display_set_color_format(ui_display,LV_COLOR_FORMAT_XRGB8888);
        lv_display_set_buffers(ui_display,ui_pixels.data(),nullptr,ui_pixels.size()*sizeof(ui_pixels[0]),LV_DISPLAY_RENDER_MODE_DIRECT);
        lv_display_set_flush_cb(ui_display,[](lv_display_t* display,const lv_area_t*,std::uint8_t*){require(std::this_thread::get_id()==ui_owner,"Media worker entered LVGL");++ui_flushes;lv_display_flush_ready(display);});
        late_load_does_not_replace_new_intent(directory);bounds_and_no_dispatch_on_seal(directory);durable_ack_before_ui_and_fresh_request_sequence(directory);late_regrant_completion_cannot_undo_revocation(directory);current_failure_and_borrowed_destruction(directory);
        lv_display_delete(ui_display);ui_display=nullptr;lv_deinit();fs::remove_all(directory);
        if(!report.empty()){std::ofstream out(report);require(bool(out<<"{\"schema\":1,\"kind\":\"a2-d4-media-ui\",\"status\":\"passed\",\"hardware_qualified\":false,\"live_admission\":false,\"actual_app\":true,\"memory_lvgl\":true,\"mailbox_before_effects\":true,\"late_intent_filtered\":true,\"no_dispatch_accounted\":true,\"seal_gates_render_input\":true,\"ack_before_ui\":true,\"fresh_request_101\":true,\"late_regrant_cannot_undo_revocation\":true,\"ui_does_not_own_actor\":true}\n"),"Cannot write UI actor evidence");}
        std::cout<<"PASS: actual App/LVGL, borrowed actor, exact permit mailbox, stale-intent filtering, seal gates and fresh request sequence\n";return 0;
    }catch(const std::exception& error){std::cerr<<"Media UI: "<<error.what()<<'\n';if(ui_display)lv_display_delete(ui_display);lv_deinit();fs::remove_all(directory);return 1;}
}
