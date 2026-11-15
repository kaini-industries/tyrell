#include "browser.h"
#include "frame_image.h"
#include "json_string.h"
#include "lcd_commit.h"
#include "outputs.h"
#include "playback.h"
#include "lvgl.h"

#include <algorithm>
#include <chrono>
#include <cctype>
#include <csignal>
#include <cmath>
#include <ctime>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>
#if defined(__unix__) || defined(__APPLE__)
#include <sys/resource.h>
#endif

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
using namespace tyrell::video;
namespace fs = std::filesystem;
constexpr int width = 320, height = 170, row_count = 5;
constexpr std::uint32_t background = 0x101c24, accent = 0x69dcc8, white = 0xe6edf2, quiet = 0x8fa7b6;
volatile std::sig_atomic_t running = 1;
void stop_signal(int) { running = 0; }
std::uint32_t tick_ms() {
    return static_cast<std::uint32_t>(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());
}

struct Options {
    fs::path media_root;
    std::string file, output = "auto", framebuffer = "/dev/fb_lcd", input, screenshot, report, page = "browser";
    double run_seconds = 0;
    unsigned frames = 0;
    bool simulator = false, diagnostics = false;
};
void usage() {
    std::cout << "tyrell-video " TYRELL_VERSION "\n"
        << "Usage: tyrell-video [FILE] [options]\n"
        << "  --media-root DIR          Start browsing in this folder\n"
        << "  --output auto|lcd|hdmi    Initial video output (default auto)\n"
        << "  --diagnostics            JSON decoder/output inventory, no display or keyboard\n"
        << "  --framebuffer PATH       LCD framebuffer (default /dev/fb_lcd)\n"
        << "  --input PATH             evdev keyboard override\n"
        << "  --simulator              SDL window, when enabled at build time\n"
        << "  --headless-screenshot PPM Deterministic LVGL preview, no device access\n"
        << "  --page browser|player|hdmi|tracks|error  Preview page\n"
        << "  --frames N               Exit after N main-loop iterations\n"
        << "  --run-seconds N          Exit after N elapsed seconds for validation\n"
        << "  --report PATH            Write JSON playback/resource measurements on exit\n"
        << "  --version                Version and source revision\n"
        << "  --help                   This help\n"
        << "Keys: arrows/WASD navigate or seek/volume; Enter open; Space pause;\n"
        << "O display; T tracks; V audio output; M mute; B folders; H help; Esc browser; Q exit.\n";
}

