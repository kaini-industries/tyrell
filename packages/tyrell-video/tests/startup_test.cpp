#include "playback.h"

#include <chrono>
#include <cmath>
#include <cstring>
#include <functional>
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
    std::string path = "/tmp/tyrell-startup-" + std::to_string(getpid()) + ".sock";
    int fd = -1, peer = -1;
    Server() {
        unlink(path.c_str());
        fd = socket(AF_UNIX, SOCK_STREAM, 0);
        require(fd >= 0, "Cannot create startup test server");
        sockaddr_un address{};
        address.sun_family = AF_UNIX;
        std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
        require(bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0 && listen(fd, 4) == 0,
                "Cannot bind startup test server");
    }
    ~Server() {
        if(peer >= 0) close(peer);
        if(fd >= 0) close(fd);
        unlink(path.c_str());
    }
    void accept_client() {
        if(peer >= 0) close(peer);
        peer = accept(fd, nullptr, nullptr);
        require(peer >= 0, "Cannot accept startup client");
    }
    void reply(const std::string& text) {
        require(send(peer, text.data(), text.size(), 0) == static_cast<ssize_t>(text.size()), "Cannot send lease reply");
    }
    void expect_closed() {
        char byte;
        require(recv(peer, &byte, 1, MSG_DONTWAIT) == 0, "A cancelled startup retained its desktop lease");
    }
    void disconnect() { close(peer); peer = -1; }
};

bool wait_for(Playback& player, const std::function<bool()>& condition, double seconds = 8) {
    const auto deadline = Clock::now() + std::chrono::milliseconds(static_cast<int>(seconds * 1000));
    while(Clock::now() < deadline) {
        player.poll();
        if(condition()) return true;
        if(!player.status().error.empty()) return false;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    return false;
}

void begin(Playback& player, Server& server, const char* fixture) {
    require(player.set_output(Output::Hdmi), "Cannot request initial HDMI");
    const auto started = Clock::now();
    require(player.load(fixture), "Cannot load fixture: " + player.status().error);
    require(Clock::now() - started < std::chrono::seconds(1), "Waiting for HDMI blocked load()");
    server.accept_client();
    const auto status = player.status();
    require(status.loaded && status.buffering && status.output_switching && status.output == Output::Lcd,
            "The controller did not expose pending initial HDMI startup");
    require(status.pipeline_builds == 0 && !player.latest_frame(), "A decoder was opened before the output was ready");
    require(status.first_render_seconds < 0 && status.first_hdmi_render_seconds < 0 && status.first_playing_seconds < 0,
            "Readiness was reported before any pipeline existed");
    const auto poll_started = Clock::now();
    player.poll();
    require(Clock::now() - poll_started < std::chrono::milliseconds(100), "Output preparation blocked poll()");
    require(player.status().pipeline_builds == 0, "Polling a silent lease opened a decoder");
}

void require_paused_target(Playback& player, double target) {
    require(wait_for(player, [&] {
        const auto frame = player.latest_frame();
        const auto status = player.status();
        return status.paused && !status.buffering && frame && std::abs(frame->position - target) < 0.05;
    }), "Startup lost paused seek intent: " + player.status().error);
    require(!player.status().playing, "Paused startup unexpectedly began playing");
}
} // namespace

