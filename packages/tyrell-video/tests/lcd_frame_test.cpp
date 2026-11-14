#include "lcd_frame.h"

#include <cstring>
#include <iostream>
#include <stdexcept>

using namespace tyrell::video;

namespace {
void require(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

GstSample* sample(int width, int height, bool caption, bool padded = false) {
    GstVideoInfo info;
    gst_video_info_set_format(&info, GST_VIDEO_FORMAT_RGB16, width, height);
    const gsize size = padded ? 16u + static_cast<gsize>(info.stride[0] + 16) * height : info.size;
    GstBuffer* buffer = gst_buffer_new_allocate(nullptr, size, nullptr);
    gst_buffer_memset(buffer, 0, 0, size);
    if(padded) {
        gsize offsets[GST_VIDEO_MAX_PLANES] = {16};
        gint strides[GST_VIDEO_MAX_PLANES] = {info.stride[0] + 16};
        require(gst_buffer_add_video_meta_full(buffer, GST_VIDEO_FRAME_FLAG_NONE, GST_VIDEO_FORMAT_RGB16,
                    width, height, 1, offsets, strides), "Cannot attach padded video layout");
    }
    if(caption) {
        GstBuffer* pixels = gst_buffer_new_allocate(nullptr, 10 * 10 * 4, nullptr);
        GstMapInfo map;
        require(gst_buffer_map(pixels, &map, GST_MAP_WRITE), "Cannot map test caption");
        for(unsigned i = 0; i < 100; ++i) {
            map.data[i * 4] = 0;
            map.data[i * 4 + 1] = 255;
            map.data[i * 4 + 2] = 0;
            map.data[i * 4 + 3] = 255;
        }
        gst_buffer_unmap(pixels, &map);
        gst_buffer_add_video_meta(pixels, GST_VIDEO_FRAME_FLAG_NONE, GST_VIDEO_FORMAT_BGRA, 10, 10);
        const int x = width == 100 ? 25 : 100, y = width == 100 ? 75 : 100;
        auto* rectangle = gst_video_overlay_rectangle_new_raw(pixels, x, y, 10, 10, GST_VIDEO_OVERLAY_FORMAT_FLAG_NONE);
        require(rectangle, "Cannot create caption rectangle");
        auto* overlay = gst_video_overlay_composition_new(rectangle);
        require(gst_buffer_add_video_overlay_composition_meta(buffer, overlay), "Cannot attach caption metadata");
        gst_video_overlay_composition_unref(overlay);
        gst_video_overlay_rectangle_unref(rectangle);
        gst_buffer_unref(pixels);
    }
    GstCaps* caps = gst_video_info_to_caps(&info);
    GstSample* result = gst_sample_new(buffer, caps, nullptr, nullptr);
    gst_caps_unref(caps); gst_buffer_unref(buffer);
    return result;
}

std::uint16_t pixel(const Frame& frame, int x, int y) {
    std::uint16_t value;
    std::memcpy(&value, frame.pixels.data() + y * frame.stride + x * 2, sizeof(value));
    return value;
}

void check(LcdFrameRenderer& renderer, int width, int height, int expected_left, int expected_top,
           int expected_width, int expected_height, bool padded = false) {
    GstSample* source = sample(width, height, true, padded);
    Frame frame;
    bool composed = false;
    std::string error;
    require(renderer.render(source, frame, composed, error), error.c_str());
    require(composed, "Timed caption metadata was ignored");
    require(frame.width == 320 && frame.height == 170 && frame.stride == 640 && frame.pixels.size() == 108800,
            "Caption rendering changed the bounded frame contract");
    int left = 320, right = -1, top = 170, bottom = -1;
    for(int y = 0; y < 170; ++y) for(int x = 0; x < 320; ++x) {
        if(pixel(frame, x, y)) {
            require(pixel(frame, x, y) == 0x07e0, "Caption colors changed or leaked outside its rectangle");
            left = std::min(left, x); right = std::max(right, x);
            top = std::min(top, y); bottom = std::max(bottom, y);
        }
    }
    require(std::abs(left - expected_left) <= 1 && std::abs(top - expected_top) <= 1 &&
            std::abs(right - left + 1 - expected_width) <= 1 && std::abs(bottom - top + 1 - expected_height) <= 1,
            "Caption placement did not follow picture scaling and letterboxing");
    GstMapInfo original;
    require(gst_buffer_map(gst_sample_get_buffer(source), &original, GST_MAP_READ), "Cannot check source immutability");
    for(gsize i = 0; i < original.size; ++i)
        require(original.data[i] == 0, "Blending modified the original video buffer");
    gst_buffer_unmap(gst_sample_get_buffer(source), &original);
    gst_sample_unref(source);
}
} // namespace

int main() {
    try {
        gst_init(nullptr, nullptr);
        GstCaps* accepted = lcd_video_caps(true);
        GstCaps* pango = gst_caps_from_string("video/x-raw(memory:SystemMemory,meta:GstVideoOverlayComposition),"
            "format=RGB16,width=320,height=136,pixel-aspect-ratio=1/1");
        GstCaps* plain_caps = gst_caps_from_string("video/x-raw,format=RGB16,width=320,height=136,pixel-aspect-ratio=1/1");
        require(gst_caps_can_intersect(accepted, pango), "LCD caps reject Pango's composition memory features");
        require(gst_caps_can_intersect(accepted, plain_caps), "LCD caps reject plain video without a subtitle renderer");
        gst_caps_unref(accepted); gst_caps_unref(pango); gst_caps_unref(plain_caps);
        LcdFrameRenderer renderer;
        check(renderer, 320, 136, 100, 117, 10, 10);
        check(renderer, 100, 100, 117, 127, 17, 17);
        // Reuse the cached small-picture converter, then switch back to the
        // no-enlargement path; neither path retains or mutates old frames.
        check(renderer, 100, 100, 117, 127, 17, 17, true);
        check(renderer, 320, 136, 100, 117, 10, 10, true);
        GstSample* plain = sample(100, 100, false);
        Frame frame; bool composed = true; std::string error;
        require(renderer.render(plain, frame, composed, error) && !composed,
                "A plain video was rejected or counted as a caption");
        for(auto byte : frame.pixels) require(byte == 0, "Plain black picture or letterbox was modified");
        gst_sample_unref(plain);
        GstSample* oversized = sample(321, 170, false);
        error.clear(); composed = true;
        require(!renderer.render(oversized, frame, composed, error) && !composed && !error.empty(),
                "Unbounded input was silently accepted");
        gst_sample_unref(oversized);
        std::cout << "PASS: LCD caption blending, upscaling, positioning, immutable input and bounded output\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "LCD composition test: " << error.what() << '\n';
        return 1;
    }
}