lv_obj_t* box(lv_obj_t* parent, int x, int y, int w, int h, std::uint32_t color, lv_opa_t opacity = LV_OPA_COVER) {
    auto* obj = lv_obj_create(parent);
    lv_obj_set_pos(obj, x, y); lv_obj_set_size(obj, w, h);
    lv_obj_remove_flag(obj, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(obj, lv_color_hex(color), 0); lv_obj_set_style_bg_opa(obj, opacity, 0);
    lv_obj_set_style_border_width(obj, 0, 0); lv_obj_set_style_radius(obj, 0, 0); lv_obj_set_style_pad_all(obj, 0, 0);
    return obj;
}
lv_obj_t* label(lv_obj_t* parent, const std::string& text, int x, int y, int w, std::uint32_t color = white, bool large = false) {
    auto* obj = lv_label_create(parent);
    lv_obj_set_pos(obj, x, y); lv_obj_set_width(obj, w);
    lv_label_set_long_mode(obj, LV_LABEL_LONG_MODE_DOTS);
    const auto* font = large ? &lv_font_montserrat_14 : &lv_font_montserrat_12;
    lv_obj_set_style_text_font(obj, font, 0);
    lv_obj_set_height(obj, lv_font_get_line_height(font));
    lv_obj_set_style_text_color(obj, lv_color_hex(color), 0);
    // DOTS temporarily truncates LVGL's own string. Keep the full value so an
    // unchanged long filename does not reset and redraw on every UI iteration.
    auto* original = new std::string(text);
    lv_obj_set_user_data(obj, original);
    lv_obj_add_event_cb(obj, [](lv_event_t* event) { delete static_cast<std::string*>(lv_event_get_user_data(event)); }, LV_EVENT_DELETE, original);
    lv_label_set_text(obj, text.c_str());
    return obj;
}
void set_label(lv_obj_t* obj, const std::string& value) {
    if(!obj) return;
    auto* original = static_cast<std::string*>(lv_obj_get_user_data(obj));
    if(value != *original) { *original = value; lv_label_set_text(obj, value.c_str()); }
}

enum class Page { Browser, Player, Menu, Error };
enum class MenuAction { AudioTrack, SubtitleTrack, AudioSink, Folder, None };
struct MenuItem {
    std::string text;
    MenuAction action = MenuAction::None;
    int index = -1;
    std::string value;
    MenuItem(std::string text, MenuAction action = MenuAction::None, int index = -1, std::string value = {})
        : text(std::move(text)), action(action), index(index), value(std::move(value)) {}
};

struct App {
    explicit App(const Options& options) : options(options), browser(options.media_root) {}
    Options options;
    Browser browser;
    std::unique_ptr<Playback> playback;
    Status status;
    Page page = Page::Browser, previous_page = Page::Browser;
    bool preview = false, rebuild = true, controls = true;
    std::string title, error, seen_error, local_notice;
    std::vector<MenuItem> items;
    std::size_t selection = 0;
    std::uint32_t controls_at = 0, notice_at = 0;
    lv_obj_t *root = nullptr, *image = nullptr, *top = nullptr, *bottom = nullptr;
    lv_obj_t *heading = nullptr, *subtitle = nullptr, *footer = nullptr, *message = nullptr, *timing = nullptr, *volume = nullptr, *bar = nullptr;
    lv_obj_t* rows[row_count]{};
    FrameImage frame_image;
    std::uint64_t lcd_frame_assignments = 0;

    void notice(const std::string& value) { local_notice = value; notice_at = tick_ms(); controls = true; controls_at = tick_ms(); }
    void change_page(Page next) { page = next; rebuild = true; controls = true; controls_at = tick_ms(); }
    void show_error(const std::string& value) { error = value; seen_error = value; change_page(Page::Error); }

    static std::string matching_srt(const fs::path& file) {
        std::error_code ec;
        for(fs::directory_iterator it(file.parent_path(), ec), end; !ec && it != end; it.increment(ec)) {
            const auto candidate = it->path();
            auto extension = candidate.extension().string();
            std::transform(extension.begin(), extension.end(), extension.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
            if(extension == ".srt" && candidate.stem() == file.stem() && it->is_regular_file(ec)) return candidate.string();
        }
        return {};
    }

    void start_file(const fs::path& path) {
        if(!playback) return;
        auto file = fs::absolute(path);
        seen_error.clear(); error.clear(); local_notice.clear();
        const auto subtitles = matching_srt(file);
        if(!playback->load(file.string(), subtitles)) {
            const auto first = playback->status();
            if(first.output == Output::Hdmi && playback->set_output(Output::Lcd) && playback->load(file.string(), subtitles)) {
                notice("HDMI unavailable; playing on LCD");
            } else { show_error(playback->status().error.empty() ? first.error : playback->status().error); return; }
        }
        status = playback->status(); change_page(Page::Player);
    }
    void back_to_browser() {
        if(playback) playback->stop();
        frame_image.clear(image);
        status = {}; seen_error.clear(); error.clear(); local_notice.clear();
        change_page(Page::Browser);
    }
    void show_menu(const std::string& menu_title, std::vector<MenuItem> menu_items) {
        if(page != Page::Menu) previous_page = page;
        title = menu_title; items = std::move(menu_items); selection = 0; change_page(Page::Menu);
    }
    static std::string track_name(const Track& track) {
        std::string text = track.title;
        if(text.empty()) text = track.language.empty() ? "Track " + std::to_string(track.index + 1) : track.language;
        if(!track.codec.empty()) text += " / " + track.codec;
        if(track.external) text += " (SRT)";
        return display_text(text);
    }
    void tracks() {
        std::vector<MenuItem> result;
        for(const auto& track : status.audio_tracks) result.push_back({std::string(status.audio_track == track.index ? "* Audio: " : "  Audio: ") + track_name(track), MenuAction::AudioTrack, track.index, {}});
        result.push_back({std::string(status.subtitle_track < 0 ? "* " : "  ") + "Subtitles: Off", MenuAction::SubtitleTrack, -1, {}});
        for(const auto& track : status.subtitle_tracks) result.push_back({std::string(status.subtitle_track == track.index ? "* Subs: " : "  Subs: ") + track_name(track), MenuAction::SubtitleTrack, track.index, {}});
        show_menu("AUDIO & SUBTITLES", std::move(result));
    }
    void audio_sinks() {
        std::vector<MenuItem> result = {{"Automatic (match video output)", MenuAction::AudioSink, -1, ""}};
        for(const auto& sink : inspect_outputs().audio) {
            if(sink.available) result.push_back({display_text(sink.description.empty() ? sink.name : sink.description), MenuAction::AudioSink, -1, sink.name});
        }
        show_menu("SOUND OUTPUT", std::move(result));
    }
    void folders() {
        std::vector<MenuItem> result;
        for(const auto& path : Browser::media_roots()) result.push_back({display_text(path.string()), MenuAction::Folder, -1, path.string()});
        show_menu("FOLDERS & MOUNTED MEDIA", std::move(result));
    }
    void help() {
        show_menu("KEY GUIDE", {
            {"Enter: Open / confirm"}, {"Space: Pause / resume"}, {"A/D or Left/Right: Seek 10s"},
            {"W/S or Up/Down: Volume"}, {"O: LCD / HDMI video"}, {"T: Audio / subtitle tracks"},
            {"V: Sound output   M: Mute"}, {"B: Folders / mounted media"}, {"Esc: Browser   Q: Exit"}});
    }
    void confirm_menu() {
        if(selection >= items.size()) return;
        const auto item = items[selection];
        bool ok = true;
        if(item.action == MenuAction::Folder) {
            if(playback) playback->stop();
            if(browser.open_directory(item.value)) change_page(Page::Browser); else show_error(browser.error());
            return;
        }
        if(playback) {
            if(item.action == MenuAction::AudioTrack) ok = playback->set_audio_track(item.index);
            if(item.action == MenuAction::SubtitleTrack) ok = playback->set_subtitle_track(item.index);
            if(item.action == MenuAction::AudioSink) ok = playback->set_audio_sink(item.value);
            status = playback->status();
        }
        change_page(previous_page);
        if(!ok) notice(status.error.empty() ? "Selection unavailable" : status.error);
    }
    void key(std::uint32_t code) {
        if(code == 'q' || code == 'Q') { running = 0; return; }
        controls = true; controls_at = tick_ms();
        const bool up = code == LV_KEY_UP || code == 'w' || code == 'W';
        const bool down = code == LV_KEY_DOWN || code == 's' || code == 'S';
        const bool left = code == LV_KEY_LEFT || code == 'a' || code == 'A';
        const bool right = code == LV_KEY_RIGHT || code == 'd' || code == 'D';
        if(page == Page::Menu) {
            if(up && selection) --selection;
            if(down && selection + 1 < items.size()) ++selection;
            if(code == LV_KEY_ENTER) confirm_menu();
            if(code == LV_KEY_ESC || left) change_page(previous_page);
            return;
        }
        if(page == Page::Error) {
            if(code == LV_KEY_ESC || code == LV_KEY_ENTER) back_to_browser();
            return;
        }
        if(code == 'h' || code == 'H') { help(); return; }
        if(code == 'b' || code == 'B') { folders(); return; }
        if(page == Page::Browser) {
            if(up) browser.move(-1);
            if(down) browser.move(1);
            if(left) {
                if(!browser.open_directory(browser.directory().parent_path())) notice(browser.error());
            }
            if(code == LV_KEY_ENTER || right) {
                const auto* selected = browser.selected();
                if(selected) {
                    if(selected->directory) { if(!browser.enter_selected()) notice(browser.error()); }
                    else start_file(selected->path);
                }
            }
            if(code == LV_KEY_ESC) running = 0;
            return;
        }
        if(!playback) return;
        if(code == LV_KEY_ESC) { back_to_browser(); return; }
        if(code == ' ' || code == LV_KEY_ENTER) {
            playback->toggle_pause();
        }
        if(left) playback->seek_relative(-10);
        if(right) playback->seek_relative(10);
        if(up) playback->set_volume(status.volume + 0.05);
        if(down) playback->set_volume(status.volume - 0.05);
        if(code == 'm' || code == 'M') playback->set_muted(!status.muted);
        if(code == 'o' || code == 'O') {
            const auto output = status.output == Output::Hdmi || status.output_switching ? Output::Lcd : Output::Hdmi;
            if(!playback->set_output(output)) {
                const auto failed = playback->status(); seen_error = failed.error;
                notice(!failed.error.empty() ? failed.error : !failed.notice.empty() ? failed.notice : "HDMI is unavailable");
            }
            rebuild = true;
        }
        if(code == 't' || code == 'T') tracks();
        if(code == 'v' || code == 'V') audio_sinks();
        status = playback->status();
    }

    void build() {
        frame_image.clear(image);
        if(root) lv_obj_delete(root);
        root = box(lv_screen_active(), 0, 0, width, height, background);
        image = top = bottom = heading = subtitle = footer = message = timing = volume = bar = nullptr;
        for(auto& row : rows) row = nullptr;
        if(page == Page::Browser) {
            label(root, "TYRELL", 9, 6, 96, accent, true);
            label(root, "VIDEO", 248, 7, 63, quiet);
            subtitle = label(root, "", 9, 26, 302, quiet);
            for(int i = 0; i < row_count; ++i) rows[i] = label(root, "", 12, 43 + i * 18, 296);
            footer = label(root, "Enter Play   B Folders   H Help   Q Exit", 9, 151, 304, accent);
            message = label(root, "", 9, 135, 302, quiet);
        } else if(page == Page::Player) {
            if(status.output == Output::Lcd) {
                image = lv_image_create(root);
                lv_obj_set_pos(image, 0, 0);
            } else {
                label(root, "PLAYING ON HDMI", 12, 43, 296, accent, true);
                message = label(root, "Video is on the external display", 12, 68, 296, quiet);
                label(root, "O LCD    T Tracks    V Sound    H Help", 12, 90, 296, quiet);
            }
            top = box(root, 0, 0, width, 26, background, LV_OPA_90);
            heading = label(top, "", 9, 6, 302, white);
            bottom = box(root, 0, 118, width, 52, background, LV_OPA_90);
            bar = box(bottom, 9, 0, 1, 3, accent);
            timing = label(bottom, "", 9, 9, 210, white);
            volume = label(bottom, "", 225, 9, 87, accent);
            subtitle = label(bottom, "", 9, 26, 302, quiet);
            footer = label(bottom, "Space Pause   T Tracks   Esc Files", 9, 39, 302, accent);
        } else if(page == Page::Menu) {
            heading = label(root, title, 9, 8, 302, accent, true);
            for(int i = 0; i < row_count; ++i) rows[i] = label(root, "", 12, 37 + i * 21, 296);
            footer = label(root, "Up/Down Select   Enter OK   Esc Back", 9, 151, 302, accent);
        } else {
            label(root, "PLAYBACK UNAVAILABLE", 10, 10, 300, 0xffbc85, true);
            message = label(root, display_text(error), 10, 39, 300, white);
            lv_label_set_long_mode(message, LV_LABEL_LONG_MODE_WRAP);
            lv_obj_set_height(message, 88);
            label(root, "Try another file or video conversion.", 10, 130, 300, quiet);
            label(root, "Enter / Esc Files              Q Exit", 10, 151, 300, accent);
        }
        rebuild = false;
    }
    void draw_preview_video() {
        auto frame = std::make_shared<Frame>();
        frame->pixels.resize(width * height * 2);
        for(int y = 0; y < height; ++y) for(int x = 0; x < width; ++x) {
            const unsigned r = 12 + static_cast<unsigned>(x * 45 / width), g = 35 + static_cast<unsigned>(y * 60 / height), b = 54 + static_cast<unsigned>((x + y) * 50 / (width + height));
            const std::uint16_t color = static_cast<std::uint16_t>(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3));
            const auto n = static_cast<std::size_t>((y * width + x) * 2);
            frame->pixels[n] = color & 255; frame->pixels[n + 1] = color >> 8;
        }
        frame_image.show(image, std::move(frame));
        label(root, "Video preview", 12, 66, 296, 0xb8e2df, true);
        label(root, "Software-rendered example", 12, 87, 296, quiet);
        lv_obj_move_foreground(top); lv_obj_move_foreground(bottom);
    }
    void render() {
        if(rebuild) { build(); if(preview && page == Page::Player && image) draw_preview_video(); }
        if(page == Page::Browser) {
            set_label(subtitle, preview ? "/home/user/Videos" : display_text(browser.directory().string()));
            const std::vector<std::string> fixtures = {"[DIR] ..", "[DIR] Short films", "Sintel - trailer.mp4", "Big Buck Bunny.mkv", "Travel diary.webm"};
            const auto index = preview ? std::size_t(2) : browser.index();
            const auto start = index / row_count * row_count;
            for(int i = 0; i < row_count; ++i) {
                const auto at = start + static_cast<std::size_t>(i);
                std::string value;
                if(preview) value = at < fixtures.size() ? fixtures[at] : "";
                else if(at < browser.entries().size()) { const auto& entry = browser.entries()[at]; value = (entry.directory ? "[DIR] " : "") + display_text(entry.name); }
                set_label(rows[i], (at == index && !value.empty() ? "> " : "  ") + value);
                lv_obj_set_style_text_color(rows[i], lv_color_hex(at == index ? accent : white), 0);
            }
            if(!preview && browser.entries().empty()) set_label(rows[0], "No videos here. Press B for folders.");
            set_label(message, !local_notice.empty() ? display_text(local_notice) : (!browser.error().empty() ? browser.error() : ""));
        } else if(page == Page::Player) {
            set_label(heading, display_text(fs::path(status.path).filename().string()));
            const auto state = status.output_switching ? "HDMI setup" : status.buffering ? "Loading" : status.playing ? "Playing" : "Paused";
            set_label(timing, std::string(state) + "  " + format_time(status.position) + " / " + format_time(status.duration));
            set_label(volume, status.muted ? "Muted" : "Vol " + std::to_string(static_cast<int>(status.volume * 100 + 0.5)) + "%");
            const auto note = !local_notice.empty() ? local_notice : !status.notice.empty() ? status.notice : status.output == Output::Hdmi ? "HDMI video / handheld controls" : "A/D Seek 10s    W/S Volume    O HDMI";
            set_label(subtitle, display_text(note));
            const auto progress = status.duration > 0 ? std::clamp(status.position / status.duration, 0.0, 1.0) : 0;
            lv_obj_set_width(bar, std::max(1, static_cast<int>(302 * progress)));
            if(!preview && image && playback) {
                const auto frame = playback->latest_frame();
                if(frame_image.show(image, frame)) {
                    ++lcd_frame_assignments;
                    lv_obj_set_pos(image, (width - frame->width) / 2, (height - frame->height) / 2);
                }
            }
            const bool show = preview || controls || status.output == Output::Hdmi || status.output_switching || status.buffering || !status.loaded || !status.error.empty();
            if(show) { lv_obj_remove_flag(top, LV_OBJ_FLAG_HIDDEN); lv_obj_remove_flag(bottom, LV_OBJ_FLAG_HIDDEN); }
            else { lv_obj_add_flag(top, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(bottom, LV_OBJ_FLAG_HIDDEN); }
        } else if(page == Page::Menu) {
            const auto start = selection / row_count * row_count;
            for(int i = 0; i < row_count; ++i) {
                const auto at = start + static_cast<std::size_t>(i);
                set_label(rows[i], at < items.size() ? std::string(at == selection ? "> " : "  ") + items[at].text : "");
                lv_obj_set_style_text_color(rows[i], lv_color_hex(at == selection ? accent : white), 0);
            }
        }
    }
    void poll() {
        if(playback) {
            playback->poll();
            const auto fresh = playback->status();
            if(fresh.output != status.output && page == Page::Player) rebuild = true;
            if(fresh.output != status.output || fresh.playing != status.playing) {
                controls = true; controls_at = tick_ms();
            }
            status = fresh;
            if(!status.error.empty() && status.error != seen_error) show_error(status.error);
        }
        if(!local_notice.empty() && tick_ms() - notice_at > 6000) local_notice.clear();
        if(tick_ms() - controls_at > 4000) controls = false;
    }
};

#if defined(__linux__)
class Keyboard {
public:
    ~Keyboard() { if(fd_ >= 0) close(fd_); }
    bool open_device(const std::string& requested) {
        if(!requested.empty()) return open_path(requested, false);
        if(open_path("/dev/input/cardputer-zero-internal", false)) return true;
        std::error_code ec;
        for(fs::directory_iterator it("/dev/input", ec), end; !ec && it != end; it.increment(ec))
            if(it->path().filename().string().rfind("event", 0) == 0 && open_path(it->path().string(), true)) return true;
        return false;
    }
    void process(App& app) {
        input_event event{};
        while(read(fd_, &event, sizeof(event)) == sizeof(event)) {
            if(event.type != EV_KEY || (event.value != 1 && event.value != 2)) continue;
            std::uint32_t key = 0;
            bool repeatable = false;
            switch(event.code) {
                case KEY_ESC: key = LV_KEY_ESC; break;
                case KEY_ENTER: key = LV_KEY_ENTER; break;
                case KEY_SPACE: key = ' '; break;
                case KEY_UP: key = LV_KEY_UP; repeatable = true; break;
                case KEY_DOWN: key = LV_KEY_DOWN; repeatable = true; break;
                case KEY_LEFT: key = LV_KEY_LEFT; repeatable = true; break;
                case KEY_RIGHT: key = LV_KEY_RIGHT; repeatable = true; break;
                case KEY_W: key = 'w'; repeatable = true; break;
                case KEY_A: key = 'a'; repeatable = true; break;
                case KEY_S: key = 's'; repeatable = true; break;
                case KEY_D: key = 'd'; repeatable = true; break;
                case KEY_Q: key = 'q'; break; case KEY_O: key = 'o'; break;
                case KEY_T: key = 't'; break; case KEY_V: key = 'v'; break;
                case KEY_M: key = 'm'; break; case KEY_B: key = 'b'; break;
                case KEY_H: key = 'h'; break; default: break;
            }
            if(key && (event.value == 1 || repeatable)) app.key(key);
        }
    }
private:
    int fd_ = -1;
    bool open_path(const std::string& path, bool check_name) {
        const int fd = open(path.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if(fd < 0) return false;
        char name[256]{};
        if(check_name && (ioctl(fd, EVIOCGNAME(sizeof(name)), name) < 0 || std::string(name).find("tca8418") == std::string::npos)) { close(fd); return false; }
        fd_ = fd; return true;
    }
};
#endif

void write_ppm(const std::string& path, const std::vector<std::uint32_t>& pixels) {
    std::ofstream out(path, std::ios::binary);
    if(!out) throw std::runtime_error("Cannot open screenshot output: " + path);
    out << "P6\n" << width << ' ' << height << "\n255\n";
    for(const auto pixel : pixels) { const char rgb[] = {static_cast<char>(pixel >> 16), static_cast<char>(pixel >> 8), static_cast<char>(pixel)}; out.write(rgb, 3); }
    out.close();
    if(!out) throw std::runtime_error("Failed writing screenshot: " + path);
}

std::pair<double, double> cpu_times() {
#if defined(__unix__) || defined(__APPLE__)
    struct rusage usage{};
    if(getrusage(RUSAGE_SELF, &usage) == 0)
        return {usage.ru_utime.tv_sec + usage.ru_utime.tv_usec / 1000000.0,
                usage.ru_stime.tv_sec + usage.ru_stime.tv_usec / 1000000.0};
#endif
    return {static_cast<double>(std::clock()) / CLOCKS_PER_SEC, 0};
}

void write_report(const App& app, double elapsed, const std::pair<double, double>& initial_cpu, unsigned iterations,
                  const FramebufferCommitStats& display_stats) {
    if(app.options.report.empty()) return;
    const auto& s = app.status;
    const auto current_cpu = cpu_times();
    const auto cpu_user_seconds = std::max(0.0, current_cpu.first - initial_cpu.first);
    const auto cpu_system_seconds = std::max(0.0, current_cpu.second - initial_cpu.second);
    const auto cpu_seconds = cpu_user_seconds + cpu_system_seconds;
    std::ofstream out(app.options.report);
    if(!out) throw std::runtime_error("Cannot open report output: " + app.options.report);
    long long max_rss_kib = 0;
#if defined(__unix__) || defined(__APPLE__)
    struct rusage usage{};
    if(getrusage(RUSAGE_SELF, &usage) == 0) {
        max_rss_kib = usage.ru_maxrss;
#if defined(__APPLE__)
        max_rss_kib /= 1024;
#endif
    }
#endif
    out << "{\"application\":\"tyrell-video\",\"version\":" << json_string(TYRELL_VERSION)
        << ",\"source_revision\":" << json_string(TYRELL_REVISION)
        << ",\"elapsed_seconds\":" << elapsed << ",\"cpu_seconds\":" << cpu_seconds
        << ",\"cpu_user_seconds\":" << cpu_user_seconds << ",\"cpu_system_seconds\":" << cpu_system_seconds
        << ",\"cpu_percent_one_core\":" << (elapsed > 0 ? cpu_seconds / elapsed * 100 : 0)
        << ",\"max_rss_kib\":" << max_rss_kib << ",\"main_loop_iterations\":" << iterations
        << ",\"lcd_frame_assignments\":" << app.lcd_frame_assignments
        << ",\"lcd_completed_redraws\":" << display_stats.completed_frames
        << ",\"lcd_fsync_calls\":" << display_stats.fsync_calls
        << ",\"lcd_fsync_seconds\":" << display_stats.fsync_total_seconds
        << ",\"lcd_fsync_max_seconds\":" << display_stats.fsync_max_seconds
        << ",\"lcd_fsync_error\":" << json_string(display_stats.error)
        << ",\"lcd_redraw_scope\":\"Completed LVGL redraws and kernel commit requests, not physical panel scanout\""
        << ",\"measurement_note\":\"LCD frame assignments count unique frames given to LVGL; they do not measure physical panel refresh or audio/video timing\""
        << ",\"headless_preview\":" << (app.preview ? "true" : "false")
        << ",\"path\":" << json_string(s.path) << ",\"output\":" << json_string(s.output == Output::Hdmi ? "hdmi" : "lcd")
        << ",\"playing\":" << (s.playing ? "true" : "false") << ",\"paused\":" << (s.paused ? "true" : "false")
        << ",\"position_seconds\":" << s.position << ",\"duration_seconds\":" << s.duration
        << ",\"pipeline_builds\":" << s.pipeline_builds
        << ",\"first_render_seconds\":" << s.first_render_seconds
        << ",\"first_hdmi_render_seconds\":" << s.first_hdmi_render_seconds
        << ",\"first_playing_seconds\":" << s.first_playing_seconds
        << ",\"startup_timing_scope\":\"Seconds since accepted file load; -1 means not observed; render calls do not measure physical scanout\""
        << ",\"source_width\":" << s.source_width << ",\"source_height\":" << s.source_height
        << ",\"received_frames\":" << s.received_frames << ",\"qos_processed\":" << s.qos_processed
        << ",\"video_buffers_delivered\":" << s.video_buffers_delivered
        << ",\"lcd_caption_compositions\":" << s.lcd_caption_compositions
        << ",\"video_frames_decoded\":" << s.video_frames_decoded
        << ",\"video_sink_rendered\":" << s.video_sink_rendered
        << ",\"video_sink_dropped\":" << s.video_sink_dropped
        << ",\"video_output_memory\":" << json_string(s.video_output_memory)
        << ",\"video_output_caps\":" << json_string(s.video_output_caps)
        << ",\"sink_stats_scope\":\"GstBaseSink render calls/drops since its most recent reset, not physical scanout\""
        << ",\"qos_dropped\":" << s.dropped_frames << ",\"video_decoder\":" << json_string(s.video_decoder)
        << ",\"software_video_fallback\":" << (s.software_video_fallback ? "true" : "false")
        << ",\"video_decode_fallbacks\":" << s.video_decode_fallbacks
        << ",\"video_decode_fallback_reason\":" << json_string(s.video_decode_fallback_reason)
        << ",\"audio_output\":" << json_string(s.audio_output)
        << ",\"error\":" << json_string(s.error.empty() ? app.error : s.error) << ",\"notice\":" << json_string(s.notice) << "}\n";
    out.close();
    if(!out) throw std::runtime_error("Failed writing report: " + app.options.report);
}

int gui(const Options& options) {
    const auto measurement_start = std::chrono::steady_clock::now();
    const auto initial_cpu = cpu_times();
    const bool headless = !options.screenshot.empty();
    if(headless && options.simulator) throw std::runtime_error("Choose either headless preview or SDL simulator");
#if !TYRELL_ENABLE_SDL
    if(options.simulator) throw std::runtime_error("SDL simulator was not enabled in this build");
#endif
    App app(options);
    app.preview = headless;
#if defined(__linux__)
    Keyboard keyboard;
    if(!headless && !options.simulator && !keyboard.open_device(options.input)) throw std::runtime_error("Cannot open Cardputer keyboard; use --input PATH or --diagnostics");
#endif
    lv_init(); lv_tick_set_cb(tick_ms);
    lv_display_t* display = nullptr;
    FramebufferCommit framebuffer_commit;
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
        if(display) { lv_sdl_window_set_title(display, "Tyrell Video / CardputerZero"); lv_sdl_window_set_zoom(display, 2.0f); }
    }
#endif
#if defined(__linux__)
    else {
        display = lv_linux_fbdev_create();
        if(display && lv_linux_fbdev_set_file(display, options.framebuffer.c_str()) != LV_RESULT_OK) { lv_display_delete(display); display = nullptr; }
    }
#endif
    if(!display) { lv_deinit(); throw std::runtime_error("Cannot initialize display; use --diagnostics or --headless-screenshot"); }
    if(lv_display_get_horizontal_resolution(display) != width || lv_display_get_vertical_resolution(display) != height) {
        lv_display_delete(display); lv_deinit(); throw std::runtime_error("Expected a 320x170 LCD display");
    }
#if defined(__linux__)
    if(!headless && !options.simulator && !framebuffer_commit.attach(display, options.framebuffer))
        std::cerr << framebuffer_commit.stats().error << '\n';
#endif
    if(headless) {
        app.status.path = "Sintel - trailer.mp4"; app.status.position = 45; app.status.duration = 148; app.status.volume = 0.65; app.status.playing = true; app.status.loaded = true;
        if(options.page == "player" || options.page == "hdmi") { app.page = Page::Player; app.status.output = options.page == "hdmi" ? Output::Hdmi : Output::Lcd; }
        if(options.page == "tracks") {
            app.status.audio_track = 0; app.status.subtitle_track = -1;
            app.status.audio_tracks = {{0, "English", "en", "AAC", false}, {1, "Commentary", "en", "AAC", false}};
            app.status.subtitle_tracks = {{0, "English", "en", "UTF-8", false}, {1, "Sintel - trailer", "", "SRT", true}};
            app.tracks();
        }
        if(options.page == "error") app.show_error("This file needs a video decoder that is not installed, or could not be opened.");
    } else {
        app.playback = std::make_unique<Playback>(PlaybackOptions{false, !options.simulator, !options.simulator});
        if(options.output == "hdmi") app.playback->set_output(Output::Hdmi);
        else if(options.output == "auto" && !options.simulator) {
            const auto inventory = inspect_outputs(false);
            if(std::any_of(inventory.hdmi.begin(), inventory.hdmi.end(), [](const auto& output) { return output.connected && !output.modes.empty(); })) app.playback->set_output(Output::Hdmi);
        }
        if(!options.file.empty()) app.start_file(options.file);
        if(!app.browser.error().empty()) app.notice(app.browser.error());
    }
#if TYRELL_ENABLE_SDL
    if(options.simulator) {
        auto* input = lv_sdl_keyboard_create(); auto* group = lv_group_create();
        lv_group_add_obj(group, lv_screen_active()); lv_group_focus_obj(lv_screen_active()); lv_indev_set_group(input, group);
        lv_obj_add_event_cb(lv_screen_active(), [](lv_event_t* event) { static_cast<App*>(lv_event_get_user_data(event))->key(lv_event_get_key(event)); }, LV_EVENT_KEY, &app);
    }
#endif
    std::signal(SIGINT, stop_signal); std::signal(SIGTERM, stop_signal);
    const auto limit = options.frames ? options.frames : headless ? 2u : 0u;
    const auto start_time = std::chrono::steady_clock::now();
    unsigned iterations = 0;
    for(unsigned frame = 0; running && (!limit || frame < limit); ++frame) {
        if(options.run_seconds > 0 && std::chrono::duration<double>(std::chrono::steady_clock::now() - start_time).count() >= options.run_seconds) break;
#if defined(__linux__)
        if(!headless && !options.simulator) keyboard.process(app);
#endif
        if(!headless) app.poll();
        app.render();
        lv_timer_handler();
        ++iterations;
        if(headless) lv_refr_now(display); else std::this_thread::sleep_for(std::chrono::milliseconds(8));
    }
    const auto elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - measurement_start).count();
    if(headless) write_ppm(options.screenshot, pixels);
    write_report(app, elapsed, initial_cpu, iterations, framebuffer_commit.stats());
    if(app.playback) app.playback->stop();
    app.frame_image.clear(app.image);
    lv_display_delete(display); lv_deinit();
    return 0;
}
} // namespace

