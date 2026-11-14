#pragma once

#include <chrono>
#include <string>

namespace tyrell::video {

// Socket activation performs the privileged display handoff outside the UI
// process. Keeping this connection alive owns the lease; EOF restores it even
// after a player crash. Polling never blocks the playback/controller thread.
class HdmiDesktopLease {
public:
    enum class State { Idle, Waiting, Ready, Failed };
    explicit HdmiDesktopLease(std::string socket_path = "/run/tyrell-video-hdmi.sock",
                              std::chrono::milliseconds timeout = std::chrono::seconds(8));
    ~HdmiDesktopLease();
    HdmiDesktopLease(const HdmiDesktopLease&) = delete;
    HdmiDesktopLease& operator=(const HdmiDesktopLease&) = delete;
    bool begin();
    State poll();
    State state() const { return state_; }
    const std::string& error() const { return error_; }
    // Release only after all GStreamer tasks and the DRM master fd are closed.
    void release();

private:
    void fail(const std::string& error);
    std::string path_, reply_, error_;
    std::chrono::milliseconds timeout_;
    std::chrono::steady_clock::time_point started_{};
    int fd_ = -1;
    State state_ = State::Idle;
};

} // namespace tyrell::video
