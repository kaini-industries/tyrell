#include "media_actor.h"

#include <algorithm>
#include <cmath>
#include <condition_variable>
#include <cctype>
#include <deque>
#include <filesystem>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <unordered_set>
#include <utility>

#include <cerrno>
#include <fcntl.h>
#include <unistd.h>

namespace tyrell::video {
namespace {
bool id(const std::string& value) {
    return value.size()==32 && value.find_first_not_of("0123456789abcdef")==std::string::npos;
}
bool valid(const MediaActorBinding& value) { return value.epoch && id(value.resource) && id(value.generation); }
bool same(const MediaActorBinding& left,const MediaActorBinding& right) {
    return left.epoch==right.epoch && left.resource==right.resource && left.generation==right.generation;
}
bool ordinary(MediaCommandKind kind) {
    return kind>=MediaCommandKind::Load && kind<=MediaCommandKind::Stop;
}
bool complete_graph(const GraphDrainEvidence& e,const MediaActorBinding& binding,const std::string& operation) {
    return same(e.binding,binding)&&e.operation==operation&&id(e.operation)&&
        e.callbacks_started==e.callbacks_completed&&!e.active_callbacks&&!e.callback_contexts&&!e.live_objects&&
        e.pipeline_finalized&&e.local_handles_released&&e.legacy_helpers_unused&&id(e.finality_receipt);
}
std::string matching_srt(const std::string& path) {
    const std::filesystem::path file(path);
    std::error_code error;
    for(std::filesystem::directory_iterator it(file.parent_path(),error),end;!error&&it!=end;it.increment(error)) {
        const auto candidate=it->path();auto extension=candidate.extension().string();
        std::transform(extension.begin(),extension.end(),extension.begin(),[](unsigned char c){return static_cast<char>(std::tolower(c));});
        if(extension==".srt"&&candidate.stem()==file.stem()&&it->is_regular_file(error))return candidate.string();
    }
    return {};
}
void clip(std::string& value,std::size_t maximum) { if(value.size()>maximum)value.resize(maximum); }
MediaBackendSnapshot bounded(MediaBackendSnapshot value) {
    // Presentation metadata is bounded before it enters the control mutex.
    // Playback retains its complete private checkpoint/retry state for resume.
    auto& status=value.status;
    clip(status.path,4096);clip(status.error,4096);clip(status.notice,4096);clip(status.audio_output,1024);
    clip(status.video_output_caps,2048);clip(status.video_output_memory,512);clip(status.video_decoder,256);
    clip(status.video_decode_fallback_reason,2048);
    for(auto* tracks:{&status.audio_tracks,&status.subtitle_tracks}) {
        if(tracks->size()>64)tracks->resize(64);
        for(auto& track:*tracks){clip(track.title,256);clip(track.language,64);clip(track.codec,128);}
    }
    if(value.checkpoint) {
        auto& checkpoint=*value.checkpoint;
        if(checkpoint.media.path.size()>4096||checkpoint.media.canonical_path.size()>4096||
           (checkpoint.external_subtitle&&(checkpoint.external_subtitle->path.size()>4096||
            checkpoint.external_subtitle->canonical_path.size()>4096)))
            throw std::runtime_error("Checkpoint identity exceeds actor publication bounds");
        clip(checkpoint.requested_audio_sink,4096);clip(checkpoint.video_decode_fallback_reason,2048);
    }
    if(value.frame&&value.frame->pixels.size()>4*1024*1024)
        throw std::runtime_error("Frame exceeds actor publication bounds");
    return value;
}
void secure_pipe(int (&fd)[2]) {
    if(pipe(fd)!=0) throw std::runtime_error("Cannot create media actor wake pipe");
    for(int value:fd) {
        const int flags=fcntl(value,F_GETFL), descriptor=fcntl(value,F_GETFD);
        if(flags<0||descriptor<0||fcntl(value,F_SETFL,flags|O_NONBLOCK)<0||fcntl(value,F_SETFD,descriptor|FD_CLOEXEC)<0) {
            close(fd[0]);close(fd[1]);fd[0]=fd[1]=-1;
            throw std::runtime_error("Cannot secure media actor wake pipe");
        }
    }
}

class PlaybackBackend final : public MediaActorBackend {
public:
    explicit PlaybackBackend(PlaybackOptions options) : player_(std::move(options)) {}
    bool execute(const MediaCommand& c,std::string& error) override {
        bool success=true;
        switch(c.kind) {
            case MediaCommandKind::Load:success=player_.load(c.path,
                c.flag&&c.external_subtitle.empty()?matching_srt(c.path):c.external_subtitle);break;
            case MediaCommandKind::Play:success=player_.play();break;
            case MediaCommandKind::Pause:success=player_.pause();break;
            case MediaCommandKind::TogglePause:success=player_.toggle_pause();break;
            case MediaCommandKind::Seek:success=player_.seek(c.number);break;
            case MediaCommandKind::SeekRelative:success=player_.seek_relative(c.number);break;
            case MediaCommandKind::Output:success=player_.set_output(c.output);break;
            case MediaCommandKind::AudioTrack:success=player_.set_audio_track(c.index);break;
            case MediaCommandKind::SubtitleTrack:success=player_.set_subtitle_track(c.index);break;
            case MediaCommandKind::AudioSink:success=player_.set_audio_sink(c.path);break;
            case MediaCommandKind::Volume:player_.set_volume(c.number);break;
            case MediaCommandKind::Muted:player_.set_muted(c.flag);break;
            case MediaCommandKind::Stop:player_.stop();break;
            default:success=false;break;
        }
        if(!success) { error=player_.status().error; if(error.empty()) error="Media operation refused"; }
        return success;
    }
    void poll() override { player_.poll(); }
    MediaBackendSnapshot snapshot() const override {
        return {player_.status(),player_.latest_frame(),player_.checkpoint(),player_.resources()};
    }
    std::optional<GraphDrainEvidence> drain(const std::string& operation,std::string& error) override {
        auto result=player_.drain_graph(operation);
        if(!result) { error=player_.status().error; if(error.empty()) error="Graph completion remains unknown"; }
        return result;
    }
    bool regrant(const MediaActorBinding& binding,std::string& error) override {
        if(!player_.rebind_managed_graph(binding)||!player_.resume()) {
            error=player_.status().error;if(error.empty())error="Fresh graph grant/rebuild refused";return false;
        }
        return true;
    }
    void stop() override { player_.stop(); }
private:
    Playback player_;
};
} // namespace

std::unique_ptr<MediaActorBackend> make_playback_actor_backend(PlaybackOptions options) {
    return std::make_unique<PlaybackBackend>(std::move(options));
}

struct MediaActor::Impl {
    mutable std::mutex mutex;
    std::condition_variable condition;
    std::thread worker;
    int wake[2]{-1,-1};
    Factory factory;
    std::chrono::milliseconds interval;
    MediaActorBinding binding;
    std::string lifetime;
    std::deque<MediaCommand> queue;
    std::deque<MediaActorEvent> events;
    std::optional<MediaCommand> drain,shutdown;
    std::unordered_set<std::string> history;
    std::size_t credits=0,epochs=1;
    std::uint64_t highest_request=0;
    MediaActorPhase phase=MediaActorPhase::Starting;
    bool sealed=false,cancelled=false,unknown=false,active=true,drain_pending=false;
    std::string active_operation,error;
    MediaBackendSnapshot media;
    std::optional<GraphDrainEvidence> graph;
    std::shared_ptr<const MediaActorSnapshot> published;

