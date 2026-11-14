#include "hdmi_video_caps.h"
#include <iostream>
#include <stdexcept>

using namespace tyrell::video;

static void accepts(GstCaps* allowed, const char* input, bool expected) {
    GstCaps* caps = gst_caps_from_string(input);
    const bool actual = gst_caps_can_intersect(allowed, caps);
    gst_caps_unref(caps);
    if(actual != expected) throw std::runtime_error(input);
}

int main() {
    gst_init(nullptr, nullptr);
    try {
        GstCaps* caps = hdmi_video_caps({"I420", "NV12"});
        accepts(caps, "video/x-raw(memory:DMABuf),format=DMA_DRM,drm-format=YU12,width=1920,height=816,colorimetry=bt709,pixel-aspect-ratio=1/1", false);
        // Concrete raw caps retain importable linear DMA buffers while allowing
        // a real I420 fallback allocation. Opaque DMA_DRM must not negotiate.
        accepts(caps, "video/x-raw,format=I420,width=1920,height=816,colorimetry=bt709,pixel-aspect-ratio=1/1", true);
        if(gst_caps_get_size(caps) != 1 || gst_caps_features_contains(gst_caps_get_features(caps, 0), "memory:DMABuf"))
            throw std::runtime_error("A concrete raw fallback format is required");
        accepts(caps, "video/x-raw,format=NV12,width=640,height=360,colorimetry=bt709,pixel-aspect-ratio=1/1", true);
        accepts(caps, "video/x-raw,format=I420,width=1920,height=816,colorimetry=bt601,pixel-aspect-ratio=1/1", false);
        accepts(caps, "video/x-raw,format=I420,width=4096,height=2160,colorimetry=bt709,pixel-aspect-ratio=1/1", false);
        accepts(caps, "video/x-raw,format=I420,width=720,height=576,colorimetry=bt709,pixel-aspect-ratio=16/15", false);
        gst_caps_unref(caps);
        caps = hdmi_video_caps({"NV12"});
        accepts(caps, "video/x-raw,format=I420", false);
        accepts(caps, "video/x-raw(memory:DMABuf),format=DMA_DRM,drm-format=YU12", false);
        gst_caps_unref(caps);
        caps = hdmi_video_caps({"unknown"});
        if(!gst_caps_is_empty(caps)) throw std::runtime_error("Unsupported planes must not advertise formats");
        gst_caps_unref(caps);
        std::cout << "PASS: native linear YUV negotiation and copy fallback, plane limits, colour and square-pixel constraints\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "HDMI caps: " << error.what() << '\n';
        return 1;
    }
}
