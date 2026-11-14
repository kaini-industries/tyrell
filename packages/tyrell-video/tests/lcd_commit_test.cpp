#include "lcd_commit.h"

#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace tyrell::video;

namespace {
void require(bool condition, const std::string& message) {
    if(!condition) throw std::runtime_error(message);
}

struct TemporaryFile {
    std::string path;
    TemporaryFile() {
        char pattern[] = "/tmp/tyrell-lcd-commit-XXXXXX";
        const int fd = mkstemp(pattern);
        require(fd >= 0, "Cannot create commit fixture");
        close(fd);
        path = pattern;
    }
    ~TemporaryFile() { unlink(path.c_str()); }
};

struct Display {
    lv_display_t* value = nullptr;
    std::vector<std::uint16_t> pixels = std::vector<std::uint16_t>(320 * 32);
    unsigned chunks = 0, last_chunks = 0;

    Display() {
        value = lv_display_create(320, 170);
        require(value != nullptr, "Cannot allocate display");
        lv_display_set_user_data(value, this);
        lv_display_set_color_format(value, LV_COLOR_FORMAT_RGB565);
        lv_display_set_buffers(value, pixels.data(), nullptr, pixels.size() * sizeof(pixels[0]),
                               LV_DISPLAY_RENDER_MODE_PARTIAL);
        lv_display_set_flush_cb(value, [](lv_display_t* display, const lv_area_t*, std::uint8_t*) {
            auto& self = *static_cast<Display*>(lv_display_get_user_data(display));
            ++self.chunks;
            if(lv_display_flush_is_last(display)) ++self.last_chunks;
            lv_display_flush_ready(display);
        });
        auto* root = lv_display_get_screen_active(value);
        lv_obj_set_style_bg_opa(root, LV_OPA_COVER, 0);
        lv_obj_set_style_bg_color(root, lv_color_hex(0x123456), 0);
    }
    ~Display() { destroy(); }
    void redraw() {
        lv_obj_invalidate(lv_display_get_screen_active(value));
        lv_refr_now(value);
    }
    void destroy() {
        if(value) lv_display_delete(value);
        value = nullptr;
    }
};

void successful_commits(const std::string& path) {
    Display display;
    const auto original_events = lv_display_get_event_count(display.value);
    FramebufferCommit commit;
    require(commit.attach(display.value, path) && commit.active(), "Commit fixture did not open");
    display.redraw();
    require(display.chunks >= 6 && display.last_chunks == 1,
            "Fixture did not render one full frame through multiple partial flushes");
    require(commit.stats().completed_frames == 1 && commit.stats().fsync_calls == 1,
            "Partial buffer strips must produce exactly one framebuffer commit");
    require(commit.stats().error.empty() && commit.stats().fsync_total_seconds >= 0 &&
            commit.stats().fsync_max_seconds <= commit.stats().fsync_total_seconds,
            "Successful commit reported invalid timings or an error");

    lv_refr_now(display.value);
    require(commit.stats().fsync_calls == 1, "A refresh without redraw committed unchanged pixels");
    display.redraw();
    require(display.last_chunks == 2 && commit.stats().completed_frames == 2 &&
            commit.stats().fsync_calls == 2, "A second redraw did not commit exactly once");

    commit.detach();
    require(!commit.attached() && !commit.active() &&
            lv_display_get_event_count(display.value) == original_events,
            "Detaching retained a descriptor or callback registration");
    display.redraw();
    require(commit.stats().completed_frames == 2 && commit.stats().fsync_calls == 2,
            "A detached helper received a display callback");
    commit.detach();

    require(commit.attach(display.value, path), "Cannot reattach framebuffer commits");
    display.destroy();
    require(!commit.attached() && !commit.active(), "Deleting the display left a borrowed pointer alive");
}

void failure_keeps_drawing(const std::string& path) {
    Display display;
    FramebufferCommit commit;
    require(commit.attach(display.value, "/dev/null"), "Cannot open fsync failure fixture");
    display.redraw();
    require(!commit.active() && commit.attached() && commit.stats().fsync_calls == 1 &&
            commit.stats().completed_frames == 1 && !commit.stats().error.empty(),
            "A failed fsync did not disable commits and record its error");
    const auto chunks = display.chunks;
    display.redraw();
    require(display.chunks > chunks && commit.stats().completed_frames == 2 &&
            commit.stats().fsync_calls == 1,
            "Commit failure interrupted drawing or kept issuing failed syscalls");

    require(!commit.attach(display.value, path + "/missing") && !commit.active() &&
            !commit.stats().error.empty(), "Open failure was not reported");
    display.redraw();
    require(commit.stats().completed_frames == 1 && commit.stats().fsync_calls == 0,
            "Open failure must leave redraws working without commit syscalls");

    const auto original_events = lv_display_get_event_count(display.value);
    commit.detach();
    require(lv_display_get_event_count(display.value) + 2 == original_events,
            "Failure-path cleanup retained callbacks");
    {
        FramebufferCommit temporary;
        require(temporary.attach(display.value, path), "Cannot open destructor fixture");
    }
    display.redraw(); // A stale callback would dereference the destroyed helper.
    require(lv_display_get_event_count(display.value) + 2 == original_events,
            "Destructor retained a display callback");
}
} // namespace

int main() {
    try {
        lv_init();
        TemporaryFile fixture;
        successful_commits(fixture.path);
        failure_keeps_drawing(fixture.path);
        lv_deinit();
        std::cout << "PASS: one commit per partial-frame redraw, failure fallback, detach and display lifetime\n";
        return 0;
    } catch(const std::exception& error) {
        lv_deinit();
        std::cerr << "LCD commit test: " << error.what() << '\n';
        return 1;
    }
}
