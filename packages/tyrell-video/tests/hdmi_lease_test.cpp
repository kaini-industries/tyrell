#include "hdmi_lease.h"

#include <chrono>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

using namespace tyrell::video;
using Clock = std::chrono::steady_clock;

namespace {
void require(bool condition, const std::string& message) {
    if(!condition) throw std::runtime_error(message);
}

struct Server {
    std::string path = "/tmp/tyrell-hdmi-lease-test-" + std::to_string(getpid());
    int fd = -1, peer = -1;
    Server() {
        unlink(path.c_str());
        fd = socket(AF_UNIX, SOCK_STREAM, 0);
        require(fd >= 0, "Cannot create test server");
        sockaddr_un address{};
        address.sun_family = AF_UNIX;
        std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
        require(bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0 && listen(fd, 4) == 0,
                "Cannot bind test server");
    }
    ~Server() {
        if(peer >= 0) close(peer);
        if(fd >= 0) close(fd);
        unlink(path.c_str());
    }
    void accept_client() {
        if(peer >= 0) close(peer);
        peer = accept(fd, nullptr, nullptr);
        require(peer >= 0, "Cannot accept test client");
    }
    void reply(const std::string& text) {
        require(send(peer, text.data(), text.size(), 0) == static_cast<ssize_t>(text.size()), "Test response write failed");
    }
    void expect_closed() {
        char byte;
        require(recv(peer, &byte, 1, MSG_DONTWAIT) == 0, "Releasing/cancelling a lease did not notify the helper");
    }
};
} // namespace

int main() {
    try {
        HdmiDesktopLease missing("/tmp/tyrell-hdmi-lease-does-not-exist-" + std::to_string(getpid()));
        require(!missing.begin() && missing.state() == HdmiDesktopLease::State::Failed && !missing.error().empty(),
                "A missing helper must fail with an actionable error");
        HdmiDesktopLease invalid(std::string(1024, 'x'));
        require(!invalid.begin(), "An oversized Unix path was accepted");

        Server server;
        HdmiDesktopLease lease(server.path, std::chrono::milliseconds(40));
        require(lease.begin(), "Cannot connect lease");
        server.accept_client();
        auto started = Clock::now();
        require(lease.poll() == HdmiDesktopLease::State::Waiting, "A lease became ready without the helper");
        require(Clock::now() - started < std::chrono::milliseconds(20), "Lease polling blocked the UI");
        server.reply("REA");
        require(lease.poll() == HdmiDesktopLease::State::Waiting, "A partial response was mistaken for READY");
        server.reply("DY\n");
        require(lease.poll() == HdmiDesktopLease::State::Ready, "READY response was not accepted");
        require(lease.poll() == HdmiDesktopLease::State::Ready, "An idle owned lease was not retained");
        lease.release();
        server.expect_closed();

        require(lease.begin(), "Cannot retry lease");
        server.accept_client();
        server.reply("ERROR Desktop did not release HDMI\n");
        require(lease.poll() == HdmiDesktopLease::State::Failed && lease.error().find("Desktop did not release HDMI") != std::string::npos,
                "The helper's useful acquisition error was lost");
        server.expect_closed();

        require(lease.begin(), "Cannot begin cancellable lease");
        server.accept_client();
        lease.release();
        require(lease.state() == HdmiDesktopLease::State::Idle, "Cancelling did not reset pending state");
        server.expect_closed();

        require(lease.begin(), "Cannot begin timeout lease");
        server.accept_client();
        std::this_thread::sleep_for(std::chrono::milliseconds(60));
        require(lease.poll() == HdmiDesktopLease::State::Failed && lease.error().find("timed out") != std::string::npos,
                "A silent helper did not time out");
        server.expect_closed();

        require(lease.begin(), "Cannot begin bounded-response lease");
        server.accept_client();
        server.reply(std::string(513, 'x'));
        require(lease.poll() == HdmiDesktopLease::State::Failed, "Unbounded response data was retained");
        server.expect_closed();

        require(lease.begin(), "Cannot begin invalid-response lease");
        server.accept_client();
        server.reply("NOT READY\n");
        require(lease.poll() == HdmiDesktopLease::State::Failed, "Invalid helper response was accepted");
        server.expect_closed();

        require(lease.begin(), "Cannot begin helper-death lease");
        server.accept_client();
        server.reply("READY\n");
        require(lease.poll() == HdmiDesktopLease::State::Ready, "Cannot prepare helper-death test");
        close(server.peer); server.peer = -1;
        require(lease.poll() == HdmiDesktopLease::State::Failed, "A dead helper did not invalidate the owned display lease");

        require(lease.begin(), "Cannot begin foreground-change lease");
        server.accept_client();
        server.reply("READY\n");
        require(lease.poll() == HdmiDesktopLease::State::Ready, "Cannot prepare foreground-change test");
        server.reply("ERROR Foreground console changed\n");
        require(lease.poll() == HdmiDesktopLease::State::Failed && lease.error().find("Foreground console changed") != std::string::npos,
                "Loss of the foreground console was not reported");
        server.expect_closed();
        std::cout << "PASS: nonblocking HDMI lease, partial replies, failure, cancellation, timeout, bounded data, EOF and foreground loss\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "HDMI lease test: " << error.what() << '\n';
        return 1;
    }
}
