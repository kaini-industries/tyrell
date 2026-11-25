#pragma once
#include "playback.h"
#include "outputs.h"
#include <utility>

namespace tyrell::video {

// UI-facing controls. The direct M5 adapter retains synchronous return values;
// actor-backed adapters return queue acceptance and publish execution results
// later via immutable snapshots. Neither interface is an ownership grant.
class PlayerControl {
public:
    virtual ~PlayerControl() = default;
    virtual bool asynchronous() const { return false; }
    virtual bool input_allowed() const { return true; }
    virtual bool render_allowed() const { return true; }
    virtual std::uint64_t control_epoch() const { return 0; }
    virtual std::string take_notice() { return {}; }
    virtual std::vector<AudioSink> available_audio_sinks() const = 0;
    virtual bool load(const std::string&,const std::string& = {}) = 0;
    virtual void poll() = 0;
    virtual Status status() const = 0;
    virtual std::shared_ptr<const Frame> latest_frame() const = 0;
    virtual bool toggle_pause() = 0;
    virtual bool seek_relative(double) = 0;
    virtual bool set_output(Output) = 0;
    virtual bool set_audio_track(int) = 0;
    virtual bool set_subtitle_track(int) = 0;
    virtual bool set_audio_sink(const std::string&) = 0;
    virtual void set_volume(double) = 0;
    virtual void set_muted(bool) = 0;
    virtual bool stop() = 0;
};

class DirectPlayerControl final : public PlayerControl {
public:
    explicit DirectPlayerControl(PlaybackOptions options = {}) : playback_(std::move(options)) {}
    std::vector<AudioSink> available_audio_sinks() const override { return inspect_outputs().audio; }
    bool load(const std::string& path,const std::string& subtitle={}) override { return playback_.load(path,subtitle); }
    void poll() override { playback_.poll(); }
    Status status() const override { return playback_.status(); }
    std::shared_ptr<const Frame> latest_frame() const override { return playback_.latest_frame(); }
    bool toggle_pause() override { return playback_.toggle_pause(); }
    bool seek_relative(double value) override { return playback_.seek_relative(value); }
    bool set_output(Output value) override { return playback_.set_output(value); }
    bool set_audio_track(int value) override { return playback_.set_audio_track(value); }
    bool set_subtitle_track(int value) override { return playback_.set_subtitle_track(value); }
    bool set_audio_sink(const std::string& value) override { return playback_.set_audio_sink(value); }
    void set_volume(double value) override { playback_.set_volume(value); }
    void set_muted(bool value) override { playback_.set_muted(value); }
    bool stop() override { playback_.stop();return true; }
private:
    Playback playback_;
};

} // namespace tyrell::video
