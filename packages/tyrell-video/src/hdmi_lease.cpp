#include "hdmi_lease.h"

#include <cerrno>
#include <cstring>
#include <utility>

#if defined(__unix__) || defined(__APPLE__)
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#endif

namespace tyrell::video {

HdmiDesktopLease::HdmiDesktopLease(std::string socket_path, std::chrono::milliseconds timeout)
    : path_(std::move(socket_path)), timeout_(timeout) {}
HdmiDesktopLease::~HdmiDesktopLease() { release(); }

void HdmiDesktopLease::release() {
#if defined(__unix__) || defined(__APPLE__)
    if(fd_ >= 0) close(fd_);
#endif
    fd_ = -1;
    state_ = State::Idle;
    reply_.clear();
    error_.clear();
}

void HdmiDesktopLease::fail(const std::string& error) {
    release();
    error_ = error;
    state_ = State::Failed;
}

bool HdmiDesktopLease::begin() {
    release();
#if defined(__unix__) || defined(__APPLE__)
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    if(path_.empty() || path_.size() >= sizeof(address.sun_path)) {
        fail("HDMI handoff socket path is invalid");
        return false;
    }
    std::memcpy(address.sun_path, path_.c_str(), path_.size() + 1);
    fd_ = socket(AF_UNIX, SOCK_STREAM, 0);
    if(fd_ < 0 || fcntl(fd_, F_SETFD, FD_CLOEXEC) != 0 || fcntl(fd_, F_SETFL, O_NONBLOCK) != 0) {
        const int error = errno;
        fail("Cannot create HDMI handoff connection: " + std::string(std::strerror(error)));
        return false;
    }
    if(connect(fd_, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) != 0 && errno != EINPROGRESS) {
        const int error = errno;
        fail("HDMI desktop handoff is unavailable: " + std::string(std::strerror(error)));
        return false;
    }
    started_ = std::chrono::steady_clock::now();
    state_ = State::Waiting;
    return true;
#else
    fail("HDMI desktop handoff requires Unix sockets");
    return false;
#endif
}

HdmiDesktopLease::State HdmiDesktopLease::poll() {
#if defined(__unix__) || defined(__APPLE__)
    if(state_ != State::Waiting && state_ != State::Ready) return state_;
    char bytes[128];
    for(;;) {
        const auto count = recv(fd_, bytes, sizeof(bytes), 0);
        if(count < 0) {
            if(errno == EINTR) continue;
            if(errno == EAGAIN || errno == EWOULDBLOCK) break;
            fail("HDMI desktop handoff connection failed");
            return state_;
        }
        if(count == 0) {
            fail("HDMI desktop handoff ended unexpectedly");
            return state_;
        }
        reply_.append(bytes, static_cast<std::size_t>(count));
        if(reply_.size() > 512) {
            fail("HDMI desktop handoff response was too large");
            return state_;
        }
        const auto end = reply_.find('\n');
        if(end != std::string::npos) {
            const auto line = reply_.substr(0, end);
            if(end + 1 != reply_.size()) {
                fail("HDMI desktop handoff sent an invalid response");
            } else if(line == "READY" && state_ == State::Waiting) {
                state_ = State::Ready;
                reply_.clear();
            } else if(line.rfind("ERROR ", 0) == 0 && line.size() > 6) {
                fail("HDMI desktop handoff: " + line.substr(6));
            } else {
                fail("HDMI desktop handoff sent an invalid response");
            }
            return state_;
        }
    }
    if(state_ == State::Waiting && std::chrono::steady_clock::now() - started_ >= timeout_)
        fail("HDMI desktop handoff timed out; try O again");
#endif
    return state_;
}

} // namespace tyrell::video
