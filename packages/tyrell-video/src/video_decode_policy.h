#pragma once

#include <gst/gst.h>
#include <cstring>

namespace tyrell::video {

// GstPlayFlags is defined by the playback plugin rather than installed core
// headers. playbin 1.26 forwards this flag to uridecodebin's per-instance
// force-sw-decoders property; it does not change registry ranks or other apps.
inline constexpr unsigned force_software_decoders_flag = 1U << 12;

inline bool decoder_is_hardware_video(const char* name, const char* klass) {
    if(!name || !klass || !std::strstr(klass, "Decoder") || !std::strstr(klass, "Video")) return false;
    return std::strstr(klass, "Hardware") || std::strstr(name, "v4l2") ||
           std::strstr(name, "omx") || std::strstr(name, "vaapi") || std::strstr(name, "qsv");
}

inline bool kms_pool_activation_error(const GError* error) {
    // This is the exact nonlocalized error emitted by kmssink 1.26's
    // ensure_internal_pool(). Do not retry corrupt files, missing codecs,
    // audio failures, mode/permission errors or unrelated allocation failures.
    return error && error->domain == GST_STREAM_ERROR && error->code == GST_STREAM_ERROR_FAILED &&
           error->message && std::strcmp(error->message, "failed to activate buffer pool") == 0;
}

inline bool retry_hdmi_with_software_video(bool hdmi, bool from_video_sink,
                                          bool hardware_video_produced, bool already_attempted,
                                          const GError* error) {
    return hdmi && from_video_sink && hardware_video_produced && !already_attempted &&
           kms_pool_activation_error(error);
}

} // namespace tyrell::video
