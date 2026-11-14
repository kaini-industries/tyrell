#pragma once

#include <chrono>
#include <string>

namespace tyrell::video {

// A normal-user, temporary PulseAudio-compatible HDMI sink. The service owns
// the module while this connection is alive, and systemd ExecStopPost removes
// it even if the helper is killed. This never selects the system default.
// Call only when no suitable existing HDMI sink/explicit choice is available.
class HdmiAudioSession {
public:
    enum class State { Idle, Waiting, Ready, Failed };
    explicit HdmiAudioSession(std::string launcher = "/usr/bin/systemd-run",
                              std::string helper = "/usr/libexec/tyrell-video-audio-session",
                              std::chrono::milliseconds timeout = std::chrono::seconds(5));
    ~HdmiAudioSession();
    HdmiAudioSession(const HdmiAudioSession&) = delete;
    HdmiAudioSession& operator=(const HdmiAudioSession&) = delete;
    bool begin();
    State poll();
    State state() const { return state_; }
    const std::string& sink_name() const { return sink_; }
    const std::string& error() const { return error_; }
    // Stop this app's audio stream before release. The background service then
    // unloads only its own sink; it cannot disturb a pre-existing HDMI sink.
    void release();

private:
    bool spawn();
    void reap();
    void fail(std::string error);
    std::string launcher_, helper_, sink_, reply_, error_;
    std::chrono::milliseconds timeout_;
    std::chrono::steady_clock::time_point started_{};
    int fd_ = -1;
    int child_ = -1;
    bool pending_spawn_ = false;
    State state_ = State::Idle;
};

} // namespace tyrell::video
