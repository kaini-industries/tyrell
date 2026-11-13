#include "playback.h"
#include "outputs.h"

#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <thread>

using namespace tyrell::video;
using Clock = std::chrono::steady_clock;

namespace {
void require(bool condition, const std::string& message) {
    if(!condition) throw std::runtime_error(message);
}

bool wait_for(Playback& player, const std::function<bool()>& condition, double seconds = 4) {
    const auto deadline = Clock::now() + std::chrono::milliseconds(static_cast<int>(seconds * 1000));
    while(Clock::now() < deadline) {
        player.poll();
        if(condition()) return true;
        if(!player.status().error.empty()) return false;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    return false;
}

struct TemporaryDirectory {
    std::filesystem::path path;
    TemporaryDirectory() {
        path = std::filesystem::temp_directory_path() /
            ("tyrell-playback-test-" + std::to_string(Clock::now().time_since_epoch().count()));
        std::filesystem::create_directory(path);
    }
    ~TemporaryDirectory() { std::error_code error; std::filesystem::remove_all(path, error); }
};
} // namespace

int main(int argc, char** argv) {
    try {
        require(argc == 2, "Pass the generated media-baseline.mp4 fixture");
        TemporaryDirectory temporary;
        const auto subtitle = temporary.path / "captions.srt";
        std::ofstream(subtitle) << "1\n00:00:00,000 --> 00:00:20,000\nTyrell subtitles\nA second readable line\n\n";
        const auto corrupt = temporary.path / "corrupt.mp4";
        std::ofstream(corrupt) << "This is not a media container";
        Playback player(PlaybackOptions{true});
        require(!player.set_audio_sink("tyrell-nonexistent-audio-output"), "A new invalid audio output selection must be rejected");

        require(!player.load((temporary.path / "missing.mp4").string()), "Missing media unexpectedly loaded");
        require(!player.status().error.empty(), "Missing media did not produce a useful error");
        player.stop();
        require(player.status().error.empty(), "Stop must clear the error before returning to the browser");
        require(!player.seek(1) && !player.play(), "Unloaded controls must fail safely");

        require(player.load(corrupt.string()), "Corrupt media should enter asynchronous probing");
        require(wait_for(player, [&] { return !player.status().error.empty(); }), "Corrupt media did not report an asynchronous error");
        player.stop();

        require(player.load(argv[1], subtitle.string()), "Fixture load failed: " + player.status().error);
        require(wait_for(player, [&] { return player.status().playing && player.latest_frame() && player.status().duration > 0; }),
                "Fixture never became playable: " + player.status().error);
        require(player.status().subtitle_track == -1, "Subtitles must start disabled");
        require(wait_for(player, [&] { return !player.status().subtitle_tracks.empty(); }), "External SRT stream was not discovered while disabled");
        require(!player.set_audio_track(99) && !player.set_subtitle_track(99), "Out-of-range track selection unexpectedly succeeded");
        auto held = player.latest_frame();
        const auto saved = held->pixels;
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
        require(player.latest_frame()->serial > held->serial && held->pixels == saved,
                "Latest-frame publication must advance without modifying an outstanding snapshot");

        require(player.pause(), "Pause failed");
        require(wait_for(player, [&] { return player.status().paused; }), "Pause did not settle");
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
        player.poll();
        const double paused_at = player.status().position;
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
        player.poll();
        require(std::abs(player.status().position - paused_at) < 0.08, "The paused media clock continued advancing");

        auto serial = player.latest_frame()->serial;
        require(player.seek(1.0), "Paused accurate seek failed");
        require(wait_for(player, [&] { auto frame = player.latest_frame(); return frame && frame->serial > serial && std::abs(frame->position - 1.0) < 0.03; }), "Paused seek did not publish a new preroll frame at the requested timestamp");
        const auto without_subtitle = player.latest_frame();
        serial = without_subtitle->serial;
        require(player.set_subtitle_track(0), "Enabling the external subtitle track failed");
        require(wait_for(player, [&] { auto frame = player.latest_frame(); return frame && frame->serial > serial && std::abs(frame->position - 1.0) < 0.03 && !player.status().buffering; }), "Subtitle seek did not produce a frame at the requested timestamp");
        const auto with_subtitle = player.latest_frame();
        std::size_t changed = 0;
        for(std::size_t i = 320 * 85 * 2; i < with_subtitle->pixels.size(); ++i)
            if(with_subtitle->pixels[i] != without_subtitle->pixels[i]) ++changed;
        if(changed <= 40) {
            std::cerr << "Subtitle pixel debug: beforePTS=" << without_subtitle->position << " afterPTS=" << with_subtitle->position
                      << " beforeSerial=" << without_subtitle->serial << " afterSerial=" << with_subtitle->serial
                      << " changed=" << changed << '\n';
        }
        require(changed > 40, "Enabling subtitles did not change the rendered lower half of the frame");
        serial = with_subtitle->serial;
        require(player.set_subtitle_track(-1), "Disabling subtitles failed");
        require(wait_for(player, [&] { auto frame = player.latest_frame(); return frame && frame->serial > serial && std::abs(frame->position - 1.0) < 0.03; }), "Disabling subtitles did not refresh the paused frame");
        require(player.latest_frame()->pixels == without_subtitle->pixels, "Disabled subtitles were still present in the rendered frame");

        const auto inventory = inspect_outputs(false);
        bool connected_hdmi = false;
        for(const auto& output : inventory.hdmi) connected_hdmi = connected_hdmi || output.connected;
        if(!connected_hdmi) {
            require(!player.set_output(Output::Hdmi), "Absent HDMI unexpectedly became active");
            require(player.status().output == Output::Lcd, "Failed HDMI selection did not retain LCD output");
            require(wait_for(player, [&] { return player.latest_frame() && !player.status().buffering; }), "LCD did not recover after absent HDMI selection");
            require(player.status().subtitle_track == -1 && player.status().paused, "Output recovery did not preserve pause/subtitle state");
        }

        const double duration = player.status().duration;
        require(player.seek(duration - 0.2) && player.play(), "Could not seek near EOS");
        const bool ended = wait_for(player, [&] { const auto s = player.status(); return s.paused && !s.playing && s.position >= duration - 0.08; });
        require(ended, "EOS did not settle in paused end state: " + player.status().error + " / " + player.status().notice +
                " position=" + std::to_string(player.status().position) + " duration=" + std::to_string(duration));
        require(player.play(), "Replay after EOS failed");
        require(wait_for(player, [&] { const auto s = player.status(); return s.playing && s.position < 1.0; }), "Replay did not restart the file");
        player.stop();
        require(!player.status().loaded && !player.latest_frame() && player.status().error.empty(), "Stop did not release playback and frame state");
        std::cout << "PASS: missing/corrupt media, bounded snapshots, subtitle rendering/off, paused seek, HDMI absence, EOS/replay and cleanup\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "Playback test: " << error.what() << '\n';
        return 1;
    }
}
