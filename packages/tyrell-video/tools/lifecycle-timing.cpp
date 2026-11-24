// Uninstalled lifecycle timing spike. Fake sinks and a controlled in-process
// streaming delay establish API behavior, never physical device acceptance.
#include <gst/gst.h>
#ifndef TYRELL_TIMING_WITH_CHECKPOINTS
#define TYRELL_TIMING_WITH_CHECKPOINTS 1
#endif
#if TYRELL_TIMING_WITH_CHECKPOINTS
#include "playback.h"
#include "json_string.h"
#endif

#include <chrono>
#include <condition_variable>
#include <cmath>
#include <fstream>
#include <functional>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using Clock = std::chrono::steady_clock;
#if TYRELL_TIMING_WITH_CHECKPOINTS
using namespace tyrell::video;
#endif
namespace {
void require(bool condition, const std::string& message) {
    if(!condition) throw std::runtime_error(message);
}
struct HeldBuffer {
    std::mutex mutex;
    std::condition_variable condition;
    bool entered = false, released = false;
    void release() {
        std::lock_guard<std::mutex> lock(mutex);
        released = true;
        condition.notify_all();
    }
    static GstPadProbeReturn hold(GstPad*, GstPadProbeInfo* info, gpointer data) {
        if(!(GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER)) return GST_PAD_PROBE_OK;
        auto& state = *static_cast<HeldBuffer*>(data);
        std::unique_lock<std::mutex> lock(state.mutex);
        state.entered = true;
        state.condition.notify_all();
        state.condition.wait(lock, [&] { return state.released; });
        return GST_PAD_PROBE_OK;
    }
};
struct Pipeline {
    GstElement* value = nullptr;
    HeldBuffer* held = nullptr;
    ~Pipeline() {
        if(held) held->release();
        if(value) {
            gst_element_set_state(value, GST_STATE_NULL);
            gst_object_unref(value);
        }
    }
};
double blocked_null(unsigned delay_ms) {
    GError* error = nullptr;
    HeldBuffer state;
    Pipeline pipeline;
    pipeline.held = &state;
    pipeline.value = gst_parse_launch("videotestsrc is-live=true ! identity name=hold ! fakesink sync=false async=false", &error);
    const std::string failure = error ? error->message : "Cannot construct controlled pipeline";
    if(error) g_error_free(error);
    require(pipeline.value != nullptr, failure);
    auto* identity = gst_bin_get_by_name(GST_BIN(pipeline.value), "hold");
    require(identity != nullptr, "Missing controlled identity element");
    auto* pad = gst_element_get_static_pad(identity, "src");
    require(pad != nullptr, "Missing controlled identity pad");
    const auto probe = gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_BUFFER, HeldBuffer::hold, &state, nullptr);
    require(probe != 0, "Cannot install streaming delay");
    require(gst_element_set_state(pipeline.value, GST_STATE_PLAYING) != GST_STATE_CHANGE_FAILURE,
            "Cannot start controlled pipeline");
    {
        std::unique_lock<std::mutex> lock(state.mutex);
        if(!state.condition.wait_for(lock, std::chrono::seconds(2), [&] { return state.entered; })) {
            state.released = true;
            state.condition.notify_all();
            throw std::runtime_error("Controlled streaming callback did not start");
        }
    }
    // This is a measurement fixture inside this process, not a cleanup worker
    // or a proposal to place helpers outside Gh0st's stopped process group.
    std::thread release([&] {
        std::this_thread::sleep_for(std::chrono::milliseconds(delay_ms));
        state.release();
    });
    const auto started = Clock::now();
    const auto result = gst_element_set_state(pipeline.value, GST_STATE_NULL);
    const double elapsed = std::chrono::duration<double, std::milli>(Clock::now() - started).count();
    release.join();
    gst_pad_remove_probe(pad, probe);
    gst_object_unref(pad);
    gst_object_unref(identity);
    require(result != GST_STATE_CHANGE_FAILURE, "Controlled pipeline failed its NULL transition");
    require(elapsed >= delay_ms * .8, "NULL transition did not wait for its in-flight streaming callback");
    return elapsed;
}
#if TYRELL_TIMING_WITH_CHECKPOINTS
struct Sample {
    double capture_ms = 0, suspend_ms = 0, resume_call_ms = 0, restore_ready_ms = 0;
    double saved_position = 0, restored_position = 0;
    bool playing = false, local_released = false, external_cleanup_unconfirmed = false;
};
void ready(Playback& player, const std::function<bool()>& predicate, const std::string& message) {
    const auto deadline = Clock::now() + std::chrono::seconds(12);
    while(Clock::now() < deadline) {
        player.poll();
        require(player.status().error.empty(), message + ": " + player.status().error);
        if(predicate()) return;
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    throw std::runtime_error(message);
}
double elapsed_ms(Clock::time_point start) {
    return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}
Sample playback_sample(const std::string& fixture, const std::string& scenario) {
    PlaybackOptions options;
    options.fake_audio = true; options.desktop_handoff = false; options.automatic_hdmi_audio = false;
    Playback player(options);
    require(player.load(fixture), "Cannot load lifecycle timing fixture");
    if(scenario != "immediate-preroll") {
        ready(player, [&] { return player.status().playing && player.latest_frame() && player.status().position >= .2; },
              "Lifecycle timing fixture never played");
        if(scenario == "user-paused") {
            require(player.pause(), "Cannot prepare user-paused timing case");
            std::this_thread::sleep_for(std::chrono::milliseconds(40)); player.poll();
        } else if(scenario == "queued-seek") {
            require(player.pause() && player.seek(1), "Cannot prepare queued-seek timing case");
        }
    }
    Sample result;
    auto started = Clock::now(); const auto capture = player.capture_checkpoint(); result.capture_ms = elapsed_ms(started);
    require(capture.has_value(), "Lifecycle timing capture failed");
    started = Clock::now(); require(player.suspend(), "Lifecycle timing suspend failed"); result.suspend_ms = elapsed_ms(started);
    const auto checkpoint = player.checkpoint(); require(checkpoint.has_value(), "Lifecycle timing lost checkpoint");
    result.saved_position = checkpoint->position; result.playing = checkpoint->playing;
    result.local_released = player.resources().local_handles_released();
    result.external_cleanup_unconfirmed = player.resources().external_cleanup_unconfirmed;
    require(result.local_released && !player.latest_frame(), "Lifecycle timing suspend retained local resources");
    started = Clock::now(); require(player.resume(), "Lifecycle timing resume was rejected"); result.resume_call_ms = elapsed_ms(started);
    ready(player, [&] {
        const auto state = player.status(); const auto frame = player.latest_frame();
        return !state.buffering && (result.playing ? state.playing : state.paused) && frame &&
            std::abs(frame->position - result.saved_position) <= (result.playing ? .25 : .05);
    }, "Lifecycle timing restore did not honor checkpoint position/intent");
    result.restore_ready_ms = elapsed_ms(started); result.restored_position = player.latest_frame()->position;
    require(player.status().video_decode_fallbacks == checkpoint->video_decode_fallbacks,
            "Lifecycle timing reset the accepted load's decoder budget");
    return result;
}
std::string emit_sample(const Sample& sample) {
    return "{\"capture_ms\":" + std::to_string(sample.capture_ms) + ",\"suspend_ms\":" + std::to_string(sample.suspend_ms) +
        ",\"resume_call_ms\":" + std::to_string(sample.resume_call_ms) + ",\"restore_ready_ms\":" + std::to_string(sample.restore_ready_ms) +
        ",\"saved_position\":" + std::to_string(sample.saved_position) + ",\"restored_position\":" + std::to_string(sample.restored_position) +
        ",\"playing\":" + (sample.playing ? "true" : "false") + ",\"local_handles_released\":" + (sample.local_released ? "true" : "false") +
        ",\"external_cleanup_unconfirmed\":" + (sample.external_cleanup_unconfirmed ? "true" : "false") + "}";
}
#endif
} // namespace

