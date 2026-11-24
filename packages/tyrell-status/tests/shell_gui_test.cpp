#define TYRELL_STATUS_NO_MAIN
#include "../src/main.cpp"

#include <functional>
#include <sys/socket.h>

void check(bool value, const char* description) {
    if(!value) { std::cerr << "shell GUI contract failed: " << description << '\n'; std::exit(1); }
}
void packet(int fd, const std::string& value) {
    check(send(fd, value.data(), value.size(), MSG_DONTWAIT | MSG_NOSIGNAL) == static_cast<ssize_t>(value.size()), "send test packet");
}
bool received(int fd, const std::string& wanted) {
    bool found = false;
    char buffer[512];
    for(unsigned n = 0; n < 16; ++n) {
        const auto count = recv(fd, buffer, sizeof(buffer), MSG_DONTWAIT);
        if(count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return found;
        check(count > 0, "receive test packet");
        const std::string message(buffer, static_cast<std::size_t>(count));
        check(message == "hello\t1" || message == wanted, "unexpected app packet");
        if(message == wanted) found = true;
    }
    check(false, "bounded app packet queue");
    return false;
}
struct FakeDevices : GuiDevices {
    int peer = -1;
    unsigned acquires = 0, releases = 0, creates = 0, reads = 0, flushes = 0, idle_count = 0;
    bool input = false, fail_acquire = false, fail_read = false;
    std::string input_path, framebuffer_path;
    std::vector<std::uint32_t> pixels;
    std::function<void(unsigned)> script;
    ~FakeDevices() override { if(peer >= 0) close(peer); }
    bool acquire_input(const std::string& path) override {
        ++acquires; input_path = path; input = !fail_acquire; return input;
    }
    void release_input() override { if(input) ++releases; input = false; }
    bool process_input(Ui&) override {
        check(input, "input read while released"); ++reads; return !fail_read;
    }
    lv_display_t* create_display(const std::string& path) override {
        check(input, "display initialized before input acquisition");
        ++creates; framebuffer_path = path; pixels.resize(width * height);
        auto* display = lv_display_create(width, height);
        lv_display_set_color_format(display, LV_COLOR_FORMAT_XRGB8888);
        lv_display_set_buffers(display, pixels.data(), nullptr, pixels.size() * sizeof(pixels[0]), LV_DISPLAY_RENDER_MODE_DIRECT);
        lv_display_set_user_data(display, this);
        lv_display_set_flush_cb(display, [](lv_display_t* d, const lv_area_t* area, std::uint8_t*) {
            auto& self = *static_cast<FakeDevices*>(lv_display_get_user_data(d));
            check(self.input, "framebuffer write while hidden or ownership lost");
            check(area->x1 == 0 && area->y1 == 0 && area->x2 == width - 1 && area->y2 == height - 1, "complete frame on startup/resume/redraw");
            ++self.flushes;
            lv_display_flush_ready(d);
        });
        return display;
    }
    void wait(const tyrell::status::WaitRequest& request) override {
        check(idle_count < 50, "bounded test completion");
        if(!input) check(request.input_fd < 0, "hidden wait omits released input");
        if(script) script(idle_count++);
    }
    void start(bool hello = true) {
        int pair[2];
        check(socketpair(AF_UNIX, SOCK_SEQPACKET, 0, pair) == 0, "socketpair");
        peer = pair[0];
        check(setenv("GH0ST_SHELL_FD", std::to_string(pair[1]).c_str(), 1) == 0, "set link environment");
        if(hello) packet(peer, "hello\t1");
        running = 1;
    }
    void disconnect() { close(peer); peer = -1; }
};

void cycles_and_hidden_loss() {
    FakeDevices d;
    d.start();
    unsigned reads = 0, writes = 0;
    d.script = [&](unsigned step) {
        if(step == 0) {
            check(d.flushes == 1 && d.acquires == 1 && d.creates == 1, "initial complete frame");
            reads = d.reads; writes = d.flushes; packet(d.peer, "pause");
        } else if(step == 1) {
            check(received(d.peer, "paused") && !d.input && d.releases == 1, "release before paused acknowledgment");
            check(d.reads == reads && d.flushes == writes, "pause prevents next read/write");
            packet(d.peer, "pause"); packet(d.peer, "redraw");
        } else if(step == 2) {
            check(received(d.peer, "paused") && d.releases == 1, "duplicate pause is idempotent");
            check(d.reads == reads && d.flushes == writes, "hidden redraw remains deferred");
            packet(d.peer, "resume");
        } else if(step == 3) {
            check(d.acquires == 2 && d.input && d.flushes == writes + 1, "resume reacquires and redraws complete frame");
            reads = d.reads; writes = d.flushes; packet(d.peer, "resume"); packet(d.peer, "pause");
        } else if(step == 4) {
            check(received(d.peer, "paused") && d.acquires == 2, "out-of-order resume does not reopen input");
            check(d.reads == reads && d.flushes == writes, "second pause prevents work");
            d.disconnect();
        } else check(false, "hidden EOF exits next iteration");
    };
    check(gui(Options{}, &d) == 0, "GUI hidden EOF exit");
    check(d.idle_count == 5 && d.reads == reads && d.flushes == writes && !d.input, "hidden EOF cleanup never redraws");
}

void startup_pause_and_loss() {
    FakeDevices d;
    d.start(); packet(d.peer, "pause");
    d.script = [&](unsigned step) {
        check(step == 0 && d.creates == 0 && d.acquires == 0 && d.flushes == 0, "queued pause before device open/unblank");
        check(received(d.peer, "paused"), "startup pause acknowledged"); d.disconnect();
    };
    check(gui(Options{}, &d) == 0 && d.creates == 0 && d.acquires == 0, "startup hidden EOF touches no devices");
}

void flood_defers_gui() {
    FakeDevices d;
    d.start();
    for(unsigned i = 0; i < 40; ++i) packet(d.peer, "future-verb");
    packet(d.peer, "pause");
    d.script = [&](unsigned step) {
        check(d.creates == 0 && d.acquires == 0, "bounded packet drain defers device work");
        check(step == 0 && received(d.peer, "paused"), "pause behind unknown packet burst"); d.disconnect();
    };
    check(gui(Options{}, &d) == 0 && d.idle_count == 1, "drain bursts without sleeping or opening display");
}

void resume_input_failure() {
    FakeDevices d;
    d.start();
    unsigned writes = 0;
    d.script = [&](unsigned step) {
        if(step == 0) { writes = d.flushes; packet(d.peer, "pause"); }
        else if(step == 1) {
            check(received(d.peer, "paused"), "pause before failed resume");
            d.fail_acquire = true; packet(d.peer, "resume");
        } else check(false, "failed resume exits");
    };
    check(gui(Options{}, &d) == 0 && d.acquires == 2 && d.flushes == writes && !d.input, "failed reacquisition does not draw");
}

void foreground_loss() {
    FakeDevices d;
    d.start();
    unsigned writes = 0, reads = 0;
    d.script = [&](unsigned step) {
        check(step == 0, "foreground EOF exits next iteration");
        writes = d.flushes; reads = d.reads; d.disconnect();
    };
    check(gui(Options{}, &d) == 0 && d.flushes == writes && d.reads == reads && !d.input, "foreground link loss relinquishes ownership");
}

void hidden_signal_shutdown() {
    FakeDevices d;
    d.start();
    unsigned writes = 0, reads = 0;
    d.script = [&](unsigned step) {
        if(step == 0) { writes = d.flushes; reads = d.reads; packet(d.peer, "pause"); }
        else if(step == 1) {
            check(received(d.peer, "paused") && !d.input, "pause before shutdown");
            std::raise(SIGTERM);
        } else check(false, "hidden signal exits");
    };
    check(gui(Options{}, &d) == 0 && d.flushes == writes && d.reads == reads && !d.input, "hidden signal cleanup never redraws");
}

void input_loss_stops_rendering() {
    FakeDevices d;
    d.start();
    unsigned writes = 0;
    d.script = [&](unsigned step) {
        check(step == 0, "input loss exits next iteration");
        writes = d.flushes; d.fail_read = true;
    };
    check(gui(Options{}, &d) == 0 && d.flushes == writes && !d.input, "input loss stops before timer/render work");
}

void invalid_link_preserves_m5() {
    FakeDevices d;
    running = 1;
    setenv("GH0ST_SHELL_FD", "invalid", 1);
    setenv("LV_LINUX_FBDEV_DEVICE", "/unrelated-shell/fb", 1);
    setenv("LV_LINUX_KEYBOARD_DEVICE", "/unrelated-shell/input", 1);
    d.script = [&](unsigned step) { check(step == 0, "ordinary M5 loop"); running = 0; };
    check(gui(Options{}, &d) == 0 && d.acquires == 1 && d.creates == 1 && d.reads == 1, "invalid link uses ordinary M5 path");
    check(d.input_path.empty() && d.framebuffer_path == "/dev/fb_lcd", "invalid link ignores shell device variables");
    unsetenv("LV_LINUX_FBDEV_DEVICE"); unsetenv("LV_LINUX_KEYBOARD_DEVICE");
}

void linked_paths_and_startup_resume() {
    FakeDevices d;
    d.start(); packet(d.peer, "pause");
    setenv("LV_LINUX_FBDEV_DEVICE", "/shell/fb", 1);
    setenv("LV_LINUX_KEYBOARD_DEVICE", "/shell/input", 1);
    d.script = [&](unsigned step) {
        if(step == 0) {
            check(d.creates == 0 && received(d.peer, "paused"), "startup pause deferred initialization"); packet(d.peer, "resume");
        } else if(step == 1) {
            check(d.input_path == "/shell/input" && d.framebuffer_path == "/shell/fb", "valid link uses shell device paths");
            check(d.flushes == 1 && d.acquires == 1, "startup resume initializes once"); d.disconnect();
        } else check(false, "linked startup completed");
    };
    check(gui(Options{}, &d) == 0, "startup resume exit");
    unsetenv("LV_LINUX_FBDEV_DEVICE"); unsetenv("LV_LINUX_KEYBOARD_DEVICE");
}

void explicit_paths_take_priority() {
    FakeDevices d;
    d.start();
    setenv("LV_LINUX_FBDEV_DEVICE", "/shell/fb", 1);
    setenv("LV_LINUX_KEYBOARD_DEVICE", "/shell/input", 1);
    Options options;
    options.framebuffer = "/explicit/fb"; options.framebuffer_explicit = true;
    options.input = "/explicit/input"; options.input_explicit = true;
    d.script = [&](unsigned step) {
        check(step == 0 && d.input_path == options.input && d.framebuffer_path == options.framebuffer, "CLI paths take priority over shell environment");
        d.disconnect();
    };
    check(gui(options, &d) == 0, "explicit linked paths exit");
    unsetenv("LV_LINUX_FBDEV_DEVICE"); unsetenv("LV_LINUX_KEYBOARD_DEVICE");
}

void handshake_timeout_and_closed_peer() {
    FakeDevices d;
    d.start(false);
    d.script = [&](unsigned) { check(d.creates == 0 && d.acquires == 0, "hello timeout no device work"); std::this_thread::sleep_for(std::chrono::milliseconds(10)); };
    check(gui(Options{}, &d) == 1 && d.creates == 0 && d.acquires == 0, "missing hello exits safely");
    FakeDevices closed;
    closed.start(); closed.disconnect();
    check(gui(Options{}, &closed) == 0 && closed.creates == 0 && closed.acquires == 0, "accepted closed peer never falls back M5");
}

int main() {
    cycles_and_hidden_loss();
    startup_pause_and_loss();
    flood_defers_gui();
    resume_input_failure();
    foreground_loss();
    hidden_signal_shutdown();
    input_loss_stops_rendering();
    invalid_link_preserves_m5();
    linked_paths_and_startup_resume();
    explicit_paths_take_priority();
    handshake_timeout_and_closed_peer();
}
