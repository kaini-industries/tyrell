#include "hdmi_audio.h"
#include "audio_session_policy.h"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <poll.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

using namespace tyrell::video;
using Clock = std::chrono::steady_clock;

namespace {
void require(bool condition, const char* message) { if(!condition) throw std::runtime_error(message); }
bool wait_for(HdmiAudioSession& session, HdmiAudioSession::State target, double seconds = 1) {
    const auto deadline = Clock::now() + std::chrono::milliseconds(static_cast<int>(seconds * 1000));
    while(Clock::now() < deadline) {
        if(session.poll() == target) return true;
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    return false;
}
int fake_launcher(int argc, char** argv) {
    const std::string token = argv[argc - 1];
    require(std::string(argv[argc - 2]) == "--lease", "Wrong service command");
    bool stop_post = false;
    for(int i = 1; i < argc; ++i)
        if(std::string(argv[i]).find("ExecStopPost=/not/an/actual/helper --cleanup " + token) != std::string::npos) stop_post = true;
    require(stop_post, "Transient service has no crash cleanup command");
    const std::string mode = std::getenv("TYRELL_AUDIO_TEST_MODE");
    if(mode == "failure") { std::cout << "ERROR HDMI device busy\n" << std::flush; return 1; }
    if(mode == "invalid") { std::cout << "READY foreign_sink\n" << std::flush; return 1; }
    if(mode == "oversized") { std::cout << std::string(513, 'x') << std::flush; return 1; }
    if(mode == "timeout") { std::this_thread::sleep_for(std::chrono::milliseconds(200)); return 0; }
    std::cout << "READY " << token << '\n' << std::flush;
    if(const char* notify = std::getenv("TYRELL_AUDIO_TEST_NOTIFY_FD")) {
        const char ready = '1';
        require(write(std::stoi(notify), &ready, 1) == 1, "Cannot notify queued READY");
    }
    if(mode == "death") { std::this_thread::sleep_for(std::chrono::milliseconds(100)); return 0; }
    char byte;
    while(recv(STDIN_FILENO, &byte, 1, 0) > 0) {}
    std::this_thread::sleep_for(std::chrono::milliseconds(80));
    return 0;
}
} // namespace

int main(int argc, char** argv) {
    try {
        if(argc > 1 && std::string(argv[1]) == "--user") return fake_launcher(argc, argv);
        const std::string token = "tyrell_video_hdmi_1000_1234_4567";
        require(valid_audio_lease_token(token, 1000), "Valid token rejected");
        require(!valid_audio_lease_token(token, 1001), "Another user's token accepted");
        require(!valid_audio_lease_token("foreign_1000_1234_4567", 1000), "Foreign prefix accepted");
        require(!valid_audio_lease_token(token + ";rm", 1000), "Command characters accepted");
        require(!valid_audio_lease_token("tyrell_video_hdmi_1000_0_4567", 1000), "Zero PID accepted");
        require(owned_audio_module("module-alsa-sink", "device=hdmi:CARD=vc4hdmi,DEV=0 sink_name=" + token + " mmap=false", token), "Owned module rejected");
        require(owned_audio_module("module-alsa-sink", "sink_name='" + token + "'", token), "Quoted owned module rejected");
        require(!owned_audio_module("module-null-sink", "sink_name=" + token, token), "Foreign module type accepted");
        require(!owned_audio_module("module-alsa-sink", "sink_name=" + token + "_foreign", token), "Prefix collision accepted");
        require(!owned_audio_module("module-alsa-sink", "sink_properties=\"device.description='sink_name=" + token + "'\"", token), "Description authorized foreign cleanup");
        require(!owned_audio_module("module-alsa-sink", "sink_name=" + token + " sink_name=foreign", token), "Duplicate key accepted");
        require(!owned_audio_module("module-alsa-sink", "sink_name='" + token, token), "Malformed quote accepted");

        HdmiAudioSession missing("/not/a/launcher");
        require(!missing.begin() && missing.state() == HdmiAudioSession::State::Failed, "Missing helper did not fail");
        setenv("TYRELL_AUDIO_TEST_MODE", "normal", 1);
        HdmiAudioSession session(argv[0], "/not/an/actual/helper", std::chrono::milliseconds(350));
        const auto started = Clock::now();
        require(session.begin(), "Cannot begin audio session");
        require(Clock::now() - started < std::chrono::milliseconds(100), "Audio setup blocked controller");
        require(wait_for(session, HdmiAudioSession::State::Ready), "Session never became ready");
        require(session.sink_name().rfind("tyrell_video_hdmi_", 0) == 0, "Sink name not owned");
        session.release();
        require(session.state() == HdmiAudioSession::State::Idle && session.sink_name().empty(), "Release did not clear audio route");
        require(session.begin(), "Cannot restart while cleanup pending");
        require(wait_for(session, HdmiAudioSession::State::Ready), "Deferred restart did not finish");
        session.release();
        std::this_thread::sleep_for(std::chrono::milliseconds(100)); session.poll();

        // Synchronize with the fake helper's flushed READY without polling the
        // audio client, then deliberately keep its UI thread busy past budget.
        // A reply queued by the successful helper must win over late polling.
        int notice[2];
        require(pipe(notice) == 0, "Cannot create late-poll notification pipe");
        const auto notify_fd = std::to_string(notice[1]);
        setenv("TYRELL_AUDIO_TEST_NOTIFY_FD", notify_fd.c_str(), 1);
        HdmiAudioSession late(argv[0], "/not/an/actual/helper", std::chrono::milliseconds(30));
        require(late.begin(), "Cannot begin late-poll session");
        unsetenv("TYRELL_AUDIO_TEST_NOTIFY_FD");
        pollfd notification{notice[0], POLLIN, 0};
        require(poll(&notification, 1, 1000) == 1 && (notification.revents & POLLIN),
                "Helper did not queue its READY reply");
        char ready;
        require(read(notice[0], &ready, 1) == 1 && ready == '1', "Invalid READY notification");
        close(notice[0]); close(notice[1]);
        std::this_thread::sleep_for(std::chrono::milliseconds(60));
        require(late.poll() == HdmiAudioSession::State::Ready,
                "Late UI polling discarded the helper's queued READY reply");
        late.release();

        for(const char* mode : {"failure", "invalid", "oversized", "timeout"}) {
            setenv("TYRELL_AUDIO_TEST_MODE", mode, 1);
            HdmiAudioSession failed(argv[0], "/not/an/actual/helper", std::chrono::milliseconds(80));
            require(failed.begin(), "Cannot start failure test");
            require(wait_for(failed, HdmiAudioSession::State::Failed), "Failure case was accepted or never timed out");
            require(!failed.error().empty() && failed.sink_name().empty(), "Failure retained an invalid route");
        }
        setenv("TYRELL_AUDIO_TEST_MODE", "death", 1);
        HdmiAudioSession dead(argv[0], "/not/an/actual/helper");
        require(dead.begin() && wait_for(dead, HdmiAudioSession::State::Ready), "Cannot prepare helper death");
        require(wait_for(dead, HdmiAudioSession::State::Failed), "Helper death did not invalidate audio lease");
        setenv("TYRELL_AUDIO_TEST_MODE", "normal", 1);
        const auto low_stdio = fork();
        require(low_stdio >= 0, "Cannot fork closed-stdio regression test");
        if(low_stdio == 0) {
            close(STDIN_FILENO); close(STDOUT_FILENO);
            HdmiAudioSession low(argv[0], "/not/an/actual/helper");
            const bool ok = low.begin() && wait_for(low, HdmiAudioSession::State::Ready);
            low.release();
            _exit(ok ? 0 : 1);
        }
        int low_status = 0;
        require(waitpid(low_stdio, &low_status, 0) == low_stdio && WIFEXITED(low_status) && WEXITSTATUS(low_status) == 0,
                "Closed stdin/stdout corrupted audio lease descriptors");
        std::cout << "PASS: audio lease setup, cleanup/restart, bounded protocol, timeout, helper death and token-scoped ownership\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "HDMI audio test: " << error.what() << '\n';
        return 1;
    }
}
