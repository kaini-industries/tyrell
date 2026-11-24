#include "event_wait.h"

#include <cerrno>
#include <chrono>
#include <climits>
#include <cstdlib>
#include <fcntl.h>
#include <iostream>
#include <stdexcept>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>

using namespace std::chrono;
using tyrell::status::WaitRequest;
using tyrell::status::wait_for_events;
using tyrell::status::deadline_timeout_ms;
namespace {
void check(bool value, const char* message) {
    if(!value) { std::cerr << "event wait contract failed: " << message << '\n'; std::exit(1); }
}
struct Pipe {
    int fd[2]{};
    Pipe() { check(pipe2(fd, O_CLOEXEC | O_NONBLOCK) == 0, "pipe"); }
    ~Pipe() { if(fd[0] >= 0) close(fd[0]); if(fd[1] >= 0) close(fd[1]); }
};
struct Pair {
    int fd[2]{};
    Pair() { check(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC | SOCK_NONBLOCK, 0, fd) == 0, "socketpair"); }
    ~Pair() { if(fd[0] >= 0) close(fd[0]); if(fd[1] >= 0) close(fd[1]); }
};
void deadlines_round_up() {
    const auto now = steady_clock::now();
    check(deadline_timeout_ms({}, now) == -1, "absent deadline is infinite");
    check(deadline_timeout_ms(now - milliseconds(1), now) == 0, "expired deadline is immediate");
    check(deadline_timeout_ms(now + nanoseconds(1), now) == 1, "positive fractional millisecond never spins");
    check(deadline_timeout_ms(now + microseconds(1501), now) == 2, "round timeout upward");
    check(deadline_timeout_ms(now + hours(24 * 365), now) == INT_MAX, "long deadline clamps safely");
}
void input_and_shutdown_wake() {
    Pipe input;
    WaitRequest request;
    request.input_fd = input.fd[0];
    request.deadline = steady_clock::now() + seconds(1);
    std::thread writer([&] { std::this_thread::sleep_for(milliseconds(20)); check(write(input.fd[1], "k", 1) == 1, "input write"); });
    const auto start = steady_clock::now();
    wait_for_events(request);
    writer.join();
    check(steady_clock::now() - start < milliseconds(500), "input wakes before telemetry deadline");
    char value = 0;
    check(read(input.fd[0], &value, 1) == 1 && value == 'k', "input was not consumed by wait");

    Pipe shutdown;
    check(write(shutdown.fd[1], "s", 1) == 1, "queue shutdown before wait");
    request = {};
    request.shutdown_fd = shutdown.fd[0];
    const auto queued = steady_clock::now();
    wait_for_events(request);
    check(steady_clock::now() - queued < milliseconds(100), "wake survives check-before-poll race");
    check(read(shutdown.fd[0], &value, 1) == 1 && value == 's', "shutdown wake remains durable");
}
void link_readiness_and_backpressure() {
    Pair pair;
    WaitRequest request;
    request.link_fd = pair.fd[0];
    request.deadline = steady_clock::now() + milliseconds(30);
    const auto idle = steady_clock::now();
    wait_for_events(request);
    check(steady_clock::now() - idle >= milliseconds(20), "writable idle socket does not cause a POLLOUT spin");

    request.link_output = true;
    request.deadline = steady_clock::now() + seconds(1);
    const auto writable = steady_clock::now();
    wait_for_events(request);
    check(steady_clock::now() - writable < milliseconds(100), "pending output wakes on writable socket");

    const int size = 1024;
    check(setsockopt(pair.fd[0], SOL_SOCKET, SO_SNDBUF, &size, sizeof(size)) == 0, "small socket buffer");
    unsigned sent = 0;
    while(send(pair.fd[0], "f", 1, MSG_DONTWAIT | MSG_NOSIGNAL) == 1) check(++sent < 10000, "bounded fixture fill");
    check(sent && (errno == EAGAIN || errno == EWOULDBLOCK), "socket reaches backpressure");
    request.deadline = steady_clock::now() + milliseconds(30);
    const auto blocked = steady_clock::now();
    wait_for_events(request);
    check(steady_clock::now() - blocked >= milliseconds(20), "backpressured output waits instead of spinning");
    std::thread reader([&] {
        std::this_thread::sleep_for(milliseconds(20));
        char value;
        for(unsigned n = 0; n < sent; ++n) check(recv(pair.fd[1], &value, 1, MSG_DONTWAIT) == 1, "drain peer");
    });
    request.deadline = steady_clock::now() + seconds(1);
    const auto resumed = steady_clock::now();
    wait_for_events(request);
    reader.join();
    check(steady_clock::now() - resumed < milliseconds(500), "output resumes when peer drains");

    request.link_output = false;
    check(send(pair.fd[1], "pause", 5, MSG_DONTWAIT | MSG_NOSIGNAL) == 5, "link packet");
    request.deadline.reset();
    wait_for_events(request);
    char packet[8]{};
    check(recv(pair.fd[0], packet, sizeof(packet), MSG_DONTWAIT) == 5, "link packet readiness preserved");
    close(pair.fd[1]); pair.fd[1] = -1;
    wait_for_events(request);
    check(recv(pair.fd[0], packet, sizeof(packet), MSG_DONTWAIT) == 0, "link EOF wakes infinite wait");
}
void input_eof_and_invalid_fd() {
    Pipe input;
    close(input.fd[1]); input.fd[1] = -1;
    WaitRequest request;
    request.input_fd = input.fd[0];
    wait_for_events(request);
    char value;
    check(read(input.fd[0], &value, 1) == 0, "input EOF wakes infinite wait");
    const int stale = input.fd[0]; close(input.fd[0]); input.fd[0] = -1;
    request.input_fd = stale;
    bool rejected = false;
    try { wait_for_events(request); } catch(const std::runtime_error&) { rejected = true; }
    check(rejected, "invalid descriptor fails instead of a hot poll loop");
}
} // namespace
int main() {
    deadlines_round_up();
    input_and_shutdown_wake();
    link_readiness_and_backpressure();
    input_eof_and_invalid_fd();
}
