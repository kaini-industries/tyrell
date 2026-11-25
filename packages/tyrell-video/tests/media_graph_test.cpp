#include "playback.h"
#include <gst/gst.h>

#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <functional>
#include <future>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <thread>

using namespace tyrell::video;
using Clock=std::chrono::steady_clock;
namespace {
void require(bool value,const std::string& message) { if(!value)throw std::runtime_error(message); }
ManagedGraphBinding binding(std::uint64_t epoch=1) {
    return {epoch,std::string(32,epoch==1?'a':'c'),std::string(32,epoch==1?'b':'d')};
}
PlaybackOptions options(bool finality=true) {
    PlaybackOptions result;
    result.fake_audio=true;result.desktop_handoff=false;result.automatic_hdmi_audio=false;
    result.managed_graph=binding();
    if(finality)result.graph_finality=[](const GraphDrainEvidence& evidence) {
        require(evidence.active_callbacks==0&&evidence.callback_contexts==0&&evidence.live_objects==0&&
            evidence.pipeline_finalized&&evidence.callbacks_started==evidence.callbacks_completed,
            "Finality hook ran before callback/object destruction");
        return evidence.operation;
    };
    return result;
}
void ready(Playback& playback,const std::function<bool()>& predicate,const std::string& message) {
    const auto deadline=Clock::now()+std::chrono::seconds(12);
    while(Clock::now()<deadline) {
        playback.poll();require(playback.status().error.empty(),message+": "+playback.status().error);
        if(predicate())return;
        std::this_thread::sleep_for(std::chrono::milliseconds(3));
    }
    throw std::runtime_error(message);
}
void released(const GraphDrainEvidence& evidence) {
    require(evidence.pipeline_finalized&&evidence.local_handles_released&&evidence.legacy_helpers_unused&&
        evidence.graph_serial>0&&evidence.callbacks_started>0&&evidence.callbacks_started==evidence.callbacks_completed&&
        evidence.active_callbacks==0&&evidence.callback_contexts==0&&evidence.live_objects==0&&
        evidence.finality_receipt==evidence.operation,"Graph result omitted a completion barrier");
}
void invalid_profiles_and_no_legacy_fallback(const char* media) {
    for(int scenario=0;scenario<4;++scenario) {
        auto requested=options();
        if(scenario==0)requested.fake_audio=false;
        if(scenario==1)requested.desktop_handoff=true;
        if(scenario==2)requested.automatic_hdmi_audio=true;
        if(scenario==3)requested.managed_graph->generation="unbound";
        Playback playback(requested);
        require(!playback.load(media)&&playback.resources().local_handles_released(),"Invalid managed profile opened a graph");
        require(!playback.drain_graph(std::string(32,'e')),"Invalid managed profile produced evidence");
    }
    Playback playback(options());
    require(!playback.set_output(Output::Hdmi)&&!playback.set_audio_sink("external"),"Managed graph reached legacy output/audio");
    require(playback.resources().local_handles_released()&&!playback.resources().external_cleanup_unconfirmed,
            "Refused managed output contacted a legacy helper");
}
void empty_and_destructively_stopped_graphs_can_finish(const char* media) {
    Playback playback(options());
    const auto empty=playback.drain_graph(std::string(32,'e'));
    require(empty.has_value()&&empty->graph_serial==0&&empty->pipeline_finalized&&empty->local_handles_released&&
        empty->callback_contexts==0&&empty->active_callbacks==0,"An empty actor could not drain its zero graph");
    require(playback.rebind_managed_graph(binding(2))&&playback.resume()&&playback.load(media),"Empty fresh grant could not load media");
    ready(playback,[&]{return playback.latest_frame()&&!playback.status().buffering;},"Stopped graph not ready");
    playback.stop();
    const auto stopped=playback.drain_graph(std::string(32,'f'));
    require(stopped.has_value()&&stopped->graph_serial==1&&stopped->pipeline_finalized&&stopped->local_handles_released&&
        !playback.checkpoint(),"A destructively stopped graph fabricated/required a checkpoint");
}
void missing_finality_is_not_release(const char* media) {
    Playback playback(options(false));
    require(playback.load(media),"Cannot load unqualified finality graph");
    ready(playback,[&]{return playback.latest_frame()&&!playback.status().buffering;},"Unqualified graph not ready");
    require(!playback.drain_graph(std::string(32,'e')),"NULL alone certified graph finality");
    require(playback.resources().local_handles_released()&&!playback.graph_drain_evidence(),"Unknown graph result became receipt");
    require(!playback.rebind_managed_graph(binding(2))&&!playback.resume(),"Unqualified drain allowed reacquisition");
}
void prebinding_sink_failure_destroys_registered_callbacks_safely(const char* media) {
    Playback playback(options());
    auto* factory=gst_element_factory_find("fakesink");
    require(factory!=nullptr,"Missing test fakesink factory");
    auto* registry=gst_registry_get();
    gst_registry_remove_feature(registry,GST_PLUGIN_FEATURE(factory));
    struct Restore {GstRegistry* registry;GstElementFactory* factory;~Restore(){gst_registry_add_feature(registry,GST_PLUGIN_FEATURE(factory));gst_object_unref(factory);}} restore{registry,factory};
    require(!playback.load(media),"Controlled prebinding audio-sink failure did not occur");
    require(playback.resources().local_handles_released(),"Failed prebinding retained local resources");
    const auto evidence=playback.drain_graph(std::string(32,'e'));
    require(evidence.has_value()&&evidence->active_callbacks==0&&evidence->callback_contexts==0&&evidence->live_objects==0,
            "Prebinding failure retained dangling registered callback data");
}

struct Gate {
    std::mutex mutex;std::condition_variable condition;
    bool entered=false,open=false;
    void hold() {
        std::unique_lock<std::mutex> guard(mutex);entered=true;condition.notify_all();
        condition.wait(guard,[&]{return open;});
    }
    void wait() {
        std::unique_lock<std::mutex> guard(mutex);
        require(condition.wait_for(guard,std::chrono::seconds(5),[&]{return entered;}),"Controlled production callback did not run");
    }
    void release() { std::lock_guard<std::mutex> guard(mutex);open=true;condition.notify_all(); }
};

void actual_streaming_callback_and_terminal_hook_are_held(const char* media) {
    Gate callback,finality,request;
    std::atomic<bool> armed{false},captured{false};
    std::promise<void> playing;
    std::promise<GraphDrainEvidence> completed;
    auto result=completed.get_future();
    std::thread owner([&] {
        try {
            auto requested=options();
            requested.managed_sample_hook=[&] {
                if(armed&& !captured.exchange(true))callback.hold();
            };
            requested.graph_finality=[&](const GraphDrainEvidence& evidence) {
                require(evidence.pipeline_finalized&&evidence.active_callbacks==0&&evidence.callback_contexts==0&&evidence.live_objects==0,
                        "Terminal hook ignored a live GStreamer callback/object");
                finality.hold();return evidence.operation;
            };
            Playback playback(requested);
            require(playback.load(media),"Cannot load held production graph");
            ready(playback,[&]{return playback.latest_frame()&&playback.status().playing&&!playback.status().buffering;},"Held graph not ready");
            playing.set_value();
            request.hold();
            const auto evidence=playback.drain_graph(std::string(32,'e'));
            require(evidence.has_value(),"Held graph drain failed: "+playback.status().error);
            completed.set_value(*evidence);
        } catch(...) { try{playing.set_value();}catch(...){} completed.set_exception(std::current_exception()); }
    });
    // Always open gates before joining, including a failed fixture assertion.
    struct Cleanup {Gate& a;Gate& b;Gate& c;std::thread& owner;~Cleanup(){a.release();b.release();c.release();if(owner.joinable())owner.join();}} cleanup{callback,finality,request,owner};
    playing.get_future().wait();
    if(result.wait_for(std::chrono::milliseconds(0))==std::future_status::ready)released(result.get());
    armed=true;callback.wait();request.release();
    require(result.wait_for(std::chrono::milliseconds(80))==std::future_status::timeout,
            "Drain completed while the actual appsink callback still held its sample");
    callback.release();finality.wait();
    require(result.wait_for(std::chrono::milliseconds(80))==std::future_status::timeout,
            "NULL/object disposal bypassed independent backend finality");
    finality.release();
    require(result.wait_for(std::chrono::seconds(5))==std::future_status::ready,"Held graph never completed after both gates");
    released(result.get());
}

void checkpoint_rebind_and_retry_budget_continuity(const char* media) {
    Playback playback(options());
    require(playback.load(media),"Cannot load checkpoint graph");
    ready(playback,[&]{return playback.latest_frame()&&playback.status().playing&&playback.status().position>.1;},"Checkpoint graph not playing");
    require(playback.pause()&&playback.seek(.75),"Cannot queue paused checkpoint seek");
    playback.set_volume(.41);playback.set_muted(true);
    auto state=playback.status();
    if(state.audio_tracks.size()>1)require(playback.set_audio_track(1),"Cannot select second audio track");
    if(!state.subtitle_tracks.empty())require(playback.set_subtitle_track(0),"Cannot select subtitle");
    const auto first=playback.drain_graph(std::string(32,'e'));
    require(first.has_value(),"Checkpoint graph drain failed: "+playback.status().error);released(*first);
    const auto saved=playback.checkpoint();require(saved.has_value(),"Managed drain discarded checkpoint");
    require(saved->position>=.70&&!saved->playing&&saved->muted&&std::abs(saved->volume-.41)<.001,
            "Managed capture lost queued seek, intent or sound settings");
    require(playback.drain_graph(std::string(32,'e'))->json()==first->json(),"Terminal graph replay changed evidence");
    require(!playback.resume()&&!playback.load(media),"Sealed graph reacquired without a fresh grant");
    require(!playback.rebind_managed_graph(binding()),"Old graph binding was reused");
    require(playback.rebind_managed_graph(binding(2))&&playback.resume(),"Fresh grant could not rebuild retained checkpoint");
    ready(playback,[&]{return playback.latest_frame()&&playback.status().paused&&!playback.status().buffering&&
        std::abs(playback.latest_frame()->position-saved->position)<.08;},"Managed rebuild lost paused seek target");
    const auto after=playback.capture_checkpoint();require(after.has_value(),"Cannot capture rebuilt graph");
    require(after->audio_track==saved->audio_track&&after->subtitle_track==saved->subtitle_track&&
        after->video_decode_retry_attempted==saved->video_decode_retry_attempted&&
        after->video_decode_fallbacks==saved->video_decode_fallbacks&&
        after->software_video_preferred==saved->software_video_preferred&&
        after->audio_fallback_attempted==saved->audio_fallback_attempted&&
        after->audio_recovery_attempted==saved->audio_recovery_attempted,
        "Fresh managed grant reset track/retry state");
    const auto second=playback.drain_graph(std::string(32,'f'));
    require(second.has_value()&&second->binding.epoch==2&&second->binding.generation==binding(2).generation,
            "Second graph receipt reused an old binding");released(*second);
}
void changed_media_is_not_an_accepted_checkpoint(const char* media) {
    const auto path=std::filesystem::temp_directory_path()/("tyrell-managed-changed-"+std::to_string(Clock::now().time_since_epoch().count())+".mp4");
    struct Remove {std::filesystem::path path;~Remove(){std::error_code error;std::filesystem::remove(path,error);}} cleanup{path};
    std::filesystem::copy_file(media,path);
    Playback playback(options());require(playback.load(path.string()),"Cannot load identity fixture");
    ready(playback,[&]{return playback.latest_frame()&&!playback.status().buffering;},"Identity fixture not ready");
    std::ofstream(path,std::ios::app)<<"changed";
    require(!playback.drain_graph(std::string(32,'e'))&&!playback.graph_drain_evidence(),"Changed media produced a managed checkpoint receipt");
    require(!playback.rebind_managed_graph(binding(2)),"Identity failure reopened a graph");
}
}

int main(int argc,char** argv) {
    try {
        require(argc==3,"Expected clock and multitrack media fixtures");
        invalid_profiles_and_no_legacy_fallback(argv[1]);
        empty_and_destructively_stopped_graphs_can_finish(argv[1]);
        missing_finality_is_not_release(argv[1]);
        prebinding_sink_failure_destroys_registered_callbacks_safely(argv[1]);
        actual_streaming_callback_and_terminal_hook_are_held(argv[1]);
        checkpoint_rebind_and_retry_budget_continuity(argv[2]);
        changed_media_is_not_an_accepted_checkpoint(argv[1]);
        std::cout<<"PASS: actual managed Playback callbacks, object finalization, explicit finality and fresh-grant checkpoint rebuild\n";
        return 0;
    }catch(const std::exception& error){std::cerr<<"Managed graph: "<<error.what()<<'\n';return 1;}
}
