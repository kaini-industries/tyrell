#include "playback.h"

#include <chrono>
#include <cmath>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <thread>

using namespace tyrell::video;
using Clock = std::chrono::steady_clock;

namespace {
void require(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}
bool wait_for(Playback& player, const std::function<bool()>& condition, double seconds = 4) {
    const auto deadline = Clock::now() + std::chrono::milliseconds(static_cast<int>(seconds * 1000));
    while(Clock::now() < deadline) {
        player.poll();
        if(!player.status().error.empty()) throw std::runtime_error(player.status().error);
        if(condition()) return true;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    return false;
}
} // namespace

int main(int argc, char** argv) {
    try {
        require(argc == 2, "Pass the generated two-audio/embedded-SRT Matroska fixture");
        Playback player(PlaybackOptions{true});
        require(player.load(argv[1]), "Multitrack fixture could not load");
        require(wait_for(player, [&] {
            const auto status = player.status();
            return status.playing && status.audio_tracks.size() == 2 && !status.subtitle_tracks.empty() && player.latest_frame();
        }), "Two audio streams and embedded SRT were not discovered");
        require(player.status().subtitle_track == -1, "Embedded subtitles must start disabled");

        // Match the live probe's back-to-back controls: an asynchronous audio
        // selector change immediately followed by caption enable and disable.
        require(player.pause(), "Initial pause failed");
        require(player.seek(0.25) && player.play(), "Initial seek/resume failed");
        require(wait_for(player, [&] { return player.status().playing && player.latest_frame(); }), "Resume did not settle");
        require(player.set_audio_track(1) && player.set_subtitle_track(0) && player.set_subtitle_track(-1),
                "Rapid playing audio/caption changes failed");
        const auto first_serial = player.latest_frame() ? player.latest_frame()->serial : 0;
        require(wait_for(player, [&] {
            const auto status = player.status();
            auto frame = player.latest_frame();
            return status.playing && status.audio_track == 1 && status.subtitle_track == -1 && frame && frame->serial > first_serial + 2;
        }), "Rapid playing track changes stopped media delivery");

        require(player.pause() && player.seek(1.0), "Paused seek failed");
        require(wait_for(player, [&] {
            auto frame = player.latest_frame();
            return player.status().paused && frame && std::abs(frame->position - 1.0) < 0.03;
        }), "Paused target frame did not arrive");
        const auto plain = player.latest_frame();
        require(player.set_audio_track(0) && player.set_subtitle_track(0) && player.set_subtitle_track(-1),
                "Rapid paused audio/caption changes failed");
        require(wait_for(player, [&] {
            const auto status = player.status();
            auto frame = player.latest_frame();
            return status.paused && status.audio_track == 0 && status.subtitle_track == -1 && frame &&
                   frame->serial > plain->serial && std::abs(frame->position - 1.0) < 0.03;
        }), "Rapid paused track changes did not settle");
        require(player.latest_frame()->pixels == plain->pixels, "Caption Off retained subtitle pixels after rapid controls");

        const auto serial = player.latest_frame()->serial;
        require(player.set_subtitle_track(0), "Embedded subtitle enable failed");
        require(wait_for(player, [&] {
            auto frame = player.latest_frame();
            return frame && frame->serial > serial && std::abs(frame->position - 1.0) < 0.03;
        }), "Enabling embedded subtitles did not redraw paused video");
        std::size_t changed = 0;
        auto captioned = player.latest_frame();
        for(std::size_t i = 320 * 85 * 2; i < captioned->pixels.size(); ++i)
            if(captioned->pixels[i] != plain->pixels[i]) ++changed;
        require(changed > 40, "Embedded subtitles did not appear in the actual video pixels");
        require(player.set_subtitle_track(-1) && player.play(), "Final caption off/resume failed");
        for(int index = 0; index < 6; ++index) {
            require(player.set_audio_track(index % 2) && player.set_subtitle_track(0) && player.set_subtitle_track(-1),
                    "Repeated playing track controls failed");
            player.poll();
            std::this_thread::sleep_for(std::chrono::milliseconds(60));
        }
        require(wait_for(player, [&] { return player.status().playing && player.latest_frame(); }), "Repeated controls stopped playback");
        player.stop();
        require(!player.status().loaded, "Multitrack stop did not release playback");
        std::cout << "PASS: real two-audio/embedded-SRT rapid track changes, paused redraw, caption pixels and stop\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "Multitrack playback test: " << error.what() << '\n';
        return 1;
    }
}
