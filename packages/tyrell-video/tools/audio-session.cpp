#include "audio_session_policy.h"

#include <pulse/pulseaudio.h>

#include <algorithm>
#include <chrono>
#include <csignal>
#include <cstring>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <cerrno>
#include <sys/socket.h>
#include <unistd.h>

namespace {
using Clock = std::chrono::steady_clock;
volatile std::sig_atomic_t stopping = 0;
void stop(int) { stopping = 1; }

bool peer_gone() {
    char byte;
    const auto count = recv(STDIN_FILENO, &byte, 1, MSG_PEEK | MSG_DONTWAIT);
    // The lease has no client commands. Treat unexpected bytes as revocation
    // too, rather than leaving buffered input that can hide a later EOF.
    return count >= 0 || (errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK);
}

std::string hdmi_device() {
    std::error_code error;
    const std::filesystem::path root("/proc/asound");
    for(std::filesystem::directory_iterator it(root, error), end; !error && it != end; it.increment(error)) {
        const auto filename = it->path().filename().string();
        if(filename.rfind("card", 0) != 0 || filename.size() <= 4 ||
           !std::all_of(filename.begin() + 4, filename.end(), [](unsigned char c) { return std::isdigit(c); })) continue;
        std::ifstream input(it->path() / "id");
        std::string id;
        if(!(input >> id) || id.rfind("vc4hdmi", 0) != 0 ||
           !std::all_of(id.begin(), id.end(), [](unsigned char c) { return std::isalnum(c) || c == '_'; })) continue;
        return "hdmi:CARD=" + id + ",DEV=0";
    }
    throw std::runtime_error("No VC4 HDMI audio device is available");
}

struct Module { unsigned index; std::string name, arguments; };
class Pulse {
public:
    explicit Pulse(bool watch_peer) : watch_peer_(watch_peer) {
        loop_ = pa_mainloop_new();
        if(!loop_) throw std::runtime_error("Cannot allocate HDMI audio connection");
        context_ = pa_context_new(pa_mainloop_get_api(loop_), "Tyrell Video HDMI audio lease");
        if(!context_) { pa_mainloop_free(loop_); loop_ = nullptr; throw std::runtime_error("Cannot allocate HDMI audio context"); }
        if(pa_context_connect(context_, nullptr, PA_CONTEXT_NOAUTOSPAWN, nullptr) < 0 ||
           !until([&] { return pa_context_get_state(context_) == PA_CONTEXT_READY; }, 1500)) {
            const std::string message = pa_strerror(pa_context_errno(context_));
            pa_context_disconnect(context_); pa_context_unref(context_); pa_mainloop_free(loop_);
            context_ = nullptr; loop_ = nullptr;
            throw std::runtime_error("Audio session unavailable: " + message);
        }
    }
    ~Pulse() {
        if(context_) { pa_context_disconnect(context_); pa_context_unref(context_); }
        if(loop_) pa_mainloop_free(loop_);
    }
    bool until(const std::function<bool()>& done, int milliseconds) {
        const auto deadline = Clock::now() + std::chrono::milliseconds(milliseconds);
        while(!done()) {
            pa_mainloop_iterate(loop_, 0, nullptr);
            if(!PA_CONTEXT_IS_GOOD(pa_context_get_state(context_)) || Clock::now() >= deadline ||
               (watch_peer_ && (stopping || peer_gone()))) return false;
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
        }
        return true;
    }
    bool operation(pa_operation* operation, const std::function<bool()>& done, int milliseconds = 1000) {
        if(!operation) return false;
        const bool success = until([&] { return done() || pa_operation_get_state(operation) != PA_OPERATION_RUNNING; }, milliseconds) && done();
        if(!success) pa_operation_cancel(operation);
        pa_operation_unref(operation);
        return success;
    }
    void create(const std::string& token) {
        bool done = false;
        unsigned index = PA_INVALID_INDEX;
        struct Query { bool* done; unsigned* index; } query{&done, &index};
        const auto arguments = "device=" + hdmi_device() + " sink_name=" + token +
            " namereg_fail=true channels=2 channel_map=front-left,front-right rate=48000 format=s32le mmap=false"
            " sink_properties=\"device.description='Tyrell HDMI' priority.session=-1000 priority.driver=-1000 node.autoconnect=false\"";
        auto* request = pa_context_load_module(context_, "module-alsa-sink", arguments.c_str(),
            [](pa_context*, unsigned result, void* data) {
                auto& query = *static_cast<Query*>(data); *query.index = result; *query.done = true;
            }, &query);
        if(!operation(request, [&] { return done; }, 2000) || index == PA_INVALID_INDEX)
            throw std::runtime_error("HDMI audio output could not open; the TV may be disconnected or the device busy");
        // PipeWire may acknowledge module creation just before publishing the
        // Pulse sink. Never give GStreamer a name that is not yet selectable.
        const auto deadline = Clock::now() + std::chrono::milliseconds(1000);
        while(Clock::now() < deadline) {
            bool finished = false, found = false;
            struct SinkQuery { bool* done; bool* found; } sink{&finished, &found};
            request = pa_context_get_sink_info_by_name(context_, token.c_str(),
                [](pa_context*, const pa_sink_info* info, int end, void* data) {
                    auto& query = *static_cast<SinkQuery*>(data);
                    if(info) *query.found = true;
                    if(end) *query.done = true;
                }, &sink);
            if(operation(request, [&] { return finished; }, 300) && found) return;
            if(stopping || peer_gone()) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
        throw std::runtime_error("HDMI audio sink did not become available");
    }
    std::vector<Module> modules() {
        bool done = false;
        std::vector<Module> result;
        struct Query { bool* done; std::vector<Module>* modules; } query{&done, &result};
        auto* request = pa_context_get_module_info_list(context_,
            [](pa_context*, const pa_module_info* info, int end, void* data) {
                auto& query = *static_cast<Query*>(data);
                if(info) query.modules->push_back({info->index, info->name ? info->name : "", info->argument ? info->argument : ""});
                if(end) *query.done = true;
            }, &query);
        if(!operation(request, [&] { return done; })) throw std::runtime_error("Cannot inspect temporary HDMI audio modules");
        return result;
    }
    void cleanup(const std::string& token) {
        watch_peer_ = false;
        for(const auto& module : modules()) {
            if(!tyrell::video::owned_audio_module(module.name, module.arguments, token)) continue;
            bool done = false, success = false;
            struct Query { bool* done; bool* success; } query{&done, &success};
            auto* request = pa_context_unload_module(context_, module.index,
                [](pa_context*, int success, void* data) {
                    auto& query = *static_cast<Query*>(data); *query.success = success; *query.done = true;
                }, &query);
            if(!operation(request, [&] { return done; }) || !success)
                throw std::runtime_error("Temporary HDMI audio cleanup did not complete");
        }
    }
    void serve() {
        while(!stopping && !peer_gone() && PA_CONTEXT_IS_GOOD(pa_context_get_state(context_))) {
            pa_mainloop_iterate(loop_, 0, nullptr);
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
    }
private:
    pa_mainloop* loop_ = nullptr;
    pa_context* context_ = nullptr;
    bool watch_peer_ = false;
};

std::string single_line(std::string message) {
    for(auto& c : message) if(c == '\n' || c == '\r') c = ' ';
    if(message.size() > 240) message.resize(240);
    return message;
}
} // namespace

int main(int argc, char** argv) {
    std::signal(SIGTERM, stop); std::signal(SIGINT, stop); std::signal(SIGPIPE, SIG_IGN);
    const bool lease = argc == 3 && std::string(argv[1]) == "--lease";
    const bool cleanup = argc == 3 && std::string(argv[1]) == "--cleanup";
    if((!lease && !cleanup) || !tyrell::video::valid_audio_lease_token(argv[2], getuid())) {
        std::cerr << "Invalid HDMI audio lease arguments\n";
        return 2;
    }
    const std::string token = argv[2];
    if(cleanup) {
        // The transient unit/systemd-run can retain other copies of this
        // descriptor. Explicit shutdown notifies the app before an abnormal
        // helper's cleanup waits for the audio server.
        shutdown(STDIN_FILENO, SHUT_RDWR);
    }
    try {
        Pulse pulse(lease);
        if(cleanup) {
            // A main helper killed while module loading may not receive its
            // load callback. Scan by the unique intended sink name after its
            // Pulse connection has gone away; no module ID can be confused
            // with another session or a pre-existing HDMI sink.
            const char* result = std::getenv("SERVICE_RESULT");
            const int passes = result && std::string(result) == "success" ? 1 : 20;
            for(int pass = 0; pass < passes; ++pass) {
                pulse.cleanup(token);
                if(pass + 1 != passes) std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
            return 0;
        }
        try {
            pulse.create(token);
            std::cout << "READY " << token << '\n' << std::flush;
            pulse.serve();
            pulse.cleanup(token);
        } catch(...) {
            try { pulse.cleanup(token); } catch(const std::exception& error) { std::cerr << single_line(error.what()) << '\n'; }
            throw;
        }
    } catch(const std::exception& error) {
        if(lease) std::cout << "ERROR " << single_line(error.what()) << '\n' << std::flush;
        std::cerr << "Tyrell HDMI audio: " << single_line(error.what()) << '\n';
        // Do not claim cleanup succeeded if the server was unreachable or
        // rejected an unload. The transient unit journal retains that failure.
        return 1;
    }
    return 0;
}
