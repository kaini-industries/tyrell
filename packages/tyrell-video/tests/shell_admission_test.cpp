// Exercise admission through the real GUI entry before any device or pipeline.
#define main video_application_main
#include "../src/main.cpp"
#undef main

#include <cstdlib>
#include <gst/gst.h>
#include <sys/socket.h>

namespace {
void require(bool value, const char* message) {
    if(!value) throw std::runtime_error(message);
}
struct Pair {
    int peer = -1, app = -1;
    Pair() {
        int descriptors[2];
        require(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, descriptors) == 0, "shell socket pair");
        peer = descriptors[0]; app = descriptors[1];
        require(setenv("GH0ST_SHELL_FD", std::to_string(app).c_str(), 1) == 0, "shell environment");
    }
    ~Pair() { if(peer >= 0) close(peer); if(app >= 0) close(app); unsetenv("GH0ST_SHELL_FD"); }
};
Options inaccessible_devices() {
    Options options;
    options.input = "/nonexistent-tyrell-test/keyboard";
    options.framebuffer = "/nonexistent-tyrell-test/framebuffer";
    options.media_root = "/nonexistent-tyrell-test/media";
    options.file = "/nonexistent-tyrell-test/movie.mp4";
    options.output = "hdmi";
    return options;
}
void rejects_before_resources(bool closed_peer) {
    require(!gst_is_initialized(), "GStreamer has not started before admission");
    Pair pair;
    if(closed_peer) { close(pair.peer); pair.peer = -1; }
    const auto adopted = pair.app;
    require(gui(inaccessible_devices()) == 2, "valid shell launch refused before opening devices or media");
    require(!gst_is_initialized(), "refused launch never initializes GStreamer");
    require(getenv("GH0ST_SHELL_FD") == nullptr, "adopted environment consumed");
    require(fcntl(adopted, F_GETFD) < 0 && errno == EBADF, "adopted link closed");
    pair.app = -1;
    if(pair.peer >= 0) {
        char packet[64];
        const auto count = recv(pair.peer, packet, sizeof(packet), MSG_DONTWAIT);
        require(count == 7 && std::string(packet, 7) == "hello\t1", "transport handshake only");
        require(recv(pair.peer, packet, sizeof(packet), MSG_DONTWAIT) == 0, "no paused acknowledgment or retained endpoint");
    }
}
void preserves_m5_and_invalid_descriptor() {
    char path[] = "/tmp/tyrell-video-admission-XXXXXX";
    const auto fd = mkstemp(path);
    require(fd >= 3, "regular file fixture"); unlink(path);
    const auto descriptor_flags = fcntl(fd, F_GETFD), status_flags = fcntl(fd, F_GETFL);
    for(const auto& value : {std::string{}, std::string("malformed"), std::to_string(fd)}) {
        if(value.empty()) unsetenv("GH0ST_SHELL_FD");
        else setenv("GH0ST_SHELL_FD", value.c_str(), 1);
        bool m5_path = false;
        try { (void)gui(inaccessible_devices()); }
        catch(const std::runtime_error& error) { m5_path = std::string(error.what()).find("Cannot open Cardputer keyboard") != std::string::npos; }
        require(m5_path, "absent or invalid link retains normal M5 path");
        require(fcntl(fd, F_GETFD) == descriptor_flags && fcntl(fd, F_GETFL) == status_flags, "unrelated descriptor untouched");
    }
    close(fd);
}
void desktop_tools_do_not_adopt() {
    for(const bool simulator : {false, true}) {
        Pair pair;
        auto options = inaccessible_devices();
        options.simulator = simulator;
        if(!simulator) options.screenshot = "/unused-preview.ppm";
        require(shell_admission(options) == 0, "desktop and headless admission remains independent");
        require(getenv("GH0ST_SHELL_FD") != nullptr && fcntl(pair.app, F_GETFD) >= 0, "desktop tool retains caller-owned descriptor");
    }
}
} // namespace
int main() {
    try {
        rejects_before_resources(false);
        rejects_before_resources(true);
        preserves_m5_and_invalid_descriptor();
        desktop_tools_do_not_adopt();
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "shell admission test: " << error.what() << '\n';
        return 1;
    }
}
