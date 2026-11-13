#include "frame_image.h"

#include <cstdint>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <vector>

using namespace tyrell::video;
void require(bool value, const char* message) { if(!value) throw std::runtime_error(message); }

std::shared_ptr<Frame> make_frame(std::uint16_t color, std::uint64_t serial) {
    auto frame = std::make_shared<Frame>();
    frame->width = 3; frame->height = 2; frame->stride = 6; frame->serial = serial;
    frame->pixels.resize(12);
    for(std::size_t i = 0; i < frame->pixels.size(); i += 2) { frame->pixels[i] = color & 255; frame->pixels[i + 1] = color >> 8; }
    return frame;
}

int main() {
    lv_init();
    auto* display = lv_display_create(32, 24);
    std::vector<std::uint32_t> pixels(32 * 24);
    lv_display_set_color_format(display, LV_COLOR_FORMAT_XRGB8888);
    lv_display_set_buffers(display, pixels.data(), nullptr, pixels.size() * sizeof(pixels[0]), LV_DISPLAY_RENDER_MODE_DIRECT);
    lv_display_set_flush_cb(display, [](lv_display_t* d, const lv_area_t*, std::uint8_t*) { lv_display_flush_ready(d); });
    // Exercise the stale-descriptor case even though production disables cache.
    lv_image_cache_resize(1024 * 1024, true);
    auto* widget = lv_image_create(lv_screen_active());
    lv_obj_set_pos(widget, 10, 10);
    FrameImage image;
    try {
        auto red = make_frame(0xf800, 1);
        std::weak_ptr<const Frame> retained = red;
        require(image.show(widget, red), "First frame assigned");
        require(!image.show(widget, red), "Same frame is not reassigned");
        red.reset();
        require(!retained.expired(), "Frame bytes retained until LVGL refresh");
        lv_refr_now(display);
        require((pixels[10 * 32 + 10] & 0xffffff) == 0xff0000, "RGB565 red rendered correctly");
        auto blue = make_frame(0x001f, 2);
        require(image.show(widget, blue), "Replacement frame assigned");
        require(retained.expired(), "Previous frame released after replacement");
        lv_refr_now(display);
        require((pixels[10 * 32 + 10] & 0xffffff) == 0x0000ff, "Same descriptor renders new pixels despite conversion cache");
        auto invalid = make_frame(0x07e0, 3); invalid->pixels.resize(1);
        require(!image.show(widget, invalid), "Truncated frame rejected before LVGL reads it");
        lv_refr_now(display);
        require((pixels[10 * 32 + 10] & 0xffffff) == 0x0000ff, "Invalid frame preserves prior display");
        retained = blue; blue.reset();
        image.clear(widget);
        require(retained.expired(), "Clear detaches source before releasing bytes");
        require(lv_image_get_src(widget) == nullptr, "Clear removes LVGL source");
        lv_obj_delete(widget); lv_display_delete(display); lv_deinit();
        std::cout << "LVGL frame ownership, RGB565 conversion and cache replacement checks passed\n";
        return 0;
    } catch(const std::exception& error) {
        image.clear(widget); lv_obj_delete(widget); lv_display_delete(display); lv_deinit();
        std::cerr << error.what() << '\n'; return 1;
    }
}
