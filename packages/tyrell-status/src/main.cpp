#include "status_model.h"
#include "input_edges.h"
#include "lvgl.h"
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
#include "shell_lifecycle.h"
#include <cstdlib>
#endif

#include <algorithm>
#include <chrono>
#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#if defined(__linux__)
#include <fcntl.h>
#include <linux/input.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include "src/drivers/display/fb/lv_linux_fbdev.h"
#endif
#if TYRELL_ENABLE_SDL
#include "src/drivers/sdl/lv_sdl_window.h"
#include "src/drivers/sdl/lv_sdl_keyboard.h"
#endif

namespace {
constexpr int width = 320;
constexpr int height = 170;
volatile std::sig_atomic_t running = 1;
void stop(int) { running = 0; }
std::uint32_t tick_ms() {
    using namespace std::chrono;
    return static_cast<std::uint32_t>(duration_cast<milliseconds>(steady_clock::now().time_since_epoch()).count());
}

struct Options {
    tyrell::StatusPaths paths;
    std::string framebuffer = "/dev/fb_lcd";
    std::string input;
    std::string screenshot;
    unsigned frames = 0;
    int page = 0;
    bool json = false;
    bool simulator = false;
    bool framebuffer_explicit = false;
    bool input_explicit = false;
};

[[maybe_unused]] void usage() {
    std::cout << "tyrell-status " TYRELL_VERSION "\n"
              << "  --json                    Print read-only telemetry; never open display/input\n"
              << "  --version                 Print version and source revision\n"
              << "  --headless-screenshot PPM Render 320x170 preview without hardware\n"
              << "  --frames N                Exit after N rendering iterations\n"
              << "  --page system|network     Select initial page\n"
              << "  --framebuffer PATH        Framebuffer (default /dev/fb_lcd)\n"
              << "  --input PATH              evdev keyboard (default stable Cardputer link/name)\n"
              << "  --simulator               SDL window, if enabled at build time\n"
              << "  --proc-root PATH          Alternate procfs root for offline fixtures\n"
              << "  --sys-root PATH           Alternate sysfs root for offline fixtures\n"
              << "  --help                    Show this help\n"
              << "Keyboard: 1/left/Z system; 2/right/C network; R refresh; ESC/Q exit.\n";
}

struct Ui {
    lv_obj_t* page_name = nullptr;
    lv_obj_t* labels[3]{};
    lv_obj_t* values[3]{};
    lv_obj_t* footer = nullptr;
    int page = 0;
    tyrell::StatusSnapshot snapshot;
    bool refresh = true;
};

lv_obj_t* text(lv_obj_t* parent, const char* value, int x, int y, int w,
               lv_color_t color, const lv_font_t* font = &lv_font_montserrat_14) {
    auto* label = lv_label_create(parent);
    lv_label_set_text(label, value);
    lv_obj_set_pos(label, x, y);
    lv_obj_set_width(label, w);
    lv_label_set_long_mode(label, LV_LABEL_LONG_MODE_DOTS);
    lv_obj_set_style_text_font(label, font, 0);
    lv_obj_set_style_text_color(label, color, 0);
    return label;
}

void build_ui(Ui& ui) {
    auto* screen = lv_screen_active();
    lv_obj_remove_flag(screen, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(screen, lv_color_hex(0x101c24), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_set_style_pad_all(screen, 0, 0);
    text(screen, "TYRELL", 10, 6, 88, lv_color_hex(0x69dcc8));
    ui.page_name = text(screen, "SYSTEM STATUS", 106, 7, 204, lv_color_hex(0xe6edf2), &lv_font_montserrat_12);
    const std::string build = std::string("v") + TYRELL_VERSION + " / " + std::string(TYRELL_REVISION).substr(0, 12);
    text(screen, build.c_str(), 10, 25, 300, lv_color_hex(0x8fa7b6), &lv_font_montserrat_12);
    for(int i = 0; i < 3; ++i) {
        const int y = 47 + i * 32;
        auto* row = lv_obj_create(screen);
        lv_obj_set_pos(row, 7, y);
        lv_obj_set_size(row, 306, 28);
        lv_obj_remove_flag(row, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_set_style_bg_color(row, lv_color_hex(0x1a2a35), 0);
        lv_obj_set_style_border_width(row, 0, 0);
        lv_obj_set_style_radius(row, 4, 0);
        lv_obj_set_style_pad_all(row, 0, 0);
        ui.labels[i] = text(row, "", 7, 7, 71, lv_color_hex(0x8fa7b6), &lv_font_montserrat_12);
        ui.values[i] = text(row, "", 80, 6, 218, lv_color_hex(0xe6edf2));
    }
    ui.footer = text(screen, "", 10, 150, 302, lv_color_hex(0x69dcc8), &lv_font_montserrat_12);
}

void update_ui(Ui& ui) {
    std::string labels[3], values[3];
    if(ui.page == 0) {
        lv_label_set_text(ui.page_name, "SYSTEM STATUS");
        labels[0] = "UPTIME"; values[0] = tyrell::format_uptime(ui.snapshot);
        labels[1] = "MEMORY"; values[1] = tyrell::format_memory(ui.snapshot);
        labels[2] = "BATTERY"; values[2] = tyrell::format_battery(ui.snapshot);
        lv_label_set_text(ui.footer, "[1] System   2 Network    ESC Exit");
    } else {
        lv_label_set_text(ui.page_name, "NETWORK STATUS");
        for(int i = 0; i < 3; ++i) {
            if(static_cast<std::size_t>(i) < ui.snapshot.network.size()) {
                const auto& iface = ui.snapshot.network[static_cast<std::size_t>(i)];
                labels[i] = iface.name;
                values[i] = iface.state.empty() ? "unknown" : iface.state;
                if(iface.carrier) values[i] += *iface.carrier ? " / link up" : " / no link";
                if(iface.wireless) values[i] += " / Wi-Fi";
            } else if(i == 0) {
                labels[i] = "LINKS"; values[i] = "No interface data";
            } else {
                labels[i] = ""; values[i] = "";
            }
        }
        lv_label_set_text(ui.footer, "1 System   [2] Network    ESC Exit");
    }
    for(int i = 0; i < 3; ++i) {
        lv_label_set_text(ui.labels[i], labels[i].c_str());
        lv_label_set_text(ui.values[i], values[i].c_str());
    }
}

[[maybe_unused]] void key(Ui& ui, std::uint32_t code) {
    switch(code) {
        case LV_KEY_ESC: case 'q': case 'Q': running = 0; break;
        case '1': case 'z': case 'Z': case LV_KEY_LEFT: ui.page = 0; update_ui(ui); break;
        case '2': case 'c': case 'C': case LV_KEY_RIGHT: ui.page = 1; update_ui(ui); break;
        case 'r': case 'R': ui.refresh = true; break;
        default: break;
    }
}

#if defined(__linux__)
class Keyboard {
public:
    ~Keyboard() { release(); }
    void release() {
        if(fd_ >= 0) close(fd_);
        fd_ = -1;
        edges_.reset();
    }
    bool open_device(const std::string& requested) {
        release();
        if(!path_.empty()) return open_path(path_, false);
        if(!requested.empty()) return open_path(requested, false);
        if(open_path("/dev/input/cardputer-zero-internal", false)) return true;
        std::error_code error;
        for(const auto& entry : std::filesystem::directory_iterator("/dev/input", error)) {
            if(entry.path().filename().string().rfind("event", 0) == 0 && open_path(entry.path().string(), true)) return true;
        }
        return false;
    }
    bool process(Ui& ui) {
        input_event event{};
        // A noisy device must not starve the shell's next pause request.
        for(unsigned n = 0; n < 64; ++n) {
            const auto count = read(fd_, &event, sizeof(event));
            if(count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return true;
            if(count != sizeof(event)) { release(); return false; }
            if(event.type == EV_SYN && event.code == SYN_DROPPED) { edges_.dropped(); continue; }
            if(edges_.needs_sync()) {
                if(event.type == EV_SYN && event.code == SYN_REPORT && !synchronize()) { release(); return false; }
                continue;
            }
            if(event.type != EV_KEY || !edges_.press(event.code, event.value)) continue;
            switch(event.code) {
                case KEY_ESC: key(ui, LV_KEY_ESC); break;
                case KEY_Q: key(ui, 'q'); break;
                case KEY_1: case KEY_LEFT: case KEY_Z: key(ui, '1'); break;
                case KEY_2: case KEY_RIGHT: case KEY_C: key(ui, '2'); break;
                case KEY_R: key(ui, 'r'); break;
                default: break;
            }
        }
        return true;
    }
private:
    int fd_ = -1;
    std::string path_;
    tyrell::status::InputEdges edges_;
    bool synchronize() {
        unsigned char bits[(KEY_MAX + 8) / 8]{};
        if(ioctl(fd_, EVIOCGKEY(sizeof(bits)), bits) < 0) return false;
        tyrell::status::InputEdges::State held{};
        for(unsigned code = 0; code <= KEY_MAX && code < held.size(); ++code)
            held[code] = (bits[code / 8] & (1U << (code % 8))) != 0;
        edges_.reset(held);
        return true;
    }
    bool open_path(const std::string& path, bool check_name) {
        int fd = open(path.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if(fd < 0) return false;
        char name[256]{};
        if(check_name && (ioctl(fd, EVIOCGNAME(sizeof(name)), name) < 0 || std::string(name).find("tca8418") == std::string::npos)) {
            close(fd); return false;
        }
        fd_ = fd;
        // A fresh evdev client has no old seat queue. Drop the bounded batch
        // that arrived during open, then baseline held keys before accepting
        // any commands. A continuously flooded device fails closed.
        input_event stale{};
        unsigned n = 0;
        while(n < 256 && read(fd_, &stale, sizeof(stale)) == sizeof(stale)) ++n;
        if(n == 256 || !synchronize()) { release(); return false; }
        path_ = path;
        return true;
    }
};
#endif

// The injected backend lets lifecycle tests exercise this exact GUI loop and
// LVGL renderer without opening a framebuffer or evdev device.
class GuiDevices {
public:
    virtual ~GuiDevices() = default;
    virtual bool acquire_input(const std::string& path) {
#if defined(__linux__)
        return keyboard_.open_device(path);
#else
        (void)path; return false;
#endif
    }
    virtual void release_input() {
#if defined(__linux__)
        keyboard_.release();
#endif
    }
    virtual bool process_input(Ui& ui) {
#if defined(__linux__)
        return keyboard_.process(ui);
#else
        (void)ui; return true;
#endif
    }
    virtual lv_display_t* create_display(const std::string& path) {
#if defined(__linux__)
        auto* display = lv_linux_fbdev_create();
        if(display && lv_linux_fbdev_set_file(display, path.c_str()) != LV_RESULT_OK) {
            lv_display_delete(display); return nullptr;
        }
        return display;
#else
        (void)path; return nullptr;
#endif
    }
    virtual void idle() { std::this_thread::sleep_for(std::chrono::milliseconds(10)); }
private:
#if defined(__linux__)
    Keyboard keyboard_;
#endif
};

void write_ppm(const std::string& path, const std::vector<std::uint32_t>& pixels) {
    std::ofstream out(path, std::ios::binary);
    if(!out) throw std::runtime_error("Cannot open screenshot output: " + path);
    out << "P6\n" << width << ' ' << height << "\n255\n";
    for(auto pixel : pixels) {
        const char rgb[] = {static_cast<char>((pixel >> 16) & 255), static_cast<char>((pixel >> 8) & 255), static_cast<char>(pixel & 255)};
        out.write(rgb, sizeof(rgb));
    }
    if(!out) throw std::runtime_error("Failed writing screenshot: " + path);
}

int gui(const Options& requested, GuiDevices* injected_devices = nullptr) {
    Options options = requested;
    const bool headless = !options.screenshot.empty();
    if(headless && options.simulator) throw std::runtime_error("Choose either headless screenshot or SDL simulator");
#if !TYRELL_ENABLE_SDL
    if(options.simulator) throw std::runtime_error("SDL simulator was not enabled in this build");
#endif
    std::signal(SIGINT, stop);
    std::signal(SIGTERM, stop);
    GuiDevices default_devices;
    auto& devices = injected_devices ? *injected_devices : default_devices;
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
    // Display-free/desktop tools never inspect or adopt native shell IPC.
    auto link = (!headless && !options.simulator)
        ? tyrell::app::ShellLink::from_environment() : tyrell::app::ShellLink{};
    const bool linked = link.connected() || link.closed();
    tyrell::status::ShellLifecycle lifecycle;
    struct Hooks {
        GuiDevices& devices;
        Options& options;
        Ui* ui = nullptr;
        bool started = false;
        bool dirty = true;
        void pause() { devices.release_input(); }
        bool resume() {
            dirty = true;
            if(ui) ui->refresh = true;
            return !started || devices.acquire_input(options.input);
        }
        void redraw() { dirty = true; }
    } hooks{devices, options};
    auto pump = [&]() {
        bool drained = false;
        for(unsigned n = 0; n < 32; ++n) {
            const auto event = link.next_event();
            if(event == tyrell::app::ShellEvent::None) { drained = true; break; }
            lifecycle.handle(event, link, hooks);
            if(lifecycle.exiting()) break;
        }
        // Read superseding resume first, so a deferred old paused ack cannot
        // be sent after the app has already reacquired the seat.
        link.service();
        // A send can discover peer loss after receive saw EAGAIN. Fence the
        // very next input/display operation in this same iteration as well.
        if(link.closed()) lifecycle.handle(tyrell::app::ShellEvent::Lost, link, hooks);
        return drained;
    };
    if(linked) {
        if(!options.framebuffer_explicit) {
            if(const char* path = std::getenv("LV_LINUX_FBDEV_DEVICE"); path && *path) options.framebuffer = path;
        }
        if(!options.input_explicit) {
            if(const char* path = std::getenv("LV_LINUX_KEYBOARD_DEVICE"); path && *path) options.input = path;
        }
        const auto hello_deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(100);
        while(running) {
            const bool drained = pump();
            if(lifecycle.exiting()) return 0;
            if(lifecycle.hello_seen() && lifecycle.foreground() && drained) break;
            if(!lifecycle.hello_seen() && std::chrono::steady_clock::now() >= hello_deadline) {
                std::cerr << "tyrell-status: shell hello timed out; leaving display and input untouched\n";
                return 1;
            }
            devices.idle();
        }
        if(!running) return 0;
    }
#endif
#if defined(__linux__)
    if(!headless && !options.simulator && !devices.acquire_input(options.input)) {
        throw std::runtime_error("Cannot open Cardputer keyboard. Use --input PATH, or --json for display-free status.");
    }
#endif
    lv_init();
    lv_tick_set_cb(tick_ms);
    lv_display_t* display = nullptr;
    std::vector<std::uint32_t> pixels;
    if(headless) {
        pixels.resize(width * height);
        display = lv_display_create(width, height);
        lv_display_set_color_format(display, LV_COLOR_FORMAT_XRGB8888);
        lv_display_set_buffers(display, pixels.data(), nullptr, pixels.size() * sizeof(pixels[0]), LV_DISPLAY_RENDER_MODE_DIRECT);
        lv_display_set_flush_cb(display, [](lv_display_t* d, const lv_area_t*, std::uint8_t*) { lv_display_flush_ready(d); });
    }
#if TYRELL_ENABLE_SDL
    else if(options.simulator) {
        display = lv_sdl_window_create(width, height);
        if(display) {
            lv_sdl_window_set_title(display, "Tyrell Status / CardputerZero");
            lv_sdl_window_set_zoom(display, 2.0f);
        }
    }
#endif
#if defined(__linux__)
    else {
        display = devices.create_display(options.framebuffer);
    }
#endif
    if(!display) { lv_deinit(); throw std::runtime_error("Cannot initialize display; use --json or --headless-screenshot for offline operation"); }
    if(lv_display_get_horizontal_resolution(display) != width || lv_display_get_vertical_resolution(display) != height) {
        lv_display_delete(display); lv_deinit(); throw std::runtime_error("Expected a 320x170 display");
    }
    Ui ui;
    ui.page = options.page;
    ui.snapshot = tyrell::collect_status(options.paths);
    build_ui(ui);
    update_ui(ui);
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
    hooks.ui = &ui;
    hooks.started = true;
#endif
#if TYRELL_ENABLE_SDL
    if(options.simulator) {
        auto* input = lv_sdl_keyboard_create();
        auto* group = lv_group_create();
        lv_group_add_obj(group, lv_screen_active());
        lv_group_focus_obj(lv_screen_active());
        lv_indev_set_group(input, group);
        lv_obj_add_event_cb(lv_screen_active(), [](lv_event_t* event) {
            key(*static_cast<Ui*>(lv_event_get_user_data(event)), lv_event_get_key(event));
        }, LV_EVENT_KEY, &ui);
    }
#endif
    auto refresh_at = std::chrono::steady_clock::now();
    const unsigned limit = options.frames ? options.frames : (headless ? 2 : 0);
    unsigned frame = 0;
    while(running && (!limit || frame < limit)) {
        bool full_redraw = false;
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
        if(linked) {
            const bool drained = pump();
            if(lifecycle.exiting()) break;
            if(!lifecycle.foreground() || !drained) { devices.idle(); continue; }
            if(hooks.dirty) {
                lv_obj_invalidate(lv_screen_active());
                ui.refresh = true;
                hooks.dirty = false;
                full_redraw = true;
            }
        }
#endif
#if defined(__linux__)
        if(!headless && !options.simulator && !devices.process_input(ui)) break;
#endif
        const auto now = std::chrono::steady_clock::now();
        if(ui.refresh || now >= refresh_at) {
            ui.snapshot = tyrell::collect_status(options.paths);
            update_ui(ui);
            ui.refresh = false;
            refresh_at = now + std::chrono::seconds(1);
        }
        lv_timer_handler();
        if(headless || full_redraw) lv_refr_now(display);
        ++frame;
        if(!headless) devices.idle();
    }
    if(headless) write_ppm(options.screenshot, pixels);
    devices.release_input();
    // The pinned LVGL fbdev DELETE callback only unmaps/closes/frees. It does
    // not restore or clear framebuffer pixels, including on hidden EOF/exit.
    lv_display_delete(display);
    lv_deinit();
    return 0;
}
} // namespace

#ifndef TYRELL_STATUS_NO_MAIN
int main(int argc, char** argv) {
    try {
        Options options;
        for(int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            auto value = [&]() -> std::string {
                if(i + 1 >= argc) throw std::runtime_error("Missing value for " + arg);
                return argv[++i];
            };
            if(arg == "--help" || arg == "-h") { usage(); return 0; }
            if(arg == "--version") { std::cout << "tyrell-status " TYRELL_VERSION " (" TYRELL_REVISION ")\n"; return 0; }
            if(arg == "--json") options.json = true;
            else if(arg == "--proc-root") options.paths.proc_root = value();
            else if(arg == "--sys-root") options.paths.sys_root = value();
            else if(arg == "--framebuffer") { options.framebuffer = value(); options.framebuffer_explicit = true; }
            else if(arg == "--input") { options.input = value(); options.input_explicit = true; }
            else if(arg == "--headless-screenshot") options.screenshot = value();
            else if(arg == "--simulator") options.simulator = true;
            else if(arg == "--frames") {
                const auto raw = value();
                std::size_t consumed = 0;
                const auto n = std::stoul(raw, &consumed);
                if(consumed != raw.size() || n == 0 || n > 1000000) throw std::runtime_error("--frames must be between 1 and 1000000");
                options.frames = static_cast<unsigned>(n);
            }
            else if(arg == "--page") {
                const auto page = value();
                if(page != "system" && page != "network") throw std::runtime_error("--page must be system or network");
                options.page = page == "network" ? 1 : 0;
            }
            else throw std::runtime_error("Unknown option: " + arg);
        }
        if(options.json) {
            std::cout << tyrell::status_to_json(tyrell::collect_status(options.paths), TYRELL_VERSION, TYRELL_REVISION) << '\n';
            return 0;
        }
        return gui(options);
    } catch(const std::exception& error) {
        std::cerr << "tyrell-status: " << error.what() << '\n';
        return 1;
    }
}
#endif