    Impl(MediaActorBinding value,std::string operation,Factory make,std::chrono::milliseconds poll)
        :factory(std::move(make)),interval(poll),binding(std::move(value)),lifetime(std::move(operation)),active_operation(lifetime) {
        secure_pipe(wake);history.insert(lifetime);publish();
    }
    ~Impl() { for(int fd:wake)if(fd>=0)close(fd); }
    void notify() noexcept { const char byte='M';if(wake[1]>=0) { const auto ignored=write(wake[1],&byte,1);(void)ignored; } }
    // mutex held; this never calls the backend or waits for graph work.
    void publish() {
        auto value=std::make_shared<MediaActorSnapshot>();
        value->binding=binding;value->phase=phase;value->media=media;
        if(sealed)value->media.frame.reset();
        value->lifetime_operation=lifetime;value->active_operation=active_operation;
        value->graph=graph;value->queued=queue.size();value->unacknowledged=events.size();
        value->sealed=sealed;value->cancelled=cancelled;value->unknown=unknown;value->error=error;
        published=std::move(value);notify();
    }
    void event(const MediaCommand& c,MediaEventOutcome outcome,std::string reason={},std::optional<GraphDrainEvidence> evidence={}) {
        // Normal acceptance is credit-limited; drain and shutdown have their
        // own reserved slots. Regrant requires the old prefix acknowledged.
        if(events.size()>=command_capacity+2) throw std::logic_error("Media actor event invariant violated");
        clip(reason,4096);events.push_back({c.kind,c.binding,c.request,c.operation,outcome,std::move(evidence),std::move(reason)});
    }
    void suppress(const char* reason) {
        while(!queue.empty()) { event(queue.front(),MediaEventOutcome::Suppressed,reason);queue.pop_front(); }
    }
    void failed(const char* message) {
        sealed=true;unknown=true;phase=MediaActorPhase::Unknown;error=message;clip(error,4096);suppress("Epoch lost before execution");
    }
    bool reserve(const std::string& operation,std::size_t maximum) {
        if(!id(operation)||history.count(operation)||history.size()>=maximum)return false;
        history.insert(operation);return true;
    }
    bool fenced() const {
        return phase==MediaActorPhase::Drained&&sealed&&!active&&!unknown&&queue.empty()&&!drain_pending&&!shutdown&&
            events.empty()&&!credits&&graph&&complete_graph(*graph,binding,graph->operation)&&
            media.resources.local_handles_released()&&!media.resources.external_cleanup_unconfirmed;
    }
    void run() {
        std::unique_ptr<MediaActorBackend> backend;
        try {
            backend=factory();
            if(!backend)throw std::runtime_error("Media actor factory returned no backend");
            auto initial=bounded(backend->snapshot());
            std::lock_guard<std::mutex> lock(mutex);media=std::move(initial);active=false;active_operation.clear();
            phase=shutdown?MediaActorPhase::Stopping:sealed?MediaActorPhase::Sealed:MediaActorPhase::Foreground;publish();
        } catch(const std::exception& failure) {
            std::lock_guard<std::mutex> lock(mutex);active=false;active_operation.clear();failed(failure.what());publish();
        } catch(...) {
            std::lock_guard<std::mutex> lock(mutex);active=false;active_operation.clear();failed("Media actor startup failed");publish();
        }
        auto next_poll=std::chrono::steady_clock::now();
        for(;;) {
            std::optional<MediaCommand> command;
            bool polling=false;
            {
                std::unique_lock<std::mutex> lock(mutex);
                for(;;) {
                    if(drain_pending) { command=drain;drain_pending=false;break; }
                    if(shutdown) { command=shutdown;break; }
                    if(!queue.empty()) { command=std::move(queue.front());queue.pop_front();break; }
                    const bool may_poll=!sealed&&!unknown&&backend;
                    if(may_poll&&std::chrono::steady_clock::now()>=next_poll) { polling=true;break; }
                    if(may_poll)condition.wait_until(lock,next_poll);
                    else condition.wait(lock);
                }
                active=true;active_operation=polling?lifetime:command->operation;publish();
            }
            if(polling) {
                try {
                    backend->poll();auto next=bounded(backend->snapshot());
                    std::lock_guard<std::mutex> lock(mutex);media=std::move(next);active=false;active_operation.clear();publish();
                } catch(const std::exception& failure) {
                    std::lock_guard<std::mutex> lock(mutex);active=false;active_operation.clear();failed(failure.what());publish();
                } catch(...) {
                    std::lock_guard<std::mutex> lock(mutex);active=false;active_operation.clear();failed("Automatic media work failed");publish();
                }
                next_poll=std::chrono::steady_clock::now()+interval;
                continue;
            }
            const auto c=*command;
            if(c.kind==MediaCommandKind::Shutdown) {
                std::optional<GraphDrainEvidence> final_graph;
                std::optional<MediaBackendSnapshot> final_media;
                std::string failure;
                bool success=false;
                try {
                    if(backend) {
                        // A shutdown permit also covers any required drain and
                        // the backend destructor. No caller joins this work.
                        {
                            std::lock_guard<std::mutex> lock(mutex);final_graph=graph;
                        }
                        const bool cached=bool(final_graph);
                        if(!cached)final_graph=backend->drain(c.operation,failure);
                        const bool verified=final_graph&&complete_graph(*final_graph,c.binding,cached?final_graph->operation:c.operation);
                        if(!verified&&failure.empty())failure="Shutdown graph completion is incomplete or stale";
                        backend->stop();final_media=bounded(backend->snapshot());
                        backend.reset();
                        success=verified&&final_media->resources.local_handles_released()&&!final_media->resources.external_cleanup_unconfirmed;
                    }
                } catch(const std::exception& value) { failure=value.what();backend.reset(); }
                catch(...) { failure="Media shutdown completion unknown";backend.reset(); }
                {
                    std::lock_guard<std::mutex> lock(mutex);
                    if(final_media)media=std::move(*final_media);
                    if(!success||unknown)unknown=true;
                    if(success)graph=final_graph;
                    event(c,unknown?MediaEventOutcome::Unknown:MediaEventOutcome::Completed,failure,final_graph);
                    sealed=true;active=false;active_operation.clear();phase=MediaActorPhase::Finished;
                    media.frame.reset();error=failure;clip(error,4096);publish();
                }
                condition.notify_all();return;
            }
            bool success=false;
            std::string failure;
            std::optional<GraphDrainEvidence> drained;
            std::optional<MediaBackendSnapshot> next;
            bool exceptional=false;
            try {
                if(!backend)throw std::runtime_error("Media backend unavailable");
                if(c.kind==MediaCommandKind::Drain) {
                    drained=backend->drain(c.operation,failure);success=drained&&complete_graph(*drained,c.binding,c.operation);
                    if(!success&&failure.empty())failure="Graph completion evidence is incomplete or stale";
                }
                else if(c.kind==MediaCommandKind::Regrant)success=backend->regrant(c.binding,failure);
                else success=backend->execute(c,failure);
                next=bounded(backend->snapshot());
                if(c.kind==MediaCommandKind::Drain&&success&&(!next->resources.local_handles_released()||
                   next->resources.external_cleanup_unconfirmed)) {
                    success=false;failure="Graph evidence contradicts retained backend resources";
                }
            } catch(const std::exception& value) { failure=value.what();exceptional=true; }
            catch(...) { failure="Media operation completion unknown";exceptional=true; }
            {
                std::lock_guard<std::mutex> lock(mutex);
                if(next)media=std::move(*next);
                active=false;active_operation.clear();
                if(c.kind==MediaCommandKind::Drain) {
                    if(success&&!unknown) { graph=drained;phase=MediaActorPhase::Drained; }
                    else { unknown=true;phase=MediaActorPhase::Unknown;error=failure;clip(error,4096); }
                    event(c,success&&!unknown?MediaEventOutcome::Completed:MediaEventOutcome::Unknown,failure,drained);
                } else {
                    if(exceptional||(!success&&c.kind==MediaCommandKind::Regrant)) {
                        unknown=true;sealed=true;phase=MediaActorPhase::Unknown;error=failure;clip(error,4096);
                        event(c,MediaEventOutcome::Unknown,failure);suppress("Epoch lost before execution");
                    } else {
                        event(c,success?MediaEventOutcome::Completed:MediaEventOutcome::Failed,failure);
                        if(c.kind==MediaCommandKind::Regrant&&!sealed)phase=MediaActorPhase::Foreground;
                    }
                }
                publish();
            }
            next_poll=std::chrono::steady_clock::now()+interval;
        }
    }
};

MediaActor::MediaActor(MediaActorBinding binding,std::string operation,Factory factory,std::chrono::milliseconds interval) {
    if(!valid(binding)||!id(operation)||!factory||interval.count()<1||interval.count()>1000)
        throw std::invalid_argument("Invalid registered media actor configuration");
    impl_=std::make_unique<Impl>(std::move(binding),std::move(operation),std::move(factory),interval);
    impl_->worker=std::thread([this]{impl_->run();});
}
MediaActor::~MediaActor() {
    if(!impl_->worker.joinable())return;
    // Explicitly blocking lifetime cleanup. UI/controller APIs never invoke it;
    // their separately owned supervisor retains the actor until Finished.
    {
        std::lock_guard<std::mutex> lock(impl_->mutex);
        if(!impl_->shutdown&&impl_->phase!=MediaActorPhase::Finished) {
            impl_->sealed=true;impl_->suppress("Actor destruction requested");
            MediaCommand c;c.kind=MediaCommandKind::Shutdown;c.binding=impl_->binding;
            c.operation=impl_->lifetime; // Abandonment remains under old held lifetime.
            impl_->shutdown=std::move(c);impl_->unknown=true;impl_->publish();
        }
    }
    impl_->condition.notify_all();impl_->worker.join();
}

bool MediaActor::submit(MediaCommand c) {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if(!same(c.binding,impl_->binding)||!ordinary(c.kind)||!c.request||c.request<=impl_->highest_request||
       impl_->sealed||impl_->shutdown||impl_->unknown||impl_->credits>=command_capacity||
       c.path.size()>4096||c.external_subtitle.size()>4096||!std::isfinite(c.number)||
       std::abs(c.number)>1e12||c.index < -1||c.index>4096||
       (c.output!=Output::Lcd&&c.output!=Output::Hdmi)||!impl_->reserve(c.operation,operation_history_limit-34))return false;
    impl_->highest_request=c.request;++impl_->credits;impl_->queue.push_back(std::move(c));
    impl_->publish();impl_->condition.notify_all();return true;
}
bool MediaActor::seal(const MediaActorBinding& binding,std::uint64_t request,const std::string& operation) {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if(!same(binding,impl_->binding)||!request||impl_->shutdown||impl_->unknown)return false;
    if(impl_->drain)return impl_->drain->request==request&&impl_->drain->operation==operation;
    if(!impl_->reserve(operation,operation_history_limit-1))return false;
    MediaCommand c;c.kind=MediaCommandKind::Drain;c.binding=binding;c.request=request;c.operation=operation;
    impl_->sealed=true;impl_->phase=MediaActorPhase::Sealed;impl_->drain=c;impl_->drain_pending=true;
    impl_->suppress("Epoch sealed before execution");impl_->publish();impl_->condition.notify_all();return true;
}
bool MediaActor::cancel(const MediaActorBinding& binding,std::uint64_t request) {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if(!same(binding,impl_->binding)||!impl_->drain||impl_->drain->request!=request||impl_->phase==MediaActorPhase::Finished)return false;
    impl_->cancelled=true;impl_->publish();return true;
}
bool MediaActor::regrant(MediaActorBinding binding,std::string lifetime,std::uint64_t request,const std::string& operation) {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if(!valid(binding)||binding.epoch<=impl_->binding.epoch||binding.resource==impl_->binding.resource||
       binding.generation==impl_->binding.generation||!request||!impl_->fenced()||impl_->epochs>=16||
       !id(lifetime)||!id(operation)||lifetime==operation||impl_->history.count(lifetime)||impl_->history.count(operation)||
       impl_->history.size()>=operation_history_limit-4)return false;
    impl_->history.insert(lifetime);impl_->history.insert(operation);++impl_->epochs;
    impl_->binding=std::move(binding);impl_->lifetime=std::move(lifetime);impl_->sealed=false;impl_->cancelled=false;
    impl_->drain.reset();impl_->graph.reset();impl_->phase=MediaActorPhase::Starting;
    impl_->highest_request=request;
    MediaCommand c;c.kind=MediaCommandKind::Regrant;c.binding=impl_->binding;c.request=request;c.operation=operation;
    impl_->queue.push_back(std::move(c));++impl_->credits;impl_->publish();impl_->condition.notify_all();return true;
}
bool MediaActor::request_shutdown(const MediaActorBinding& binding,std::uint64_t request,const std::string& operation) {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if(!same(binding,impl_->binding)||!request)return false;
    if(impl_->shutdown)return impl_->shutdown->request==request&&impl_->shutdown->operation==operation;
    if(impl_->phase==MediaActorPhase::Finished||!impl_->reserve(operation,operation_history_limit))return false;
    MediaCommand c;c.kind=MediaCommandKind::Shutdown;c.binding=binding;c.request=request;c.operation=operation;
    impl_->sealed=true;impl_->suppress("Shutdown before execution");impl_->shutdown=c;
    if(!impl_->drain_pending)impl_->phase=MediaActorPhase::Stopping;
    impl_->publish();impl_->condition.notify_all();return true;
}
std::shared_ptr<const MediaActorSnapshot> MediaActor::snapshot() const {
    std::lock_guard<std::mutex> lock(impl_->mutex);return impl_->published;
}
std::vector<MediaActorEvent> MediaActor::peek_events(std::size_t maximum) const {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    maximum=std::min({maximum,std::size_t(64),impl_->events.size()});
    return {impl_->events.begin(),impl_->events.begin()+maximum};
}
bool MediaActor::acknowledge_events(std::size_t count) {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if(count>impl_->events.size())return false;
    while(count--) {
        if(ordinary(impl_->events.front().kind)||impl_->events.front().kind==MediaCommandKind::Regrant)--impl_->credits;
        impl_->events.pop_front();
    }
    impl_->publish();return true;
}
std::vector<MediaActorEvent> MediaActor::take_events(std::size_t maximum) {
    auto result=peek_events(maximum);acknowledge_events(result.size());return result;
}
int MediaActor::wake_fd() const noexcept { return impl_->wake[0]; }
void MediaActor::consume_wake() noexcept {
    char bytes[128];while(read(impl_->wake[0],bytes,sizeof(bytes))>0){}
}
bool MediaActor::finished() const { return snapshot()->phase==MediaActorPhase::Finished; }
bool MediaActor::join_finished() {
    if(!finished())return false;
    if(impl_->worker.joinable())impl_->worker.join();
    return true;
}
bool MediaActor::epoch_fenced(const MediaActorBinding& binding) const {
    std::lock_guard<std::mutex> lock(impl_->mutex);return same(binding,impl_->binding)&&impl_->fenced();
}

} // namespace tyrell::video
