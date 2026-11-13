#include "playback.h"
#include "outputs.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cctype>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <iomanip>
#include <mutex>
#include <sstream>
#include <utility>

#if defined(__linux__)
#include <unistd.h>
#endif

#include <gst/gst.h>
#include <gst/app/gstappsink.h>
#include <gst/video/video.h>

namespace tyrell::video {
namespace {
constexpr unsigned text_flag = 1U << 2;
constexpr unsigned visualization_flag = 1U << 3;
constexpr int lcd_width = 320;
constexpr int lcd_height = 170;

std::string json_string(const std::string& value) {
    std::ostringstream out;
    out << '"';
    for(unsigned char c : value) {
        switch(c) {
            case '"': out << "\\\""; break;
            case '\\': out << "\\\\"; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:
                if(c < 32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<unsigned>(c) << std::dec;
                else out << static_cast<char>(c);
        }
    }
    return out.str() + '"';
}

std::string tag_string(const GstTagList* tags, const char* tag) {
    gchar* value = nullptr;
    if(!tags || !gst_tag_list_get_string(tags, tag, &value)) return {};
    std::string result = value;
    g_free(value);
    return result;
}

std::string uri_for_file(const std::string& path) {
    GError* error = nullptr;
    gchar* uri = gst_filename_to_uri(path.c_str(), &error);
    std::string result = uri ? uri : "";
    g_free(uri);
    if(error) g_error_free(error);
    return result;
}

bool bitmap_subtitle_caps(const GstCaps* caps) {
    if(!caps || gst_caps_is_any(caps) || gst_caps_is_empty(caps)) return false;
    for(guint index = 0; index < gst_caps_get_size(caps); ++index) {
        const char* name = gst_structure_get_name(gst_caps_get_structure(caps, index));
        if(g_str_has_prefix(name, "subpicture/") || g_str_has_prefix(name, "image/")) return true;
    }
    return false;
}

bool bitmap_subtitle_codec(std::string codec) {
    std::transform(codec.begin(), codec.end(), codec.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return codec.find("pgs") != std::string::npos || codec.find("dvd") != std::string::npos ||
           codec.find("dvb") != std::string::npos || codec.find("bitmap") != std::string::npos;
}

std::string plugin_diagnostics_json() {
    std::ostringstream out;
    out << "{\"required_elements\":{";
    bool comma = false;
    for(const char* name : {"playbin", "videoconvert", "videoscale", "appsink", "pulsesink", "kmssink",
                           "subtitleoverlay", "textoverlay", "subparse", "avdec_h264", "h264parse", "qtdemux", "matroskademux"}) {
        GstElementFactory* factory = gst_element_factory_find(name);
        if(comma) out << ',';
        comma = true;
        out << json_string(name) << ':' << (factory ? "true" : "false");
        if(factory) gst_object_unref(factory);
    }
    out << "},\"decoders\":[";
    comma = false;
    GList* factories = gst_element_factory_list_get_elements(GST_ELEMENT_FACTORY_TYPE_DECODER, GST_RANK_NONE);
    for(GList* item = factories; item; item = item->next) {
        auto* factory = GST_ELEMENT_FACTORY(item->data);
        const char* name = gst_plugin_feature_get_name(GST_PLUGIN_FEATURE(factory));
        const char* klass = gst_element_factory_get_metadata(factory, GST_ELEMENT_METADATA_KLASS);
        if(!klass || (!std::strstr(klass, "Video") && !std::strstr(klass, "Audio"))) continue;
        const bool hardware_hint = std::strstr(klass, "Hardware") || std::strstr(name, "v4l2") ||
                                   std::strstr(name, "omx") || std::strstr(name, "vaapi") || std::strstr(name, "qsv");
        if(comma) out << ',';
        comma = true;
        out << "{\"name\":" << json_string(name) << ",\"klass\":" << json_string(klass)
            << ",\"rank\":" << gst_plugin_feature_get_rank(GST_PLUGIN_FEATURE(factory))
            << ",\"hardware_hint\":" << (hardware_hint ? "true" : "false") << '}';
    }
    gst_plugin_feature_list_free(factories);
    out << "],\"hardware_hint_scope\":\"Registry metadata only; not a successful hardware decoding test\"}";
    return out.str();
}

std::string device_access_json(const OutputInventory& inventory) {
    std::vector<std::string> paths = {"/dev/fb_lcd", "/dev/input/cardputer-zero-internal", "/dev/dri/cardputer-zero-internal"};
    std::error_code directory_error;
    for(const auto& entry : std::filesystem::directory_iterator("/dev/dri", directory_error)) {
        const auto name = entry.path().filename().string();
        if(name.size() > 4 && name.rfind("card", 0) == 0 &&
           std::all_of(name.begin() + 4, name.end(), [](unsigned char c) { return std::isdigit(c); }))
            paths.push_back(entry.path().string());
    }
    for(const auto& item : inventory.hdmi)
        if(std::find(paths.begin(), paths.end(), item.device) == paths.end()) paths.push_back(item.device);
    std::ostringstream out;
    out << '[';
    bool comma = false;
    for(const auto& path : paths) {
        if(comma) out << ',';
        comma = true;
        std::error_code error;
        const bool exists = std::filesystem::exists(path, error);
        bool readable = false, writable = false;
#if defined(__linux__)
        readable = access(path.c_str(), R_OK) == 0;
        writable = access(path.c_str(), W_OK) == 0;
#endif
        out << "{\"path\":" << json_string(path) << ",\"exists\":" << (exists ? "true" : "false")
            << ",\"readable\":" << (readable ? "true" : "false") << ",\"writable\":" << (writable ? "true" : "false") << '}';
    }
    return out.str() + ']';
}

} // namespace

struct Playback::Impl {
    Status state;
    bool initialized = false;
    PlaybackOptions options;
    GstElement* pipeline = nullptr;
    GstBus* bus = nullptr;
    GstElement* audio_sink = nullptr; // borrowed from the pipeline
    HdmiSession hdmi;
    std::string external_subtitle;
    std::string requested_audio_sink;
    std::string subtitle_font;
    OutputInventory inventory;
    mutable std::mutex frame_mutex;
    std::shared_ptr<const Frame> frame;
    std::atomic<std::uint64_t> received{0};
    std::atomic<bool> subtitles_enabled{false};
    std::atomic<bool> bitmap_blocked{false};
    bool bitmap_notice_shown = false;
    mutable std::mutex subtitle_mutex;
    std::vector<GstElement*> subtitle_overlays;
    std::uint64_t qos_processed = 0;
    std::uint64_t qos_dropped = 0;
    bool ended = false;
    bool pending_preroll = false;
    bool desired_playing = true;
    double restart_position = 0;
    int restart_audio_track = -1;
    int restart_subtitle_track = -1;
    std::chrono::steady_clock::time_point last_query{};
    std::chrono::steady_clock::time_point last_hotplug{};
    std::chrono::steady_clock::time_point load_started{};

    explicit Impl(PlaybackOptions requested_options) : options(requested_options) {
        GError* error = nullptr;
        initialized = gst_init_check(nullptr, nullptr, &error);
        if(!initialized) state.error = error ? error->message : "Cannot initialize GStreamer";
        if(error) g_error_free(error);
    }

    ~Impl() { destroy_pipeline(); }

    void clear_frame() {
        std::lock_guard<std::mutex> lock(frame_mutex);
        frame.reset();
    }

    void destroy_pipeline() {
        if(pipeline) {
            // NULL joins the streaming tasks before releasing our callback data.
            gst_element_set_state(pipeline, GST_STATE_NULL);
            if(bus) gst_object_unref(bus);
            bus = nullptr;
            gst_object_unref(pipeline);
            pipeline = nullptr;
            audio_sink = nullptr;
        }
        {
            std::lock_guard<std::mutex> lock(subtitle_mutex);
            for(auto* overlay : subtitle_overlays) gst_object_unref(overlay);
            subtitle_overlays.clear();
        }
        hdmi.release();
        pending_preroll = false;
        clear_frame();
    }

    static GstFlowReturn sample_ready(GstAppSink* sink, gpointer data) {
        return static_cast<Impl*>(data)->accept_sample(gst_app_sink_pull_sample(sink));
    }
    static GstFlowReturn preroll_ready(GstAppSink* sink, gpointer data) {
        return static_cast<Impl*>(data)->accept_sample(gst_app_sink_pull_preroll(sink));
    }

    static void subtitle_silent_changed(GObject* element, GParamSpec*, gpointer data) {
        auto& player = *static_cast<Impl*>(data);
        gboolean silent = FALSE;
        g_object_get(element, "silent", &silent, nullptr);
        const bool wanted = !player.subtitles_enabled.load();
        if(static_cast<bool>(silent) != wanted) g_object_set(element, "silent", wanted, nullptr);
    }

    static GstPadProbeReturn subtitle_payload_guard(GstPad* pad, GstPadProbeInfo*, gpointer data) {
        GstCaps* caps = gst_pad_get_current_caps(pad);
        const bool bitmap = bitmap_subtitle_caps(caps);
        if(caps) gst_caps_unref(caps);
        if(!bitmap) return GST_PAD_PROBE_OK;
        static_cast<Impl*>(data)->bitmap_blocked = true;
        // Preserve caps, segments, gaps, flush and EOS events. Only unsupported
        // bitmap payloads are excluded; supported text still reaches Pango.
        return GST_PAD_PROBE_DROP;
    }

    static void element_setup(GstElement*, GstElement* element, gpointer data) {
        auto& player = *static_cast<Impl*>(data);
        GstElementFactory* factory = gst_element_get_factory(element);
        if(!factory) return;
        const char* factory_name = gst_plugin_feature_get_name(GST_PLUGIN_FEATURE(factory));
        if(std::strcmp(factory_name, "subtitleoverlay") == 0) {
            if(GstPad* pad = gst_element_get_static_pad(element, "subtitle_sink")) {
                gst_pad_add_probe(pad, static_cast<GstPadProbeType>(GST_PAD_PROBE_TYPE_BUFFER | GST_PAD_PROBE_TYPE_BUFFER_LIST),
                                  subtitle_payload_guard, data, nullptr);
                gst_object_unref(pad);
            }
        }
        if(std::strcmp(factory_name, "dvbsuboverlay") == 0) {
            g_object_set(element, "enable", FALSE, "wait-text", FALSE, nullptr);
        }
        if(std::strcmp(factory_name, "textoverlay") != 0) return;
        if(!g_object_class_find_property(G_OBJECT_GET_CLASS(element), "silent")) return;
        // Silence the final Pango renderer, rather than subtitleoverlay itself:
        // the latter bypasses its parser when initially silent, which can turn
        // external subtitle segments into byte segments and break later seeks.
        g_signal_connect(element, "notify::silent", G_CALLBACK(subtitle_silent_changed), data);
        std::lock_guard<std::mutex> lock(player.subtitle_mutex);
        g_object_set(element, "silent", !player.subtitles_enabled.load(), nullptr);
        player.subtitle_overlays.push_back(GST_ELEMENT(gst_object_ref(element)));
    }

    GstFlowReturn accept_sample(GstSample* sample) {
        if(!sample) return GST_FLOW_OK;
        GstVideoInfo info;
        gst_video_info_init(&info);
        GstCaps* caps = gst_sample_get_caps(sample);
        GstBuffer* buffer = gst_sample_get_buffer(sample);
        GstVideoFrame mapped;
        if(!caps || !buffer || !gst_video_info_from_caps(&info, caps) ||
           GST_VIDEO_INFO_WIDTH(&info) != lcd_width || GST_VIDEO_INFO_HEIGHT(&info) != lcd_height ||
           GST_VIDEO_INFO_FORMAT(&info) != GST_VIDEO_FORMAT_RGB16 ||
           !gst_video_frame_map(&mapped, &info, buffer, GST_MAP_READ)) {
            gst_sample_unref(sample);
            return GST_FLOW_OK;
        }
        auto next = std::make_shared<Frame>();
        if(GST_BUFFER_PTS_IS_VALID(buffer)) next->position = static_cast<double>(GST_BUFFER_PTS(buffer)) / GST_SECOND;
        next->pixels.resize(lcd_width * lcd_height * 2);
        const auto* pixels = static_cast<const std::uint8_t*>(GST_VIDEO_FRAME_PLANE_DATA(&mapped, 0));
        const int stride = GST_VIDEO_FRAME_PLANE_STRIDE(&mapped, 0);
        for(int y = 0; y < lcd_height; ++y)
            std::memcpy(next->pixels.data() + y * next->stride, pixels + y * stride, next->stride);
        next->serial = received.fetch_add(1) + 1;
        gst_video_frame_unmap(&mapped);
        gst_sample_unref(sample);
        // Replacing this single snapshot bounds storage even if the UI stalls.
        std::lock_guard<std::mutex> lock(frame_mutex);
        frame = std::move(next);
        return GST_FLOW_OK;
    }

    GstElement* make_video_sink(Output output, std::string& error) {
        GstElement* bin = gst_bin_new("tyrell-video-output");
        GstElement* convert = gst_element_factory_make("videoconvert", nullptr);
        GstElement* scale = gst_element_factory_make("videoscale", nullptr);
        GstElement* filter = gst_element_factory_make("capsfilter", nullptr);
        GstElement* sink = gst_element_factory_make(output == Output::Lcd ? "appsink" : "kmssink", "tyrell-video-sink");
        if(!bin || !convert || !scale || !filter || !sink) {
            if(convert) gst_object_unref(convert);
            if(scale) gst_object_unref(scale);
            if(filter) gst_object_unref(filter);
            if(sink) gst_object_unref(sink);
            if(bin) gst_object_unref(bin);
            error = output == Output::Lcd ? "Missing GStreamer video conversion/appsink plugin" : "Missing GStreamer kmssink plugin";
            return nullptr;
        }
        int width = lcd_width, height = lcd_height;
        const char* format = "RGB16";
        if(output == Output::Hdmi) {
            if(!hdmi.acquire(error)) {
                gst_object_unref(convert); gst_object_unref(scale); gst_object_unref(filter); gst_object_unref(sink); gst_object_unref(bin);
                return nullptr;
            }
            if(!g_object_class_find_property(G_OBJECT_GET_CLASS(sink), "fd")) {
                error = "HDMI requires GStreamer kmssink with the fd property (1.22 or newer)";
                gst_object_unref(convert); gst_object_unref(scale); gst_object_unref(filter); gst_object_unref(sink); gst_object_unref(bin);
                hdmi.release();
                return nullptr;
            }
            const auto mode = hdmi.mode();
            width = mode.width;
            height = mode.height;
            format = "BGRx";
            g_object_set(sink, "fd", hdmi.fd(), "connector-id", static_cast<int>(hdmi.connector_id()),
                         "force-modesetting", TRUE, "sync", TRUE, "qos", TRUE, nullptr);
            if(g_object_class_find_property(G_OBJECT_GET_CLASS(sink), "restore-crtc"))
                g_object_set(sink, "restore-crtc", TRUE, nullptr);
        } else {
            g_object_set(sink, "sync", TRUE, "max-buffers", 1U, "drop", TRUE,
                         "enable-last-sample", FALSE, "wait-on-eos", FALSE, "qos", TRUE, nullptr);
            GstAppSinkCallbacks callbacks{};
            callbacks.new_sample = sample_ready;
            callbacks.new_preroll = preroll_ready;
            gst_app_sink_set_callbacks(GST_APP_SINK(sink), &callbacks, this, nullptr);
        }
        g_object_set(scale, "add-borders", TRUE, nullptr);
        GstCaps* caps = gst_caps_new_simple("video/x-raw", "format", G_TYPE_STRING, format,
                                           "width", G_TYPE_INT, width, "height", G_TYPE_INT, height,
                                           "pixel-aspect-ratio", GST_TYPE_FRACTION, 1, 1, nullptr);
        g_object_set(filter, "caps", caps, nullptr);
        gst_caps_unref(caps);
        gst_bin_add_many(GST_BIN(bin), convert, scale, filter, sink, nullptr);
        if(!gst_element_link_many(convert, scale, filter, sink, nullptr)) {
            error = "Cannot link video output pipeline";
            gst_object_unref(bin);
            hdmi.release();
            return nullptr;
        }
        GstPad* pad = gst_element_get_static_pad(convert, "sink");
        GstPad* ghost = gst_ghost_pad_new("sink", pad);
        gst_object_unref(pad);
        if(!ghost || !gst_element_add_pad(bin, ghost)) {
            if(ghost) gst_object_unref(ghost);
            error = "Cannot create video output pad";
            gst_object_unref(bin);
            hdmi.release();
            return nullptr;
        }
        return bin;
    }

    GstElement* make_audio_sink(Output output, std::string& error) {
        if(options.fake_audio) {
            GstElement* sink = gst_element_factory_make("fakesink", "tyrell-test-audio-output");
            if(sink) g_object_set(sink, "sync", TRUE, nullptr);
            else error = "Missing test fakesink plugin";
            state.audio_output = "fakesink (explicit test option)";
            return sink;
        }
        inventory = inspect_outputs(true);
        const auto route = choose_audio_route(inventory.audio, output == Output::Hdmi, requested_audio_sink);
        const std::string& destination = route.sink_name;
        if(route.reset_previous_selection) {
            requested_audio_sink.clear();
            state.notice = "Previous audio output is unavailable; using automatic audio output";
        } else if(route.hdmi_fallback) {
            state.notice = "HDMI audio unavailable; using onboard/system audio";
        }
        // Let playbin activate this sink only if the file has an audio stream.
        // A silent video can therefore play even when no sound server exists;
        // audio-bearing files still report the real PulseAudio connection error.
        GstElement* sink = gst_element_factory_make("pulsesink", "tyrell-audio-output");
        if(sink && !destination.empty()) g_object_set(sink, "device", destination.c_str(), nullptr);
        if(!sink) error = "Missing GStreamer PulseAudio output plugin";
        if(sink) state.audio_output = destination.empty() ? "System default" : destination;
        return sink;
    }

    void apply_subtitle_selection(int index) {
        if(!pipeline) return;
        subtitles_enabled = index >= 0;
        guint flags = 0;
        g_object_get(pipeline, "flags", &flags, nullptr);
        // Keep text discovery enabled even when captions are hidden. Clearing
        // GST_PLAY_FLAG_TEXT prevents playbin from opening an external suburi.
        if(!(flags & text_flag)) g_object_set(pipeline, "flags", flags | text_flag, nullptr);
        if(index >= 0) g_object_set(pipeline, "current-text", index, nullptr);
        std::vector<GstElement*> overlays;
        {
            std::lock_guard<std::mutex> lock(subtitle_mutex);
            for(auto* overlay : subtitle_overlays) overlays.push_back(GST_ELEMENT(gst_object_ref(overlay)));
        }
        for(auto* overlay : overlays) {
            g_object_set(overlay, "silent", index < 0, nullptr);
            gst_object_unref(overlay);
        }
        state.subtitle_track = index;
    }

    bool create_pipeline(double position, bool autoplay, int audio_track, int subtitle_track) {
        std::string error;
        pipeline = gst_element_factory_make("playbin", "tyrell-player");
        if(!pipeline) { state.error = "Missing GStreamer playbin plugin"; return false; }
        subtitles_enabled = subtitle_track >= 0;
        g_signal_connect(pipeline, "element-setup", G_CALLBACK(element_setup), this);
        GstElement* video = make_video_sink(state.output, error);
        if(!video) { state.error = error; destroy_pipeline(); return false; }
        GstElement* audio = make_audio_sink(state.output, error);
        if(!audio) { gst_object_unref(video); state.error = error; destroy_pipeline(); return false; }
        const auto uri = uri_for_file(state.path);
        if(uri.empty()) {
            gst_object_unref(video); gst_object_unref(audio);
            state.error = "Cannot convert media path to a file URI"; destroy_pipeline(); return false;
        }
        // Keep our own strong reference until playbin has accepted the sinks.
        gst_object_ref_sink(video);
        gst_object_ref_sink(audio);
        subtitle_font = state.output == Output::Lcd ? "Sans 30px" : "Sans 18px";
        g_object_set(pipeline, "video-sink", video, "audio-sink", audio,
                     "uri", uri.c_str(), "volume", state.volume, "mute", state.muted,
                     "subtitle-font-desc", subtitle_font.c_str(), nullptr);
        audio_sink = audio;
        gst_object_unref(video);
        gst_object_unref(audio);
        if(!external_subtitle.empty()) {
            const auto subtitle_uri = uri_for_file(external_subtitle);
            if(!subtitle_uri.empty()) g_object_set(pipeline, "suburi", subtitle_uri.c_str(), nullptr);
        }
        guint flags = 0;
        g_object_get(pipeline, "flags", &flags, nullptr);
        flags &= ~visualization_flag;
        flags |= text_flag;
        g_object_set(pipeline, "flags", flags, nullptr);
        bus = gst_element_get_bus(pipeline);
        restart_position = position;
        restart_audio_track = audio_track;
        restart_subtitle_track = subtitle_track;
        pending_preroll = true;
        desired_playing = autoplay;
        ended = false;
        load_started = std::chrono::steady_clock::now();
        state.playing = false;
        state.paused = !autoplay;
        state.buffering = true;
        if(gst_element_set_state(pipeline, GST_STATE_PAUSED) == GST_STATE_CHANGE_FAILURE) {
            state.error = "Cannot prepare playback; check the media and output devices";
            destroy_pipeline();
            return false;
        }
        return true;
    }

    void refresh_tracks() {
        if(!pipeline) return;
        int count = 0;
        g_object_get(pipeline, "n-audio", &count, nullptr);
        state.audio_tracks.clear();
        for(int index = 0; index < count; ++index) {
            GstTagList* tags = nullptr;
            g_signal_emit_by_name(pipeline, "get-audio-tags", index, &tags);
            Track track;
            track.index = index;
            track.title = tag_string(tags, GST_TAG_TITLE);
            track.language = tag_string(tags, GST_TAG_LANGUAGE_CODE);
            track.codec = tag_string(tags, GST_TAG_AUDIO_CODEC);
            if(track.title.empty()) track.title = "Audio " + std::to_string(index + 1);
            state.audio_tracks.push_back(std::move(track));
            if(tags) gst_tag_list_unref(tags);
        }
        g_object_get(pipeline, "n-text", &count, nullptr);
        state.subtitle_tracks.clear();
        for(int index = 0; index < count; ++index) {
            GstTagList* tags = nullptr;
            g_signal_emit_by_name(pipeline, "get-text-tags", index, &tags);
            Track track;
            track.index = index;
            track.title = tag_string(tags, GST_TAG_TITLE);
            track.language = tag_string(tags, GST_TAG_LANGUAGE_CODE);
            track.codec = tag_string(tags, GST_TAG_SUBTITLE_CODEC);
            // External subtitle tags are not guaranteed to identify a filename;
            // avoid falsely labelling an embedded stream as the external track.
            if(track.title.empty()) track.title = "Subtitles " + std::to_string(index + 1);
            if(text_track_is_bitmap(index) || bitmap_subtitle_codec(track.codec)) track.title += " (unsupported bitmap)";
            state.subtitle_tracks.push_back(std::move(track));
            if(tags) gst_tag_list_unref(tags);
        }
        g_object_get(pipeline, "current-audio", &state.audio_track, nullptr);
    }

    bool text_track_is_bitmap(int index) const {
        GstPad* pad = nullptr;
        g_signal_emit_by_name(pipeline, "get-text-pad", index, &pad);
        if(!pad) return false;
        GstCaps* caps = gst_pad_get_current_caps(pad);
        const bool result = bitmap_subtitle_caps(caps);
        if(caps) gst_caps_unref(caps);
        gst_object_unref(pad);
        return result;
    }

    void query() {
        if(!pipeline) return;
        gint64 value = 0;
        if(gst_element_query_position(pipeline, GST_FORMAT_TIME, &value) && value >= 0) state.position = static_cast<double>(value) / GST_SECOND;
        if(gst_element_query_duration(pipeline, GST_FORMAT_TIME, &value) && value >= 0) state.duration = static_cast<double>(value) / GST_SECOND;
        refresh_tracks();
        int current = 0;
        g_object_get(pipeline, "current-video", &current, nullptr);
        GstPad* pad = nullptr;
        if(current >= 0) g_signal_emit_by_name(pipeline, "get-video-pad", current, &pad);
        if(pad) {
            GstCaps* caps = gst_pad_get_current_caps(pad);
            GstVideoInfo info;
            gst_video_info_init(&info);
            if(caps && gst_video_info_from_caps(&info, caps)) {
                state.source_width = GST_VIDEO_INFO_WIDTH(&info);
                state.source_height = GST_VIDEO_INFO_HEIGHT(&info);
                const double dar = static_cast<double>(state.source_width) * GST_VIDEO_INFO_PAR_N(&info) /
                                   (std::max(1, state.source_height) * std::max(1, GST_VIDEO_INFO_PAR_D(&info)));
                const double output_width = state.output == Output::Lcd ? lcd_width : hdmi.mode().width;
                const double output_height = state.output == Output::Lcd ? lcd_height : hdmi.mode().height;
                const double fitted_width = std::max(1.0, std::min(output_width, output_height * dar));
                const double desired_pixels = state.output == Output::Lcd ? 15.0 : 30.0;
                // Pango auto-resize uses source width / 640, so its projected
                // size after square-pixel fitting is independent of source size.
                std::ostringstream font;
                font << "Sans " << std::fixed << std::setprecision(2) << desired_pixels * 640.0 / fitted_width << "px";
                if(font.str() != subtitle_font) {
                    subtitle_font = font.str();
                    g_object_set(pipeline, "subtitle-font-desc", subtitle_font.c_str(), nullptr);
                }
            }
            if(caps) gst_caps_unref(caps);
            gst_object_unref(pad);
        }
        state.decoded_frames = std::max(received.load(), qos_processed);
        state.dropped_frames = qos_dropped;
        state.received_frames = received.load();
        state.qos_processed = qos_processed;
        if(state.video_decoder.empty()) {
            GstIterator* iterator = gst_bin_iterate_recurse(GST_BIN(pipeline));
            GValue value = G_VALUE_INIT;
            for(;;) {
                const auto result = gst_iterator_next(iterator, &value);
                if(result == GST_ITERATOR_RESYNC) { gst_iterator_resync(iterator); continue; }
                if(result != GST_ITERATOR_OK) break;
                GstElement* element = GST_ELEMENT(g_value_get_object(&value));
                GstElementFactory* factory = gst_element_get_factory(element);
                const char* klass = factory ? gst_element_factory_get_metadata(factory, GST_ELEMENT_METADATA_KLASS) : nullptr;
                if(klass && std::strstr(klass, "Decoder") && std::strstr(klass, "Video"))
                    state.video_decoder = gst_plugin_feature_get_name(GST_PLUGIN_FEATURE(factory));
                g_value_reset(&value);
                if(!state.video_decoder.empty()) break;
            }
            if(G_IS_VALUE(&value)) g_value_unset(&value);
            gst_iterator_free(iterator);
        }
    }

    void preroll_finished() {
        if(!pending_preroll || !pipeline) return;
        pending_preroll = false;
        refresh_tracks();
        if(restart_audio_track >= 0 && restart_audio_track < static_cast<int>(state.audio_tracks.size()))
            g_object_set(pipeline, "current-audio", restart_audio_track, nullptr);
        apply_subtitle_selection(restart_subtitle_track);
        if(restart_position > 0 && !gst_element_seek_simple(pipeline, GST_FORMAT_TIME,
                static_cast<GstSeekFlags>(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_ACCURATE),
                static_cast<gint64>(restart_position * GST_SECOND))) state.notice = "This file cannot seek; playback starts at the beginning";
        gst_element_set_state(pipeline, desired_playing ? GST_STATE_PLAYING : GST_STATE_PAUSED);
        state.buffering = false;
        state.paused = !desired_playing;
    }
};

Playback::Playback(PlaybackOptions options) : impl_(std::make_unique<Impl>(options)) {}
Playback::~Playback() = default;

bool Playback::load(const std::string& path, const std::string& external_subtitle) {
    auto& p = *impl_;
    if(!p.initialized) return false;
    std::error_code error;
    const auto absolute = std::filesystem::absolute(path, error);
    if(error || !std::filesystem::is_regular_file(absolute, error)) {
        p.state.error = "Media file is missing or unreadable";
        return false;
    }
    p.destroy_pipeline();
    const Output output = p.state.output;
    const double volume = p.state.volume;
    const bool muted = p.state.muted;
    p.state = {};
    p.state.output = output;
    p.state.volume = volume;
    p.state.muted = muted;
    p.state.path = absolute.string();
    p.state.loaded = true;
    p.received = 0;
    p.qos_processed = p.qos_dropped = 0;
    p.bitmap_blocked = false;
    p.bitmap_notice_shown = false;
    p.external_subtitle.clear();
    if(!external_subtitle.empty()) {
        const auto subtitle = std::filesystem::absolute(external_subtitle, error);
        if(!error && std::filesystem::is_regular_file(subtitle, error)) p.external_subtitle = subtitle.string();
        else p.state.notice = "External subtitle file is unavailable";
    }
    if(p.create_pipeline(0, true, -1, -1)) return true;
    if(output == Output::Hdmi) {
        const auto hdmi_error = p.state.error;
        p.state.output = Output::Lcd;
        p.state.error.clear();
        p.state.notice = hdmi_error + "; using LCD";
        if(p.create_pipeline(0, true, -1, -1)) return true;
    }
    p.state.loaded = false;
    return false;
}

void Playback::poll() {
    auto& p = *impl_;
    if(!p.pipeline) return;
    while(GstMessage* message = gst_bus_pop(p.bus)) {
        bool fatal = false;
        switch(GST_MESSAGE_TYPE(message)) {
            case GST_MESSAGE_ERROR: {
                GError* error = nullptr;
                gchar* debug = nullptr;
                gst_message_parse_error(message, &error, &debug);
                p.state.error = error ? error->message : "Playback failed";
                if(error) g_error_free(error);
                g_free(debug);
                p.state.playing = false;
                p.state.paused = true;
                p.state.buffering = false;
                fatal = true;
                break;
            }
            case GST_MESSAGE_EOS:
                p.ended = true;
                p.desired_playing = false;
                p.state.playing = false;
                p.state.paused = true;
                p.state.position = p.state.duration;
                gst_element_set_state(p.pipeline, GST_STATE_PAUSED);
                break;
            case GST_MESSAGE_ASYNC_DONE: p.preroll_finished(); break;
            case GST_MESSAGE_STATE_CHANGED:
                if(GST_MESSAGE_SRC(message) == GST_OBJECT(p.pipeline)) {
                    GstState old_state, new_state, pending;
                    gst_message_parse_state_changed(message, &old_state, &new_state, &pending);
                    p.state.playing = new_state == GST_STATE_PLAYING;
                    p.state.paused = new_state == GST_STATE_PAUSED && !p.pending_preroll;
                }
                break;
            case GST_MESSAGE_BUFFERING: {
                gint percent = 100;
                gst_message_parse_buffering(message, &percent);
                p.state.buffering = percent < 100;
                if(!p.pending_preroll) gst_element_set_state(p.pipeline, percent < 100 ? GST_STATE_PAUSED : (p.desired_playing ? GST_STATE_PLAYING : GST_STATE_PAUSED));
                break;
            }
            case GST_MESSAGE_QOS: {
                GstFormat format;
                guint64 processed = 0, dropped = 0;
                gst_message_parse_qos_stats(message, &format, &processed, &dropped);
                if(format == GST_FORMAT_BUFFERS) {
                    p.qos_processed = std::max(p.qos_processed, processed);
                    p.qos_dropped = std::max(p.qos_dropped, dropped);
                }
                break;
            }
            case GST_MESSAGE_TAG: case GST_MESSAGE_STREAM_COLLECTION: case GST_MESSAGE_DURATION_CHANGED:
                p.last_query = {};
                break;
            default: break;
        }
        gst_message_unref(message);
        if(fatal) {
            if(p.state.output == Output::Hdmi) {
                const auto before = p.state;
                p.destroy_pipeline();
                p.state.output = Output::Lcd;
                p.state.error.clear();
                p.state.notice = before.error + "; paused on LCD";
                if(!p.create_pipeline(before.position, false, before.audio_track, before.subtitle_track))
                    p.state.error = "HDMI failed and LCD recovery failed: " + p.state.error;
            } else p.destroy_pipeline();
            return;
        }
    }
    const auto now = std::chrono::steady_clock::now();
    if(p.bitmap_blocked.exchange(false) && !p.bitmap_notice_shown) {
        p.bitmap_notice_shown = true;
        p.state.notice = "Bitmap subtitle tracks are not supported; text subtitles remain available";
    }
    if(p.pending_preroll && now - p.load_started > std::chrono::seconds(15)) {
        p.state.error = "Playback did not become ready within 15 seconds";
        p.state.playing = false;
        p.state.buffering = false;
        p.destroy_pipeline();
        return;
    }
    if(now - p.last_query >= std::chrono::milliseconds(250)) { p.query(); p.last_query = now; }
    if(p.state.output == Output::Hdmi && now - p.last_hotplug >= std::chrono::milliseconds(500)) {
        p.last_hotplug = now;
        if(!p.hdmi.connected()) {
            pause();
            if(set_output(Output::Lcd)) p.state.notice = "HDMI disconnected; paused on LCD. Press Space to resume";
        }
    }
}

Status Playback::status() const { return impl_->state; }
std::shared_ptr<const Frame> Playback::latest_frame() const {
    std::lock_guard<std::mutex> lock(impl_->frame_mutex);
    return impl_->frame;
}

bool Playback::play() {
    auto& p = *impl_;
    if(!p.pipeline) return false;
    if(p.ended) { p.ended = false; seek(0); }
    p.desired_playing = true;
    if(p.pending_preroll) return true;
    return gst_element_set_state(p.pipeline, GST_STATE_PLAYING) != GST_STATE_CHANGE_FAILURE;
}
bool Playback::pause() {
    auto& p = *impl_;
    if(!p.pipeline) return false;
    p.desired_playing = false;
    p.state.playing = false;
    p.state.paused = true;
    return gst_element_set_state(p.pipeline, GST_STATE_PAUSED) != GST_STATE_CHANGE_FAILURE;
}
bool Playback::toggle_pause() { return impl_->desired_playing ? pause() : play(); }
bool Playback::seek(double seconds) {
    auto& p = *impl_;
    if(!p.pipeline || !std::isfinite(seconds)) return false;
    seconds = std::max(0.0, seconds);
    if(p.state.duration > 0) seconds = std::min(seconds, std::max(0.0, p.state.duration - 0.01));
    if(p.pending_preroll) { p.restart_position = seconds; return true; }
    const bool result = gst_element_seek_simple(p.pipeline, GST_FORMAT_TIME,
        static_cast<GstSeekFlags>(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_ACCURATE), static_cast<gint64>(seconds * GST_SECOND));
    if(result) { p.state.position = seconds; p.ended = false; p.clear_frame(); }
    else p.state.notice = "This file does not support seeking";
    return result;
}
bool Playback::seek_relative(double seconds) { return seek(impl_->state.position + seconds); }

bool Playback::set_output(Output output) {
    auto& p = *impl_;
    if(output == p.state.output) return true;
    if(!p.pipeline) { p.state.output = output; return true; }
    p.query();
    const auto before = p.state;
    const bool autoplay = p.desired_playing;
    p.destroy_pipeline();
    p.state.output = output;
    p.state.error.clear();
    if(p.create_pipeline(before.position, autoplay, before.audio_track, before.subtitle_track)) return true;
    const std::string failure = p.state.error;
    p.state.output = before.output;
    p.state.error.clear();
    p.state.notice = failure;
    if(!p.create_pipeline(before.position, autoplay, before.audio_track, before.subtitle_track)) p.state.error = failure + "; previous output also failed: " + p.state.error;
    return false;
}

bool Playback::set_audio_track(int index) {
    auto& p = *impl_;
    if(!p.pipeline || index < 0 || index >= static_cast<int>(p.state.audio_tracks.size())) return false;
    p.state.audio_track = index;
    p.restart_audio_track = index;
    g_object_set(p.pipeline, "current-audio", index, nullptr);
    return true;
}
bool Playback::set_subtitle_track(int index) {
    auto& p = *impl_;
    if(!p.pipeline || index < -1 || index >= static_cast<int>(p.state.subtitle_tracks.size())) return false;
    if(index >= 0 && (p.text_track_is_bitmap(index) || bitmap_subtitle_codec(p.state.subtitle_tracks[index].codec))) {
        p.state.notice = "Bitmap subtitle tracks are not supported";
        return false;
    }
    p.restart_subtitle_track = index;
    p.apply_subtitle_selection(index);
    // Pango remains in the running pipeline while hidden. Avoid tearing down
    // playbin during a concurrent asynchronous audio-stream switch; a paused
    // frame only needs a flushing seek to redraw with the new caption setting.
    if(!p.desired_playing && !p.pending_preroll) return seek(p.state.position);
    return true;
}
bool Playback::set_audio_sink(const std::string& sink_name) {
    auto& p = *impl_;
    if(!sink_name.empty()) {
        const auto inventory = inspect_outputs(true);
        if(std::none_of(inventory.audio.begin(), inventory.audio.end(), [&](const AudioSink& item) { return item.name == sink_name && item.available; })) {
            p.state.notice = "Selected audio output is unavailable";
            return false;
        }
    }
    p.requested_audio_sink = sink_name;
    if(p.audio_sink && g_object_class_find_property(G_OBJECT_GET_CLASS(p.audio_sink), "device")) {
        std::string destination = sink_name;
        if(destination.empty()) {
            const auto inventory = inspect_outputs(true);
            const auto match = std::find_if(inventory.audio.begin(), inventory.audio.end(), [&](const AudioSink& item) {
                return item.available && (p.state.output == Output::Hdmi ? item.hdmi : item.onboard);
            });
            if(match != inventory.audio.end()) destination = match->name;
        }
        g_object_set(p.audio_sink, "device", destination.empty() ? nullptr : destination.c_str(), nullptr);
        p.state.audio_output = destination.empty() ? "System default" : destination;
    }
    return true;
}
void Playback::set_volume(double volume) {
    auto& p = *impl_;
    if(!std::isfinite(volume)) return;
    p.state.volume = std::clamp(volume, 0.0, 1.0);
    if(p.pipeline) g_object_set(p.pipeline, "volume", p.state.volume, nullptr);
}
void Playback::set_muted(bool muted) {
    impl_->state.muted = muted;
    if(impl_->pipeline) g_object_set(impl_->pipeline, "mute", muted, nullptr);
}
void Playback::stop() {
    auto& p = *impl_;
    p.destroy_pipeline();
    p.state.loaded = p.state.playing = p.state.paused = p.state.buffering = false;
    p.state.path.clear();
    p.state.position = p.state.duration = 0;
    p.state.audio_tracks.clear();
    p.state.subtitle_tracks.clear();
    p.state.audio_track = p.state.subtitle_track = -1;
    p.state.error.clear();
    p.state.notice.clear();
}

std::string Playback::diagnostics_json() const {
    const auto& p = *impl_;
    guint major = 0, minor = 0, micro = 0, nano = 0;
    gst_version(&major, &minor, &micro, &nano);
    const auto inventory = inspect_outputs(true);
    std::size_t subtitle_renderer_count = 0;
    {
        std::lock_guard<std::mutex> lock(p.subtitle_mutex);
        subtitle_renderer_count = p.subtitle_overlays.size();
    }
    std::ostringstream out;
    out << "{\"gstreamer\":" << json_string(std::to_string(major) + "." + std::to_string(minor) + "." + std::to_string(micro))
        << ",\"loaded\":" << (p.state.loaded ? "true" : "false") << ",\"playing\":" << (p.state.playing ? "true" : "false")
        << ",\"output\":" << json_string(p.state.output == Output::Lcd ? "lcd" : "hdmi")
        << ",\"path\":" << json_string(p.state.path) << ",\"position_seconds\":" << p.state.position
        << ",\"duration_seconds\":" << p.state.duration << ",\"received_frames\":" << p.received.load()
        << ",\"video_decoder\":" << json_string(p.state.video_decoder) << ",\"fake_audio\":" << (p.options.fake_audio ? "true" : "false")
        << ",\"audio_output\":" << json_string(p.state.audio_output)
        << ",\"subtitle_renderer_count\":" << subtitle_renderer_count
        << ",\"qos_processed\":" << p.qos_processed << ",\"qos_dropped\":" << p.qos_dropped
        << ",\"error\":" << json_string(p.state.error) << ",\"notice\":" << json_string(p.state.notice)
        << ",\"registry\":" << plugin_diagnostics_json()
        << ",\"device_access\":" << device_access_json(inventory)
        << ",\"lcd_appsink\":{\"max_buffers\":1,\"drop\":true,\"sync\":true,\"format\":\"RGB565\",\"width\":320,\"height\":170,\"mailbox_frames\":1}"
        << ",\"outputs\":" << output_inventory_json(inventory) << '}';
    return out.str();
}

} // namespace tyrell::video
