#pragma once

#include "playback.h"

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace tyrell::video {

// Native correlation only. The trusted coordinator binds this to its full
// incarnation/app/lease identity and journals permits before calling the actor.
using MediaActorBinding = ManagedGraphBinding;

enum class MediaCommandKind {
    Load, Play, Pause, TogglePause, Seek, SeekRelative, Output, AudioTrack,
    SubtitleTrack, AudioSink, Volume, Muted, Stop, Regrant, Drain, Shutdown
};

struct MediaCommand {
    MediaCommandKind kind = MediaCommandKind::Pause;
    MediaActorBinding binding;
    std::uint64_t request = 0;
    std::string operation;
    std::string path, external_subtitle;
    double number = 0;
    int index = -1;
    Output output = Output::Lcd;
    bool flag = false;
};

enum class MediaActorPhase { Starting, Foreground, Sealed, Drained, Unknown, Stopping, Finished };
enum class MediaEventOutcome { Completed, Suppressed, Failed, Unknown };

struct MediaActorEvent {
    MediaCommandKind kind = MediaCommandKind::Pause;
    MediaActorBinding binding;
    std::uint64_t request = 0;
    std::string operation;
    MediaEventOutcome outcome = MediaEventOutcome::Unknown;
    std::optional<GraphDrainEvidence> graph;
    std::string error;
};

struct MediaBackendSnapshot {
    Status status;
    std::shared_ptr<const Frame> frame;
    std::optional<PlaybackCheckpoint> checkpoint;
    PlaybackResources resources;
};

struct MediaActorSnapshot {
    MediaActorBinding binding;
    MediaActorPhase phase = MediaActorPhase::Starting;
    MediaBackendSnapshot media;
    std::string lifetime_operation;
    std::string active_operation;
    std::optional<GraphDrainEvidence> graph;
    std::size_t queued = 0;
    std::size_t unacknowledged = 0;
    bool sealed = false;
    bool cancelled = false;
    // Finished means the worker and backend are destroyed, not that unknown
    // server/driver work is released. The terminal event retains that result.
    bool unknown = false;
    std::string error;
};

// This object is constructed, used and destroyed only by the actor thread.
// Implementations must not call the UI or wait for it. Automatic poll/rebuild
// work remains under the pre-journaled lifetime/graph permit until drain.
class MediaActorBackend {
public:
    virtual ~MediaActorBackend() = default;
    virtual bool execute(const MediaCommand&, std::string& error) = 0;
    virtual void poll() = 0;
    virtual MediaBackendSnapshot snapshot() const = 0;
    virtual std::optional<GraphDrainEvidence> drain(const std::string& operation, std::string& error) = 0;
    virtual bool regrant(const MediaActorBinding&, std::string& error) = 0;
    virtual void stop() = 0;
};

std::unique_ptr<MediaActorBackend> make_playback_actor_backend(PlaybackOptions options);

class MediaActor {
public:
    using Factory = std::function<std::unique_ptr<MediaActorBackend>()>;
    static constexpr std::size_t command_capacity = 32;
    static constexpr std::size_t operation_history_limit = 1024;

    // The lifetime operation is durable before this starts a worker. It covers
    // factory/startup and automatic poll/rebuild work, including a blocked call.
    MediaActor(MediaActorBinding binding, std::string lifetime_operation, Factory factory,
               std::chrono::milliseconds poll_interval = std::chrono::milliseconds(10));
    // Destruction joins and may block. A control/UI owner must BORROW this actor
    // and retain its separately owned supervisor until finished()/join_finished.
    // No worker is detached and no destructor can manufacture release evidence.
    ~MediaActor();
    MediaActor(const MediaActor&) = delete;
    MediaActor& operator=(const MediaActor&) = delete;

    bool submit(MediaCommand command); // Queue acceptance, never execution success.
    bool seal(const MediaActorBinding&, std::uint64_t request, const std::string& drain_operation);
    bool cancel(const MediaActorBinding&, std::uint64_t request);
    // Only after positive old drain, no active call, and old events acknowledged.
    // The coordinator must independently authenticate an actual fresh grant.
    bool regrant(MediaActorBinding binding, std::string lifetime_operation,
                 std::uint64_t request, const std::string& resume_operation);
    bool request_shutdown(const MediaActorBinding&, std::uint64_t request, const std::string& shutdown_operation);

    std::shared_ptr<const MediaActorSnapshot> snapshot() const;
    std::vector<MediaActorEvent> peek_events(std::size_t maximum = 64) const;
    bool acknowledge_events(std::size_t count); // Persist the observed prefix first.
    // Convenience for a sole non-authority consumer. A durable coordinator uses
    // peek/commit/ack and retains a batch until its journal confirms completion.
    std::vector<MediaActorEvent> take_events(std::size_t maximum = 64);
    int wake_fd() const noexcept;
    void consume_wake() noexcept;
    bool finished() const;
    bool join_finished(); // Never waits for backend work; false until Finished.
    // Positive parked old-epoch fence. The worker may retain its CPU checkpoint
    // but cannot poll, submit or rebuild until a separately trusted fresh grant.
    bool epoch_fenced(const MediaActorBinding&) const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace tyrell::video
