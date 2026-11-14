#include "playback.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

using namespace tyrell::video;
using Clock = std::chrono::steady_clock;

namespace {
void require(bool condition, const std::string& message) {
    if(!condition) throw std::runtime_error(message);
}

struct Captions {
    std::filesystem::path directory = std::filesystem::temp_directory_path() /
        ("tyrell-caption-clock-" + std::to_string(Clock::now().time_since_epoch().count()));
    std::filesystem::path path = directory / "captions.srt";
    Captions() {
        require(std::filesystem::create_directory(directory), "Cannot create caption fixture directory");
        std::ofstream output(path);
        output << "1\n00:00:00,000 --> 00:01:30,000\nCaption clock continuity\nPlayback must stay here\n\n";
        output.close();
        require(static_cast<bool>(output), "Cannot write caption clock fixture");
    }
    ~Captions() {
        std::error_code error;
        std::filesystem::remove_all(directory, error);
    }
};

void wait_for(Playback& player, const std::function<bool()>& ready, const std::string& failure,
              const std::function<void()>& observe = {}) {
    const auto deadline = Clock::now() + std::chrono::seconds(12);
    while(Clock::now() < deadline) {
        player.poll();
        require(player.status().error.empty(), failure + ": " + player.status().error);
        if(observe) observe();
        if(ready()) return;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    const auto status = player.status();
    const auto frame = player.latest_frame();
    throw std::runtime_error(failure + " (clock=" + std::to_string(status.position) +
        ", frame=" + (frame ? std::to_string(frame->position) : "none") + ")");
}

void check_time(const Playback& player, double minimum, double maximum, std::uint64_t builds) {
    const auto status = player.status();
    const auto frame = player.latest_frame();
    require(status.error.empty(), "Playback failed: " + status.error);
    require(status.pipeline_builds == builds, "Caption or pause rebuilt the playback pipeline");
    require(status.position >= minimum && status.position <= maximum,
            "Caption/pause jumped the media clock outside [" + std::to_string(minimum) + ", " +
            std::to_string(maximum) + "]: " + std::to_string(status.position));
    require(frame && frame->position >= minimum && frame->position <= maximum,
            "Caption/pause jumped the decoded video timestamp");
}

void hold_paused(Playback& player, double anchor, std::uint64_t builds, double seconds = 0.32) {
    const auto deadline = Clock::now() + std::chrono::milliseconds(static_cast<int>(seconds * 1000));
    while(Clock::now() < deadline) {
        player.poll();
        const auto status = player.status();
        check_time(player, anchor - 0.06, anchor + 0.06, builds);
        require(status.paused && !status.playing, "Caption selection resumed paused playback");
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
}

// Query sampling is slower than frame delivery. Check both throughout a window
// longer than a query period, permitting ordinary sampling/decoder latency but
// never a return to the beginning or an unexpected forward seek.
void continue_from(Playback& player, double anchor, std::uint64_t builds, double seconds = 1.0) {
    const auto started = Clock::now();
    const auto deadline = started + std::chrono::milliseconds(static_cast<int>(seconds * 1000));
    double furthest_clock = anchor, furthest_frame = anchor;
    while(Clock::now() < deadline) {
        player.poll();
        const auto status = player.status();
        const auto frame = player.latest_frame();
        const double elapsed = std::chrono::duration<double>(Clock::now() - started).count();
        check_time(player, anchor - 0.28, anchor + elapsed + 0.55, builds);
        furthest_clock = std::max(furthest_clock, status.position);
        furthest_frame = std::max(furthest_frame, frame->position);
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    require(furthest_clock > anchor + 0.10 && furthest_frame > anchor + 0.10,
            "Caption/resume stalled instead of continuing from the nonzero position (anchor=" +
            std::to_string(anchor) + ", clock=" + std::to_string(furthest_clock) +
            ", frame=" + std::to_string(furthest_frame) + ", window=" + std::to_string(seconds) + ")");
}

void exercise(const char* file, const std::string& subtitle, bool real_audio, const std::string& description) {
        PlaybackOptions options;
        options.fake_audio = !real_audio;
        options.desktop_handoff = false;
        options.automatic_hdmi_audio = false;
        Playback player(options);
        require(player.load(file, subtitle), "Cannot load long caption fixture");
        wait_for(player, [&] {
            const auto status = player.status();
            return status.playing && !status.buffering && status.duration >= 45 &&
                   !status.subtitle_tracks.empty() && player.latest_frame();
        }, "Long fixture or external SRT did not become ready");
        require(player.status().subtitle_track == -1, "Captions did not start off");
        const auto builds = player.status().pipeline_builds;
        constexpr double target = 30.5;

        // First exercise a nonzero playing seek and an ordinary pause. Then
        // pin an exact paused target so subtitle pixel comparisons are stable.
        require(player.seek(target), "Cannot seek into the middle of the fixture");
        wait_for(player, [&] {
            const auto frame = player.latest_frame();
            return frame && frame->position >= target && player.status().position >= target;
        }, "Playing seek did not reach thirty seconds");
        const auto before_pause = player.latest_frame();
        require(player.pause(), "Cannot pause at a nonzero position");
        wait_for(player, [&] {
            return player.status().paused && !player.status().playing;
        }, "Ordinary pause did not settle", [&] {
            check_time(player, before_pause->position - 0.06, before_pause->position + 0.35, builds);
        });
        require(player.seek(target), "Cannot pin the paused caption comparison target");
        wait_for(player, [&] {
            const auto status = player.status();
            const auto frame = player.latest_frame();
            return status.paused && !status.playing && !status.buffering && frame &&
                   std::abs(frame->position - target) < 0.04;
        }, "Paused seek did not retain its nonzero target", [&] {
            check_time(player, target - 0.06, target + 0.35, builds);
        });
        hold_paused(player, target, builds);
        const auto plain = player.latest_frame();

        require(player.set_subtitle_track(0), "Cannot enable paused captions");
        check_time(player, target - 0.06, target + 0.06, builds);
        wait_for(player, [&] {
            const auto frame = player.latest_frame();
            return frame && frame->serial > plain->serial && std::abs(frame->position - target) < 0.04 &&
                   frame->pixels != plain->pixels;
        }, "Enabling paused captions did not redraw the same video time", [&] {
            check_time(player, target - 0.06, target + 0.06, builds);
        });
        const auto captioned = player.latest_frame();
        require(captioned->pixels.size() == plain->pixels.size(), "Caption redraw changed the frame storage size");
        std::size_t changed = 0;
        for(std::size_t i = 0; i < plain->pixels.size(); ++i)
            if(plain->pixels[i] != captioned->pixels[i]) ++changed;
        require(changed > 40, "Enabled captions did not paint visible pixels");
        require(player.status().subtitle_track == 0, "Paused caption selection was lost");
        hold_paused(player, target, builds);

        require(player.set_subtitle_track(-1), "Cannot disable paused captions");
        check_time(player, target - 0.06, target + 0.06, builds);
        wait_for(player, [&] {
            const auto frame = player.latest_frame();
            return frame && frame->serial > captioned->serial && std::abs(frame->position - target) < 0.04 &&
                   frame->pixels == plain->pixels;
        }, "Disabling paused captions did not restore the picture at the same video time", [&] {
            check_time(player, target - 0.06, target + 0.06, builds);
        });
        hold_paused(player, target, builds);

        // An explicit paused seek is also a new intent. A time-refresh fix
        // must not replace it with the previous pipeline clock when captions
        // are selected immediately, before the seek has produced its frame.
        constexpr double queued_target = 36.5;
        const auto before_queued_seek = player.latest_frame();
        require(player.seek(queued_target) && player.set_subtitle_track(0),
                "Paused seek and immediate caption enable failed");
        require(std::abs(player.status().position - queued_target) < 0.06,
                "Caption enable replaced a queued paused seek with the old media clock");
        wait_for(player, [&] {
            const auto status = player.status();
            const auto frame = player.latest_frame();
            return status.paused && !status.playing && !status.buffering && frame &&
                   frame->serial > before_queued_seek->serial &&
                   std::abs(frame->position - queued_target) < 0.04 &&
                   std::abs(status.position - queued_target) < 0.06;
        }, "Caption enable did not preserve the queued paused seek target", [&] {
            // The retained snapshot can still show the previous target until
            // the new preroll arrives. Neither observation may reset to zero.
            check_time(player, target - 0.06, queued_target + 0.06, builds);
        });
        hold_paused(player, queued_target, builds);

        require(player.play(), "Cannot resume from the nonzero paused seek");
        continue_from(player, queued_target, builds);
        for(const int track : {0, -1, 0}) {
            const double anchor = player.status().position;
            require(player.set_subtitle_track(track), "Playing caption selection failed");
            check_time(player, anchor - 0.28, anchor + 0.55, builds);
            continue_from(player, anchor, builds);
            require(player.status().subtitle_track == track, "Playing caption selection was lost");
        }

        // A playing seek can still be in flight when the next UI event pauses
        // and enables captions. Refreshing the pause clock must preserve that
        // explicit target rather than querying back the pre-seek position.
        require(player.set_subtitle_track(-1), "Cannot prepare playing seek/pause case");
        const auto before_playing_seek = player.latest_frame();
        constexpr double playing_seek_target = 42.5;
        require(player.seek(playing_seek_target) && player.pause() && player.set_subtitle_track(0),
                "Playing seek, pause and immediate caption enable failed");
        require(std::abs(player.status().position - playing_seek_target) < 0.06,
                "Pause/caption enable replaced an unsettled playing seek with the old media clock");
        wait_for(player, [&] {
            const auto status = player.status();
            const auto frame = player.latest_frame();
            return status.paused && !status.playing && !status.buffering && frame &&
                   frame->serial > before_playing_seek->serial &&
                   std::abs(frame->position - playing_seek_target) < 0.04 &&
                   std::abs(status.position - playing_seek_target) < 0.06;
        }, "Playing seek/pause/caption batch lost its target", [&] {
            check_time(player, before_playing_seek->position - 0.3, playing_seek_target + 0.06, builds);
        });
        hold_paused(player, playing_seek_target, builds);
        require(player.play(), "Cannot resume the playing-seek/pause case");
        continue_from(player, playing_seek_target, builds);

        // The UI queries at 250 ms intervals, but streaming continues between
        // queries. Pause followed immediately by caption enable must use the
        // actual paused time, rather than seeking back to that cached value.
        require(player.set_subtitle_track(-1), "Cannot prepare stale-query case");
        continue_from(player, player.status().position, builds);
        player.poll();
        const double cached = player.status().position;
        std::this_thread::sleep_for(std::chrono::milliseconds(180));
        const auto before_stale_pause = player.latest_frame();
        require(before_stale_pause && before_stale_pause->position >= cached + 0.08,
                "Stale-query fixture did not advance streaming frames between UI polls");
        require(player.pause() && player.set_subtitle_track(0), "Immediate pause and caption enable failed");
        const double minimum = before_stale_pause->position - (1.0 / 24.0 + 0.02);
        const double maximum = before_stale_pause->position + 0.25;
        check_time(player, minimum, maximum, builds);
        wait_for(player, [&] {
            const auto status = player.status();
            const auto frame = player.latest_frame();
            return status.paused && !status.playing && !status.buffering && frame &&
                   frame->serial > before_stale_pause->serial;
        }, "Immediate pause/caption enable rewound the video", [&] {
            check_time(player, minimum, maximum, builds);
        });
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
        player.poll();
        check_time(player, minimum, maximum, builds);
        const double stale_paused_at = player.status().position;
        hold_paused(player, stale_paused_at, builds);
        require(player.play(), "Cannot resume the stale-query case");
        continue_from(player, stale_paused_at, builds);

        const auto before = player.status();
        require(player.pause() && player.set_subtitle_track(-1) && player.play() &&
                player.set_subtitle_track(0) && player.pause(), "Rapid caption/pause controls failed");
        wait_for(player, [&] {
            const auto status = player.status();
            return status.paused && !status.playing && !status.buffering &&
                   status.position >= before.position - 0.3 && status.position < before.position + 0.6;
        }, "Rapid controls reset time or lost the final paused state", [&] {
            check_time(player, before.position - 0.3, before.position + 0.6, builds);
        });
        // Allow a pending paused seek/query to settle before checking its clock.
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
        player.poll();
        const auto paused = player.status();
        const auto frozen = player.latest_frame();
        require(paused.position > target - 0.5 && frozen && frozen->position > target - 0.5,
                "Rapid controls returned playback to the beginning");
        check_time(player, before.position - 0.3, before.position + 0.6, builds);
        hold_paused(player, paused.position, builds);
        require(player.play(), "Cannot resume after rapid caption/pause controls");
        continue_from(player, paused.position, builds);
        player.stop();
        std::cout << description << ": preserved media clock beyond " << target
                  << " seconds, stale-query lag " << before_stale_pause->position - cached << " seconds\n";
}
} // namespace

int main(int argc, char** argv) {
    try {
        require(argc == 3 || (argc == 4 && std::string(argv[3]) == "--real-audio"),
                "Pass >=45-second constant-color MP4 and embedded-SRT MKV fixtures, optionally --real-audio");
        const bool real_audio = argc == 4;
        Captions captions;
        exercise(argv[1], captions.path.string(), real_audio, "External SRT");
        exercise(argv[2], {}, real_audio, "Embedded SRT");
        std::cout << "PASS: captions and pause/resume preserve nonzero media clock and frame timestamps, "
                     "including paused redraws and rapid controls\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "Caption clock test: " << error.what() << '\n';
        return 1;
    }
}
