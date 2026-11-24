#include <tyrell/app/shell_link.hpp>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <utility>

#if defined(__linux__)
#include <cerrno>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

using tyrell::app::ShellEvent;
using tyrell::app::ShellLink;
namespace {
void require(bool value, const char* label) {
    if(!value) { std::cerr << label << '\n'; std::exit(1); }
}

#if defined(__linux__)
struct Pair {
    int shell = -1;
    int app = -1;
    explicit Pair(int type = SOCK_SEQPACKET) {
        int fds[2];
        require(::socketpair(AF_UNIX, type, 0, fds) == 0, "socketpair");
        shell = fds[0]; app = fds[1];
    }
    ~Pair() { if(shell >= 0) ::close(shell); if(app >= 0) ::close(app); }
    ShellLink adopt() {
        require(::setenv("GH0ST_SHELL_FD", std::to_string(app).c_str(), 1) == 0, "setenv");
        auto link = ShellLink::from_environment();
        // Adoption transfers the fd even when its peer is already gone.
        if(link.connected() || link.closed()) app = -1;
        return link;
    }
    void send(const std::string& packet) {
        require(::send(shell, packet.data(), packet.size(), MSG_NOSIGNAL) == static_cast<ssize_t>(packet.size()), "shell send");
    }
    std::string read() {
        char data[1024];
        const auto n = ::recv(shell, data, sizeof(data), MSG_DONTWAIT);
        if(n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return {};
        require(n >= 0, "shell read");
        return std::string(data, static_cast<std::size_t>(n));
    }
    void hello(ShellLink& link) {
        send("hello\t1");
        require(link.next_event() == ShellEvent::Hello, "hello event");
    }
};

void invalid_environment() {
    int stdio_flags[3]{};
    for(int descriptor = 0; descriptor < 3; ++descriptor) stdio_flags[descriptor] = ::fcntl(descriptor, F_GETFD);
    ::unsetenv("GH0ST_SHELL_FD");
    auto absent = ShellLink::from_environment();
    require(!absent.connected() && !absent.closed(), "absent link");
    for(const char* value : {"", "0", "1", "2", "-3", "+3", " 3", "3 ", "03", "3x", "2147483648", "999999999999999999999"}) {
        ::setenv("GH0ST_SHELL_FD", value, 1);
        auto invalid = ShellLink::from_environment();
        require(!invalid.connected() && !invalid.closed(), "invalid env rejected");
        require(std::getenv("GH0ST_SHELL_FD") == nullptr, "invalid env removed");
    }
    for(int descriptor = 0; descriptor < 3; ++descriptor) require(::fcntl(descriptor, F_GETFD) == stdio_flags[descriptor], "stdio retained unchanged");
    const int closed = ::dup(STDERR_FILENO);
    require(closed >= 3, "duplicate");
    ::close(closed);
    ::setenv("GH0ST_SHELL_FD", std::to_string(closed).c_str(), 1);
    require(!ShellLink::from_environment().connected(), "unopened fd rejected");

    char path[] = "/tmp/tyrell-shell-link-XXXXXX";
    const int file = ::mkstemp(path);
    require(file >= 3, "temporary file");
    ::unlink(path);
    require(::fcntl(file, F_SETFD, 0) == 0, "unset cloexec");
    const int file_flags = ::fcntl(file, F_GETFL);
    ::setenv("GH0ST_SHELL_FD", std::to_string(file).c_str(), 1);
    require(!ShellLink::from_environment().connected(), "regular file rejected");
    require(::fcntl(file, F_GETFD) == 0 && ::fcntl(file, F_GETFL) == file_flags, "invalid file unchanged");
    ::close(file);

    for(const int type : {SOCK_STREAM, SOCK_DGRAM}) {
        Pair pair(type);
        const int flags = ::fcntl(pair.app, F_GETFL);
        require(!pair.adopt().connected(), "wrong socket type rejected");
        require(::fcntl(pair.app, F_GETFD) == 0 && ::fcntl(pair.app, F_GETFL) == flags, "wrong socket unchanged");
    }
    for(const int family : {AF_UNIX, AF_INET}) {
        const int socket = ::socket(family, SOCK_SEQPACKET, 0);
        // AF_INET may not implement SEQPACKET at all.
        if(socket < 0 && family == AF_INET) continue;
        require(socket >= 3, "unconnected socket");
        ::setenv("GH0ST_SHELL_FD", std::to_string(socket).c_str(), 1);
        require(!ShellLink::from_environment().connected(), "unconnected socket rejected");
        require(::fcntl(socket, F_GETFD) == 0, "unconnected fd retained");
        ::close(socket);
    }
    if(::geteuid() == 0) {
        Pair pair;
        const auto child = ::fork();
        require(child >= 0, "fork different-user validation");
        if(child == 0) {
            if(::setuid(65534) != 0) ::_exit(2);
            const int flags = ::fcntl(pair.app, F_GETFL);
            ::setenv("GH0ST_SHELL_FD", std::to_string(pair.app).c_str(), 1);
            const auto rejected = ShellLink::from_environment();
            ::_exit(!rejected.connected() && !rejected.closed() && ::fcntl(pair.app, F_GETFD) == 0 && ::fcntl(pair.app, F_GETFL) == flags ? 0 : 1);
        }
        int status = 0;
        require(::waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0, "different-user peer rejected without descriptor mutation");
    }
}

void protocol_and_lifetime(const char* executable) {
    Pair pair;
    const int adopted = pair.app;
    const int flags = ::fcntl(adopted, F_GETFL);
    auto link = pair.adopt();
    require(link.connected() && !link.closed() && link.fd() == adopted, "adoption");
    require(std::getenv("GH0ST_SHELL_FD") == nullptr, "valid env removed");
    require((::fcntl(adopted, F_GETFD) & FD_CLOEXEC) != 0, "close on exec");
    require(::fcntl(adopted, F_GETFL) == flags && !(flags & O_NONBLOCK), "shared file flags untouched");
    require(pair.read() == "hello\t1", "app hello");
    require(link.next_event() == ShellEvent::None, "nonblocking empty recv");
    pair.send("pause");
    require(link.next_event() == ShellEvent::Ignored, "lifecycle before hello ignored");
    pair.hello(link);
    for(const auto& item : {std::pair{"pause", ShellEvent::Pause}, {"pause", ShellEvent::Pause}, {"resume", ShellEvent::Resume}, {"redraw", ShellEvent::Redraw}, {"resume", ShellEvent::Resume}}) {
        pair.send(item.first);
        require(link.next_event() == item.second, "lifecycle packet");
    }
    require(pair.read().empty(), "transport never automatically acknowledges pause");
    require(link.acknowledge_paused(), "explicit ack accepted");
    require(pair.read() == "paused", "paused ack packet");
    require(link.send_awake(true) && pair.read() == "awake\t1", "awake true");
    require(link.send_awake(false) && pair.read() == "awake\t0", "awake false");

    const auto child = ::fork();
    require(child >= 0, "fork helper");
    if(child == 0) {
        const auto number = std::to_string(adopted);
        ::execl(executable, executable, "--helper", number.c_str(), nullptr);
        ::_exit(127);
    }
    int status = 0;
    require(::waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0, "descriptor and env not inherited by exec helper");
    auto moved = std::move(link);
    require(!link.connected() && moved.fd() == adopted, "move ownership");
    { ShellLink replacement; replacement = std::move(moved); require(replacement.fd() == adopted, "move assignment"); }
    require(::fcntl(adopted, F_GETFD) < 0 && errno == EBADF, "shutdown closes owned endpoint");
}

void malformed_packets() {
    Pair pair;
    auto link = pair.adopt();
    pair.read();
    pair.hello(link);
    for(const auto& packet : {std::string("unknown\tvalid"), std::string("pause\t1"), std::string("resume\t"), std::string("redraw\textra"), std::string("pause\n"), std::string("pa\0use", 6), std::string("\xc0\xaf", 2), std::string("\xed\xa0\x80", 3), std::string("\xf4\x90\x80\x80", 4), std::string("\xe2\x82", 2), std::string(512, 'x'), std::string(513, 'x'), std::string(4096, 'x')}) {
        pair.send(packet);
        require(link.next_event() == ShellEvent::Ignored && link.connected(), "malformed/unknown packet rejected");
    }
    // Discarded ancillary rights must not leak into the application.
    const int file = ::open("/dev/null", O_RDONLY | O_CLOEXEC);
    require(file >= 3, "rights fixture");
    alignas(cmsghdr) char control[CMSG_SPACE(sizeof(int))]{};
    char packet[] = "pause";
    iovec vector{packet, sizeof(packet) - 1};
    msghdr message{};
    message.msg_iov = &vector; message.msg_iovlen = 1;
    message.msg_control = control; message.msg_controllen = sizeof(control);
    auto* header = CMSG_FIRSTHDR(&message);
    header->cmsg_level = SOL_SOCKET; header->cmsg_type = SCM_RIGHTS; header->cmsg_len = CMSG_LEN(sizeof(int));
    std::memcpy(CMSG_DATA(header), &file, sizeof(file));
    const int probe = ::dup(file);
    require(probe >= 3, "rights leak probe");
    ::close(probe);
    require(::sendmsg(pair.shell, &message, MSG_NOSIGNAL) == 5, "send ancillary rights");
    require(link.next_event() == ShellEvent::Ignored, "ancillary packet rejected");
    const int after = ::dup(file);
    require(after == probe, "discarded ancillary descriptor closed by kernel");
    ::close(after); ::close(file);
    pair.send("pause");
    require(link.next_event() == ShellEvent::Pause, "packet boundaries survive truncation");
    pair.send("hello\t2");
    require(link.next_event() == ShellEvent::Lost && link.closed(), "unsupported protocol closes link");
    require(link.next_event() == ShellEvent::None, "loss emitted once");
}

void backpressure_and_close() {
    Pair pair;
    const int buffer = 1024;
    require(::setsockopt(pair.app, SOL_SOCKET, SO_SNDBUF, &buffer, sizeof(buffer)) == 0, "small send buffer");
    unsigned filled = 0;
    while(::send(pair.app, "filler", 6, MSG_NOSIGNAL | MSG_DONTWAIT) == 6) require(++filled < 10000, "bounded fixture fill");
    require(filled && (errno == EAGAIN || errno == EWOULDBLOCK), "fixture reaches backpressure");
    auto link = pair.adopt();
    require(link.pending_output() && link.connected(), "hello retained on backpressure");
    require(link.acknowledge_paused() && link.send_awake(true), "bounded output accepted under backpressure");
    link.cancel_paused();
    for(unsigned i = 0; i < filled; ++i) require(pair.read() == "filler", "drain fixture");
    link.service();
    require(pair.read() == "hello\t1", "hello retries first");
    require(pair.read() == "awake\t1", "awake follows hello without stale paused");
    require(pair.read().empty() && !link.pending_output(), "bounded queue drained");
    pair.hello(link);
    // Acknowledgment backpressure must retain and deliver exactly one paused.
    filled = 0;
    while(::send(link.fd(), "filler", 6, MSG_NOSIGNAL | MSG_DONTWAIT) == 6) require(++filled < 10000, "bounded ack fixture fill");
    require(filled && (errno == EAGAIN || errno == EWOULDBLOCK), "ack fixture reaches backpressure");
    require(link.acknowledge_paused() && link.pending_output(), "paused retained under backpressure");
    require(link.acknowledge_paused(), "repeated pending acknowledgment coalesced");
    for(unsigned i = 0; i < filled; ++i) require(pair.read() == "filler", "drain ack fixture");
    link.service();
    require(pair.read() == "paused" && pair.read().empty() && !link.pending_output(), "paused retry delivered exactly once");
    ::close(pair.shell); pair.shell = -1;
    require(!link.send_awake(false), "send on peer close is safe without SIGPIPE");
    require(link.closed() && !link.connected() && !link.pending_output(), "fatal send closes link");
    require(link.next_event() == ShellEvent::Lost && link.next_event() == ShellEvent::None, "fatal send loss event once");

    Pair closed;
    ::close(closed.shell); closed.shell = -1;
    auto dead = closed.adopt();
    require(dead.closed() && dead.next_event() == ShellEvent::Lost, "closed inherited endpoint stays a lost link, not M5 fallback");

    Pair eof;
    auto reader = eof.adopt(); eof.read();
    ::close(eof.shell); eof.shell = -1;
    require(reader.next_event() == ShellEvent::Lost && reader.closed(), "read EOF");

    Pair empty;
    auto empty_reader = empty.adopt(); empty.read();
    empty.send("");
    require(empty_reader.next_event() == ShellEvent::Lost, "zero packet fails closed");
}
#endif
} // namespace

int main(int argc, char** argv) {
#if defined(__linux__)
    if(argc == 3 && std::string(argv[1]) == "--helper") {
        const int fd = std::stoi(argv[2]);
        return ::fcntl(fd, F_GETFD) < 0 && errno == EBADF && std::getenv("GH0ST_SHELL_FD") == nullptr ? 0 : 1;
    }
    invalid_environment();
    protocol_and_lifetime(argv[0]);
    malformed_packets();
    backpressure_and_close();
#else
    (void)argc; (void)argv;
    auto link = ShellLink::from_environment();
    require(!link.connected() && !link.closed() && link.fd() == -1, "portable disconnected stub");
    require(link.next_event() == ShellEvent::None && !link.acknowledge_paused() && !link.send_awake(true), "portable stub operations");
    link.service();
#endif
    std::cout << "shell link contract passed\n";
}
