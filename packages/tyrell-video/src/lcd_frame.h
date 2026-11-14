#pragma once

#include "playback.h"

#include <gst/gst.h>
#include <gst/video/video.h>
#include <gst/video/video-overlay-composition.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <memory>
#include <string>

namespace tyrell::video {

// Prefer timed overlay metadata when Pango is present, while retaining plain
// raw video for files that do not need a text renderer at all. Both converters
// in GStreamer 1.26 can preserve and scale this metadata.
inline GstCaps* lcd_video_caps(bool rgb16) {
    GstCaps* plain = gst_caps_new_simple("video/x-raw",
        "width", GST_TYPE_INT_RANGE, 1, 320,
        "height", GST_TYPE_INT_RANGE, 1, 170,
        "pixel-aspect-ratio", GST_TYPE_FRACTION, 1, 1, nullptr);
    if(rgb16) gst_caps_set_simple(plain, "format", G_TYPE_STRING, "RGB16", nullptr);
    GstCaps* result = gst_caps_copy(plain);
    gst_caps_set_features(result, 0,
        gst_caps_features_new(GST_CAPS_FEATURE_MEMORY_SYSTEM_MEMORY,
            GST_CAPS_FEATURE_META_GST_VIDEO_OVERLAY_COMPOSITION, nullptr));
    gst_caps_append(result, plain);
    return result;
}

// Streaming-thread-only renderer. No frame or composition is retained. The
// optional converter is bounded to <=320x170 and used only when a small source
// needs enlarging after caps fixation retained its original dimensions.
class LcdFrameRenderer {
public:
    ~LcdFrameRenderer() { reset(); }
    LcdFrameRenderer() = default;
    LcdFrameRenderer(const LcdFrameRenderer&) = delete;
    LcdFrameRenderer& operator=(const LcdFrameRenderer&) = delete;

    void reset() {
        if(converter_) gst_video_converter_free(converter_);
        converter_ = nullptr;
    }

