#include "playback.h"

#include <chrono>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <thread>

#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

using namespace tyrell::video;
using Clock = std::chrono::steady_clock;
namespace {
void require(bool value, const std::string& message) {
    if(!value) throw std::runtime_error(message);
}
void wait_for(Playback& player, const std::function<bool()>& ready, const std::string& message) {
    const auto end = Clock::now() + std::chrono::seconds(12);
    do {
        player.poll();
        require(player.status().error.empty(), message + ": " + player.status().error);
        if(ready()) return;
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    } while(Clock::now() < end);
    const auto status = player.status(); const auto frame = player.latest_frame();
    throw std::runtime_error(message + " (position=" + std::to_string(status.position) +
        ", frame=" + (frame ? std::to_string(frame->position) : "none") +
        ", playing=" + (status.playing ? "true" : "false") + ", paused=" + (status.paused ? "true" : "false") + ")");
}
PlaybackOptions options() {
    PlaybackOptions result;
    result.fake_audio = true;
    result.desktop_handoff = false;
    result.automatic_hdmi_audio = false;
    return result;
}
struct Files {
    std::filesystem::path root = std::filesystem::temp_directory_path() /
        ("tyrell-checkpoint-" + std::to_string(Clock::now().time_since_epoch().count()));
    std::filesystem::path subtitle = root / "captions.srt";
    Files() {
        require(std::filesystem::create_directory(root), "Cannot create checkpoint fixtures");
        std::ofstream(subtitle) << "1\n00:00:00,000 --> 00:01:30,000\nCheckpoint caption continuity\n\n";
    }
    ~Files() { std::error_code error; std::filesystem::remove_all(root, error); }
};
void settled_paused(Playback& player, double target) {
    wait_for(player, [&] {
        const auto state = player.status(); const auto frame = player.latest_frame();
        return state.paused && !state.playing && !state.buffering && frame && std::abs(frame->position - target) < .045;
    }, "Paused checkpoint did not restore its target frame");
}
void check_released(Playback& player) {
    require(player.suspended() && player.status().suspended, "Suspend state was not exposed");
    require(player.resources().local_handles_released(), "Suspension retained a local graph/DRM/lease handle");
    require(!player.latest_frame(), "Suspension retained the app's latest-frame publication");
}
void captions_and_settings(const char* file) {
    std::cout << "checkpoint: external captions/settings\n" << std::flush;
    Files files;
    Playback player(options());
    require(!player.capture_checkpoint(), "Unloaded capture produced a checkpoint");
    require(player.load(file, files.subtitle.string()), "Cannot load external-caption fixture");
    wait_for(player, [&] { return player.status().playing && !player.status().subtitle_tracks.empty() && player.latest_frame(); },
             "External caption stream was not discovered");
    player.set_volume(.27); player.set_muted(true);
    require(player.set_audio_sink("") && player.pause() && player.seek(1), "Cannot prepare paused settings");
    settled_paused(player, 1);
    require(player.resources().pipeline_present, "User pause unexpectedly destroyed the pipeline");
    const auto plain = player.latest_frame();
    require(player.set_subtitle_track(0), "Cannot enable external captions");
    wait_for(player, [&] {
        const auto frame = player.latest_frame();
        return frame && frame->serial > plain->serial && std::abs(frame->position - 1) < .045 && frame->pixels != plain->pixels;
    }, "Selected external captions did not render");
    const auto captioned = player.latest_frame();
    const auto caption_pixels = captioned->pixels;
    const auto builds = player.status().pipeline_builds;
    const auto active = player.capture_checkpoint();
    require(active && !active->playing && !active->ended && active->external_subtitle && active->subtitle_track == 0,
            "Capture lost paused/external-caption intent");
    require(active->media.canonical_path == std::filesystem::canonical(file).string() && active->media.bytes > 0,
            "Capture lacks accepted media identity");
    require(active->muted && std::abs(active->volume - .27) < .001 && active->requested_audio_sink.empty(),
            "Capture lost volume/mute/audio route preference");
    std::cout << "checkpoint: suspend paused captioned graph\n" << std::flush;
    require(player.suspend(), "Cannot suspend paused playback");
    check_released(player);
    require(!player.resources().external_cleanup_unconfirmed, "Pure fake-audio LCD claimed external cleanup");
    auto retained = player.checkpoint();
    require(retained && std::abs(retained->position - 1) < .045, "Suspended checkpoint lost paused time");
    const auto position = retained->position;
    retained->position = 40;
    require(std::abs(player.checkpoint()->position - position) < .001, "Checkpoint accessor exposes mutable authoritative state");
    require(player.suspend() && std::abs(player.checkpoint()->position - position) < .001,
            "Repeated suspend did not retain the authoritative checkpoint");
    require(captioned->pixels == caption_pixels, "Suspension mutated an outstanding immutable frame");
    std::cout << "checkpoint: resume paused captioned graph\n" << std::flush;
    require(player.resume(), "Cannot resume paused checkpoint");
    settled_paused(player, 1);
    auto restored = player.status();
    require(restored.pipeline_builds == builds + 1 && restored.subtitle_track == 0 && restored.muted && std::abs(restored.volume - .27) < .001,
            "Resume did not rebuild once with selected settings");
    require(player.latest_frame()->pixels == captioned->pixels, "Resume lost caption pixels at the saved paused timestamp");
    const auto resume_builds = restored.pipeline_builds;
    (void)player.resume();
    require(player.status().pipeline_builds == resume_builds, "Repeated resume rebuilt a live graph");
    for(unsigned cycle = 0; cycle < 2; ++cycle) {
        std::cout << "checkpoint: captioned repeat cycle " << cycle << '\n' << std::flush;
        require(player.suspend(), "Repeated checkpoint cycle failed");
        const auto checkpoint = player.checkpoint();
        require(checkpoint && !checkpoint->video_decode_retry_attempted && !checkpoint->software_video_preferred && checkpoint->video_decode_fallbacks == 0,
                "An unused decoder budget changed during suspension");
        require(player.resume(), "Repeated checkpoint restore failed");
        settled_paused(player, 1);
    }
    player.stop();
    require(!player.suspended() && !player.checkpoint(), "Stop did not invalidate the checkpoint");
    (void)player.resume();
    require(!player.status().loaded && player.resources().local_handles_released(), "Resume after stop reopened discarded media");
    require(player.load(file), "Fresh accepted load failed after stop");
    require(player.status().video_decode_fallbacks == 0 && !player.status().software_video_fallback,
            "Fresh accepted load did not start a new decoder budget");
    player.stop();
}
void fresh_and_pending_positions(const char* file) {
    std::cout << "checkpoint: fresh/queued positions\n" << std::flush;
    Playback player(options());
    require(player.load(file), "Cannot load fresh-position fixture");
    wait_for(player, [&] { return player.status().playing && player.latest_frame() && player.status().position >= .2; }, "Fixture never started");
    const auto cached = player.status().position;
    std::this_thread::sleep_for(std::chrono::milliseconds(120));
    const auto fresh = player.capture_checkpoint();
    require(fresh && fresh->playing && fresh->position_source == CheckpointPosition::FreshQuery && fresh->position >= cached + .04,
            "Stable running capture reused the stale 250 ms status position");
    require(player.suspend(), "Cannot suspend playing media"); check_released(player);
    const auto anchor = player.checkpoint()->position;
    require(player.resume(), "Cannot restore playing media");
    wait_for(player, [&] {
        auto frame = player.latest_frame();
        return player.status().playing && !player.status().buffering && frame && std::abs(frame->position - anchor) <= .25;
    }, "Playing resume did not continue within 250 ms of the saved target");

    require(player.pause() && player.seek(2), "Cannot queue an accurate paused seek");
    const auto pending = player.capture_checkpoint();
    require(pending && std::abs(pending->position - 2) < .045,
            "Immediate capture queried pre-seek position instead of preserving queued target");
    require(player.suspend() && player.resume(), "Cannot cycle a pending seek"); settled_paused(player, 2);
    player.stop();
    require(player.load(file) && player.seek(3) && player.pause(), "Cannot queue initial preroll intent");
    const auto preroll = player.capture_checkpoint();
    require(preroll && preroll->position_source == CheckpointPosition::PendingTarget && std::abs(preroll->position - 3) < .001 && !preroll->playing,
            "Initial preroll capture lost queued pause/seek intent");
    require(player.suspend() && player.resume(), "Cannot restore preroll checkpoint"); settled_paused(player, 3);
}
void embedded_tracks(const char* file) {
    std::cout << "checkpoint: embedded track selections\n" << std::flush;
    Playback player(options()); require(player.load(file), "Cannot load multitrack fixture");
    wait_for(player, [&] { return player.status().audio_tracks.size() == 2 && !player.status().subtitle_tracks.empty() && player.latest_frame(); },
             "Multitrack streams were not discovered");
    // Both the baseline and candidate GStreamer stack can block on a paused
    // 0->1 audio switch immediately followed by a caption flushing seek. Keep
    // this checkpoint test on a completed selection; the separate rapid-track
    // regression suite continues to exercise the existing controller path.
    const auto before_audio = player.latest_frame()->serial;
    std::cout << "checkpoint: embedded select playing audio\n" << std::flush;
    require(player.set_audio_track(1), "Cannot select embedded audio track");
    wait_for(player, [&] {
        const auto frame = player.latest_frame();
        return player.status().playing && player.status().audio_track == 1 && frame && frame->serial > before_audio + 3;
    }, "Playing audio selection did not settle");
    std::cout << "checkpoint: embedded pause/seek\n" << std::flush;
    require(player.pause() && player.seek(1), "Cannot prepare multitrack target"); settled_paused(player, 1);
    const auto plain = player.latest_frame();
    std::cout << "checkpoint: embedded enable caption\n" << std::flush;
    require(player.set_subtitle_track(0), "Cannot select embedded caption track");
    std::cout << "checkpoint: embedded settle selected tracks\n" << std::flush;
    wait_for(player, [&] {
        const auto frame = player.latest_frame();
        return player.status().paused && player.status().subtitle_track == 0 && frame && frame->serial > plain->serial &&
            std::abs(frame->position - 1) < .045 && frame->pixels != plain->pixels;
    }, "Selected embedded caption did not render before checkpoint");
    std::cout << "checkpoint: embedded suspend\n" << std::flush;
    require(player.suspend(), "Cannot suspend selected embedded tracks");
    const auto checkpoint = player.checkpoint();
    require(checkpoint && checkpoint->audio_track == 1 && checkpoint->subtitle_track == 0 && !checkpoint->external_subtitle,
            "Capture lost embedded track identity");
    std::cout << "checkpoint: embedded resume\n" << std::flush;
    require(player.resume(), "Cannot restore selected embedded tracks");
    wait_for(player, [&] { return player.status().audio_track == 1 && player.status().subtitle_track == 0 && player.latest_frame() && !player.status().buffering; },
             "Embedded track selections did not survive resume");
    settled_paused(player, 1);
    const auto caption_pixels = player.latest_frame()->pixels;
    for(unsigned cycle = 0; cycle < 2; ++cycle) {
        // A quick second capture could hide an unconfirmed-seek latch behind
        // its grace interval. Later selector SEGMENT events must not erase
        // proof of the completed seek or keep returning its old target.
        std::this_thread::sleep_for(std::chrono::milliseconds(1100));
        player.poll();
        const auto fresh = player.capture_checkpoint();
        require(fresh && fresh->position_source == CheckpointPosition::FreshQuery && std::abs(fresh->position - 1) < .045 &&
                fresh->audio_track == 1 && fresh->subtitle_track == 0,
                "Completed embedded restore retained an unconfirmed seek or lost selected streams");
        require(player.suspend() && player.resume(), "Repeated embedded checkpoint cycle failed");
        settled_paused(player, 1);
        require(player.status().audio_track == 1 && player.status().subtitle_track == 0 && player.latest_frame()->pixels == caption_pixels,
                "Repeated embedded checkpoint lost selected caption pixels");
    }
}
void changed_files_are_rejected(const char* source) {
    std::cout << "checkpoint: changed media/sidecar identities\n" << std::flush;
    for(bool change_subtitle : {false, true}) {
        Files files; const auto media = files.root / "media.mp4";
        std::filesystem::copy_file(source, media);
        Playback player(options());
        require(player.load(media.string(), files.subtitle.string()), "Cannot load identity fixture");
        require(player.suspend(), "Cannot suspend identity fixture");
        const auto builds = player.status().pipeline_builds;
        std::ofstream(change_subtitle ? files.subtitle : media, std::ios::app) << "changed\n";
        require(!player.resume(), "Resume accepted changed media/sidecar identity");
        require(player.suspended() && player.resources().local_handles_released() && player.status().pipeline_builds == builds,
                "Changed-file rejection opened replacement resources");
        require(!player.status().error.empty(), "Changed-file rejection lacks an actionable error");
        player.stop(); require(!player.checkpoint(), "Stop retained an invalid file checkpoint");
    }
}
void eos_checkpoint(const char* file) {
    std::cout << "checkpoint: EOS and rejected replacement load\n" << std::flush;
    Playback player(options()); require(player.load(file), "Cannot load EOS fixture");
    wait_for(player, [&] { return player.status().duration > 2 && player.latest_frame(); }, "EOS fixture never became ready");
    const auto duration = player.status().duration;
    require(player.seek(duration - .15) && player.play(), "Cannot approach EOS");
    wait_for(player, [&] { return player.status().paused && !player.status().playing && player.status().position >= duration; }, "Fixture did not reach EOS");
    require(player.suspend(), "Cannot suspend at EOS");
    const auto saved = player.checkpoint();
    require(saved && saved->ended && !saved->playing && saved->position_source == CheckpointPosition::EndOfFile,
            "EOS marker was not preserved in checkpoint");
    require(!player.load("/tyrell-test-missing/rejected-new-load.mp4"), "Missing replacement load unexpectedly succeeded");
    require(player.suspended() && player.checkpoint() && player.checkpoint()->media.inode == saved->media.inode &&
            std::abs(player.checkpoint()->position - saved->position) < .001 && player.resources().local_handles_released(),
            "Rejected new load discarded or reopened the suspended checkpoint");
    require(player.resume(), "Cannot restore EOS checkpoint after rejected load");
    wait_for(player, [&] { return player.status().paused && !player.status().buffering && player.latest_frame(); }, "Restored EOS did not remain paused");
    require(player.play(), "Cannot replay restored EOS");
    wait_for(player, [&] { const auto frame = player.latest_frame(); return player.status().playing && frame && frame->position < .5; },
             "Play after restored EOS did not restart at the beginning");
}
struct LeaseServer {
    std::string path = "/tmp/tyrell-checkpoint-lease-" + std::to_string(getpid()) + ".sock";
    int listener = -1, peer = -1;
    LeaseServer() {
        unlink(path.c_str()); listener = socket(AF_UNIX, SOCK_STREAM, 0); require(listener >= 0, "Cannot create lease test server");
        sockaddr_un address{}; address.sun_family = AF_UNIX; std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
        require(bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0 && listen(listener, 4) == 0, "Cannot bind lease test server");
    }
    ~LeaseServer() { if(peer >= 0) close(peer); if(listener >= 0) close(listener); unlink(path.c_str()); }
    void accept_client() {
        if(peer >= 0) close(peer);
        pollfd ready{listener, POLLIN, 0}; require(poll(&ready, 1, 1000) == 1, "Missing resumed desktop lease");
        peer = accept(listener, nullptr, nullptr); require(peer >= 0, "Cannot accept lease client");
    }
    void closed() {
        pollfd ready{peer, POLLIN, 0}; require(poll(&ready, 1, 1000) == 1, "Suspension retained its desktop lease connection");
        char byte = 0; require(recv(peer, &byte, 1, MSG_DONTWAIT) == 0, "Suspension did not close desktop lease");
    }
    void reject() {
        const std::string message = "ERROR Controlled test has no HDMI display\n";
        require(send(peer, message.data(), message.size(), MSG_NOSIGNAL) == static_cast<ssize_t>(message.size()), "Cannot reject controlled HDMI lease");
    }
};
void pending_handoff(const char* file) {
    std::cout << "checkpoint: pending output handoff\n" << std::flush;
    LeaseServer server; auto opts = options(); opts.desktop_handoff = true; opts.desktop_handoff_socket = server.path;
    Playback player(opts); require(player.set_output(Output::Hdmi) && player.load(file), "Cannot prepare pending HDMI handoff"); server.accept_client();
    require(player.seek(1.25) && player.pause(), "Cannot queue controls during handoff");
    const auto checkpoint = player.capture_checkpoint();
    require(checkpoint && checkpoint->preferred_output == Output::Hdmi && checkpoint->actual_output == Output::Lcd && !checkpoint->playing &&
            checkpoint->position_source == CheckpointPosition::PendingTarget && std::abs(checkpoint->position - 1.25) < .001,
            "Pending handoff lost requested output/queued controls");
    require(player.resources().desktop_lease_open && player.status().pipeline_builds == 0, "Pending handoff did not own only its lease");
    require(player.suspend(), "Cannot suspend pending output handoff"); server.closed(); check_released(player);
    require(player.resources().external_cleanup_unconfirmed, "Local lease close was misrepresented as external cleanup completion");
    require(player.resume(), "Cannot resume pending output preference"); server.accept_client();
    require(player.set_output(Output::Lcd), "Cannot cancel resumed handoff to LCD"); server.closed(); settled_paused(player, 1.25);
}
void eos_seek_during_resumed_handoff(const char* file) {
    std::cout << "checkpoint: EOS seek during resumed output handoff\n" << std::flush;
    LeaseServer server; auto opts = options(); opts.desktop_handoff = true; opts.desktop_handoff_socket = server.path;
    Playback player(opts); require(player.set_output(Output::Hdmi) && player.load(file), "Cannot begin EOS output-preference fixture");
    server.accept_client(); server.reject();
    wait_for(player, [&] { return player.status().duration > 2 && player.latest_frame() && player.status().playing; },
             "Controlled HDMI failure did not fall back to LCD");
    server.closed(); const auto duration = player.status().duration;
    require(player.seek(duration - .15) && player.play(), "Cannot approach preferred-HDMI EOS");
    wait_for(player, [&] { return player.status().paused && !player.status().playing && player.status().position >= duration; }, "Preferred-HDMI fixture did not reach EOS");
    require(player.suspend() && player.checkpoint() && player.checkpoint()->ended && player.checkpoint()->preferred_output == Output::Hdmi,
            "EOS checkpoint lost HDMI output preference");
    require(player.resume(), "Cannot resume EOF into pending handoff"); server.accept_client();
    require(player.seek(1.25) && player.play() && player.set_output(Output::Lcd), "Cannot replace ended intent while handoff is pending");
    server.closed();
    // Resumed HDMI failure/cancellation deliberately pauses the LCD fallback.
    // An explicit play after that safe fallback must honor the new seek.
    settled_paused(player, 1.25);
    require(player.play(), "Cannot play after conservative LCD fallback");
    wait_for(player, [&] {
        const auto frame = player.latest_frame();
        return player.status().playing && !player.status().buffering && frame && std::abs(frame->position - 1.25) <= .25;
    }, "A seek during resumed HDMI preparation retained EOS or rewound to zero");
}
} // namespace

int main(int argc, char** argv) {
    try {
        require(argc == 3, "Pass media-clock.mp4 and media-multitrack.mkv");
        captions_and_settings(argv[1]); fresh_and_pending_positions(argv[1]); embedded_tracks(argv[2]);
        changed_files_are_rejected(argv[1]); eos_checkpoint(argv[1]); pending_handoff(argv[1]); eos_seek_during_resumed_handoff(argv[1]);
        std::cout << "PASS: real fake-audio LCD checkpoints, file identity, fresh/queued positions, selected captions/tracks, settings and local lease release\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "Checkpoint playback test: " << error.what() << '\n'; return 1;
    }
}