int main(int argc, char** argv) {
    try {
        Options options;
        bool positional = false;
        for(int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            auto value = [&]() -> std::string { if(i + 1 >= argc) throw std::runtime_error("Missing value for " + arg); return argv[++i]; };
            if(!positional && (arg == "--help" || arg == "-h")) { usage(); return 0; }
            if(!positional && arg == "--version") { std::cout << "tyrell-video " TYRELL_VERSION " (" TYRELL_REVISION ")\n"; return 0; }
            if(!positional && arg == "--") positional = true;
            else if(!positional && arg == "--media-root") options.media_root = value();
            else if(!positional && arg == "--output") { options.output = value(); if(options.output != "auto" && options.output != "lcd" && options.output != "hdmi") throw std::runtime_error("--output must be auto, lcd or hdmi"); }
            else if(!positional && arg == "--diagnostics") options.diagnostics = true;
            else if(!positional && arg == "--framebuffer") options.framebuffer = value();
            else if(!positional && arg == "--input") options.input = value();
            else if(!positional && arg == "--simulator") options.simulator = true;
            else if(!positional && arg == "--headless-screenshot") options.screenshot = value();
            else if(!positional && arg == "--report") options.report = value();
            else if(!positional && arg == "--run-seconds") {
                const auto raw = value(); std::size_t consumed = 0;
                options.run_seconds = std::stod(raw, &consumed);
                if(consumed != raw.size() || !std::isfinite(options.run_seconds) || options.run_seconds <= 0 || options.run_seconds > 86400) throw std::runtime_error("--run-seconds must be greater than 0 and at most 86400");
            }
            else if(!positional && arg == "--page") {
                options.page = value();
                if(options.page != "browser" && options.page != "player" && options.page != "hdmi" && options.page != "tracks" && options.page != "error") throw std::runtime_error("Unknown preview page");
            } else if(!positional && arg == "--frames") {
                const auto raw = value(); std::size_t consumed = 0; const auto n = std::stoul(raw, &consumed);
                if(consumed != raw.size() || !n || n > 1000000) throw std::runtime_error("--frames must be between 1 and 1000000");
                options.frames = static_cast<unsigned>(n);
            } else if(!positional && !arg.empty() && arg.front() == '-') throw std::runtime_error("Unknown option: " + arg);
            else if(options.file.empty()) options.file = arg;
            else throw std::runtime_error("Only one video file may be specified");
        }
        if(options.diagnostics) { Playback engine; std::cout << engine.diagnostics_json() << '\n'; return 0; }
        return gui(options);
    } catch(const std::exception& error) { std::cerr << "tyrell-video: " << error.what() << '\n'; return 1; }
}