int main(int argc, char** argv) {
    try {
        unsigned delay_ms = 250;
        unsigned samples = 5;
        std::string report, fixture;
        for(int i = 1; i < argc; ++i) {
            const std::string argument = argv[i];
            require(i + 1 < argc, "Expected a value after " + argument);
            const std::string value = argv[++i];
            if(argument == "--delay-ms") {
                std::size_t consumed = 0;
                const auto parsed = std::stoul(value, &consumed);
                require(consumed == value.size() && parsed >= 150 && parsed <= 1000,
                        "--delay-ms must be 150..1000");
                delay_ms = static_cast<unsigned>(parsed);
            } else if(argument == "--samples") {
                std::size_t consumed = 0; const auto parsed = std::stoul(value, &consumed);
                require(consumed == value.size() && parsed >= 1 && parsed <= 20, "--samples must be 1..20");
                samples = static_cast<unsigned>(parsed);
            } else if(argument == "--fixture") fixture = value;
            else if(argument == "--report") report = value;
            else throw std::runtime_error("Unknown argument: " + argument);
        }
        GError* error = nullptr;
        const bool initialized = gst_init_check(nullptr, nullptr, &error);
        const std::string failure = error ? error->message : "Cannot initialize GStreamer";
        if(error) g_error_free(error);
        require(initialized, failure);
        const auto elapsed = blocked_null(delay_ms);
        require(elapsed > 100, "Controlled teardown unexpectedly fit the shell pause window");
        std::string json = "{\"schema\":1,\"scope\":\"controlled-gstreamer-teardown-spike\","
            "\"status\":\"passed\",\"hardware_qualified\":false,\"fake_sink\":true,"
            "\"delay_ms\":" + std::to_string(delay_ms) + ",\"null_transition_ms\":" + std::to_string(elapsed) +
            ",\"within_shell_100ms\":false,\"active_shell_qualified\":false,"
            "\"finding\":\"GST_STATE_NULL waits for an in-flight streaming callback; no universal 100 ms bound\",\"playback_cases\":[";
        bool first = true;
#if TYRELL_TIMING_WITH_CHECKPOINTS
        if(!fixture.empty()) {
            for(const auto* scenario : {"playing", "user-paused", "immediate-preroll", "queued-seek"}) {
                if(!first) json += ',';
                first = false;
                json += "{\"scenario\":" + json_string(scenario) + ",\"output\":\"LCD\",\"fake_audio\":true,\"samples\":[";
                for(unsigned sample = 0; sample < samples; ++sample) {
                    if(sample) json += ',';
                    json += emit_sample(playback_sample(fixture, scenario));
                }
                json += "]}";
            }
        }
#else
        (void)samples; (void)first;
        require(fixture.empty(), "This early-spike build excludes checkpoint measurements");
#endif
        json += "],\"not_tested\":[\"physical HDMI/DRM/VT\",\"PulseAudio service cleanup completion\",\"device pause deadline\"]}\n";
        if(!report.empty()) {
            std::ofstream output(report);
            require(static_cast<bool>(output << json), "Cannot write lifecycle timing report");
        }
        std::cout << json;
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "Lifecycle timing spike failed: " << error.what() << '\n';
        return 1;
    }
}
