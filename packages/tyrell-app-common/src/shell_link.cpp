#include <tyrell/app/shell_link.hpp>

#include <climits>
#include <cstdlib>
#include <cstring>
#include <utility>

#if defined(__linux__)
#include <cerrno>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#endif

namespace tyrell::app {
namespace {
constexpr const char* environment = "GH0ST_SHELL_FD";

int environment_fd() noexcept {
    const char* value = std::getenv(environment);
    int fd = -1;
    if(value && *value && *value != '0') {
        unsigned result = 0;
        bool valid = true;
        for(const char* p = value; *p; ++p) {
            if(*p < '0' || *p > '9' || result > (static_cast<unsigned>(INT_MAX) - static_cast<unsigned>(*p - '0')) / 10) {
                valid = false;
                break;
            }
            result = result * 10 + static_cast<unsigned>(*p - '0');
        }
        if(valid && result >= 3) fd = static_cast<int>(result);
    }
#if defined(__unix__) || defined(__APPLE__)
    ::unsetenv(environment);
#elif defined(_WIN32)
    ::_putenv_s(environment, "");
#endif
    return fd;
}

#if defined(__linux__)
constexpr unsigned packet_limit = 512;
bool valid_endpoint(int fd) noexcept {
    if(fd < 3 || ::fcntl(fd, F_GETFD) < 0) return false;
    int type = 0;
    socklen_t size = sizeof(type);
    if(::getsockopt(fd, SOL_SOCKET, SO_TYPE, &type, &size) < 0 || type != SOCK_SEQPACKET) return false;
    sockaddr_un local{}, peer{};
    size = sizeof(local);
    if(::getsockname(fd, reinterpret_cast<sockaddr*>(&local), &size) < 0 || local.sun_family != AF_UNIX) return false;
    size = sizeof(peer);
    if(::getpeername(fd, reinterpret_cast<sockaddr*>(&peer), &size) < 0 || peer.sun_family != AF_UNIX) return false;
    int listening = 0;
    size = sizeof(listening);
    if(::getsockopt(fd, SOL_SOCKET, SO_ACCEPTCONN, &listening, &size) < 0 || listening) return false;
    // The reviewed launcher and app run as the same user. Privilege-boundary
    // launchers are deliberately outside this experimental protocol subset.
    ucred credentials{};
    size = sizeof(credentials);
    return ::getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &credentials, &size) == 0 &&
           size == sizeof(credentials) && credentials.pid > 0 && credentials.uid == ::geteuid();
}

bool valid_utf8(const unsigned char* text, unsigned length) noexcept {
    for(unsigned i = 0; i < length;) {
        const auto c = text[i++];
        if(c < 0x80) {
            if((c < 0x20 && c != '\t') || c == 0x7f) return false;
            continue;
        }
        unsigned remaining = 0, code = 0, minimum = 0;
        if(c >= 0xc2 && c <= 0xdf) { remaining = 1; code = c & 0x1f; minimum = 0x80; }
        else if(c >= 0xe0 && c <= 0xef) { remaining = 2; code = c & 0x0f; minimum = 0x800; }
        else if(c >= 0xf0 && c <= 0xf4) { remaining = 3; code = c & 0x07; minimum = 0x10000; }
        else return false;
        if(remaining > length - i) return false;
        while(remaining--) {
            const auto next = text[i++];
            if((next & 0xc0) != 0x80) return false;
            code = (code << 6) | (next & 0x3f);
        }
        if(code < minimum || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) return false;
    }
    return true;
}
#endif
} // namespace

ShellLink::~ShellLink() { release(); }
ShellLink::ShellLink(ShellLink&& other) noexcept { *this = std::move(other); }
ShellLink& ShellLink::operator=(ShellLink&& other) noexcept {
    if(this == &other) return *this;
    release();
    fd_ = std::exchange(other.fd_, -1);
    lost_ = std::exchange(other.lost_, false);
    lost_pending_ = std::exchange(other.lost_pending_, false);
    hello_received_ = std::exchange(other.hello_received_, false);
    hello_pending_ = std::exchange(other.hello_pending_, false);
    paused_pending_ = std::exchange(other.paused_pending_, false);
    awake_pending_ = std::exchange(other.awake_pending_, false);
    awake_ = other.awake_;
    return *this;
}

