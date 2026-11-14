#include "playback.h"
#include "json_string.h"

#include <chrono>
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <sys/resource.h>

using namespace tyrell::video;
using Clock = std::chrono::steady_clock;

static double cpu_seconds(const rusage& r) {
    return r.ru_utime.tv_sec + r.ru_stime.tv_sec +
        (r.ru_utime.tv_usec + r.ru_stime.tv_usec) / 1000000.0;
}

int main(int argc, char** argv) {
    try {
        std::string file, output = "lcd", subtitle;
        double seconds = 6;
        bool fake_audio = false, exercise = false;
        for(int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            auto value = [&]() -> std::string {
                if(i + 1 >= argc) throw std::runtime_error("Missing value for " + arg);
                return argv[++i];
            };
            if(arg == "--seconds") seconds = std::stod(value());
            else if(arg == "--output") output = value();
            else if(arg == "--subtitle") subtitle = value();
            else if(arg == "--fake-audio") fake_audio = true;
            else if(arg == "--exercise") exercise = true;
            else if(arg == "--help") {
                std::cout << "tyrell-video-probe FILE [--seconds N] [--output lcd|hdmi] "
                             "[--subtitle SRT] [--fake-audio] [--exercise]\n"
                             "No LCD rendering or input access. JSON reports sample delivery, not display FPS.\n";
                return 0;
            } else if(!arg.empty() && arg[0] == '-') throw std::runtime_error("Unknown option " + arg);
            else if(file.empty()) file = arg;
            else throw std::runtime_error("Only one media file is accepted");
        }
        if(file.empty() || !std::isfinite(seconds) || seconds <= 0 || seconds > 86400 ||
           (output != "lcd" && output != "hdmi")) throw std::runtime_error("Invalid file, runtime or output; use --help");
        rusage before{}; getrusage(RUSAGE_SELF, &before);
        Playback player(PlaybackOptions{fake_audio, false, false});
        bool ok = output != "hdmi" || player.set_output(Output::Hdmi);
        if(ok) ok = player.load(file, subtitle);
        const auto started = Clock::now();
        std::uint64_t serial = 0, unique_frames = 0;
        bool pause_checked = false, resumed = false, track_checked = false, observed_playing = false;
        bool pause_sampled = false, seek_checked = false;
        double paused_position = 0;
        auto exercise_started = Clock::time_point{};
        std::string check_error;
        while(ok && std::chrono::duration<double>(Clock::now() - started).count() < seconds) {
            player.poll();
            const auto status = player.status();
            observed_playing = observed_playing || status.playing;
            if(!status.error.empty()) { ok = false; break; }
            auto frame = player.latest_frame();
            if(frame && frame->serial != serial) {
                serial = frame->serial; ++unique_frames;
                if(frame->width != 320 || frame->height != 170 || frame->stride != 640 ||
                   frame->pixels.size() != 320u * 170u * 2u) {
                    check_error = "Invalid LCD frame dimensions/stride/storage"; ok = false; break;
                }
            }
            const bool video_delivered = output == "lcd" ? unique_frames > 0 : status.video_buffers_delivered > 0;
            if(exercise_started == Clock::time_point{} && status.playing && video_delivered)
                exercise_started = Clock::now();
            const double elapsed = exercise_started == Clock::time_point{} ? -1.0 :
                std::chrono::duration<double>(Clock::now() - exercise_started).count();
            if(exercise && !pause_checked && elapsed >= 0.8 && video_delivered) {
                ok = player.pause(); pause_checked = true;
                player.set_volume(0.3); player.set_muted(true);
                if(!ok) check_error = "Pause failed";
            }
            if(exercise && pause_checked && !resumed && elapsed >= 1.0 && !pause_sampled) {
                paused_position = status.position; pause_sampled = true;
            }
            if(exercise && pause_checked && !resumed && elapsed >= 1.2) {
                ok = status.paused && status.muted && std::abs(status.volume - 0.3) < 0.01 &&
                     pause_sampled && std::abs(status.position - paused_position) < 0.15;
                if(!ok) check_error = "Paused clock/volume/mute state did not settle";
                if(ok) ok = player.seek(0.25) && player.play();
                resumed = true;
                player.set_muted(false);
                if(!ok && check_error.empty()) check_error = "Seek/resume failed";
            }
            if(exercise && resumed && !seek_checked && elapsed >= 1.45) {
                // The source clock would be near one second without the seek.
                // Verify the queried pipeline clock actually moved backwards.
                ok = status.position >= 0.2 && status.position < 0.85 && !status.muted;
                seek_checked = true;
                if(!ok) check_error = "Seek did not move the pipeline clock backwards";
            }
            if(exercise && resumed && !track_checked && elapsed >= 1.5) {
                if(!status.audio_tracks.empty()) ok = player.set_audio_track(status.audio_tracks.back().index);
                if(ok && !status.subtitle_tracks.empty()) ok = player.set_subtitle_track(status.subtitle_tracks.front().index);
                if(ok) ok = player.set_subtitle_track(-1);
                track_checked = true;
                if(!ok) check_error = "Track selection failed";
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        player.poll();
        const auto status = player.status();
        rusage after{}; getrusage(RUSAGE_SELF, &after);
        const double wall = std::chrono::duration<double>(Clock::now() - started).count();
        const bool output_achieved = status.output == (output == "hdmi" ? Output::Hdmi : Output::Lcd);
        if(!output_achieved) { ok = false; check_error = "Requested display was unavailable; fallback is not an HDMI pass"; }
        if(!observed_playing) { ok = false; if(check_error.empty()) check_error = "Pipeline never reached playing state"; }
        if(output == "lcd" && unique_frames == 0) { ok = false; check_error = "No video frames delivered"; }
        if(output == "hdmi" && status.video_buffers_delivered == 0) {
            ok = false; if(check_error.empty()) check_error = "No video buffers delivered to the HDMI sink";
        }
        if(exercise && !(pause_checked && resumed && seek_checked && track_checked)) {
            ok = false;
            if(check_error.empty()) check_error = "Control exercise did not complete";
        }
        if(!status.error.empty()) ok = false;
        std::cout << "{\"ok\":" << (ok ? "true" : "false")
            << ",\"measurement\":\"GStreamer video delivery; no physical display or audio verification\""
            << ",\"fake_audio\":" << (fake_audio ? "true" : "false")
            << ",\"output_achieved\":" << (output_achieved ? "true" : "false")
            << ",\"observed_playing\":" << (observed_playing ? "true" : "false")
            << ",\"elapsed_seconds\":" << wall
            << ",\"cpu_seconds\":" << cpu_seconds(after) - cpu_seconds(before)
            << ",\"max_rss_kib\":"
#if defined(__APPLE__)
            << after.ru_maxrss / 1024
#else
            << after.ru_maxrss
#endif
            << ",\"unique_frames_consumed\":" << unique_frames
            << ",\"control_exercise\":" << (exercise ? "true" : "false")
            << ",\"check_error\":" << json_string(check_error)
            << ",\"playback\":" << player.diagnostics_json() << "}\n";
        player.stop();
        return ok ? 0 : 1;
    } catch(const std::exception& error) {
        std::cerr << "tyrell-video-probe: " << error.what() << '\n';
        return 2;
    }
}
