#pragma once

#include <gst/gst.h>
#include <string>
#include <vector>

namespace tyrell::video {

// Linear DMA buffers can use ordinary raw caps: kmssink imports their allocator
// directly. Keep a concrete YUV format so its 1.26 copy fallback can still
// allocate a valid frame when V4L2's capture pool briefly supplies copied memory.
// This linear path does not require opaque DMA_DRM negotiation.
inline GstCaps* hdmi_video_caps(const std::vector<std::string>& plane_formats) {
    GstCaps* caps = gst_caps_new_empty();
    GValue formats = G_VALUE_INIT;
    g_value_init(&formats, GST_TYPE_LIST);
    for(const auto& name : plane_formats) {
        if(name != "I420" && name != "NV12" && name != "YV12") continue;
        GValue value = G_VALUE_INIT;
        g_value_init(&value, G_TYPE_STRING);
        g_value_set_string(&value, name.c_str());
        gst_value_list_append_value(&formats, &value);
        g_value_unset(&value);
    }
    if(gst_value_list_get_size(&formats)) {
        auto* raw = gst_structure_new("video/x-raw", "colorimetry", G_TYPE_STRING, "bt709",
            "width", GST_TYPE_INT_RANGE, 2, 1920, "height", GST_TYPE_INT_RANGE, 2, 1080,
            "pixel-aspect-ratio", GST_TYPE_FRACTION, 1, 1, nullptr);
        gst_structure_set_value(raw, "format", &formats);
        gst_caps_append_structure(caps, raw);
    }
    g_value_unset(&formats);
    return caps;
}

} // namespace tyrell::video
