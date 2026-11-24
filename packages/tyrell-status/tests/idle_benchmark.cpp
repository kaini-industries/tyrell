// Compile this same harness against both the retained PR #8 main.cpp and the
// candidate. The driver is synthetic; measurements do not qualify hardware.
#define TYRELL_STATUS_NO_MAIN
#ifndef STATUS_MAIN_SOURCE
#define STATUS_MAIN_SOURCE "../src/main.cpp"
#endif
#include STATUS_MAIN_SOURCE

#include <atomic>
#include <functional>
#include <iomanip>
#include <pthread.h>
#include <sstream>
#include <sys/socket.h>
#include <time.h>

using namespace std::chrono;
namespace {
void require(bool value, const char* message) {
    if(!value) { std::cerr << "idle benchmark failed: " << message << '\n'; std::exit(1); }
}
long long now_ns() { return duration_cast<nanoseconds>(steady_clock::now().time_since_epoch()).count(); }
long long cpu_ns(clockid_t clock) {
    timespec value{}; require(clock_gettime(clock, &value) == 0, "GUI thread CPU clock");
    return static_cast<long long>(value.tv_sec) * 1000000000LL + value.tv_nsec;
}
template<class Predicate> void eventually(Predicate predicate, const char* message) {
    const auto end = steady_clock::now() + seconds(2);
    while(!predicate() && steady_clock::now() < end) std::this_thread::sleep_for(microseconds(100));
    require(predicate(), message);
}
struct Fixtures {
    std::filesystem::path root;
    Fixtures() {
        char pattern[] = "/tmp/tyrell-status-bench-XXXXXX";
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
    std::atomic<unsigned> foreground_waits{0}, hidden_waits{0}, reads{0}, flushes{0}, acquires{0}, wait_generation{0};
    std::atomic<bool> owned{false};
    std::atomic<int> pending_kind{0}, wanted_page{0}, wanted_uptime{0};
    std::atomic<unsigned> wanted_acquires{0};
    std::atomic<unsigned> manual_consumed{0}, wanted_manual{0};
    std::atomic<unsigned> visible_wait_generation{0};
    std::atomic<long long> visible_at{0};
    unsigned last_command_wait = 0; // Driver-thread-only action barrier.
    Devices() { require(pipe2(keys, O_NONBLOCK | O_CLOEXEC) == 0, "input pipe"); }
    ~Devices() override { for(auto fd : keys) if(fd >= 0) close(fd); }
    bool acquire_input(const std::string&) override { input = true; ++acquires; owned = true; return true; }
    void release_input() override { input = false; owned = false; }
#if !A1BENCH_BASELINE
    int input_fd() const override { return input ? keys[0] : -1; }
#endif
    bool process_input(Ui& state) override {
        require(input, "no input read while hidden"); ++reads; ui = &state;
        char bytes[64]; const auto count = read(keys[0], bytes, sizeof(bytes));
        if(count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return true;
        require(count > 0, "input remains connected");
        for(ssize_t i = 0; i < count; ++i) {
            key(state, static_cast<unsigned char>(bytes[i]));
            if(bytes[i] == 'r' || bytes[i] == 'R') ++manual_consumed;
        }
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
            require(self.input, "no flush while hidden"); ++self.flushes;
            const auto kind = self.pending_kind.load();
            if(self.ui && ((kind == 1 && self.ui->page == self.wanted_page.load()) ||
               (kind == 2 && self.manual_consumed.load() >= self.wanted_manual.load() &&
                static_cast<int>(self.ui->snapshot.uptime_seconds.value_or(-1)) == self.wanted_uptime.load()) ||
               (kind == 3 && self.acquires.load() >= self.wanted_acquires.load()))) {
                self.visible_at = now_ns();
                self.visible_wait_generation = self.wait_generation.load();
                self.pending_kind = 0;
            }
            lv_display_flush_ready(d);
        });
        return display;
    }
    void count_wait() { if(input) ++foreground_waits; else ++hidden_waits; ++wait_generation; }
    void idle() override { count_wait(); GuiDevices::idle(); }
#if !A1BENCH_BASELINE
    void wait(const tyrell::status::WaitRequest& request) override {
        require(request.input_fd == input_fd(), "wait follows seat ownership");
        if(!input) require(!request.deadline.has_value(), "hidden wait has no periodic deadline");
        count_wait(); GuiDevices::wait(request);
    }
#endif
    void send_key(char value) { require(write(keys[1], &value, 1) == 1, "keyboard write"); }
};
void send_packet(int peer, const char* packet) {
    const auto length = std::strlen(packet);
    require(send(peer, packet, length, MSG_NOSIGNAL) == static_cast<ssize_t>(length), "shell send");
}
void paused(int peer) {
    bool ack = false;
    eventually([&] {
        char data[512]; const auto n = recv(peer, data, sizeof(data), MSG_DONTWAIT);
        if(n > 0 && std::string(data, static_cast<std::size_t>(n)) == "paused") ack = true;
        return ack;
    }, "paused acknowledgment");
}
struct IdleMeasurement {
    double wall_ms = 0, cpu_ms = 0, process_cpu_ms = 0;
    unsigned waits = 0, reads = 0, flushes = 0;
};
IdleMeasurement measure_idle(Devices& devices, clockid_t clock, bool hidden, milliseconds duration) {
    const auto start = now_ns(), cpu = cpu_ns(clock), process_cpu = cpu_ns(CLOCK_PROCESS_CPUTIME_ID);
    const auto waits = hidden ? devices.hidden_waits.load() : devices.foreground_waits.load();
    const auto reads = devices.reads.load(), flushes = devices.flushes.load();
    std::this_thread::sleep_for(duration);
    return {(now_ns() - start) / 1e6, (cpu_ns(clock) - cpu) / 1e6, (cpu_ns(CLOCK_PROCESS_CPUTIME_ID) - process_cpu) / 1e6,
            (hidden ? devices.hidden_waits.load() : devices.foreground_waits.load()) - waits,
            devices.reads.load() - reads, devices.flushes.load() - flushes};
}
double visible_action(Devices& devices, int kind, const std::function<void()>& send) {
    // A prior flush callback can finish before its containing refresh returns.
    // Wait for the following idle/wait boundary so an old flush cannot satisfy
    // this command. Page/uptime/acquisition generations additionally establish
    // that the relevant command has actually changed the visible app state.
    eventually([&] { return devices.wait_generation.load() > devices.last_command_wait; }, "post-render action barrier");
    if(kind == 3) devices.wanted_acquires = devices.acquires.load() + 1;
    if(kind == 2) devices.wanted_manual = devices.manual_consumed.load() + 1;
    const auto start = now_ns();
    devices.pending_kind = kind; send();
    eventually([&] { return devices.pending_kind.load() == 0; }, "visible command completion");
    devices.last_command_wait = devices.visible_wait_generation.load();
    return (devices.visible_at.load() - start) / 1e6;
}
void emit_idle(std::ostream& out, const IdleMeasurement& value) {
    out << "{\"wall_ms\":" << value.wall_ms << ",\"gui_thread_cpu_ms\":" << value.cpu_ms
        << ",\"process_cpu_ms\":" << value.process_cpu_ms
        << ",\"wait_calls\":" << value.waits << ",\"input_reads\":" << value.reads << ",\"flushes\":" << value.flushes << '}';
}
double shutdown_sample(const Fixtures& fixtures, pthread_t main_thread) {
    Devices devices;
    int pair[2]; require(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, pair) == 0, "shutdown shell pair");
    setenv("GH0ST_SHELL_FD", std::to_string(pair[1]).c_str(), 1); send_packet(pair[0], "hello\t1");
    running = 1;
    std::atomic<long long> sent{0};
    std::thread driver([&] {
        eventually([&] { return devices.foreground_waits.load() > 0; }, "fresh shutdown session first frame");
        send_packet(pair[0], "pause"); paused(pair[0]);
        require(!devices.owned.load(), "fresh shutdown session relinquished input");
        sent = now_ns(); require(pthread_kill(main_thread, SIGTERM) == 0, "fresh hidden shutdown signal");
    });
    require(gui(fixtures.options(), &devices) == 0, "fresh hidden shutdown GUI exit");
    const auto ended = now_ns(); driver.join(); close(pair[0]);
    require(!devices.owned.load(), "shutdown session keeps input released");
    return (ended - sent.load()) / 1e6;
}
void emit_samples(std::ostream& out, const std::vector<double>& values) {
    out << '[';
    for(std::size_t i = 0; i < values.size(); ++i) { if(i) out << ','; out << values[i]; }
    out << ']';
}
} // namespace
int main(int argc, char** argv) {
    std::string report;
    unsigned idle_ms = 3000, hidden_ms = 2000, samples = 20;
    for(int i = 1; i < argc; ++i) {
        const std::string option = argv[i];
        require(i + 1 < argc, "option value"); const std::string value = argv[++i];
        if(option == "--report") report = value;
        else if(option == "--idle-ms") idle_ms = static_cast<unsigned>(std::stoul(value));
        else if(option == "--hidden-ms") hidden_ms = static_cast<unsigned>(std::stoul(value));
        else if(option == "--samples") samples = static_cast<unsigned>(std::stoul(value));
        else require(false, "unknown benchmark option");
    }
    require(idle_ms > 0 && hidden_ms > 0 && samples > 0 && samples <= 1000, "bounded positive benchmark arguments");
    Fixtures fixtures; Devices devices;
    int pair[2]; require(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, pair) == 0, "shell pair");
    setenv("GH0ST_SHELL_FD", std::to_string(pair[1]).c_str(), 1); send_packet(pair[0], "hello\t1");
    running = 1;
    const auto main_thread = pthread_self();
    clockid_t gui_clock{}; require(pthread_getcpuclockid(main_thread, &gui_clock) == 0, "GUI clock identity");
    IdleMeasurement foreground, hidden;
    std::vector<double> keyboard, refresh, pause, resume, shutdown;
    std::atomic<long long> shutdown_sent{0};
    std::thread driver([&] {
        eventually([&] { return devices.flushes.load() > 0 && devices.foreground_waits.load() > 0; }, "initial frame and native wait");
        foreground = measure_idle(devices, gui_clock, false, milliseconds(idle_ms));
        send_packet(pair[0], "pause"); paused(pair[0]);
        require(!devices.owned.load(), "pause releases input before acknowledgment");
        hidden = measure_idle(devices, gui_clock, true, milliseconds(hidden_ms));
        require(hidden.reads == 0 && hidden.flushes == 0, "no hidden input or render work");
        (void)visible_action(devices, 3, [&] { send_packet(pair[0], "resume"); });

        for(unsigned i = 0; i < samples; ++i) {
            devices.wanted_page = i % 2 == 0 ? 1 : 0;
            keyboard.push_back(visible_action(devices, 1, [&] { devices.send_key(i % 2 == 0 ? '2' : '1'); }));
        }
        if(samples % 2) {
            devices.wanted_page = 0;
            (void)visible_action(devices, 1, [&] { devices.send_key('1'); });
        }
        // Establish a fresh collection deadline before changing the telemetry
        // fixture. Each measured R also resets that deadline. This prevents an
        // unrelated periodic refresh from being mistaken for an R completion.
        send_packet(pair[0], "pause"); paused(pair[0]);
        (void)visible_action(devices, 3, [&] { send_packet(pair[0], "resume"); });
        for(unsigned i = 0; i < samples; ++i) {
            require(now_ns() - devices.visible_at.load() < 500000000LL, "fresh telemetry deadline before manual refresh sample");
            devices.wanted_uptime = 120 + static_cast<int>(60 * i);
            fixtures.uptime(static_cast<unsigned>(devices.wanted_uptime.load()));
            refresh.push_back(visible_action(devices, 2, [&] { devices.send_key('r'); }));
        }
        for(unsigned i = 0; i < samples; ++i) {
            const auto start = now_ns(); send_packet(pair[0], "pause"); paused(pair[0]);
            pause.push_back((now_ns() - start) / 1e6);
            require(!devices.owned.load(), "each pause relinquishes seat");
            resume.push_back(visible_action(devices, 3, [&] { send_packet(pair[0], "resume"); }));
        }
        send_packet(pair[0], "pause"); paused(pair[0]);
        shutdown_sent = now_ns(); require(pthread_kill(main_thread, SIGTERM) == 0, "hidden shutdown signal");
    });
    const auto result = gui(fixtures.options(), &devices);
    const auto shutdown_ended = now_ns(); driver.join(); close(pair[0]);
    require(result == 0 && !devices.owned.load(), "GUI shuts down without retaining input");
    shutdown.push_back((shutdown_ended - shutdown_sent.load()) / 1e6);
    for(unsigned i = 1; i < samples; ++i) shutdown.push_back(shutdown_sample(fixtures, main_thread));
    std::ostringstream json; json << std::fixed << std::setprecision(6);
    json << "{\"schema\":1,\"scope\":\"synthetic-native-gui-loop-comparison\",\"variant\":\""
#if A1BENCH_BASELINE
         << "baseline"
#else
         << "candidate"
#endif
         << "\",\"status\":\"passed\",\"hardware_qualified\":false,\"samples_requested\":" << samples << ",\"foreground_idle\":";
    emit_idle(json, foreground); json << ",\"hidden_idle\":"; emit_idle(json, hidden);
    json << ",\"keyboard_visible_ms\":"; emit_samples(json, keyboard);
    json << ",\"manual_refresh_visible_ms\":"; emit_samples(json, refresh);
    json << ",\"pause_ack_ms\":"; emit_samples(json, pause);
    json << ",\"resume_visible_ms\":"; emit_samples(json, resume);
    json << ",\"hidden_shutdown_ms\":"; emit_samples(json, shutdown); json << "}\n";
    if(!report.empty()) { std::ofstream output(report); require(static_cast<bool>(output << json.str()), "write benchmark report"); }
    std::cout << json.str();
}
