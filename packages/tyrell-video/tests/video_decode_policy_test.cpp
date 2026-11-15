#include "video_decode_policy.h"

#include <initializer_list>
#include <iostream>
#include <memory>
#include <stdexcept>

using namespace tyrell::video;

namespace {
void require(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

using Error = std::unique_ptr<GError, decltype(&g_error_free)>;
Error make_error(GQuark domain, int code, const char* message) {
    return Error(g_error_new_literal(domain, code, message), &g_error_free);
}

void check_error_scope() {
    auto allocation = make_error(GST_STREAM_ERROR, GST_STREAM_ERROR_FAILED,
                                 "failed to activate buffer pool");
    require(kms_pool_activation_error(allocation.get()), "Known KMS allocation failure was not recognized");
    require(!kms_pool_activation_error(nullptr), "A missing error must not trigger recovery");

    auto wrong_domain = make_error(GST_RESOURCE_ERROR, GST_RESOURCE_ERROR_FAILED,
                                   "failed to activate buffer pool");
    auto wrong_code = make_error(GST_STREAM_ERROR, GST_STREAM_ERROR_DECODE,
                                 "failed to activate buffer pool");
    require(!kms_pool_activation_error(wrong_domain.get()), "An unrelated resource error was treated as KMS allocation failure");
    require(!kms_pool_activation_error(wrong_code.get()), "A decoding error was treated as KMS allocation failure");
    for(const char* text : {"", "Failed to activate buffer pool", "failed to activate buffer pool: other failure",
                            "Could not decode stream", "Permission denied", "No space left on device"}) {
        auto other = make_error(GST_STREAM_ERROR, GST_STREAM_ERROR_FAILED, text);
        require(!kms_pool_activation_error(other.get()), "Recovery must match the specific KMS error, not a generic failure");
    }
    GError malformed{};
    malformed.domain = GST_STREAM_ERROR;
    malformed.code = GST_STREAM_ERROR_FAILED;
    require(!kms_pool_activation_error(&malformed), "An error without a message must fail closed");

    require(retry_hdmi_with_software_video(true, true, true, false, allocation.get()),
            "An eligible HDMI hardware-buffer failure must allow one software retry");
    require(!retry_hdmi_with_software_video(false, true, true, false, allocation.get()),
            "LCD failure must not enter the HDMI recovery path");
    require(!retry_hdmi_with_software_video(true, false, true, false, allocation.get()),
            "The same error from audio or another element must not trigger a video retry");
    require(!retry_hdmi_with_software_video(true, true, false, false, allocation.get()),
            "A failure before a hardware video buffer must not trigger this retry");
    require(!retry_hdmi_with_software_video(true, true, true, true, allocation.get()),
            "An exhausted retry budget must prevent a recovery loop");
    require(!retry_hdmi_with_software_video(true, true, true, false, wrong_domain.get()) &&
            !retry_hdmi_with_software_video(true, true, true, false, wrong_code.get()) &&
            !retry_hdmi_with_software_video(true, true, true, false, nullptr),
            "The retry decision must retain the exact error classification guard");
}

void check_decoder_classification() {
    require(decoder_is_hardware_video("v4l2h264dec", "Codec/Decoder/Video"),
            "The Zero's V4L2 video decoder must be recognized");
    require(decoder_is_hardware_video("vendor-decoder", "Codec/Decoder/Video/Hardware"),
            "Explicit hardware video metadata must be recognized");
    for(const char* name : {"omxh264dec", "vaapih264dec", "qsvh264dec"})
        require(decoder_is_hardware_video(name, "Codec/Decoder/Video"), "A known hardware video family was missed");
    require(!decoder_is_hardware_video("avdec_h264", "Codec/Decoder/Video"),
            "The software decoder must not be classified as hardware");
    require(!decoder_is_hardware_video("v4l2h264enc", "Codec/Encoder/Video/Hardware"),
            "An encoder must not mark the decoder path as hardware");
    require(!decoder_is_hardware_video("v4l2audio", "Codec/Decoder/Audio/Hardware"),
            "A hardware audio element must not mark the video path");
    require(!decoder_is_hardware_video("v4l2h264dec", "Video") &&
            !decoder_is_hardware_video(nullptr, "Codec/Decoder/Video/Hardware") &&
            !decoder_is_hardware_video("v4l2h264dec", nullptr),
            "Incomplete decoder metadata must fail closed");
}

void check_installed_playbin_flag() {
    auto release = [](GstElement* element) { if(element) gst_object_unref(element); };
    std::unique_ptr<GstElement, decltype(release)> retry(gst_element_factory_make("playbin", nullptr), release);
    std::unique_ptr<GstElement, decltype(release)> untouched(gst_element_factory_make("playbin", nullptr), release);
    require(retry && untouched, "The required installed playbin plugin is unavailable");
    GParamSpec* spec = g_object_class_find_property(G_OBJECT_GET_CLASS(retry.get()), "flags");
    require(spec && G_TYPE_IS_FLAGS(G_PARAM_SPEC_VALUE_TYPE(spec)), "playbin must expose typed playback flags");
    auto* values = static_cast<GFlagsClass*>(g_type_class_ref(G_PARAM_SPEC_VALUE_TYPE(spec)));
    const auto* software = g_flags_get_value_by_nick(values, "force-sw-decoders");
    const bool matching_flag = software && software->value == force_software_decoders_flag;
    g_type_class_unref(values);
    require(matching_flag, "The software-only bit must match installed GStreamer's force-sw-decoders flag");

    guint before = 0, after = 0, separate = 0;
    g_object_get(retry.get(), "flags", &before, nullptr);
    require(!(before & force_software_decoders_flag), "Default playbin unexpectedly forces software decoders");
    g_object_set(retry.get(), "flags", before | force_software_decoders_flag, nullptr);
    g_object_get(retry.get(), "flags", &after, nullptr);
    g_object_get(untouched.get(), "flags", &separate, nullptr);
    require((after & force_software_decoders_flag) && (after & before) == before,
            "Setting software-only decoding must preserve existing playback flags");
    require(!(separate & force_software_decoders_flag),
            "The retry must not change other playbin instances or global decoder policy");
}
} // namespace

int main() {
    gst_init(nullptr, nullptr);
    try {
        check_error_scope();
        check_decoder_classification();
        check_installed_playbin_flag();
        std::cout << "PASS: narrowly scoped HDMI allocation retry, one-shot guard, hardware-video classification and per-playbin software flag\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "Video decode policy: " << error.what() << '\n';
        return 1;
    }
}