int main(int argc, char** argv) {
    try {
        require(argc == 2, "Pass the generated media-baseline.mp4 fixture");
        Server server;
        PlaybackOptions options;
        options.fake_audio = true;
        options.automatic_hdmi_audio = false;
        options.desktop_handoff_socket = server.path;
        Playback player(options);

        begin(player, server, argv[1]);
        require(player.seek(0.75) && player.seek_relative(0.5) && player.pause(), "Deferred controls rejected input");
        require(player.set_subtitle_track(-1), "Disabling captions during preparation failed");
        player.set_volume(0.3);
        player.set_muted(true);
        require(player.set_output(Output::Lcd), "Cancelling startup did not open the LCD");
        server.expect_closed();
        require_paused_target(player, 1.25);
        require(player.status().pipeline_builds == 1 && !player.status().output_switching,
                "Cancelling initial HDMI must open exactly one LCD graph");
        require(player.status().muted && std::abs(player.status().volume - 0.3) < 0.01,
                "Deferred volume and mute were lost");
        require(player.play(), "Cancelled startup could not resume");
        require(wait_for(player, [&] {
            const auto s = player.status();
            return s.playing && s.first_playing_seconds >= 0 && s.first_render_seconds >= 0;
        }), "Readiness metrics did not advance after actual playback");
        require(player.status().first_hdmi_render_seconds < 0, "LCD rendering was counted as HDMI rendering");
        player.stop();

        begin(player, server, argv[1]);
        require(player.seek(0.5) && player.pause(), "Cannot queue helper-failure controls");
        server.reply("ERROR Desktop is occupied\n");
        require_paused_target(player, 0.5);
        server.expect_closed();
        require(player.status().output == Output::Lcd && player.status().pipeline_builds == 1 &&
                player.status().notice.find("Desktop is occupied") != std::string::npos,
                "Handoff failure did not create one usable LCD pipeline with an actionable notice");
        player.stop();

        begin(player, server, argv[1]);
        require(player.seek(0.5), "Cannot queue an initial seek");
        require(player.pause() && player.play() && player.pause(), "Pending pause/resume intent could not be changed");
        // READY permits the real KMS attempt. A VM with no HDMI falls back to
        // LCD, while a machine with a free display can retain HDMI.
        server.reply("READY\n");
        require(wait_for(player, [&] {
            const auto s = player.status();
            if(!s.paused || s.buffering || s.output_switching || std::abs(s.position - 0.5) >= 0.08) return false;
            const auto frame = player.latest_frame();
            return s.output == Output::Lcd ? frame && std::abs(frame->position - 0.5) < 0.05 : s.video_buffers_delivered >= 2;
        }), "READY lost the deferred position/pause or failed LCD recovery: " + player.status().error);
        require(player.status().pipeline_builds >= 1 && player.status().pipeline_builds <= 2,
                "READY created redundant pipeline graphs");
        player.stop();
        server.expect_closed();

        begin(player, server, argv[1]);
        require(player.load(argv[1]), "Replacing a file during preparation failed");
        require(player.status().pipeline_builds == 0 && player.status().output_switching,
                "Replacing a pending file prematurely opened a pipeline");
        player.stop();
        server.expect_closed();
        require(!player.status().loaded && !player.status().output_switching && !player.latest_frame() &&
                player.status().pipeline_builds == 0 && !player.play() && !player.seek(1),
                "Stop did not cancel pending startup completely");

        begin(player, server, argv[1]);
        require(player.pause(), "Cannot pause a pending helper-death case");
        server.disconnect();
        require_paused_target(player, 0);
        require(player.status().output == Output::Lcd && player.status().pipeline_builds == 1,
                "Helper death did not recover to a single LCD graph");
        player.stop();

        {
            Playback dying(options);
            begin(dying, server, argv[1]);
        }
        server.expect_closed();

        PlaybackOptions missing_options = options;
        missing_options.desktop_handoff_socket += ".missing";
        Playback missing(missing_options);
        require(missing.set_output(Output::Hdmi) && missing.load(argv[1]), "Missing service prevented the free-display/LCD fallback");
        require(wait_for(missing, [&] { return !missing.status().buffering; }), "Missing-service fallback did not settle");
        if(missing.status().output == Output::Lcd) {
            require(missing.status().notice.find("HDMI desktop handoff is unavailable") != std::string::npos &&
                    missing.status().notice.find("; using LCD") != std::string::npos,
                    "Direct KMS failure discarded the desktop-service failure context");
        }
        missing.stop();
        std::cout << "PASS: deferred HDMI startup, no early decoder, responsive queued controls, cancel/failure/READY, replacement and cleanup\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "Startup test: " << error.what() << '\n';
        return 1;
    }
}