ShellLink ShellLink::from_environment() noexcept {
    ShellLink result;
    const int fd = environment_fd();
#if defined(__linux__)
    if(!valid_endpoint(fd)) return result;
    const int flags = ::fcntl(fd, F_GETFD);
    if(flags < 0 || ::fcntl(fd, F_SETFD, flags | FD_CLOEXEC) < 0) return result;
    result.fd_ = fd;
    result.hello_pending_ = true;
    result.service();
#else
    (void)fd;
#endif
    return result;
}

void ShellLink::release() noexcept {
#if defined(__linux__)
    if(fd_ >= 0) ::close(fd_);
#endif
    fd_ = -1;
    hello_pending_ = paused_pending_ = awake_pending_ = false;
}

void ShellLink::lose() noexcept {
    if(fd_ < 0) return;
    release();
    lost_ = lost_pending_ = true;
}

bool ShellLink::send_packet(const char* packet, unsigned length) noexcept {
#if defined(__linux__)
    const auto count = ::send(fd_, packet, length, MSG_DONTWAIT | MSG_NOSIGNAL);
    if(count == static_cast<ssize_t>(length)) return true;
    if(count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return false;
    lose();
#else
    (void)packet; (void)length;
#endif
    return false;
}

void ShellLink::service() noexcept {
    if(!connected()) return;
    if(hello_pending_) {
        if(!send_packet("hello\t1", 7)) return;
        hello_pending_ = false;
    }
    if(paused_pending_) {
        if(!send_packet("paused", 6)) return;
        paused_pending_ = false;
    }
    if(awake_pending_) {
        if(!send_packet(awake_ ? "awake\t1" : "awake\t0", 7)) return;
        awake_pending_ = false;
    }
}

bool ShellLink::acknowledge_paused() noexcept {
    if(!connected()) return false;
    paused_pending_ = true;
    service();
    return connected();
}

bool ShellLink::send_awake(bool awake) noexcept {
    if(!connected()) return false;
    awake_ = awake;
    awake_pending_ = true;
    service();
    return connected();
}

ShellEvent ShellLink::next_event() noexcept {
    if(lost_pending_) { lost_pending_ = false; return ShellEvent::Lost; }
    if(!connected()) return ShellEvent::None;
#if defined(__linux__)
    unsigned char buffer[packet_limit]{};
    iovec vector{buffer, sizeof(buffer)};
    msghdr message{};
    message.msg_iov = &vector;
    message.msg_iovlen = 1;
    // No ancillary buffer: the kernel closes discarded SCM_RIGHTS descriptors.
    const auto count = ::recvmsg(fd_, &message, MSG_DONTWAIT | MSG_CMSG_CLOEXEC);
    if(count < 0) {
        if(errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) return ShellEvent::None;
        lose(); lost_pending_ = false; return ShellEvent::Lost;
    }
    if(count == 0) { lose(); lost_pending_ = false; return ShellEvent::Lost; }
    if((message.msg_flags & (MSG_TRUNC | MSG_CTRUNC)) || count > packet_limit ||
       !valid_utf8(buffer, static_cast<unsigned>(count))) return ShellEvent::Ignored;
    const auto matches = [&](const char* packet) {
        const auto length = std::strlen(packet);
        return static_cast<unsigned>(count) == length && std::memcmp(buffer, packet, length) == 0;
    };
    if(matches("hello\t1")) { hello_received_ = true; return ShellEvent::Hello; }
    if(count > 6 && std::memcmp(buffer, "hello\t", 6) == 0) {
        lose(); lost_pending_ = false; return ShellEvent::Lost;
    }
    if(!hello_received_) return ShellEvent::Ignored;
    if(matches("pause")) return ShellEvent::Pause;
    if(matches("resume")) return ShellEvent::Resume;
    if(matches("redraw")) return ShellEvent::Redraw;
#endif
    return ShellEvent::Ignored;
}

} // namespace tyrell::app
