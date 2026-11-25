#pragma once
#include "media_actor.h"
#include "player_control.h"
#include <map>
#include <set>
#include <deque>

namespace tyrell::video {

// One UI/control-thread adapter. The supervisor owns and retains MediaActor;
// destroying this borrowed facade never waits for a worker or destroys a graph.
class ActorPlayerControl final : public PlayerControl {
public:
    // Must pop an already journaled permit from a bounded local reserve. No
    // filesystem/network/backend call belongs in the UI acceptance path.
    // A nonblocking mailbox lookup for this exact request/payload. A missing
    // permit leaves the bounded UI intent queued; the coordinator journals it
    // off the UI thread before providing a matching permit.
    using PermitSource = std::function<std::optional<std::string>(const MediaCommand&)>;
    // Nonblocking submission-result queue; rejection must retire an unused
    // reserved permit durably, rather than silently losing the reservation.
    using SubmissionObserver = std::function<void(const MediaCommand&,bool)>;
    ActorPlayerControl(MediaActor& actor,MediaActorBinding binding,PermitSource permits,SubmissionObserver submissions);
    bool asynchronous() const override { return true; }
    bool input_allowed() const override;
    bool render_allowed() const override { return input_allowed(); }
    std::uint64_t control_epoch() const override { return binding_.epoch; }
    std::vector<AudioSink> available_audio_sinks() const override { return {}; }
    std::string take_notice() override;
    bool load(const std::string&,const std::string& = {}) override;
    void poll() override;
    Status status() const override { return status_; }
    std::shared_ptr<const Frame> latest_frame() const override { return frame_; }
    bool toggle_pause() override;
    bool seek_relative(double) override;
    bool set_output(Output) override;
    bool set_audio_track(int) override;
    bool set_subtitle_track(int) override;
    bool set_audio_sink(const std::string&) override;
    void set_volume(double) override;
    void set_muted(bool) override;
    bool stop() override;

    // Trusted lifecycle/coordinator entry points, never user key bindings.
    bool seal(std::uint64_t request,const std::string& drain_operation);
    bool cancel(std::uint64_t request);
    bool regrant(MediaActorBinding,std::string lifetime_operation,std::uint64_t request,const std::string& resume_operation);
    std::vector<MediaActorEvent> peek_audit(std::size_t maximum=64) const;
    bool acknowledge_audit(std::size_t count); // Only after durable recording.
    std::vector<MediaCommand> awaiting_permits() const;
    int wake_fd() const noexcept { return actor_.wake_fd(); }
private:
    struct Pending { std::uint64_t intent;MediaCommand command; };
    bool submit(MediaCommand,bool new_intent=false);
    void dispatch_ready();
    void observe(const std::vector<MediaActorEvent>&);
    void refresh_presentation();
    MediaCommand command(MediaCommandKind) const;
    MediaActor& actor_;
    MediaActorBinding binding_;
    PermitSource permits_;
    SubmissionObserver submissions_;
    std::map<std::string,Pending> pending_;
    std::deque<Pending> awaiting_;
    std::set<std::string> observed_;
    std::uint64_t request_=0,intent_=0,presentation_request_=0;
    bool frozen_=false,presentation_pending_=false;
    Status status_;
    std::shared_ptr<const Frame> frame_;
    std::string notice_,ignored_error_;
};

} // namespace tyrell::video
