#include "playback.h"
#include "outputs.h"
#include "hdmi_lease.h"
#include "hdmi_video_caps.h"
#include "hdmi_audio.h"
#include "json_string.h"
#include "lcd_frame.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cctype>
#include <cmath>
#include <cstring>
#include <exception>
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
    GstElement* video_sink = nullptr; // borrowed; valid while pipeline exists
    HdmiSession hdmi;
    HdmiDesktopLease desktop_lease;
    HdmiAudioSession hdmi_audio;
    bool audio_attempted = false;
    bool audio_fallback_attempted = false;
    bool audio_recovery_attempted = false;
    bool audio_owned_seen = false;
    bool audio_restart_pending = false;
    bool pending_hdmi = false;
    bool initial_hdmi_pending = false;
    bool initial_hdmi_direct = false;
    std::string initial_handoff_error;
    std::string external_subtitle;
    std::string requested_audio_sink;
    std::string subtitle_font;
    OutputInventory inventory;
    mutable std::mutex frame_mutex;
    std::shared_ptr<const Frame> frame;
    std::atomic<std::uint64_t> received{0};
    std::atomic<std::uint64_t> lcd_caption_compositions{0};
    LcdFrameRenderer lcd_renderer;
    std::atomic<std::uint64_t> video_buffers_delivered{0};
    std::atomic<std::uint64_t> video_frames_decoded{0};
    std::atomic<bool> video_memory_sampled{false};
    mutable std::mutex video_metadata_mutex;
    std::string video_output_memory;
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
    std::chrono::steady_clock::time_point file_load_started{};

    double load_elapsed() const {
        return std::chrono::duration<double>(std::chrono::steady_clock::now() - file_load_started).count();
    }

    explicit Impl(PlaybackOptions requested_options) : options(std::move(requested_options)),
        desktop_lease(options.desktop_handoff_socket) {
        GError* error = nullptr;
        initialized = gst_init_check(nullptr, nullptr, &error);
        if(!initialized) state.error = error ? error->message : "Cannot initialize GStreamer";
        if(error) g_error_free(error);
    }

    ~Impl() { destroy_pipeline(); hdmi_audio.release(); desktop_lease.release(); }

    void clear_frame() {
        std::lock_guard<std::mutex> lock(frame_mutex);
        frame.reset();
    }

    struct Restart {
        double position;
        int audio_track;
        int subtitle_track;
    };

    Restart restart_target() const {
        // Preroll queries describe the new pipeline's initial position, before
        // the queued seek and track selections have been applied.
        return pending_preroll ? Restart{restart_position, restart_audio_track, restart_subtitle_track}
                               : Restart{state.position, restart_audio_track >= 0 ? restart_audio_track : state.audio_track,
                                         state.subtitle_track};
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
            video_sink = nullptr;
        }
        video_memory_sampled = false;
        lcd_renderer.reset();
        audio_restart_pending = false;
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

    static gboolean lcd_allocation(GstAppSink*, GstQuery* query, gpointer) {
        // Caps alone are insufficient: Pango prefers CPU blitting unless the
        // downstream allocation query also advertises a composition consumer.
        guint index = 0;
        if(!gst_query_find_allocation_meta(query, GST_VIDEO_OVERLAY_COMPOSITION_META_API_TYPE, &index))
            gst_query_add_allocation_meta(query, GST_VIDEO_OVERLAY_COMPOSITION_META_API_TYPE, nullptr);
        return TRUE;
    }

    static GstPadProbeReturn decoded_delivery(GstPad*, GstPadProbeInfo* info, gpointer data) {
        auto& player = *static_cast<Impl*>(data);
        if(GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER_LIST) {
            auto* list = GST_PAD_PROBE_INFO_BUFFER_LIST(info);
            if(list) player.video_frames_decoded.fetch_add(gst_buffer_list_length(list));
        } else if(GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER)
            player.video_frames_decoded.fetch_add(1);
        return GST_PAD_PROBE_OK;
    }

    static GstPadProbeReturn video_delivery(GstPad*, GstPadProbeInfo* info, gpointer data) {
        auto& player = *static_cast<Impl*>(data);
        if(GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER_LIST) {
            GstBufferList* buffers = GST_PAD_PROBE_INFO_BUFFER_LIST(info);
            if(buffers) player.video_buffers_delivered.fetch_add(gst_buffer_list_length(buffers));
        } else if(GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER) {
            player.video_buffers_delivered.fetch_add(1);
        }
        GstBuffer* first = GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER
            ? GST_PAD_PROBE_INFO_BUFFER(info) : nullptr;
        if(!first && (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER_LIST)) {
            auto* list = GST_PAD_PROBE_INFO_BUFFER_LIST(info);
            if(list && gst_buffer_list_length(list)) first = gst_buffer_list_get(list, 0);
        }
        if(first && !player.video_memory_sampled.exchange(true)) {
            std::string types;
            for(guint i = 0; i < gst_buffer_n_memory(first); ++i) {
                auto* memory = gst_buffer_peek_memory(first, i);
                if(!types.empty()) types += ',';
                types += memory && memory->allocator && memory->allocator->mem_type ? memory->allocator->mem_type : "unknown";
            }
            std::lock_guard<std::mutex> lock(player.video_metadata_mutex);
            player.video_output_memory = std::move(types);
        }
        return GST_PAD_PROBE_OK;
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
        const char* klass = gst_element_factory_get_metadata(factory, GST_ELEMENT_METADATA_KLASS);
        if(klass && std::strstr(klass, "Decoder") && std::strstr(klass, "Video")) {
            if(GstPad* pad = gst_element_get_static_pad(element, "src")) {
                gst_pad_add_probe(pad, static_cast<GstPadProbeType>(GST_PAD_PROBE_TYPE_BUFFER | GST_PAD_PROBE_TYPE_BUFFER_LIST),
                                  decoded_delivery, data, nullptr);
                gst_object_unref(pad);
            }
        }
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
        std::unique_ptr<GstSample, void(*)(GstSample*)> source(sample, gst_sample_unref);
        try {
            auto next = std::make_shared<Frame>();
            bool composed = false;
            std::string error;
            const bool rendered = lcd_renderer.render(sample, *next, composed, error);
            if(!rendered) {
                GST_ELEMENT_ERROR(video_sink, STREAM, FORMAT, ("Cannot render LCD video or captions"), ("%s", error.c_str()));
                return GST_FLOW_ERROR;
            }
            if(composed) lcd_caption_compositions.fetch_add(1);
            next->serial = received.fetch_add(1) + 1;
            // Replacing this single snapshot bounds storage even if the UI stalls.
            std::lock_guard<std::mutex> lock(frame_mutex);
            frame = std::move(next);
            return GST_FLOW_OK;
        } catch(const std::exception& error) {
            GST_ELEMENT_ERROR(video_sink, RESOURCE, FAILED, ("Cannot prepare LCD video storage"), ("%s", error.what()));
            return GST_FLOW_ERROR;
        }
    }

    GstElement* make_video_sink(Output output, std::string& error) {
        GstElement* bin = gst_bin_new("tyrell-video-output");
        GstElement* convert = gst_element_factory_make("videoconvert", nullptr);
        GstElement* scale = gst_element_factory_make("videoscale", nullptr);
        GstElement* fitted = output == Output::Lcd ? gst_element_factory_make("capsfilter", nullptr) : nullptr;
        GstElement* filter = gst_element_factory_make("capsfilter", nullptr);
        GstElement* sink = gst_element_factory_make(output == Output::Lcd ? "appsink" : "kmssink", "tyrell-video-sink");
        if(!bin || !convert || !scale || (output == Output::Lcd && !fitted) || !filter || !sink) {
            if(convert) gst_object_unref(convert);
            if(scale) gst_object_unref(scale);
            if(fitted) gst_object_unref(fitted);
            if(filter) gst_object_unref(filter);
            if(sink) gst_object_unref(sink);
            if(bin) gst_object_unref(bin);
            error = output == Output::Lcd ? "Missing GStreamer video conversion/appsink plugin" : "Missing GStreamer kmssink plugin";
            return nullptr;
        }
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
            g_object_set(sink, "fd", hdmi.fd(), "connector-id", static_cast<int>(hdmi.connector_id()),
                         "plane-id", static_cast<int>(hdmi.plane_id()),
                         "force-modesetting", FALSE, "can-scale", TRUE,
                         // VC4's atomic plane commit already waits for vblank.
                         // GStreamer still synchronizes frames to its A/V clock.
                         "skip-vsync", TRUE, "sync", TRUE, "qos", TRUE, nullptr);
            if(g_object_class_find_property(G_OBJECT_GET_CLASS(sink), "restore-crtc"))
                g_object_set(sink, "restore-crtc", TRUE, nullptr);
        } else {
            g_object_set(sink, "sync", TRUE, "max-buffers", 1U, "drop", TRUE,
                         "enable-last-sample", FALSE, "wait-on-eos", FALSE, "qos", TRUE, nullptr);
            GstAppSinkCallbacks callbacks{};
            callbacks.new_sample = sample_ready;
            callbacks.new_preroll = preroll_ready;
            callbacks.propose_allocation = lcd_allocation;
            gst_app_sink_set_callbacks(GST_APP_SINK(sink), &callbacks, this, nullptr);
        }
        g_object_set(scale, "add-borders", output == Output::Hdmi, nullptr);
        // Keep HDMI video at its native size when possible. The owned KMS
        // overlay scales and letterboxes it. Preserve the decoder's planar
        // YUV/DMABuf layout: forcing NV12 makes HD playback CPU-bound. BT.709 limited matches
        // the overlay's explicitly configured YUV conversion properties.
        GstCaps* caps = output == Output::Hdmi
            ? hdmi_video_caps(hdmi.video_formats())
            : lcd_video_caps(true);
        g_object_set(filter, "caps", caps, nullptr);
        gst_caps_unref(caps);
        if(fitted) {
            // Fit without borders so composition coordinates scale with the
            // picture. The bounded consumer adds the final black bars after
            // blending captions, avoiding full-resolution per-frame blits.
            GstCaps* fitted_caps = lcd_video_caps(false);
            g_object_set(fitted, "caps", fitted_caps, nullptr);
            gst_caps_unref(fitted_caps);
            gst_bin_add(GST_BIN(bin), fitted);
        }
        gst_bin_add_many(GST_BIN(bin), convert, scale, filter, sink, nullptr);
        const bool linked = fitted ? gst_element_link_many(scale, fitted, convert, filter, sink, nullptr)
                                   : gst_element_link_many(convert, scale, filter, sink, nullptr);
        if(!linked) {
            error = "Cannot link video output pipeline";
            gst_object_unref(bin);
            hdmi.release();
            return nullptr;
        }
        GstPad* pad = gst_element_get_static_pad(fitted ? scale : convert, "sink");
        GstPad* ghost = gst_ghost_pad_new("sink", pad);
        gst_object_unref(pad);
        if(!ghost || !gst_element_add_pad(bin, ghost)) {
            if(ghost) gst_object_unref(ghost);
            error = "Cannot create video output pad";
            gst_object_unref(bin);
            hdmi.release();
            return nullptr;
        }
        // Count actual video payloads at either final sink. This survives
        // pause/segment/flush resets of GstBaseSink's rendering statistics.
        if(GstPad* sink_pad = gst_element_get_static_pad(sink, "sink")) {
            gst_pad_add_probe(sink_pad,
                static_cast<GstPadProbeType>(GST_PAD_PROBE_TYPE_BUFFER | GST_PAD_PROBE_TYPE_BUFFER_LIST),
                video_delivery, this, nullptr);
            gst_object_unref(sink_pad);
        }
        video_sink = sink;
        return bin;
    }

    static bool temporary_audio(const std::string& name) {
        return name.rfind("tyrell_video_hdmi_", 0) == 0;
    }

    OutputInventory audio_outputs() const {
        auto result = inspect_outputs(true);
        // A previous player's temporary sink may still be unloading. Only the
        // lease owned by this instance has a useful, predictable lifetime.
        result.audio.erase(std::remove_if(result.audio.begin(), result.audio.end(), [&](const AudioSink& sink) {
            return temporary_audio(sink.name) &&
                   !(hdmi_audio.state() == HdmiAudioSession::State::Ready && sink.name == hdmi_audio.sink_name());
        }), result.audio.end());
        return result;
    }

    bool explicit_owned_audio() const {
        return hdmi_audio.state() == HdmiAudioSession::State::Ready &&
               requested_audio_sink == hdmi_audio.sink_name();
    }

    void release_hdmi_audio(bool force = false) {
        if(!force && explicit_owned_audio()) return;
        if(requested_audio_sink == hdmi_audio.sink_name()) requested_audio_sink.clear();
        hdmi_audio.release();
        audio_attempted = false;
        audio_fallback_attempted = false;
        audio_recovery_attempted = false;
        audio_owned_seen = false;
    }

    void move_audio(const AudioRoute& route) {
        if(route.reset_previous_selection) requested_audio_sink.clear();
        if(options.fake_audio) return;
        const std::string target = route.sink_name.empty() ? "System default" : route.sink_name;
        // GStreamer 1.26.2's live pulsesink.device setter leaks its PA mainloop
        // lock when a stream has not acquired an index yet. A preroll/state
        // check cannot close the race with an audio-track reconfiguration.
        // Keep only the latest request and rebuild using a fresh NULL sink;
        // make_audio_sink() is the only place that sets the device property.
        audio_restart_pending = pipeline && target != state.audio_output;
    }

    void apply_audio_restart() {
        if(!audio_restart_pending || !pipeline || pending_preroll || state.buffering) return;
        // Capture at application time, not at enqueue time: intervening seek,
        // pause and track requests must be carried into the replacement.
        query();
        const auto target = restart_target();
        const bool autoplay = desired_playing;
        destroy_pipeline();
        state.error.clear();
        if(create_pipeline(target.position, autoplay, target.audio_track, target.subtitle_track)) {
            if(state.output == Output::Lcd) release_hdmi_audio();
            return;
        }
        if(state.output == Output::Hdmi) fallback_to_lcd("Audio switch failed: " + state.error);
        else {
            release_hdmi_audio(true);
            state.playing = false;
            state.paused = true;
            state.buffering = false;
        }
    }

    void poll_hdmi_audio() {
        if(options.fake_audio || !options.automatic_hdmi_audio || (!pipeline && !initial_hdmi_pending)) return;
        const bool prefer_hdmi = state.output == Output::Hdmi || initial_hdmi_pending;
        const auto before = hdmi_audio.state();
        if(before == HdmiAudioSession::State::Idle && prefer_hdmi && !audio_attempted) {
            inventory = audio_outputs();
            const bool explicit_available = !requested_audio_sink.empty() &&
                std::any_of(inventory.audio.begin(), inventory.audio.end(), [&](const AudioSink& sink) {
                    return sink.available && sink.name == requested_audio_sink;
                });
            const bool hdmi_available = std::any_of(inventory.audio.begin(), inventory.audio.end(), [](const AudioSink& sink) {
                return sink.available && sink.hdmi;
            });
            audio_attempted = true;
            if(explicit_available || hdmi_available) {
                move_audio(choose_audio_route(inventory.audio, true, requested_audio_sink));
                return;
            }
            if(!hdmi_audio.begin()) {
                state.notice = hdmi_audio.error();
                return;
            }
            state.notice = "Preparing TV audio; video controls remain available";
        }
        const auto ready = hdmi_audio.poll();
        if(ready == HdmiAudioSession::State::Ready && before != HdmiAudioSession::State::Ready) {
            audio_owned_seen = true;
            inventory = audio_outputs();
            const auto route = choose_audio_route(inventory.audio, prefer_hdmi, requested_audio_sink);
            move_audio(route);
            state.notice = route.hdmi_fallback ? "TV audio is unavailable; using onboard/system audio" : "Switching to TV audio";
        } else if(ready == HdmiAudioSession::State::Failed && before != HdmiAudioSession::State::Failed) {
            audio_fallback_attempted = true;
            const auto failure = hdmi_audio.error();
            inventory = audio_outputs();
            move_audio(choose_audio_route(inventory.audio, false, requested_audio_sink));
            state.notice = failure;
        }
    }

    bool finish_initial_hdmi(Output output, std::string notice = {}) {
        // No decoder has been started yet. User input while the output leases
        // were pending updated these same intents, so there is no stale clock
        // or asynchronous preroll to query before building the first graph.
        const auto position = restart_position;
        const auto audio_track = restart_audio_track;
        const auto subtitle_track = restart_subtitle_track;
        const bool autoplay = desired_playing;
        const auto handoff_error = std::exchange(initial_handoff_error, {});
        initial_hdmi_pending = false;
        state.output_switching = false;
        state.output = output;
        state.error.clear();
        state.notice = std::move(notice);
        if(output == Output::Lcd) {
            release_hdmi_audio();
            desktop_lease.release();
        }
        if(create_pipeline(position, autoplay, audio_track, subtitle_track)) return true;
        if(output == Output::Hdmi) {
            const auto failure = handoff_error.empty() ? state.error : handoff_error + "; " + state.error;
            state.output = Output::Lcd;
            state.error.clear();
            state.notice = failure + "; using LCD";
            release_hdmi_audio(true);
            desktop_lease.release();
            if(create_pipeline(position, autoplay, audio_track, subtitle_track)) return true;
        }
        state.loaded = state.playing = state.buffering = false;
        state.paused = true;
        release_hdmi_audio(true);
        desktop_lease.release();
        return false;
    }

    void poll_initial_hdmi() {
        if(!initial_hdmi_pending) return;
        const auto lease = initial_hdmi_direct ? HdmiDesktopLease::State::Ready : desktop_lease.poll();
        if(lease == HdmiDesktopLease::State::Failed) {
            finish_initial_hdmi(Output::Lcd, desktop_lease.error() + "; using LCD");
            return;
        }
        // Start sound acquisition while the desktop is releasing DRM master,
        // rather than restarting an already-prerolled decoder for each lease.
        poll_hdmi_audio();
        if(lease == HdmiDesktopLease::State::Waiting || hdmi_audio.state() == HdmiAudioSession::State::Waiting) return;
        const auto notice = hdmi_audio.state() == HdmiAudioSession::State::Failed ? hdmi_audio.error() : std::string{};
        finish_initial_hdmi(Output::Hdmi, notice);
    }

    bool begin_initial_hdmi() {
        initial_hdmi_pending = true;
        state.output = Output::Lcd;
        state.output_switching = true;
        state.buffering = true;
        state.playing = state.paused = false;
        state.notice = "Preparing HDMI and TV audio; O cancels";
        desired_playing = true;
        restart_position = 0;
        restart_audio_track = restart_subtitle_track = -1;
        initial_handoff_error.clear();
        // Older installations can still use a free DRM display even when the
        // handoff service is absent. A waiting lease is reused across a new
        // file selection instead of asking the desktop to hand over twice.
        initial_hdmi_direct = !options.desktop_handoff;
        if(options.desktop_handoff && desktop_lease.state() != HdmiDesktopLease::State::Ready &&
           desktop_lease.state() != HdmiDesktopLease::State::Waiting) {
            initial_hdmi_direct = !desktop_lease.begin();
            if(initial_hdmi_direct) initial_handoff_error = desktop_lease.error();
        }
        poll_initial_hdmi();
        return initial_hdmi_pending || pipeline;
    }

    GstElement* make_audio_sink(Output output, std::string& error) {
        if(options.fake_audio) {
            GstElement* sink = gst_element_factory_make("fakesink", "tyrell-test-audio-output");
            if(sink) g_object_set(sink, "sync", TRUE, nullptr);
            else error = "Missing test fakesink plugin";
            state.audio_output = "fakesink (explicit test option)";
            return sink;
        }
        inventory = audio_outputs();
        const auto route = choose_audio_route(inventory.audio, output == Output::Hdmi && !audio_fallback_attempted, requested_audio_sink);
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
        ++state.pipeline_builds;
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
        if(!pending_preroll && gst_element_query_position(pipeline, GST_FORMAT_TIME, &value) && value >= 0)
            state.position = static_cast<double>(value) / GST_SECOND;
        if(gst_element_query_duration(pipeline, GST_FORMAT_TIME, &value) && value >= 0) state.duration = static_cast<double>(value) / GST_SECOND;
        refresh_tracks();
        int current = 0;
        g_object_get(pipeline, "current-video", &current, nullptr);
        GstPad* pad = nullptr;
        if(current >= 0) g_signal_emit_by_name(pipeline, "get-video-pad", current, &pad);
        if(pad) {
            GstCaps* caps = gst_pad_get_current_caps(pad);
            int width = 0, height = 0, par_n = 1, par_d = 1;
            const GstStructure* fields = caps && gst_caps_get_size(caps) ? gst_caps_get_structure(caps, 0) : nullptr;
            if(fields && gst_structure_get_int(fields, "width", &width) && gst_structure_get_int(fields, "height", &height) && width > 0 && height > 0) {
                gst_structure_get_fraction(fields, "pixel-aspect-ratio", &par_n, &par_d);
                state.source_width = width;
                state.source_height = height;
                const double dar = static_cast<double>(width) * std::max(1, par_n) /
                                   (static_cast<double>(height) * std::max(1, par_d));
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
        state.lcd_caption_compositions = lcd_caption_compositions.load();
        state.video_buffers_delivered = video_buffers_delivered.load();
        state.qos_processed = qos_processed;
        state.video_frames_decoded = video_frames_decoded.load();
        {
            std::lock_guard<std::mutex> lock(video_metadata_mutex);
            state.video_output_memory = video_output_memory;
        }
        if(video_sink) {
            if(g_object_class_find_property(G_OBJECT_GET_CLASS(video_sink), "stats")) {
                GstStructure* stats = nullptr;
                g_object_get(video_sink, "stats", &stats, nullptr);
                if(stats) {
                    guint64 rendered = 0, dropped = 0;
                    gst_structure_get_uint64(stats, "rendered", &rendered);
                    gst_structure_get_uint64(stats, "dropped", &dropped);
                    state.video_sink_rendered = rendered;
                    state.video_sink_dropped = dropped;
                    if(rendered && state.first_render_seconds < 0) state.first_render_seconds = load_elapsed();
                    if(rendered && state.output == Output::Hdmi && state.first_hdmi_render_seconds < 0)
                        state.first_hdmi_render_seconds = load_elapsed();
                    gst_structure_free(stats);
                }
            }
            if(GstPad* sink_pad = gst_element_get_static_pad(video_sink, "sink")) {
                GstCaps* output_caps = gst_pad_get_current_caps(sink_pad);
                if(output_caps) {
                    gchar* text = gst_caps_to_string(output_caps);
                    state.video_output_caps = text ? text : "";
                    g_free(text); gst_caps_unref(output_caps);
                }
                gst_object_unref(sink_pad);
            }
        }
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

    bool recover_audio(std::string failure) {
        query();
        const auto target = restart_target();
        const bool autoplay = desired_playing;
        destroy_pipeline();
        release_hdmi_audio(true);
        audio_attempted = true;
        audio_fallback_attempted = true;
        audio_recovery_attempted = true;
        requested_audio_sink.clear();
        state.error.clear();
        state.notice = failure + "; using onboard/system audio";
        return create_pipeline(target.position, autoplay, target.audio_track, target.subtitle_track);
    }

    bool fallback_to_lcd(std::string notice) {
        // Lease loss and unplug are terminal for the owned HDMI session. A
        // failed LCD recovery must never roll back onto a display we no longer
        // own, or keep the desktop helper waiting for our DRM master fd.
        query();
        const auto target = restart_target();
        destroy_pipeline();
        release_hdmi_audio(true);
        desktop_lease.release();
        pending_hdmi = false;
        state.output_switching = false;
        state.output = Output::Lcd;
        state.error.clear();
        state.notice = std::move(notice);
        if(create_pipeline(target.position, false, target.audio_track, target.subtitle_track)) return true;
        state.error = "HDMI ended and LCD recovery failed: " + state.error;
        return false;
    }

    bool change_output(Output output) {
        if(output == state.output) return true;
        if(!pipeline) { state.output = output; return true; }
        query();
        const auto before = state;
        const auto target = restart_target();
        const bool autoplay = desired_playing;
        destroy_pipeline();
        state.output = output;
        state.error.clear();
        if(create_pipeline(target.position, autoplay, target.audio_track, target.subtitle_track)) {
            if(output == Output::Lcd) {
                release_hdmi_audio();
                desktop_lease.release();
            }
            return true;
        }
        const std::string failure = state.error;
        state.output = before.output;
        state.error.clear();
        state.notice = failure;
        if(!create_pipeline(target.position, autoplay, target.audio_track, target.subtitle_track))
            state.error = failure + "; previous output also failed: " + state.error;
        if(state.output == Output::Lcd || !pipeline) {
            release_hdmi_audio(!pipeline);
            desktop_lease.release();
        }
        return false;
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
                static_cast<gint64>(restart_position * GST_SECOND)))
            state.notice = "This file cannot seek; playback starts at the beginning";
        gst_element_set_state(pipeline, desired_playing ? GST_STATE_PLAYING : GST_STATE_PAUSED);
        GST_DEBUG_BIN_TO_DOT_FILE_WITH_TS(GST_BIN(pipeline), GST_DEBUG_GRAPH_SHOW_ALL, "tyrell-video-prerolled");
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
    const auto accepted_at = std::chrono::steady_clock::now();
    const Output output = p.pending_hdmi || p.initial_hdmi_pending ? Output::Hdmi : p.state.output;
    p.pending_hdmi = false;
    p.initial_hdmi_pending = false;
    p.destroy_pipeline();
    if(output == Output::Lcd) { p.release_hdmi_audio(); p.desktop_lease.release(); }
    const double volume = p.state.volume;
    const bool muted = p.state.muted;
    p.state = {};
    p.state.output = output;
    p.state.volume = volume;
    p.state.muted = muted;
    p.state.path = absolute.string();
    p.state.loaded = true;
    p.file_load_started = accepted_at;
    p.received = 0;
    p.lcd_caption_compositions = 0;
    p.video_buffers_delivered = 0;
    p.video_frames_decoded = 0;
    p.qos_processed = p.qos_dropped = 0;
    p.bitmap_blocked = false;
    p.bitmap_notice_shown = false;
    p.external_subtitle.clear();
    if(!external_subtitle.empty()) {
        const auto subtitle = std::filesystem::absolute(external_subtitle, error);
        if(!error && std::filesystem::is_regular_file(subtitle, error)) p.external_subtitle = subtitle.string();
        else p.state.notice = "External subtitle file is unavailable";
    }
    // The handheld controller is already usable without a video pipeline.
    // Prepare display and sound together before opening the file once; the
    // old LCD -> HDMI -> TV-audio sequence decoded/prerolled it three times.
    if(output == Output::Hdmi) return p.begin_initial_hdmi();
    if(p.create_pipeline(0, true, -1, -1)) return true;
    p.state.loaded = false;
    p.release_hdmi_audio(true);
    p.desktop_lease.release();
    return false;
}

void Playback::poll() {
    auto& p = *impl_;
    if(p.initial_hdmi_pending) {
        p.poll_initial_hdmi();
        if(p.initial_hdmi_pending) return;
    }
    if(p.pending_hdmi) {
        const auto lease = p.desktop_lease.poll();
        if(lease == HdmiDesktopLease::State::Ready) {
            p.pending_hdmi = false;
            p.state.output_switching = false;
            p.state.notice.clear();
            p.change_output(Output::Hdmi);
        } else if(lease == HdmiDesktopLease::State::Failed) {
            p.pending_hdmi = false;
            p.state.output_switching = false;
            p.state.notice = p.desktop_lease.error() + "; using LCD";
            p.desktop_lease.release();
        }
    } else if(p.state.output == Output::Hdmi && p.desktop_lease.state() == HdmiDesktopLease::State::Ready &&
              p.desktop_lease.poll() == HdmiDesktopLease::State::Failed) {
        const auto error = p.desktop_lease.error();
        p.fallback_to_lcd(error + "; paused on LCD");
    }
    if(!p.pipeline) { p.hdmi_audio.poll(); return; }
    while(GstMessage* message = gst_bus_pop(p.bus)) {
        bool fatal = false, failed_audio = false;
        switch(GST_MESSAGE_TYPE(message)) {
            case GST_MESSAGE_ERROR: {
                GError* error = nullptr;
                gchar* debug = nullptr;
                gst_message_parse_error(message, &error, &debug);
                p.state.error = error ? error->message : "Playback failed";
                failed_audio = p.audio_sink && GST_MESSAGE_SRC(message) == GST_OBJECT(p.audio_sink);
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
                    if(p.state.playing && p.state.first_playing_seconds < 0)
                        p.state.first_playing_seconds = p.load_elapsed();
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
            if(failed_audio && (p.state.output == Output::Hdmi || p.audio_owned_seen) &&
               !p.audio_recovery_attempted && p.recover_audio(p.state.error)) return;
            if(p.state.output == Output::Hdmi) {
                p.fallback_to_lcd(p.state.error + "; paused on LCD");
            } else {
                p.destroy_pipeline();
                p.release_hdmi_audio(true);
                p.pending_hdmi = false;
                p.state.output_switching = false;
                p.desktop_lease.release();
            }
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
        p.release_hdmi_audio(true);
        p.pending_hdmi = false;
        p.state.output_switching = false;
        p.desktop_lease.release();
        return;
    }
    p.poll_hdmi_audio();
    p.apply_audio_restart();
    if(now - p.last_query >= std::chrono::milliseconds(250)) { p.query(); p.last_query = now; }
    if(p.state.output == Output::Hdmi && now - p.last_hotplug >= std::chrono::milliseconds(500)) {
        p.last_hotplug = now;
        if(!p.hdmi.connected()) {
            p.fallback_to_lcd("HDMI disconnected; paused on LCD. Press Space to resume");
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
    if(p.initial_hdmi_pending) { p.desired_playing = true; p.state.paused = false; return true; }
    if(!p.pipeline) return false;
    if(p.ended) { p.ended = false; seek(0); }
    p.desired_playing = true;
    if(p.pending_preroll) return true;
    return gst_element_set_state(p.pipeline, GST_STATE_PLAYING) != GST_STATE_CHANGE_FAILURE;
}
bool Playback::pause() {
    auto& p = *impl_;
    if(p.initial_hdmi_pending) { p.desired_playing = false; p.state.paused = true; return true; }
    if(!p.pipeline) return false;
    p.desired_playing = false;
    p.state.playing = false;
    p.state.paused = true;
    return gst_element_set_state(p.pipeline, GST_STATE_PAUSED) != GST_STATE_CHANGE_FAILURE;
}
bool Playback::toggle_pause() { return impl_->desired_playing ? pause() : play(); }
bool Playback::seek(double seconds) {
    auto& p = *impl_;
    if((!p.pipeline && !p.initial_hdmi_pending) || !std::isfinite(seconds)) return false;
    seconds = std::max(0.0, seconds);
    if(p.state.duration > 0) seconds = std::min(seconds, std::max(0.0, p.state.duration - 0.01));
    if(p.pending_preroll || p.initial_hdmi_pending) { p.restart_position = seconds; p.state.position = seconds; p.ended = false; return true; }
    const bool result = gst_element_seek_simple(p.pipeline, GST_FORMAT_TIME,
        static_cast<GstSeekFlags>(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_ACCURATE), static_cast<gint64>(seconds * GST_SECOND));
    // Keep the previous immutable snapshot until the streaming thread replaces
    // it. A fast paused preroll can already be published before seek returns.
    if(result) { p.state.position = seconds; p.ended = false; }
    else p.state.notice = "This file does not support seeking";
    return result;
}
bool Playback::seek_relative(double seconds) { return seek(impl_->state.position + seconds); }

bool Playback::set_output(Output output) {
    auto& p = *impl_;
    if(p.initial_hdmi_pending) {
        if(output == Output::Hdmi) return true;
        return p.finish_initial_hdmi(Output::Lcd, "HDMI setup cancelled; using LCD");
    }
    if(output == Output::Lcd) {
        const bool cancelled = p.pending_hdmi;
        p.pending_hdmi = false;
        p.state.output_switching = false;
        const bool result = p.change_output(output);
        if(p.state.output == Output::Lcd) { p.release_hdmi_audio(); p.desktop_lease.release(); }
        if(cancelled) p.state.notice = "HDMI setup cancelled; using LCD";
        return result;
    }
    if(output == p.state.output) return true;
    if(!p.pipeline) { p.state.output = output; return true; }
    if(p.pending_hdmi) return true;
    if(p.options.desktop_handoff && p.desktop_lease.state() != HdmiDesktopLease::State::Ready) {
        const auto inventory = inspect_outputs(false);
        if(std::none_of(inventory.hdmi.begin(), inventory.hdmi.end(), [](const HdmiConnector& item) {
            return item.connected && !item.modes.empty();
        })) {
            p.state.notice = "No connected HDMI display; using LCD";
            return false;
        }
        if(p.desktop_lease.begin()) {
            p.pending_hdmi = true;
            p.state.output_switching = true;
            p.state.notice = "Preparing HDMI; O cancels";
            return true;
        }
        // An older/non-systemd installation can still use a free display.
        // A busy display remains untouched and reports both actionable causes.
        const auto handoff_error = p.desktop_lease.error();
        if(p.change_output(output)) return true;
        p.state.notice = handoff_error + "; " + p.state.notice;
        return false;
    }
    return p.change_output(output);
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
    if(p.initial_hdmi_pending && index == -1) { p.restart_subtitle_track = -1; return true; }
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
        const auto inventory = p.audio_outputs();
        if(std::none_of(inventory.audio.begin(), inventory.audio.end(), [&](const AudioSink& item) { return item.name == sink_name && item.available; })) {
            p.state.notice = "Selected audio output is unavailable";
            return false;
        }
    }
    p.requested_audio_sink = sink_name;
    p.audio_fallback_attempted = false;
    p.audio_recovery_attempted = false;
    if(p.initial_hdmi_pending && !sink_name.empty() && p.hdmi_audio.state() == HdmiAudioSession::State::Waiting) {
        p.hdmi_audio.release();
        p.audio_attempted = false;
    }
    if(sink_name.empty() && p.hdmi_audio.state() != HdmiAudioSession::State::Ready) {
        p.hdmi_audio.release();
        p.audio_attempted = false;
    }
    if(p.pipeline) {
        const auto inventory = p.audio_outputs();
        p.move_audio(choose_audio_route(inventory.audio, p.state.output == Output::Hdmi, sink_name));
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
    p.initial_hdmi_pending = false;
    p.destroy_pipeline();
    p.release_hdmi_audio(true);
    p.desktop_lease.release();
    p.pending_hdmi = false;
    p.state.output_switching = false;
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
        << ",\"output_switching\":" << (p.state.output_switching ? "true" : "false")
        << ",\"path\":" << json_string(p.state.path) << ",\"position_seconds\":" << p.state.position
        << ",\"duration_seconds\":" << p.state.duration << ",\"received_frames\":" << p.received.load()
        << ",\"lcd_caption_compositions\":" << p.lcd_caption_compositions.load()
        << ",\"video_buffers_delivered\":" << p.video_buffers_delivered.load()
        << ",\"video_frames_decoded\":" << p.state.video_frames_decoded
        << ",\"video_sink_rendered\":" << p.state.video_sink_rendered
        << ",\"video_sink_dropped\":" << p.state.video_sink_dropped
        << ",\"video_output_memory\":" << json_string(p.state.video_output_memory)
        << ",\"video_output_caps\":" << json_string(p.state.video_output_caps)
        << ",\"sink_stats_scope\":\"GstBaseSink render calls/drops since its most recent reset, not physical scanout\""
        << ",\"video_decoder\":" << json_string(p.state.video_decoder) << ",\"fake_audio\":" << (p.options.fake_audio ? "true" : "false")
        << ",\"audio_output\":" << json_string(p.state.audio_output)
        << ",\"pipeline_builds\":" << p.state.pipeline_builds
        << ",\"first_render_seconds\":" << p.state.first_render_seconds
        << ",\"first_hdmi_render_seconds\":" << p.state.first_hdmi_render_seconds
        << ",\"first_playing_seconds\":" << p.state.first_playing_seconds
        << ",\"readiness_scope\":\"Observed since accepted load by UI poll; sink render statistics sampled every 250 ms, not physical scanout\""
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
