#include "actor_player_control.h"
#include <cmath>
#include <limits>
#include <stdexcept>

namespace tyrell::video {
namespace {
bool same(const MediaActorBinding& a,const MediaActorBinding& b) {
    return a.epoch==b.epoch&&a.resource==b.resource&&a.generation==b.generation;
}
bool valid_id(const std::string& id) {
    return id.size()==32&&id.find_first_not_of("0123456789abcdef")==std::string::npos;
}
}
ActorPlayerControl::ActorPlayerControl(MediaActor& actor,MediaActorBinding binding,PermitSource permits,SubmissionObserver submissions)
    :actor_(actor),binding_(std::move(binding)),permits_(std::move(permits)),submissions_(std::move(submissions)) {
    if(!permits_||!submissions_||!binding_.epoch||!valid_id(binding_.resource)||!valid_id(binding_.generation))
        throw std::invalid_argument("Trusted actor binding and permit reserve required");
    poll();
}
bool ActorPlayerControl::input_allowed() const {
    const auto snapshot=actor_.snapshot();
    return !frozen_&&same(snapshot->binding,binding_)&&!snapshot->sealed&&!snapshot->unknown&&
           (snapshot->phase==MediaActorPhase::Foreground||snapshot->phase==MediaActorPhase::Starting);
}
std::string ActorPlayerControl::take_notice() { auto value=std::move(notice_);notice_.clear();return value; }
MediaCommand ActorPlayerControl::command(MediaCommandKind kind) const { MediaCommand value;value.kind=kind;value.binding=binding_;return value; }
bool ActorPlayerControl::submit(MediaCommand value,bool new_intent) {
    if(!input_allowed()||value.path.size()>4096||value.external_subtitle.size()>4096||!std::isfinite(value.number)||
       std::abs(value.number)>1e12||value.index < -1||value.index>4096||
       (value.output!=Output::Lcd&&value.output!=Output::Hdmi)||
       pending_.size()+awaiting_.size()>=MediaActor::command_capacity||request_==std::numeric_limits<std::uint64_t>::max()||
       (new_intent&&intent_==std::numeric_limits<std::uint64_t>::max())) return false;
    value.request=request_+1;
    ++request_;if(new_intent)++intent_;
    awaiting_.push_back({intent_,value});
    if(new_intent) { presentation_request_=value.request;presentation_pending_=true;ignored_error_.clear();frame_.reset(); }
    return true;
}
void ActorPlayerControl::dispatch_ready() {
    while(!awaiting_.empty()) {
        auto value=awaiting_.front();
        const auto permit=permits_(value.command);
        if(!permit)break;
        if(!valid_id(*permit))throw std::invalid_argument("Malformed trusted media permit");
        value.command.operation=*permit;
        const bool accepted=input_allowed()&&actor_.submit(value.command);
        if(accepted)pending_[*permit]=value;
        awaiting_.pop_front();
        submissions_(value.command,accepted);
        if(!accepted&&value.intent==intent_&&value.command.request==presentation_request_) {
            presentation_pending_=false;status_.buffering=false;
            notice_="Playback request was not accepted; try again.";
        }
    }
}
std::vector<MediaCommand> ActorPlayerControl::awaiting_permits() const {
    std::vector<MediaCommand> result;for(const auto& value:awaiting_)result.push_back(value.command);return result;
}
bool ActorPlayerControl::load(const std::string& path,const std::string& subtitle) {
    auto value=command(MediaCommandKind::Load);value.path=path;value.external_subtitle=subtitle;value.flag=subtitle.empty();
    if(!submit(value,true)) return false;
    const auto volume=status_.volume;const auto muted=status_.muted;
    status_={};status_.path=path;status_.buffering=true;status_.volume=volume;status_.muted=muted;
    return true;
}
bool ActorPlayerControl::stop() {
    if(!submit(command(MediaCommandKind::Stop),true)) return false;
    status_={};return true;
}
bool ActorPlayerControl::toggle_pause() { return submit(command(MediaCommandKind::TogglePause)); }
bool ActorPlayerControl::seek_relative(double number) { auto value=command(MediaCommandKind::SeekRelative);value.number=number;return submit(value); }
bool ActorPlayerControl::set_output(Output output) { auto value=command(MediaCommandKind::Output);value.output=output;return submit(value); }
bool ActorPlayerControl::set_audio_track(int index) { auto value=command(MediaCommandKind::AudioTrack);value.index=index;return submit(value); }
bool ActorPlayerControl::set_subtitle_track(int index) { auto value=command(MediaCommandKind::SubtitleTrack);value.index=index;return submit(value); }
bool ActorPlayerControl::set_audio_sink(const std::string& path) { auto value=command(MediaCommandKind::AudioSink);value.path=path;return submit(value); }
void ActorPlayerControl::set_volume(double number) {
    auto value=command(MediaCommandKind::Volume);value.number=number;if(!submit(value))notice_="Player is busy; try again.";
}
void ActorPlayerControl::set_muted(bool flag) {
    auto value=command(MediaCommandKind::Muted);value.flag=flag;if(!submit(value))notice_="Player is busy; try again.";
}
void ActorPlayerControl::poll() {
    dispatch_ready();
    actor_.consume_wake();
    observe(actor_.peek_events(64));
    refresh_presentation();
}
void ActorPlayerControl::observe(const std::vector<MediaActorEvent>& events) {
    for(const auto& event:events) {
        if(!observed_.insert(event.operation).second) continue;
        if(event.kind==MediaCommandKind::Regrant&&same(event.binding,binding_)&&event.request==presentation_request_) {
            presentation_pending_=false;
            frozen_=revoked_||event.outcome!=MediaEventOutcome::Completed;
            if(event.outcome!=MediaEventOutcome::Completed)notice_=event.error.empty()?"Playback could not resume":event.error;
        }
        const auto found=pending_.find(event.operation);
        if(found==pending_.end()||found->second.intent!=intent_||!same(event.binding,binding_)) continue;
        if(event.request==presentation_request_) presentation_pending_=false;
        if(event.outcome==MediaEventOutcome::Failed||event.outcome==MediaEventOutcome::Unknown) {
            const auto error=event.error.empty()?"Playback command could not finish":event.error;
            if(event.kind==MediaCommandKind::Load) status_.error=error;
            else { notice_=error;ignored_error_=error; }
        }
    }
}
void ActorPlayerControl::refresh_presentation() {
    const auto snapshot=actor_.snapshot();
    if(!same(snapshot->binding,binding_)||presentation_pending_||frozen_)return;
    const auto load_error=status_.error;
    status_=snapshot->media.status;
    if(status_.error==ignored_error_)status_.error.clear();
    if(status_.error.empty()&&!load_error.empty())status_.error=load_error;
    frame_=snapshot->media.frame;
}
void ActorPlayerControl::revoke_admission() noexcept { revoked_=frozen_=true;frame_.reset(); }
bool ActorPlayerControl::seal(std::uint64_t request,const std::string& operation) {
    if(!actor_.seal(binding_,request,operation))return false;
    revoke_admission();
    // Ready reservations get an explicit no-dispatch result. Unreserved UI
    // intents have no backend/authority operation to retire.
    for(auto& value:awaiting_)if(const auto permit=permits_(value.command)) {
        value.command.operation=*permit;submissions_(value.command,false);
    }
    awaiting_.clear();return true;
}
bool ActorPlayerControl::cancel(std::uint64_t request) { return actor_.cancel(binding_,request); }
bool ActorPlayerControl::regrant(MediaActorBinding binding,std::string lifetime,std::uint64_t request,const std::string& operation) {
    if(!pending_.empty()||!awaiting_.empty()||intent_==std::numeric_limits<std::uint64_t>::max()||
       !actor_.regrant(binding,std::move(lifetime),request,operation))return false;
    binding_=std::move(binding);revoked_=false;frozen_=true;presentation_pending_=true;presentation_request_=request;
    request_=request;++intent_;
    auto resume=command(MediaCommandKind::Regrant);resume.request=request;resume.operation=operation;
    pending_[operation]={intent_,std::move(resume)};
    status_={};frame_.reset();return true;
}
std::vector<MediaActorEvent> ActorPlayerControl::peek_audit(std::size_t maximum) const { return actor_.peek_events(maximum); }
bool ActorPlayerControl::acknowledge_audit(std::size_t count) {
    auto events=actor_.peek_events(64);if(count>events.size())return false;
    events.resize(count);
    observe(events);refresh_presentation();
    if(!actor_.acknowledge_events(count))return false;
    for(std::size_t i=0;i<count;++i) { pending_.erase(events[i].operation);observed_.erase(events[i].operation); }
    return true;
}
} // namespace tyrell::video
