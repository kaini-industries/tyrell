#define TYRELL_STATUS_NO_MAIN
#include "../src/main.cpp"

#include <atomic>
#include <functional>
#include <pthread.h>
#include <sys/socket.h>

using namespace std::chrono;
namespace {
void require(bool value, const char* message) {
    if(!value) { std::cerr << "idle GUI contract failed: " << message << '\n'; std::exit(1); }
}
template<class Predicate> void eventually(Predicate predicate, milliseconds limit, const char* message) {
    const auto end = steady_clock::now() + limit;
    while(!predicate() && steady_clock::now() < end) std::this_thread::sleep_for(milliseconds(1));
    require(predicate(), message);
}
struct Fixtures {
    std::filesystem::path root;
    Fixtures() {
        char pattern[] = "/tmp/tyrell-status-idle-XXXXXX";
        const auto* made = mkdtemp(pattern); require(made, "fixture directory"); root = made;
        std::filesystem::create_directories(root / "proc");
        std::filesystem::create_directories(root / "sys/class/net");
        std::filesystem::create_directories(root / "sys/class/power_supply");
        std::ofstream(root / "proc/meminfo") << "MemTotal: 1000 kB\nMemAvailable: 500 kB\n";
        uptime(60);
    }
    ~Fixtures() { std::filesystem::remove_all(root); }
    void uptime(unsigned seconds) {
        { std::ofstream out(root / "proc/uptime.new"); out << seconds << ".0 0.0\n"; }
        std::filesystem::rename(root / "proc/uptime.new", root / "proc/uptime");
    }
    Options options() const { Options result; result.paths.proc_root = root / "proc"; result.paths.sys_root = root / "sys"; return result; }
};
struct Devices : GuiDevices {
    int keys[2]{-1, -1};
    bool input = false;
    Ui* ui = nullptr;
    std::vector<std::uint32_t> pixels;
    std::atomic<unsigned> waits{0}, reads{0}, flushes{0};
    std::atomic<int> page{-1}, uptime{-1};
    std::atomic<bool> owned{false};
    bool signal_before_wait = false;
    Devices() { require(pipe2(keys, O_NONBLOCK | O_CLOEXEC) == 0, "input pipe"); }
    ~Devices() override { for(auto fd : keys) if(fd >= 0) close(fd); }
    bool acquire_input(const std::string&) override { input = true; owned = true; return true; }
    void release_input() override { input = false; owned = false; }
    int input_fd() const override { return input ? keys[0] : -1; }
    bool process_input(Ui& state) override {
        require(input, "no input read while hidden"); ++reads; ui = &state;
        char bytes[64];
        const auto count = read(keys[0], bytes, sizeof(bytes));
        if(count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return true;
        if(count == 0) return false;
        require(count > 0, "input read");
        for(ssize_t i = 0; i < count; ++i) key(state, static_cast<unsigned char>(bytes[i]));
        return true;
    }
    lv_display_t* create_display(const std::string&) override {
        pixels.resize(width * height);
        auto* display = lv_display_create(width, height);
        lv_display_set_color_format(display, LV_COLOR_FORMAT_XRGB8888);
        lv_display_set_buffers(display, pixels.data(), nullptr, pixels.size() * sizeof(pixels[0]), LV_DISPLAY_RENDER_MODE_DIRECT);
        lv_display_set_user_data(display, this);
        lv_display_set_flush_cb(display, [](lv_display_t* d, const lv_area_t*, std::uint8_t*) {
            auto& self = *static_cast<Devices*>(lv_display_get_user_data(d));
            require(self.input, "no display flush after ownership loss");
            if(self.ui) {
                self.page = self.ui->page;
                self.uptime = static_cast<int>(self.ui->snapshot.uptime_seconds.value_or(-1));
            }
            ++self.flushes;
            lv_display_flush_ready(d);
        });
        return display;
    }
    void wait(const tyrell::status::WaitRequest& request) override {
        require(request.shutdown_fd >= 0, "native wait includes shutdown wake descriptor");
        require(request.input_fd == input_fd(), "wait follows current keyboard ownership");
        ++waits;
        if(signal_before_wait) { signal_before_wait = false; std::raise(SIGTERM); }
        GuiDevices::wait(request);
    }
    void send_key(char value) { require(write(keys[1], &value, 1) == 1, "input event write"); }
};
void native_idle_input_refresh_and_deadline() {
    Fixtures fixtures;
    Devices devices;
    running = 1;
    unsetenv("GH0ST_SHELL_FD");
    std::thread driver([&] {
        eventually([&] { return devices.flushes.load() > 0 && devices.waits.load() > 0; }, milliseconds(500), "first frame reaches wait");
        const auto waits = devices.waits.load(), reads = devices.reads.load(), flushes = devices.flushes.load();
        std::this_thread::sleep_for(milliseconds(300));
        require(devices.waits.load() - waits <= 2, "idle GUI does not wake at a fixed 10/30 ms interval");
        require(devices.reads.load() - reads <= 2 && devices.flushes.load() == flushes, "idle GUI avoids needless input reads and display writes");

        auto start = steady_clock::now(); devices.send_key('2');
        eventually([&] { return devices.page.load() == 1; }, milliseconds(100), "keyboard wakes and updates visible page");
        require(steady_clock::now() - start < milliseconds(100), "keyboard response tolerance");
        devices.send_key('1');
        eventually([&] { return devices.page.load() == 0; }, milliseconds(100), "return to system page");
        // Only the final byte changes the page: the queue must survive more
        // than two bounded 64-event batches without waiting for telemetry.
        const std::string burst = std::string(129, '1') + '2';
        require(write(devices.keys[1], burst.data(), burst.size()) == static_cast<ssize_t>(burst.size()), "queued input burst");
        eventually([&] { return devices.page.load() == 1; }, milliseconds(100), "bounded input batches do not strand ready work");
        devices.send_key('1');
        eventually([&] { return devices.page.load() == 0; }, milliseconds(100), "system page after burst");
        fixtures.uptime(120);
        start = steady_clock::now(); devices.send_key('r');
        eventually([&] { return devices.uptime.load() == 120; }, milliseconds(100), "manual refresh precedes telemetry deadline");
        require(steady_clock::now() - start < milliseconds(100), "manual refresh response tolerance");

        fixtures.uptime(180); start = steady_clock::now();
        eventually([&] { return devices.uptime.load() == 180; }, milliseconds(1300), "telemetry deadline wakes without input");
        require(steady_clock::now() - start >= milliseconds(800), "telemetry is not polled continuously");
        const auto writes = devices.flushes.load();
        close(devices.keys[1]); devices.keys[1] = -1;
        eventually([&] { return !devices.owned.load(); }, milliseconds(100), "input EOF wakes and relinquishes ownership");
        require(devices.flushes.load() == writes, "input EOF exits before another flush");
    });
    require(gui(fixtures.options(), &devices) == 0, "native event-driven GUI exit");
    driver.join();
}
void shutdown_before_and_during_wait() {
    Fixtures fixtures;
    Devices queued;
    queued.signal_before_wait = true;
    running = 1;
    const auto start = steady_clock::now();
    require(gui(fixtures.options(), &queued) == 0, "queued signal GUI exit");
    require(steady_clock::now() - start < milliseconds(100), "signal between running check and poll is not lost");

    Devices blocked;
    running = 1;
    const auto main_thread = pthread_self();
    std::atomic<long long> sent{0};
    std::thread driver([&] {
        eventually([&] { return blocked.waits.load() > 0; }, milliseconds(500), "GUI enters blocking wait");
        std::this_thread::sleep_for(milliseconds(20));
        sent = duration_cast<nanoseconds>(steady_clock::now().time_since_epoch()).count();
        require(pthread_kill(main_thread, SIGTERM) == 0, "send GUI thread shutdown signal");
    });
    require(gui(fixtures.options(), &blocked) == 0, "blocked signal GUI exit");
    const auto ended = steady_clock::now(); driver.join();
    require(ended.time_since_epoch() - nanoseconds(sent.load()) < milliseconds(100), "SIGTERM interrupts long native wait");
}
#if TYRELL_ENABLE_SHELL_LINK
void hidden_link_wait() {
    Fixtures fixtures;
    Devices devices;
    int pair[2]; require(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, pair) == 0, "shell pair");
    setenv("GH0ST_SHELL_FD", std::to_string(pair[1]).c_str(), 1);
    require(send(pair[0], "hello\t1", 7, MSG_NOSIGNAL) == 7, "shell hello");
    running = 1;
    std::thread driver([&] {
        eventually([&] { return devices.waits.load() > 0; }, milliseconds(500), "linked GUI idle");
        require(send(pair[0], "pause", 5, MSG_NOSIGNAL) == 5, "shell pause");
        bool ack = false;
        eventually([&] {
            char bytes[512];
            const auto n = recv(pair[0], bytes, sizeof(bytes), MSG_DONTWAIT);
            if(n > 0 && std::string(bytes, static_cast<std::size_t>(n)) == "paused") ack = true;
            return ack;
        }, milliseconds(100), "pause wakes foreground wait");
        require(!devices.owned.load(), "paused acknowledgment follows input release");
        const auto waits = devices.waits.load(), reads = devices.reads.load(), writes = devices.flushes.load();
        std::this_thread::sleep_for(milliseconds(150));
        require(devices.waits.load() - waits <= 1, "hidden wait has no periodic refresh deadline");
        require(devices.reads.load() == reads && devices.flushes.load() == writes, "hidden GUI does no input or display work");
        close(pair[0]);
    });
    require(gui(fixtures.options(), &devices) == 0, "hidden EOF wakes indefinite wait");
    driver.join();
}
#endif
} // namespace
int main() {
    native_idle_input_refresh_and_deadline();
    shutdown_before_and_during_wait();
#if TYRELL_ENABLE_SHELL_LINK
    hidden_link_wait();
#endif
}
