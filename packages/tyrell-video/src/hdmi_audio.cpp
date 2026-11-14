#include "hdmi_audio.h"

#include <cerrno>
#include <cstring>
#include <utility>
#include <vector>

#if defined(__unix__) || defined(__APPLE__)
#include <fcntl.h>
#include <spawn.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>
extern char** environ;
#endif

namespace tyrell::video {

HdmiAudioSession::HdmiAudioSession(std::string launcher, std::string helper,
                                 std::chrono::milliseconds timeout)
    : launcher_(std::move(launcher)), helper_(std::move(helper)), timeout_(timeout) {}
HdmiAudioSession::~HdmiAudioSession() { release(); reap(); }

void HdmiAudioSession::reap() {
#if defined(__unix__) || defined(__APPLE__)
    if(child_ > 0) {
        const auto result = waitpid(child_, nullptr, WNOHANG);
        if(result == child_ || (result < 0 && errno == ECHILD)) child_ = -1;
    }
#endif
}

void HdmiAudioSession::release() {
#if defined(__unix__) || defined(__APPLE__)
    if(fd_ >= 0) close(fd_);
#endif
    fd_ = -1;
    pending_spawn_ = false;
    state_ = State::Idle;
    sink_.clear(); reply_.clear(); error_.clear();
    reap();
}

void HdmiAudioSession::fail(std::string error) {
    release();
    error_ = std::move(error);
    state_ = State::Failed;
}

bool HdmiAudioSession::spawn() {
#if defined(__unix__) || defined(__APPLE__)
    int pair[2];
    #if defined(SOCK_CLOEXEC)
    constexpr int socket_type = SOCK_STREAM | SOCK_CLOEXEC;
#else
    constexpr int socket_type = SOCK_STREAM;
#endif
    if(socketpair(AF_UNIX, socket_type, 0, pair) != 0) {
        fail("Cannot create HDMI audio lease: " + std::string(std::strerror(errno)));
        return false;
    }
    // The launcher inherits only the service end. The client end must close on
    // exec or an unrelated process could accidentally prolong the lease.
    for(int& socket : pair) {
        if(socket < 3) {
            const int duplicate = fcntl(socket, F_DUPFD_CLOEXEC, 3);
            if(duplicate < 0) {
                const int error = errno;
                close(pair[0]); close(pair[1]);
                fail("Cannot prepare HDMI audio descriptors: " + std::string(std::strerror(error)));
                return false;
            }
            close(socket);
            socket = duplicate;
        }
        if(fcntl(socket, F_SETFD, FD_CLOEXEC) != 0) {
            const int error = errno;
            close(pair[0]); close(pair[1]);
            fail("Cannot isolate HDMI audio descriptors: " + std::string(std::strerror(error)));
            return false;
        }
    }
    const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    sink_ = "tyrell_video_hdmi_" + std::to_string(getuid()) + "_" +
            std::to_string(getpid()) + "_" + std::to_string(stamp);
    const std::string unit = "tyrell-video-audio-" + sink_;
    const std::string cleanup = "ExecStopPost=" + helper_ + " --cleanup " + sink_;
    std::vector<std::string> words = {launcher_, "--user", "--quiet", "--pipe", "--wait", "--collect",
        "--service-type=exec", "--unit=" + unit, "--property=TimeoutStopSec=4s",
        "--property=NoNewPrivileges=yes", "--property=" + cleanup, helper_, "--lease", sink_};
    std::vector<char*> arguments;
    for(auto& word : words) arguments.push_back(word.data());
    arguments.push_back(nullptr);
    posix_spawn_file_actions_t actions;
    int result = posix_spawn_file_actions_init(&actions);
    if(result == 0) {
        auto action = [&](int code) { if(result == 0) result = code; };
        action(posix_spawn_file_actions_adddup2(&actions, pair[1], STDIN_FILENO));
        action(posix_spawn_file_actions_adddup2(&actions, pair[1], STDOUT_FILENO));
        action(posix_spawn_file_actions_addclose(&actions, pair[0]));
        action(posix_spawn_file_actions_addclose(&actions, pair[1]));
        pid_t child = -1;
        if(result == 0) result = posix_spawn(&child, launcher_.c_str(), &actions, nullptr, arguments.data(), environ);
        posix_spawn_file_actions_destroy(&actions);
        if(result == 0) child_ = child;
    }
    close(pair[1]);
    if(result != 0 || fcntl(pair[0], F_SETFL, O_NONBLOCK) != 0) {
        const int error = result ? result : errno;
        close(pair[0]);
        fail("Cannot start HDMI audio helper: " + std::string(std::strerror(error)));
        return false;
    }
    fd_ = pair[0];
    pending_spawn_ = false;
    return true;
#else
    fail("Automatic HDMI audio requires Linux systemd and PulseAudio");
    return false;
#endif
}

bool HdmiAudioSession::begin() {
    release();
    started_ = std::chrono::steady_clock::now();
    state_ = State::Waiting;
    // At most one cleanup can be pending. Quick LCD/HDMI toggles wait
    // asynchronously for the previous sink to release the ALSA device.
    pending_spawn_ = child_ > 0;
    return pending_spawn_ || spawn();
}

HdmiAudioSession::State HdmiAudioSession::poll() {
    reap();
#if defined(__unix__) || defined(__APPLE__)
    if(state_ != State::Waiting && state_ != State::Ready) return state_;
    if(pending_spawn_) {
        // There cannot be a READY reply before this process is spawned. Keep
        // deferred cleanup/startup bounded without launching an expired lease.
        if(std::chrono::steady_clock::now() - started_ >= timeout_) {
            fail("HDMI audio setup timed out; using onboard sound");
            return state_;
        }
        if(child_ > 0) return state_;
        if(!spawn()) return state_;
    }
    char bytes[128];
    for(;;) {
        const auto count = recv(fd_, bytes, sizeof(bytes), 0);
        if(count < 0) {
            if(errno == EINTR) continue;
            if(errno == EAGAIN || errno == EWOULDBLOCK) break;
            fail("HDMI audio lease connection failed");
            return state_;
        }
        if(count == 0) {
            fail("HDMI audio helper ended; using onboard sound");
            return state_;
        }
        reply_.append(bytes, static_cast<std::size_t>(count));
        if(reply_.size() > 512) { fail("HDMI audio response was too large"); return state_; }
        const auto end = reply_.find('\n');
        if(end != std::string::npos) {
            const auto line = reply_.substr(0, end);
            if(end + 1 != reply_.size()) fail("Invalid HDMI audio helper response");
            else if(line == "READY " + sink_ && state_ == State::Waiting) {
                state_ = State::Ready;
                reply_.clear();
            } else if(line.rfind("ERROR ", 0) == 0 && line.size() > 6) fail("HDMI audio: " + line.substr(6));
            else fail("Invalid HDMI audio helper response");
            return state_;
        }
    }
    // GStreamer startup can keep the UI thread busy after the helper has
    // already sent READY. Drain that queued reply before applying the client
    // deadline, or a timely successful setup would be discarded on late poll.
    if(state_ == State::Waiting && std::chrono::steady_clock::now() - started_ >= timeout_)
        fail("HDMI audio setup timed out; using onboard sound");
#endif
    return state_;
}

} // namespace tyrell::video
