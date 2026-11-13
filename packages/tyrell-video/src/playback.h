#pragma once

#include <cstdint>
#include <memory>
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
    std::uint64_t video_buffers_delivered = 0; // Buffers reaching the final sink, not physical scanout.
    std::uint64_t qos_processed = 0;
    std::string video_decoder;
    std::string audio_output;
};

struct PlaybackOptions {
    // Explicit test-only audio output; normal playback always uses PulseAudio.
    bool fake_audio = false;
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
    void stop();
    std::string diagnostics_json() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace tyrell::video
