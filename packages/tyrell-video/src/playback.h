#pragma once

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace tyrell::video {

enum class Output { Lcd, Hdmi };

struct Frame {
    int width = 320;
    int height = 170;
    int stride = 640;
    std::uint64_t serial = 0;
    double position = 0; // Presentation timestamp in seconds, for diagnostics.
    std::vector<std::uint8_t> pixels;
};

struct Track {
    int index = -1;
    std::string title;
    std::string language;
    std::string codec;
    bool external = false;
};

struct Status {
    bool loaded = false;
    bool playing = false;
    bool paused = false;
    bool buffering = false;
    bool suspended = false; // Lifecycle checkpoint; distinct from user pause.
    bool output_switching = false; // HDMI desktop handoff is pending; LCD controls remain live.
    bool muted = false;
    double position = 0;
    double duration = 0;
    double volume = 0.65;
    Output output = Output::Lcd;
    int audio_track = -1;
    int subtitle_track = -1;
    int source_width = 0;
    int source_height = 0;
    std::vector<Track> audio_tracks;
    std::vector<Track> subtitle_tracks;
    std::string path;
    std::string error;
    std::string notice;
    std::uint64_t decoded_frames = 0;
    std::uint64_t dropped_frames = 0;
    std::uint64_t received_frames = 0;
    std::uint64_t lcd_caption_compositions = 0; // Frames with timed overlays blended after LCD scaling.
    std::uint64_t video_buffers_delivered = 0; // Buffers reaching the final sink, not physical scanout.
    std::uint64_t qos_processed = 0;
    std::uint64_t video_frames_decoded = 0;
    // GstBaseSink counters reset on flush/segment; render calls are not scanout.
    std::uint64_t video_sink_rendered = 0;
    std::uint64_t video_sink_dropped = 0;
    std::string video_output_caps;
    std::string video_output_memory;
    std::string video_decoder;
    // Current HDMI graph requests software decoding after a bounded allocation
    // retry. The built-in playbin flag also excludes future hardware audio
    // decoders, while preserving selected audio tracks and output routing.
    bool software_video_fallback = false;
    std::uint64_t video_decode_fallbacks = 0; // At most one retry per accepted load.
    std::string video_decode_fallback_reason;
    std::string audio_output;
    // Per accepted load. Readiness is observed on poll(), with render statistics
    // sampled every 250 ms; these are not physical display measurements.
    std::uint64_t pipeline_builds = 0;
    double first_render_seconds = -1;
    double first_hdmi_render_seconds = -1;
    double first_playing_seconds = -1;
};

// Metadata identity, not a content hash or an atomic seal against concurrent
// writers. The accepted load identity is checked again before suspend/resume.
struct FileIdentity {
    std::string path;
    std::string canonical_path;
    std::uint64_t device = 0, inode = 0, bytes = 0;
    std::int64_t modified_seconds = 0, modified_nanoseconds = 0;
    std::int64_t changed_seconds = 0, changed_nanoseconds = 0;
};

enum class CheckpointPosition { FreshQuery, PendingTarget, EndOfFile };
struct PlaybackCheckpoint {
    FileIdentity media;
    std::optional<FileIdentity> external_subtitle;
    double position = 0, duration = 0;
    CheckpointPosition position_source = CheckpointPosition::FreshQuery;
    bool playing = false, ended = false;
    int audio_track = -1, subtitle_track = -1;
    double volume = 0.65;
    bool muted = false;
    Output preferred_output = Output::Lcd;
    Output actual_output = Output::Lcd;
    std::string requested_audio_sink;
    bool requested_owned_audio = false;
    bool software_video_preferred = false;
    bool video_decode_retry_attempted = false;
    std::uint64_t video_decode_fallbacks = 0;
    std::string video_decode_fallback_reason;
    bool audio_fallback_attempted = false;
    bool audio_recovery_attempted = false;
};

struct PlaybackResources {
    bool pipeline_present = false;
    bool drm_fd_open = false;
    bool desktop_lease_open = false;
    bool audio_lease_open = false;
    // Closing a lease is not confirmation that external VT/audio cleanup has
    // finished. These services currently provide no completion acknowledgment.
    bool external_cleanup_unconfirmed = false;
    bool local_handles_released() const {
        return !pipeline_present && !drm_fd_open && !desktop_lease_open && !audio_lease_open;
    }
};

struct PlaybackOptions {
    // Explicit test-only audio output; normal playback always uses PulseAudio.
    bool fake_audio = false;
    // Simulators/probes can use an already free DRM output without taking a desktop lease.
    bool desktop_handoff = true;
    // Normal-user temporary TV sound; disabled for probes and simulators.
    bool automatic_hdmi_audio = true;
    // Override only for an alternate desktop integration or a controlled test
    // lease server. The normal application uses the installed system socket.
    std::string desktop_handoff_socket = "/run/tyrell-video-hdmi.sock";
};

// Call control methods and poll() on the UI thread. GStreamer streaming threads
// publish a bounded latest-frame snapshot and never call LVGL.
class Playback {
public:
    explicit Playback(PlaybackOptions options = {});
    ~Playback();
    Playback(const Playback&) = delete;
    Playback& operator=(const Playback&) = delete;

    bool load(const std::string& path, const std::string& external_subtitle = {});
    void poll();
    Status status() const;
    std::shared_ptr<const Frame> latest_frame() const;
    // Suspended controls return false; volume/mute setters do nothing. Only
    // resume, accepted replacement load, or destructive stop ends suspension.
    bool play();
    bool pause();
    bool toggle_pause();
    bool seek(double seconds);
    bool seek_relative(double seconds);
    bool set_output(Output output);
    bool set_audio_track(int index);
    bool set_subtitle_track(int index); // -1 disables subtitles.
    bool set_audio_sink(const std::string& sink_name); // Empty restores automatic routing.
    void set_volume(double volume);
    void set_muted(bool muted);
    // These calls may block in filesystem/GStreamer/driver operations. They
    // are NOT sufficient to acknowledge a bounded shell pause deadline.
    std::optional<PlaybackCheckpoint> capture_checkpoint();
    bool suspend();
    bool resume(); // Accepted rebuild may still preroll asynchronously.
    bool suspended() const;
    std::optional<PlaybackCheckpoint> checkpoint() const;
    PlaybackResources resources() const;
    void stop();
    std::string diagnostics_json() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace tyrell::video