    bool render(GstSample* sample, Frame& target, bool& composed, std::string& error) {
        composed = false;
        GstCaps* caps = gst_sample_get_caps(sample);
        GstBuffer* buffer = gst_sample_get_buffer(sample);
        GstVideoInfo info;
        gst_video_info_init(&info);
        if(!caps || !buffer || !gst_video_info_from_caps(&info, caps) ||
           GST_VIDEO_INFO_FORMAT(&info) != GST_VIDEO_FORMAT_RGB16 ||
           GST_VIDEO_INFO_WIDTH(&info) < 1 || GST_VIDEO_INFO_WIDTH(&info) > 320 ||
           GST_VIDEO_INFO_HEIGHT(&info) < 1 || GST_VIDEO_INFO_HEIGHT(&info) > 170 ||
           GST_VIDEO_INFO_PAR_N(&info) != GST_VIDEO_INFO_PAR_D(&info)) {
            error = "LCD video did not negotiate bounded square-pixel RGB565";
            return false;
        }
        VideoMap input;
        if(!input.map(&info, buffer, GST_MAP_READ)) {
            error = "Cannot map the LCD video frame";
            return false;
        }
        const int width = GST_VIDEO_FRAME_WIDTH(&input.frame);
        const int height = GST_VIDEO_FRAME_HEIGHT(&input.frame);
        if(width < 1 || width > 320 || height < 1 || height > 170 ||
           GST_VIDEO_FRAME_FORMAT(&input.frame) != GST_VIDEO_FORMAT_RGB16) {
            error = "Mapped LCD video exceeds its negotiated RGB565 bounds";
            return false;
        }
        const double scale = std::min(320.0 / width, 170.0 / height);
        const int fitted_width = std::clamp(static_cast<int>(std::lround(width * scale)), 1, 320);
        const int fitted_height = std::clamp(static_cast<int>(std::lround(height * scale)), 1, 170);
        const int left = (320 - fitted_width) / 2, top = (170 - fitted_height) / 2;
        target.width = 320; target.height = 170; target.stride = 640;
        target.pixels.assign(320u * 170u * 2u, 0);
        if(GST_BUFFER_PTS_IS_VALID(buffer)) target.position = static_cast<double>(GST_BUFFER_PTS(buffer)) / GST_SECOND;

        GstVideoInfo view;
        gst_video_info_set_format(&view, GST_VIDEO_FORMAT_RGB16, fitted_width, fitted_height);
        view.colorimetry = input.frame.info.colorimetry;
        view.interlace_mode = input.frame.info.interlace_mode;
        view.fps_n = input.frame.info.fps_n; view.fps_d = input.frame.info.fps_d;
        view.stride[0] = target.stride;
        view.offset[0] = static_cast<gsize>(top * target.stride + left * 2);
        view.size = target.pixels.size();
        const bool enlarge = fitted_width != width || fitted_height != height;
        if(!enlarge) {
            const auto* pixels = static_cast<const std::uint8_t*>(GST_VIDEO_FRAME_PLANE_DATA(&input.frame, 0));
            const int stride = GST_VIDEO_FRAME_PLANE_STRIDE(&input.frame, 0);
            for(int y = 0; y < height; ++y)
                std::memcpy(target.pixels.data() + view.offset[0] + y * target.stride,
                            pixels + y * stride, static_cast<std::size_t>(width) * 2);
        }
        GstVideoOverlayCompositionMeta* meta = gst_buffer_get_video_overlay_composition_meta(buffer);
        if(!enlarge && !meta) return true;

        // This buffer borrows the unpublished vector. Mapping it never exposes
        // writable storage to GStreamer or LVGL after this method returns.
        std::unique_ptr<GstBuffer, void(*)(GstBuffer*)> output(
            gst_buffer_new_wrapped_full(static_cast<GstMemoryFlags>(0), target.pixels.data(),
                target.pixels.size(), 0, target.pixels.size(), nullptr, nullptr), gst_buffer_unref);
        if(!output) { error = "Cannot prepare LCD rendering storage"; return false; }
        if(enlarge && meta) {
            // Add only the composition metadata, before mapping adds a buffer
            // reference. A copied GstVideoMeta would override our viewport.
            GstVideoMetaTransform transform{&input.frame.info, &view};
            gpointer iterator = nullptr;
            while(auto* value = gst_buffer_iterate_meta_filtered(buffer, &iterator,
                      GST_VIDEO_OVERLAY_COMPOSITION_META_API_TYPE)) {
                if(!value->info->transform_func ||
                   !value->info->transform_func(output.get(), value, buffer,
                        gst_video_meta_transform_scale_get_quark(), &transform)) {
                    error = "Cannot resize LCD captions";
                    return false;
                }
            }
            auto* resized = gst_buffer_get_video_overlay_composition_meta(output.get());
            if(!resized || !resized->overlay) { error = "Resized LCD captions are unavailable"; return false; }
        }
        VideoMap destination;
        if(!destination.map(&view, output.get(), GST_MAP_READWRITE)) {
            error = "Cannot map LCD rendering storage";
            return false;
        }
        if(enlarge) {
            if(!converter_ || !gst_video_info_is_equal(&converter_input_, &input.frame.info) ||
               !gst_video_info_is_equal(&converter_output_, &view)) {
                reset();
                converter_ = gst_video_converter_new(&input.frame.info, &view, nullptr);
                converter_input_ = input.frame.info; converter_output_ = view;
            }
            if(!converter_) { error = "Cannot enlarge the LCD picture"; return false; }
            gst_video_converter_frame(converter_, &input.frame, &destination.frame);
        }
        gpointer iterator = nullptr;
        while(auto* value = gst_buffer_iterate_meta_filtered(enlarge ? output.get() : buffer, &iterator,
                  GST_VIDEO_OVERLAY_COMPOSITION_META_API_TYPE)) {
            auto* composition = reinterpret_cast<GstVideoOverlayCompositionMeta*>(value);
            if(!composition->overlay) continue;
            if(!gst_video_overlay_composition_blend(composition->overlay, &destination.frame)) {
                error = "Cannot blend LCD captions";
                return false;
            }
            composed = true;
        }
        return true;
    }

private:
    struct VideoMap {
        GstVideoFrame frame{};
        bool mapped = false;
        ~VideoMap() { if(mapped) gst_video_frame_unmap(&frame); }
        bool map(const GstVideoInfo* info, GstBuffer* buffer, GstMapFlags flags) {
            mapped = gst_video_frame_map(&frame, info, buffer, flags);
            return mapped;
        }
    };
    GstVideoConverter* converter_ = nullptr;
    GstVideoInfo converter_input_{}, converter_output_{};
};

} // namespace tyrell::video
