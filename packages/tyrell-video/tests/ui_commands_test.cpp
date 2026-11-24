// Exercise the real controller and LVGL tree with fixed UI time. No framebuffer,
// audio server, HDMI helper or media decoder is used by these UI fixtures.
#define main video_application_main
#include "../src/main.cpp"
#undef main

#include <set>

namespace {
void require(bool ok, const std::string& message) { if(!ok) throw std::runtime_error(message); }
std::uint32_t fake_now = 100;
std::uint32_t fake_tick() { return fake_now; }
std::vector<std::uint32_t> pixels(width * height);
lv_display_t* display = nullptr;

void bounds(lv_obj_t* object) {
    if(lv_obj_has_flag(object, LV_OBJ_FLAG_HIDDEN)) return;
    lv_area_t area{}; lv_obj_get_coords(object, &area);
    require(area.x1 >= 0 && area.y1 >= 0 && area.x2 < width && area.y2 < height,
            "Visible UI object escaped 320x170 at " + std::to_string(area.x1) + "," + std::to_string(area.y1));
    for(std::uint32_t n = 0; n < lv_obj_get_child_count(object); ++n) bounds(lv_obj_get_child(object, n));
}
void layout_json(std::ostream& out, lv_obj_t* object, bool& first) {
    if(lv_obj_has_flag(object, LV_OBJ_FLAG_HIDDEN)) return;
    lv_area_t area{}; lv_obj_get_coords(object, &area);
    if(!first) out << ',';
    first = false;
    out << "{\"x\":" << area.x1 << ",\"y\":" << area.y1
        << ",\"width\":" << lv_obj_get_width(object) << ",\"height\":" << lv_obj_get_height(object);
    if(lv_obj_check_type(object, &lv_label_class)) {
        const auto* original = static_cast<std::string*>(lv_obj_get_user_data(object));
        out << ",\"text\":" << json_string(original ? *original : lv_label_get_text(object))
            << ",\"long_mode\":" << static_cast<int>(lv_label_get_long_mode(object));
    }
    out << '}';
    for(std::uint32_t n = 0; n < lv_obj_get_child_count(object); ++n) layout_json(out, lv_obj_get_child(object, n), first);
}
void render(App& app, const std::string& name = {}) {
    app.render(); lv_obj_update_layout(app.root); lv_refr_now(display); bounds(app.root);
    if(!name.empty()) {
        fs::create_directories(U1_PREVIEW_DIR);
        write_ppm((fs::path(U1_PREVIEW_DIR) / (name + ".ppm")).string(), pixels);
        std::ofstream report(fs::path(U1_PREVIEW_DIR) / (name + ".json"));
        report << "{\"width\":320,\"height\":170,\"ui_time_ms\":" << fake_now << ",\"objects\":[";
        bool first = true; layout_json(report, app.root, first); report << "]}\n";
        require(bool(report), "Could not write semantic layout report");
    }
}
void dispose(App& app) {
    app.frame_image.clear(app.image);
    if(app.root) lv_obj_delete(app.root);
    app.root = nullptr;
}
std::size_t entry(const App& app, const std::string& name) {
    for(std::size_t i = 0; i < app.browser.entries().size(); ++i)
        if(app.browser.entries()[i].name == name) return i;
    throw std::runtime_error("Missing test browser entry " + name);
}
void metadata() {
    for(const auto page : {Page::Browser, Page::Player, Page::Menu, Page::Error})
        for(bool engine : {false, true}) for(bool available : {false, true}) {
            const auto commands = commands_for(page, engine, available, available);
            const auto errors = tyrell::app::command_errors(commands);
            require(errors.empty(), errors.empty() ? "" : errors.front());
            const auto help = tyrell::app::help_lines(commands);
            for(const auto& command : commands) {
                for(const auto key : command.keys) {
                    const auto* match = tyrell::app::find_command(commands, key);
                    require(match && match->id == command.id, "A help command's binding does not resolve uniquely");
                }
                if(command.enabled)
                    require(std::find(help.begin(), help.end(), tyrell::app::help_line(command)) != help.end(),
                            "Enabled action omitted from contextual help");
                else
                    require(std::find(help.begin(), help.end(), tyrell::app::help_line(command)) == help.end(),
                            "Unavailable action advertised as enabled");
                if(command.confirmation == tyrell::app::Confirmation::Required)
                    require(!tyrell::app::find_command(commands, command.keys.front(), true),
                            "Repeated input can confirm a selection");
            }
        }
}
void browser_and_help(const fs::path& directory) {
    Options options; options.media_root = directory;
    App app(options, fake_tick);
    const std::string filename(95, 'v');
    std::ofstream(directory / (filename + ".mp4")) << "UI fixture, not media";
    app.browser.open_directory(directory);
    app.browser.select(entry(app, filename + ".mp4"));
    render(app, "video-browser-long-name");
    const auto index = app.browser.index();
    const auto slot = index % row_count;
    require(static_cast<std::string*>(lv_obj_get_user_data(app.rows[slot]))->find(filename) != std::string::npos,
            "Truncated display text discarded the actual filename");
    app.key('h');
    require(app.page == Page::Menu && app.title == "KEY GUIDE", "H did not open contextual help");
    const auto expected = tyrell::app::help_lines(commands_for(Page::Browser, false, true, false));
    require(app.items.size() == expected.size(), "Help was not built from current command definitions");
    for(std::size_t i = 0; i < expected.size(); ++i) require(app.items[i].text == expected[i], "Help row drifted from its action");
    for(std::size_t i = 0; i < app.items.size(); ++i) {
        app.selection = i; render(app);
        for(auto* row : app.rows) {
            const auto& text = *static_cast<std::string*>(lv_obj_get_user_data(row));
            lv_point_t measured{};
            lv_text_get_size(&measured, text.c_str(), &lv_font_montserrat_12, 0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
            require(measured.x <= lv_obj_get_width(row), "An actionable help row was silently truncated");
        }
    }
    app.selection = 0; render(app, "video-browser-help");
    app.key(LV_KEY_ENTER, true);
    require(app.page == Page::Menu, "Held Enter closed help");
    app.key(LV_KEY_ENTER);
    require(app.page == Page::Browser, "A fresh Enter no longer closes help");
    dispose(app);
}
void time_and_render(const fs::path& directory) {
    Options options; options.media_root = directory;
    App app(options, fake_tick);
    app.status.loaded = true; app.status.path = std::string(160, 'p') + ".mp4";
    app.status.duration = 120; app.status.position = 45;
    app.change_page(Page::Player);
    render(app, "video-player-no-engine");
    const auto same = pixels;
    render(app); require(same == pixels, "Unchanged fixed-time UI produced a different frame");
    fake_now = 4100; app.poll(); render(app);
    require(!lv_obj_has_flag(app.bottom, LV_OBJ_FLAG_HIDDEN), "Controls hid before their exact deadline");
    fake_now = 4101; app.poll(); render(app);
    require(lv_obj_has_flag(app.bottom, LV_OBJ_FLAG_HIDDEN), "Controls did not hide after their deadline");
    app.notice("A very long, deliberately clipped output notice " + std::string(100, 'n'));
    render(app, "video-notice");
    fake_now += 6000; app.poll(); require(!app.local_notice.empty(), "Notice expired before its exact deadline");
    ++fake_now; app.poll(); require(app.local_notice.empty(), "Notice failed to expire under fake time");
    const std::string full_error = "Decoder unavailable: " + std::string(500, 'e');
    app.show_error(full_error); render(app, "video-error-long");
    require(app.error == full_error, "Fitting an error discarded the diagnostic");
    const auto text = *static_cast<std::string*>(lv_obj_get_user_data(app.message));
    lv_point_t measured{};
    lv_text_get_size(&measured, text.c_str(), &lv_font_montserrat_12, 0, 0, 300, LV_TEXT_FLAG_NONE);
    require(measured.y <= 88 && text.find("...") != std::string::npos, "Long error is clipped without a fitted ellipsis");
    std::string accented;
    for(int n = 0; n < 300; ++n) accented += "caf\xc3\xa9 ";
    const auto fitted = wrapped_error(accented);
    require(display_text(fitted) == fitted, "Error fitting split a UTF-8 glyph");
    app.key(LV_KEY_ESC); require(app.page == Page::Browser, "Error recovery binding changed");
    app.status.audio_tracks = {{0, std::string(100, 't'), "en", "AAC", false}};
    app.status.subtitle_tracks = {{1, std::string(100, 's'), "en", "SRT", true}};
    app.tracks(); render(app, "video-tracks-long");
    require(app.items[0].text.find(std::string(90, 't')) != std::string::npos, "Track display fitting discarded the selection identity");
    app.preview = true; app.status.loaded = true; app.status.playing = true;
    app.status.path = "Example movie.mp4"; app.status.duration = 120; app.status.position = 45;
    app.change_page(Page::Player); render(app, "video-player-controls");
    require(!static_cast<std::string*>(lv_obj_get_user_data(app.footer))->empty(),
            "Enabled player preview has no command footer");
    require(*static_cast<std::string*>(lv_obj_get_user_data(app.subtitle)) ==
                tyrell::app::footer(app.commands(), {Action::Seek, Action::Volume, Action::Output}),
            "Inline player hints drifted from the dispatch definitions");
    const auto player_help = tyrell::app::help_lines(app.commands());
    app.key('h');
    require(app.items.size() == player_help.size(), "Player help lost available playback controls");
    for(std::size_t i = 0; i < app.items.size(); ++i) {
        require(app.items[i].text == player_help[i], "Player help drifted from its descriptor");
        app.selection = i; render(app);
    }
    app.selection = 0; render(app, "video-player-help");
    dispose(app); fake_now = 100;
}
void dispatch_and_edges(const fs::path& directory) {
    Options options; options.media_root = directory;
    App app(options, fake_tick);
    app.playback = std::make_unique<Playback>(PlaybackOptions{true, false, false});
    app.page = Page::Player; app.status = app.playback->status();
    app.key('m'); require(app.status.muted, "Mute descriptor did not dispatch to playback");
    app.key('m', true); require(app.status.muted, "Mute autorepeated");
    const auto before = app.status.volume;
    app.key('w', true); require(app.status.volume > before, "Repeatable volume stopped working");
    app.key('o'); require(app.status.output == Output::Hdmi, "Display action did not preserve output toggle");
    app.key('o', true); require(app.status.output == Output::Hdmi, "Display toggle autorepeated");
    app.key('t'); require(app.page == Page::Menu, "Tracks action did not dispatch");
    app.key(LV_KEY_ENTER, true); require(app.page == Page::Menu, "Track confirmation autorepeated");
    app.key(LV_KEY_ESC); require(app.page == Page::Player, "Menu escape changed");
    InputEdges edges;
    app.change_page(Page::Browser); app.browser.open_directory(directory);
    app.browser.select(entry(app, "a"));
    auto send = [&](int physical, int logical, int value) {
        edges.event(physical, logical, value, app.input_epoch,
                    [&](int key, bool repeat) { app.key(key, repeat); });
    };
    send(1, LV_KEY_RIGHT, 1); require(app.browser.directory() == directory / "a", "Right no longer opens a directory");
    send(1, LV_KEY_RIGHT, 2); require(app.browser.directory() == directory / "a", "Held Right crossed a directory transition");
    send(2, LV_KEY_DOWN, 1); require(app.browser.selected()->name == "b", "Held navigation initial press failed");
    send(3, LV_KEY_ENTER, 1); require(app.browser.directory() == directory / "a/b", "Enter did not activate selected directory");
    send(2, LV_KEY_DOWN, 2); require(app.browser.index() == 0, "Held Down acquired a new context after Enter");
    send(2, LV_KEY_DOWN, 0); send(2, LV_KEY_DOWN, 1);
    require(app.browser.index() == 1, "A genuine second press was mistaken for autorepeat");
    edges.reset(); send(3, LV_KEY_ENTER, 2);
    require(app.browser.directory() == directory / "a/b", "Unknown held key was replayed after lost input");
    send(4, LV_KEY_LEFT, 1); send(4, LV_KEY_LEFT, 2);
    require(app.browser.directory() == directory, "Held parent-folder navigation stopped working");
    dispose(app);
}

#if defined(__linux__)
void raw_evdev(const fs::path& directory) {
    Options options; options.media_root = directory;
    App app(options, fake_tick); Keyboard keyboard;
    auto send = [&](int type, int code, int value) {
        input_event event{}; event.type = type; event.code = code; event.value = value;
        keyboard.process_event(app, event);
    };
    app.browser.select(entry(app, "a"));
    send(EV_KEY, KEY_RIGHT, 1); send(EV_KEY, KEY_RIGHT, 2);
    require(app.browser.directory() == directory / "a", "EV_KEY repeat opened multiple folders");
    send(EV_SYN, SYN_DROPPED, 0); send(EV_KEY, KEY_ENTER, 1);
    require(app.browser.directory() == directory / "a", "Dropped input was dispatched before resynchronization");
    send(EV_SYN, SYN_REPORT, 0); send(EV_KEY, KEY_RIGHT, 2);
    require(app.browser.directory() == directory / "a", "Unknown held key replayed after SYN_DROPPED");
    send(EV_KEY, KEY_RIGHT, 0); send(EV_KEY, KEY_RIGHT, 1);
    require(app.browser.directory() == directory, "Fresh press after resynchronization was lost");
}
#endif

#if TYRELL_ENABLE_SDL
void raw_sdl(const fs::path& directory) {
    require(SDL_Init(SDL_INIT_EVENTS) == 0, "Cannot initialize SDL event fixtures");
    {
        Options options; options.media_root = directory; App app(options, fake_tick);
        SdlKeyboard keyboard(17); app.browser.select(entry(app, "a"));
        auto send = [&](SDL_Keycode code, SDL_Scancode scan, bool repeated, bool release = false, std::uint32_t window = 17) {
            SDL_Event event{}; event.type = release ? SDL_KEYUP : SDL_KEYDOWN;
            event.key.windowID = window; event.key.keysym.sym = code; event.key.keysym.scancode = scan; event.key.repeat = repeated;
            require(SDL_PushEvent(&event) == 1, "SDL rejected a scripted keyboard event"); keyboard.process(app);
        };
        send(SDLK_RIGHT, SDL_SCANCODE_RIGHT, false); send(SDLK_RIGHT, SDL_SCANCODE_RIGHT, true);
        require(app.browser.directory() == directory / "a", "SDL repeat crossed activation context");
        send(SDLK_RIGHT, SDL_SCANCODE_RIGHT, false, true); send(SDLK_RIGHT, SDL_SCANCODE_RIGHT, false);
        require(app.browser.directory() == directory, "Second deliberate SDL tap was suppressed");
        const auto index = app.browser.index(); send(SDLK_DOWN, SDL_SCANCODE_DOWN, false, false, 99);
        require(app.browser.index() == index, "Input from another SDL window was dispatched");
        SDL_Event focus{}; focus.type = SDL_WINDOWEVENT; focus.window.windowID = 17;
        focus.window.event = SDL_WINDOWEVENT_FOCUS_LOST; SDL_PushEvent(&focus); keyboard.process(app);
        send(SDLK_RIGHT, SDL_SCANCODE_RIGHT, true);
        require(app.browser.directory() == directory, "Focus loss replayed a held SDL key");
        app.browser.select(entry(app, "a"));
        send(SDLK_KP_PLUS, SDL_SCANCODE_KP_PLUS, false);
        require(app.browser.directory() == directory / "a", "SDL keypad-plus alias was lost");
        send(SDLK_KP_MINUS, SDL_SCANCODE_KP_MINUS, false);
        require(app.browser.directory() == directory, "SDL keypad-minus alias was lost");
        send(SDLK_h, SDL_SCANCODE_H, false);
        send(SDLK_DOWN, SDL_SCANCODE_DOWN, false);
        const auto before_overflow = app.selection;
        for(int n = 0; n < 200; ++n) {
            SDL_Event event{}; event.type = SDL_KEYDOWN; event.key.windowID = 17;
            event.key.keysym.sym = SDLK_DOWN; event.key.keysym.scancode = SDL_SCANCODE_DOWN; event.key.repeat = 1;
            require(SDL_PushEvent(&event) == 1, "SDL rejected an overflow fixture");
        }
        keyboard.process(app);
        require(app.selection == before_overflow, "SDL overflow replayed a held navigation key");
        send(SDLK_DOWN, SDL_SCANCODE_DOWN, false, true); send(SDLK_DOWN, SDL_SCANCODE_DOWN, false);
        require(app.selection == before_overflow + 1, "Fresh SDL input was lost after overflow recovery");
    }
    SDL_Quit();
}
#endif
} // namespace

int main() {
    const auto directory = fs::temp_directory_path() / ("tyrell-video-u1-" + std::to_string(tick_ms()));
    try {
        fs::create_directories(directory / "a/b/z");
        lv_init(); lv_tick_set_cb(fake_tick); display = lv_display_create(width, height);
        lv_display_set_color_format(display, LV_COLOR_FORMAT_XRGB8888);
        lv_display_set_buffers(display, pixels.data(), nullptr, pixels.size() * sizeof(pixels[0]), LV_DISPLAY_RENDER_MODE_DIRECT);
        lv_display_set_flush_cb(display, [](lv_display_t* d, const lv_area_t*, std::uint8_t*) { lv_display_flush_ready(d); });
        metadata(); browser_and_help(directory); time_and_render(directory); dispatch_and_edges(directory);
#if defined(__linux__)
        raw_evdev(directory);
#endif
#if TYRELL_ENABLE_SDL
        raw_sdl(directory);
#endif
        lv_display_delete(display); lv_deinit(); fs::remove_all(directory);
        std::cout << "Video command/help, native input epochs, fixed-time UI and 320x170 bounds passed.\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n'; fs::remove_all(directory); return 1;
    }
}
