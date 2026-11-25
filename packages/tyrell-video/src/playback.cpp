#include "playback.h"
#include "outputs.h"
#include "hdmi_lease.h"
#include "hdmi_video_caps.h"
#include "hdmi_audio.h"
#include "json_string.h"
#include "lcd_frame.h"
#include "video_decode_policy.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cctype>
#include <cmath>
#include <cstring>
#include <exception>
#include <filesystem>
#include <iomanip>
#include <mutex>
#include <unordered_set>
#include <sstream>
#include <utility>

#if defined(__unix__) || defined(__APPLE__)
#include <sys/stat.h>
#endif

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

bool graph_id(const std::string& value) {
    return value.size() == 32 && value.find_first_not_of("0123456789abcdef") == std::string::npos;
}
bool graph_binding(const ManagedGraphBinding& binding) {
    return binding.epoch && graph_id(binding.resource) && graph_id(binding.generation);
}

struct GraphLifetime {
    std::atomic<std::uint64_t> started{0}, completed{0}, active{0}, contexts{0};
    std::atomic<std::uint64_t> created_graphs{0}, finalized_graphs{0};
    std::atomic<bool> closing{false}, failed{false};
    std::mutex mutex;
    std::condition_variable condition;
    std::unordered_set<GstObject*> objects;
};

struct GraphObject {
    std::shared_ptr<GraphLifetime> lifetime;
    GstObject* object;
    bool pipeline;
};
void graph_object_finalized(gpointer data, GObject*) {
    std::unique_ptr<GraphObject> context(static_cast<GraphObject*>(data));
    auto& lifetime = *context->lifetime;
    {
        std::lock_guard<std::mutex> guard(lifetime.mutex);
        lifetime.objects.erase(context->object);
    }
    if(context->pipeline) ++lifetime.finalized_graphs;
    lifetime.condition.notify_all();
}

struct GraphCallback {
    std::shared_ptr<GraphLifetime> lifetime;
    explicit GraphCallback(std::shared_ptr<GraphLifetime> value) : lifetime(std::move(value)) {
        ++lifetime->started; ++lifetime->active;
    }
    ~GraphCallback() {
        ++lifetime->completed; --lifetime->active;
        lifetime->condition.notify_all();
    }
    bool accepting() const { return !lifetime->closing.load(); }
};

std::optional<FileIdentity> file_identity(const std::string& path) {
#if defined(__unix__) || defined(__APPLE__)
    struct stat info{};
    if(::stat(path.c_str(), &info) != 0 || !S_ISREG(info.st_mode) || info.st_size < 0) return {};
    std::error_code error;
    const auto canonical = std::filesystem::canonical(path, error);
    if(error) return {};
    FileIdentity identity;
    identity.path = path;
    identity.canonical_path = canonical.string();
    identity.device = static_cast<std::uint64_t>(info.st_dev);
    identity.inode = static_cast<std::uint64_t>(info.st_ino);
    identity.bytes = static_cast<std::uint64_t>(info.st_size);
#if defined(__APPLE__)
    identity.modified_seconds = info.st_mtimespec.tv_sec;
    identity.modified_nanoseconds = info.st_mtimespec.tv_nsec;
    identity.changed_seconds = info.st_ctimespec.tv_sec;
    identity.changed_nanoseconds = info.st_ctimespec.tv_nsec;
#else
    identity.modified_seconds = info.st_mtim.tv_sec;
    identity.modified_nanoseconds = info.st_mtim.tv_nsec;
    identity.changed_seconds = info.st_ctim.tv_sec;
    identity.changed_nanoseconds = info.st_ctim.tv_nsec;
#endif
    return identity;
#else
    (void)path;
    return {};
#endif
}

bool same_identity(const FileIdentity& left, const FileIdentity& right) {
    return left.path == right.path && left.canonical_path == right.canonical_path &&
        left.device == right.device && left.inode == right.inode && left.bytes == right.bytes &&
        left.modified_seconds == right.modified_seconds && left.modified_nanoseconds == right.modified_nanoseconds &&
        left.changed_seconds == right.changed_seconds && left.changed_nanoseconds == right.changed_nanoseconds;
}

bool unchanged_file(const FileIdentity& expected) {
    const auto current = file_identity(expected.path);
    return current && same_identity(expected, *current);
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
    GstElement* video_sink = nullptr; // borrowed; valid while pipeline exists
    GstAppSink* managed_appsink = nullptr; // Strong until callback replacement, including pre-bind failures.
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
    std::optional<FileIdentity> media_identity;
    std::optional<FileIdentity> subtitle_identity;
    std::optional<PlaybackCheckpoint> saved_checkpoint;
    Output output_preference = Output::Lcd;
    bool retain_recovery_budgets = false;
    bool external_cleanup_unconfirmed = false;
    bool resume_hdmi_fallback_pause = false;
    bool resume_ended = false;
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
    // Streaming callbacks only publish whether a hardware video decoder has
    // actually delivered a buffer. Trying an unused hardware factory is not
    // enough to make a later software-decoder error eligible for recovery.
    std::atomic<bool> hardware_video_produced{false};
    bool software_video_preferred = false; // Retained across output/audio rebuilds for this load.
    bool video_decode_retry_attempted = false;
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
    bool seek_pending = false;
    double seek_position = 0;
    unsigned seek_streams = 0;
    std::atomic<guint32> seek_sequence{GST_SEQNUM_INVALID};
    std::atomic<guint32> video_seek_segment{GST_SEQNUM_INVALID};
    std::atomic<guint32> audio_seek_segment{GST_SEQNUM_INVALID};
    std::chrono::steady_clock::time_point seek_started{};
    std::chrono::steady_clock::time_point last_query{};
    std::chrono::steady_clock::time_point last_hotplug{};
    std::chrono::steady_clock::time_point load_started{};
    std::chrono::steady_clock::time_point file_load_started{};
    std::shared_ptr<GraphLifetime> graph_lifetime;
    std::optional<GraphDrainEvidence> graph_receipt;
    bool managed_epoch_sealed = false;
    struct CallbackContext {
        Impl* player;
        std::shared_ptr<GraphLifetime> lifetime;
        GstPadProbeCallback probe = nullptr;
    };
    struct ProbeRegistration { GstPad* pad; gulong id; };
    struct SignalRegistration { GObject* object; gulong id; };
    std::mutex registration_mutex;
    std::vector<ProbeRegistration> graph_probes;
    std::vector<SignalRegistration> graph_signals;

    bool managed() const { return options.managed_graph.has_value(); }

    void track_object(GstObject* object, bool root = false) {
        auto lifetime = std::atomic_load(&graph_lifetime);
        if(!lifetime || !object) return;
        {
            std::lock_guard<std::mutex> guard(lifetime->mutex);
            if(lifetime->objects.count(object)) return;
            if(lifetime->objects.size() >= 4096) { lifetime->failed = true; return; }
            lifetime->objects.insert(object);
        }
        if(root) ++lifetime->created_graphs;
        g_object_weak_ref(G_OBJECT(object), graph_object_finalized, new GraphObject{lifetime,object,root});
    }

    CallbackContext* callback_context(GstPadProbeCallback probe = nullptr) {
        auto lifetime = std::atomic_load(&graph_lifetime);
        auto* context = new CallbackContext{this,lifetime,probe};
        ++lifetime->contexts;
        return context;
    }
    static void destroy_context(gpointer data) {
        std::unique_ptr<CallbackContext> context(static_cast<CallbackContext*>(data));
        --context->lifetime->contexts;
        context->lifetime->condition.notify_all();
    }
    static void destroy_closure(gpointer data, GClosure*) { destroy_context(data); }

    static GstPadProbeReturn managed_probe(GstPad* pad, GstPadProbeInfo* info, gpointer data) {
        auto& context = *static_cast<CallbackContext*>(data);
        GraphCallback callback(context.lifetime);
        if(!callback.accepting()) return GST_PAD_PROBE_OK;
        try { return context.probe(pad,info,context.player); }
        catch(...) { context.lifetime->failed=true; return GST_PAD_PROBE_DROP; }
    }
    void add_probe(GstPad* pad, GstPadProbeType type, GstPadProbeCallback callback) {
        if(!managed()) { gst_pad_add_probe(pad,type,callback,this,nullptr); return; }
        std::lock_guard<std::mutex> guard(registration_mutex);
        if(graph_lifetime->closing || graph_lifetime->failed) return;
        auto* context = callback_context(callback);
        const auto id = gst_pad_add_probe(pad,type,managed_probe,context,destroy_context);
        if(id) graph_probes.push_back({GST_PAD(gst_object_ref(pad)),id});
        else graph_lifetime->failed = true;
    }
    void add_signal(GObject* object, const char* name, GCallback callback) {
        if(!managed()) { g_signal_connect(object,name,callback,this); return; }
        std::lock_guard<std::mutex> guard(registration_mutex);
        if(graph_lifetime->closing || graph_lifetime->failed) return;
        const auto id = g_signal_connect_data(object,name,callback,callback_context(),destroy_closure,G_CONNECT_DEFAULT);
        if(id) graph_signals.push_back({G_OBJECT(g_object_ref(object)),id});
        else graph_lifetime->failed = true;
    }
    static void managed_element_setup(GstElement* parent, GstElement* element, gpointer data) {
        auto& context = *static_cast<CallbackContext*>(data);
        GraphCallback callback(context.lifetime);
        if(callback.accepting()) {
            try { context.player->element_setup(parent,element,context.player); }
            catch(...) { context.lifetime->failed=true; }
        }
    }
    static void managed_silent_changed(GObject* element, GParamSpec* property, gpointer data) {
        auto& context = *static_cast<CallbackContext*>(data);
        GraphCallback callback(context.lifetime);
        if(callback.accepting()) {
            try { context.player->subtitle_silent_changed(element,property,context.player); }
            catch(...) { context.lifetime->failed=true; }
        }
    }
    static GstFlowReturn managed_sample(GstAppSink* sink, gpointer data) {
        auto& context = *static_cast<CallbackContext*>(data);
        GraphCallback callback(context.lifetime);
        if(!callback.accepting()) return GST_FLOW_FLUSHING;
        try { return context.player->accept_sample(gst_app_sink_pull_sample(sink)); }
        catch(...) { context.lifetime->failed=true; return GST_FLOW_ERROR; }
    }
    static GstFlowReturn managed_preroll(GstAppSink* sink, gpointer data) {
        auto& context = *static_cast<CallbackContext*>(data);
        GraphCallback callback(context.lifetime);
        if(!callback.accepting()) return GST_FLOW_FLUSHING;
        try { return context.player->accept_sample(gst_app_sink_pull_preroll(sink)); }
        catch(...) { context.lifetime->failed=true; return GST_FLOW_ERROR; }
    }
    static gboolean managed_allocation(GstAppSink* sink, GstQuery* query, gpointer data) {
        auto& context = *static_cast<CallbackContext*>(data);
        GraphCallback callback(context.lifetime);
        try { return callback.accepting() ? lcd_allocation(sink,query,context.player) : FALSE; }
        catch(...) { context.lifetime->failed=true; return FALSE; }
    }
    void close_callbacks() {
        if(!managed() || !graph_lifetime) return;
        graph_lifetime->closing = true;
        std::vector<ProbeRegistration> probes;
        std::vector<SignalRegistration> signals;
        {
            std::lock_guard<std::mutex> guard(registration_mutex);
            probes.swap(graph_probes); signals.swap(graph_signals);
        }
        for(const auto& registration : probes) {
            gst_pad_remove_probe(registration.pad,registration.id);
            gst_object_unref(registration.pad);
        }
        for(const auto& registration : signals) {
            g_signal_handler_disconnect(registration.object,registration.id);
            g_object_unref(registration.object);
        }
        if(managed_appsink) {
            // GStreamer 1.26 requires a zeroed callback struct, not nullptr.
            GstAppSinkCallbacks callbacks{};
            gst_app_sink_set_callbacks(managed_appsink,&callbacks,nullptr,nullptr);
            gst_object_unref(managed_appsink);
            managed_appsink = nullptr;
        }
    }
    void wait_callbacks() {
        if(!managed() || !graph_lifetime) return;
        std::unique_lock<std::mutex> guard(graph_lifetime->mutex);
        graph_lifetime->condition.wait(guard,[&] {
            return graph_lifetime->contexts.load()==0 && graph_lifetime->active.load()==0;
        });
    }
    void release_desktop() { if(!managed()) desktop_lease.release(); }

    double load_elapsed() const {
        return std::chrono::duration<double>(std::chrono::steady_clock::now() - file_load_started).count();
    }

    explicit Impl(PlaybackOptions requested_options) : options(std::move(requested_options)),
        desktop_lease(options.desktop_handoff_socket) {
        if(managed()) {
            if(!graph_binding(*options.managed_graph) || !options.fake_audio || options.desktop_handoff || options.automatic_hdmi_audio) {
                state.error = "Managed graph requires an explicit disposable LCD/fake-audio profile";
                return;
            }
            graph_lifetime = std::make_shared<GraphLifetime>();
        }
        GError* error = nullptr;
        initialized = gst_init_check(nullptr, nullptr, &error);
        if(!initialized) state.error = error ? error->message : "Cannot initialize GStreamer";
        if(error) g_error_free(error);
    }

    ~Impl() { destroy_pipeline(); if(!managed()) hdmi_audio.release(); release_desktop(); }

    void clear_frame() {
        std::lock_guard<std::mutex> lock(frame_mutex);
        frame.reset();
    }

    struct Restart {
        double position;
        int audio_track;
        int subtitle_track;
    };

    bool pending_seek_target() const {
        if(!seek_pending || !pipeline) return false;
        if(std::chrono::steady_clock::now() - seek_started < std::chrono::seconds(1)) return true;
        GstState current = GST_STATE_NULL, pending = GST_STATE_VOID_PENDING;
        return gst_element_get_state(pipeline, &current, &pending, 0) != GST_STATE_CHANGE_SUCCESS ||
               pending != GST_STATE_VOID_PENDING;
    }

    Restart restart_target() const {
        // Preroll queries describe the new pipeline's initial position, before
        // the queued seek and track selections have been applied.
        if(pending_seek_target())
            return Restart{seek_position, restart_audio_track >= 0 ? restart_audio_track : state.audio_track,
                           state.subtitle_track};
        return pending_preroll ? Restart{restart_position, restart_audio_track, restart_subtitle_track}
                               : Restart{state.position, restart_audio_track >= 0 ? restart_audio_track : state.audio_track,
                                         state.subtitle_track};
    }

    void destroy_pipeline(bool already_null = false) {
        close_callbacks();
        if(pipeline) {
            // NULL joins the streaming tasks before releasing our callback data.
            if(!already_null) {
                const auto result = gst_element_set_state(pipeline, GST_STATE_NULL);
                if(managed() && result != GST_STATE_CHANGE_SUCCESS) graph_lifetime->failed = true;
            }
            if(bus) gst_object_unref(bus);
            bus = nullptr;
            gst_object_unref(pipeline);
            pipeline = nullptr;
            audio_sink = nullptr;
            video_sink = nullptr;
        }
        video_memory_sampled = false;
        hardware_video_produced = false;
        lcd_renderer.reset();
        audio_restart_pending = false;
        {
            std::lock_guard<std::mutex> lock(subtitle_mutex);
            for(auto* overlay : subtitle_overlays) gst_object_unref(overlay);
            subtitle_overlays.clear();
        }
        if(!managed()) hdmi.release();
        pending_preroll = false;
        seek_pending = false;
        seek_sequence = video_seek_segment = audio_seek_segment = GST_SEQNUM_INVALID;
        clear_frame();
        wait_callbacks();
    }

    void observe_segment(GstPadProbeInfo* info, std::atomic<guint32>& observed) {
        if(!(GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM)) return;
        auto* event = GST_PAD_PROBE_INFO_EVENT(info);
        if(event && GST_EVENT_TYPE(event) == GST_EVENT_SEGMENT) {
            const auto sequence = gst_event_get_seqnum(event);
            // A later selector segment must not erase proof that this seek
            // reached the sink; stale segments never prove a newer request.
            if(sequence == seek_sequence.load()) observed = sequence;
        }
    }

    static GstPadProbeReturn audio_segment(GstPad*, GstPadProbeInfo* info, gpointer data) {
        auto& player = *static_cast<Impl*>(data);
        player.observe_segment(info, player.audio_seek_segment);
        return GST_PAD_PROBE_OK;
    }

    void settle_seek() {
        if(!seek_pending) return;
        const auto sequence = seek_sequence.load();
        if(!seek_streams || ((seek_streams & 1U) && video_seek_segment.load() != sequence) ||
           ((seek_streams & 2U) && audio_seek_segment.load() != sequence)) return;
        GstState current = GST_STATE_NULL, pending = GST_STATE_VOID_PENDING;
        if(gst_element_get_state(pipeline, &current, &pending, 0) == GST_STATE_CHANGE_SUCCESS &&
           pending == GST_STATE_VOID_PENDING) {
            seek_pending = false;
            seek_sequence = GST_SEQNUM_INVALID;
        }
    }

    bool issue_seek(double position) {
        if(!pipeline || !std::isfinite(position) || position < 0 ||
           position > static_cast<double>(G_MAXINT64 / GST_SECOND)) return false;
        gint video = 0, audio = 0;
        g_object_get(pipeline, "n-video", &video, "n-audio", &audio, nullptr);
        seek_streams = (video > 0 ? 1U : 0U) | (audio > 0 ? 2U : 0U);
        const auto sequence = gst_util_seqnum_next();
        seek_position = position;
        seek_started = std::chrono::steady_clock::now();
        seek_pending = true;
        video_seek_segment = audio_seek_segment = GST_SEQNUM_INVALID;
        seek_sequence = sequence;
        auto* event = gst_event_new_seek(1.0, GST_FORMAT_TIME,
            static_cast<GstSeekFlags>(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_ACCURATE),
            GST_SEEK_TYPE_SET, static_cast<gint64>(position * GST_SECOND), GST_SEEK_TYPE_NONE, GST_CLOCK_TIME_NONE);
        gst_event_set_seqnum(event, sequence);
        if(gst_element_send_event(pipeline, event)) return true;
        seek_pending = false;
        seek_sequence = GST_SEQNUM_INVALID;
        return false;
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

    static GstPadProbeReturn hardware_decoded_delivery(GstPad* pad, GstPadProbeInfo* info, gpointer data) {
        if(GST_PAD_PROBE_INFO_TYPE(info) & (GST_PAD_PROBE_TYPE_BUFFER | GST_PAD_PROBE_TYPE_BUFFER_LIST))
            static_cast<Impl*>(data)->hardware_video_produced = true;
        return decoded_delivery(pad, info, data);
    }

    static GstPadProbeReturn video_delivery(GstPad*, GstPadProbeInfo* info, gpointer data) {
        auto& player = *static_cast<Impl*>(data);
        player.observe_segment(info, player.video_seek_segment);
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
        player.track_object(GST_OBJECT(element));
        if(player.managed() && player.graph_lifetime->failed) return;
        GstElementFactory* factory = gst_element_get_factory(element);
        if(!factory) return;
        const char* factory_name = gst_plugin_feature_get_name(GST_PLUGIN_FEATURE(factory));
        const char* klass = gst_element_factory_get_metadata(factory, GST_ELEMENT_METADATA_KLASS);
        if(klass && std::strstr(klass, "Decoder") && std::strstr(klass, "Video")) {
            if(GstPad* pad = gst_element_get_static_pad(element, "src")) {
                player.add_probe(pad, static_cast<GstPadProbeType>(GST_PAD_PROBE_TYPE_BUFFER | GST_PAD_PROBE_TYPE_BUFFER_LIST),
                                  decoder_is_hardware_video(factory_name, klass) ? hardware_decoded_delivery : decoded_delivery);
                gst_object_unref(pad);
            }
        }
        if(std::strcmp(factory_name, "subtitleoverlay") == 0) {
            if(GstPad* pad = gst_element_get_static_pad(element, "subtitle_sink")) {
                player.add_probe(pad, static_cast<GstPadProbeType>(GST_PAD_PROBE_TYPE_BUFFER | GST_PAD_PROBE_TYPE_BUFFER_LIST), subtitle_payload_guard);
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
        player.add_signal(G_OBJECT(element), "notify::silent", G_CALLBACK(player.managed() ? managed_silent_changed : subtitle_silent_changed));
        std::lock_guard<std::mutex> lock(player.subtitle_mutex);
        g_object_set(element, "silent", !player.subtitles_enabled.load(), nullptr);
        player.subtitle_overlays.push_back(GST_ELEMENT(gst_object_ref(element)));
    }

    GstFlowReturn accept_sample(GstSample* sample) {
        if(!sample) return GST_FLOW_OK;
        std::unique_ptr<GstSample, void(*)(GstSample*)> source(sample, gst_sample_unref);
        try {
            if(managed() && options.managed_sample_hook) options.managed_sample_hook();
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
        if(managed() && output != Output::Lcd) { error = "Managed HDMI graph admission is not qualified"; return nullptr; }
        GstElement* bin = gst_bin_new("tyrell-video-output");
        GstElement* convert = gst_element_factory_make("videoconvert", nullptr);
        GstElement* scale = gst_element_factory_make("videoscale", nullptr);
        GstElement* fitted = output == Output::Lcd ? gst_element_factory_make("capsfilter", nullptr) : nullptr;
        GstElement* filter = gst_element_factory_make("capsfilter", nullptr);
        GstElement* sink = gst_element_factory_make(output == Output::Lcd ? "appsink" : "kmssink", "tyrell-video-sink");
        for(auto* element : {bin,convert,scale,fitted,filter,sink}) if(element) track_object(GST_OBJECT(element));
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
            callbacks.new_sample = managed() ? managed_sample : sample_ready;
            callbacks.new_preroll = managed() ? managed_preroll : preroll_ready;
            callbacks.propose_allocation = managed() ? managed_allocation : lcd_allocation;
            if(managed()) managed_appsink = GST_APP_SINK(gst_object_ref(sink));
            gst_app_sink_set_callbacks(GST_APP_SINK(sink), &callbacks, managed() ? static_cast<gpointer>(callback_context()) : this,
                                      managed() ? destroy_context : nullptr);
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
            add_probe(sink_pad,
                static_cast<GstPadProbeType>(GST_PAD_PROBE_TYPE_BUFFER | GST_PAD_PROBE_TYPE_BUFFER_LIST | GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM),
                video_delivery);
            gst_object_unref(sink_pad);
        }
        video_sink = sink;
        return bin;
    }

    static bool temporary_audio(const std::string& name) {
        return name.rfind("tyrell_video_hdmi_", 0) == 0;
    }

    OutputInventory audio_outputs() const {
        if(managed()) return {};
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
        if(managed()) return;
        if(!force && explicit_owned_audio()) return;
        if(requested_audio_sink == hdmi_audio.sink_name()) requested_audio_sink.clear();
        hdmi_audio.release();
        audio_attempted = false;
        if(!retain_recovery_budgets) {
            audio_fallback_attempted = false;
            audio_recovery_attempted = false;
        }
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
            if(retain_recovery_budgets && audio_fallback_attempted) {
                audio_attempted = true;
                return;
            }
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
            external_cleanup_unconfirmed = true;
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
        const bool autoplay = desired_playing && !(resume_hdmi_fallback_pause && output == Output::Lcd);
        const bool fallback_autoplay = desired_playing && !resume_hdmi_fallback_pause;
        const auto handoff_error = std::exchange(initial_handoff_error, {});
        initial_hdmi_pending = false;
        state.output_switching = false;
        state.output = output;
        state.error.clear();
        state.notice = std::move(notice);
        if(output == Output::Lcd) {
            release_hdmi_audio();
            release_desktop();
        }
        if(create_pipeline(position, autoplay, audio_track, subtitle_track)) {
            resume_hdmi_fallback_pause = false;
            return true;
        }
        if(output == Output::Hdmi) {
            const auto failure = handoff_error.empty() ? state.error : handoff_error + "; " + state.error;
            state.output = Output::Lcd;
            state.error.clear();
            state.notice = failure + "; using LCD";
            release_hdmi_audio(true);
            release_desktop();
            if(create_pipeline(position, fallback_autoplay, audio_track, subtitle_track)) {
                resume_hdmi_fallback_pause = false;
                return true;
            }
        }
        state.loaded = state.playing = state.buffering = false;
        state.paused = true;
        release_hdmi_audio(true);
        release_desktop();
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

    bool begin_initial_hdmi(double position = 0, bool autoplay = true, int audio_track = -1, int subtitle_track = -1) {
        if(managed()) { state.error = "Managed HDMI graph admission is not qualified"; return false; }
        initial_hdmi_pending = true;
        state.output = Output::Lcd;
        state.output_switching = true;
        state.buffering = true;
        state.playing = false;
        state.paused = !autoplay;
        state.notice = "Preparing HDMI and TV audio; O cancels";
        desired_playing = autoplay;
        restart_position = position;
        restart_audio_track = audio_track;
        restart_subtitle_track = subtitle_track;
        initial_handoff_error.clear();
        // Older installations can still use a free DRM display even when the
        // handoff service is absent. A waiting lease is reused across a new
        // file selection instead of asking the desktop to hand over twice.
        initial_hdmi_direct = !options.desktop_handoff;
        if(options.desktop_handoff && desktop_lease.state() != HdmiDesktopLease::State::Ready &&
           desktop_lease.state() != HdmiDesktopLease::State::Waiting) {
            external_cleanup_unconfirmed = true;
            initial_hdmi_direct = !desktop_lease.begin();
            if(initial_hdmi_direct) initial_handoff_error = desktop_lease.error();
        }
        poll_initial_hdmi();
        return initial_hdmi_pending || pipeline;
    }

    GstElement* make_audio_sink(Output output, std::string& error) {
        if(options.fake_audio) {
            GstElement* sink = gst_element_factory_make("fakesink", "tyrell-test-audio-output");
            if(sink) track_object(GST_OBJECT(sink));
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
        if(sink) track_object(GST_OBJECT(sink));
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
        if(managed()) {
            if(managed_epoch_sealed || graph_lifetime->failed || graph_lifetime->created_graphs >= 128 || state.output != Output::Lcd) {
                state.error = "Managed graph is sealed, uncertain or outside its disposable scope";
                return false;
            }
            graph_lifetime->closing = false;
        }
        // These identify the current graph, unlike the cumulative per-load
        // delivery/readiness counters. A replacement must not report its old
        // hardware decoder or allocator while the software graph prepares.
        state.video_decoder.clear();
        state.video_output_caps.clear();
        state.video_output_memory.clear();
        {
            std::lock_guard<std::mutex> lock(video_metadata_mutex);
            video_output_memory.clear();
        }
        hardware_video_produced = false;
        state.software_video_fallback = software_video_preferred && state.output == Output::Hdmi;
        ++state.pipeline_builds;
        pipeline = gst_element_factory_make("playbin", "tyrell-player");
        if(!pipeline) { state.error = "Missing GStreamer playbin plugin"; return false; }
        track_object(GST_OBJECT(pipeline),true);
        subtitles_enabled = subtitle_track >= 0;
        add_signal(G_OBJECT(pipeline), "element-setup", G_CALLBACK(managed() ? managed_element_setup : element_setup));
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
        if(GstPad* pad = gst_element_get_static_pad(audio, "sink")) {
            add_probe(pad, GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM, audio_segment);
            gst_object_unref(pad);
        }
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
        if(state.software_video_fallback) flags |= force_software_decoders_flag;
        g_object_set(pipeline, "flags", flags, nullptr);
        bus = gst_element_get_bus(pipeline);
        track_object(GST_OBJECT(bus));
        restart_position = position;
        restart_audio_track = audio_track;
        restart_subtitle_track = subtitle_track;
        pending_preroll = true;
        desired_playing = autoplay;
        ended = resume_ended;
        resume_ended = false;
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
        settle_seek();
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

    bool recover_hdmi_video_memory(std::string failure) {
        query();
        const auto target = restart_target();
        const bool autoplay = desired_playing;
        // Consume before recreating anything: a second failure, including one
        // during an audio/output rebuild, must take the ordinary LCD fallback.
        video_decode_retry_attempted = true;
        software_video_preferred = true;
        ++state.video_decode_fallbacks;
        state.video_decode_fallback_reason = std::move(failure);
        destroy_pipeline();
        // make_video_sink()/make_audio_sink() can fail before create_pipeline
        // publishes its preroll fields. Keep the saved intent valid even then,
        // so the existing LCD fallback can recover the same queued position.
        state.position = restart_position = target.position;
        state.audio_track = restart_audio_track = target.audio_track;
        state.subtitle_track = restart_subtitle_track = target.subtitle_track;
        // Keep the desktop and audio leases. Only the decoder/video graph is
        // replaced; releasing the hardware capture pool gives KMS's small copy
        // pool room without changing CMA, firmware or the desktop service.
        state.error.clear();
        state.notice = "HDMI video memory is tight; trying software decoding";
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
        release_desktop();
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
                release_desktop();
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
            release_desktop();
        }
        return false;
    }

    void preroll_finished() {
        if(!pending_preroll || !pipeline) return;
        pending_preroll = false;
        refresh_tracks();
        apply_subtitle_selection(restart_subtitle_track);
        if(restart_position > 0 && !issue_seek(restart_position))
            state.notice = "This file cannot seek; playback starts at the beginning";
        // On playbin 1.26, switching a paused audio selector and then issuing a
        // caption/seek flush can deadlock. Prepare text/seek first and select
        // audio last, without transiently playing a user-paused checkpoint.
        if(restart_audio_track >= 0 && restart_audio_track < static_cast<int>(state.audio_tracks.size()))
            g_object_set(pipeline, "current-audio", restart_audio_track, nullptr);
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
    if(p.managed() && p.managed_epoch_sealed) { p.state.error = "A fresh managed grant is required before load"; return false; }
    if(!p.initialized) return false;
    std::error_code error;
    const auto absolute = std::filesystem::absolute(path, error);
    if(error || !std::filesystem::is_regular_file(absolute, error)) {
        p.state.error = "Media file is missing or unreadable";
        return false;
    }
    const auto accepted_at = std::chrono::steady_clock::now();
    const auto identity = file_identity(absolute.string());
    const Output output = p.pending_hdmi || p.initial_hdmi_pending ? Output::Hdmi : p.state.output;
    p.pending_hdmi = false;
    p.initial_hdmi_pending = false;
    p.destroy_pipeline();
    p.saved_checkpoint.reset();
    p.media_identity = identity;
    p.subtitle_identity.reset();
    p.output_preference = output;
    p.retain_recovery_budgets = false;
    p.resume_hdmi_fallback_pause = p.resume_ended = false;
    if(output == Output::Lcd) { p.release_hdmi_audio(); p.release_desktop(); }
    const double volume = p.state.volume;
    const bool muted = p.state.muted;
    p.state = {};
    p.state.output = output;
    p.state.volume = volume;
    p.state.muted = muted;
    p.state.path = absolute.string();
    p.state.loaded = true;
    p.software_video_preferred = false;
    p.video_decode_retry_attempted = false;
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
        if(!error && std::filesystem::is_regular_file(subtitle, error)) {
            p.external_subtitle = subtitle.string();
            p.subtitle_identity = file_identity(p.external_subtitle);
        }
        else p.state.notice = "External subtitle file is unavailable";
    }
    // The handheld controller is already usable without a video pipeline.
    // Prepare display and sound together before opening the file once; the
    // old LCD -> HDMI -> TV-audio sequence decoded/prerolled it three times.
    if(output == Output::Hdmi) return p.begin_initial_hdmi();
    if(p.create_pipeline(0, true, -1, -1)) return true;
    p.state.loaded = false;
    p.release_hdmi_audio(true);
    p.release_desktop();
    return false;
}

void Playback::poll() {
    auto& p = *impl_;
    if(p.managed() && p.managed_epoch_sealed) return;
    if(p.state.suspended || (p.managed() && p.managed_epoch_sealed)) return;
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
            p.release_desktop();
        }
    } else if(p.state.output == Output::Hdmi && p.desktop_lease.state() == HdmiDesktopLease::State::Ready &&
              p.desktop_lease.poll() == HdmiDesktopLease::State::Failed) {
        const auto error = p.desktop_lease.error();
        p.fallback_to_lcd(error + "; paused on LCD");
    }
    if(!p.pipeline) { if(!p.managed()) p.hdmi_audio.poll(); return; }
    while(GstMessage* message = gst_bus_pop(p.bus)) {
        bool fatal = false, failed_audio = false, retry_video_memory = false;
        switch(GST_MESSAGE_TYPE(message)) {
            case GST_MESSAGE_ERROR: {
                GError* error = nullptr;
                gchar* debug = nullptr;
                gst_message_parse_error(message, &error, &debug);
                p.state.error = error ? error->message : "Playback failed";
                failed_audio = p.audio_sink && GST_MESSAGE_SRC(message) == GST_OBJECT(p.audio_sink);
                retry_video_memory = retry_hdmi_with_software_video(
                    p.state.output == Output::Hdmi,
                    p.video_sink && GST_MESSAGE_SRC(message) == GST_OBJECT(p.video_sink),
                    p.hardware_video_produced.load(), p.video_decode_retry_attempted, error);
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
            if(retry_video_memory && p.recover_hdmi_video_memory(p.state.error)) return;
            if(failed_audio && (p.state.output == Output::Hdmi || p.audio_owned_seen) &&
               !p.audio_recovery_attempted && p.recover_audio(p.state.error)) return;
            if(p.state.output == Output::Hdmi) {
                p.fallback_to_lcd(p.state.error + "; paused on LCD");
            } else {
                p.destroy_pipeline();
                p.release_hdmi_audio(true);
                p.pending_hdmi = false;
                p.state.output_switching = false;
                p.release_desktop();
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
        p.release_desktop();
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
    if(p.state.suspended || (p.managed() && p.managed_epoch_sealed)) return false;
    if(p.initial_hdmi_pending) {
        if(p.ended || p.resume_ended) { p.restart_position = 0; p.state.position = 0; p.ended = p.resume_ended = false; }
        p.desired_playing = true; p.state.paused = false; return true;
    }
    if(!p.pipeline) return false;
    if(p.ended) { p.ended = false; seek(0); }
    p.desired_playing = true;
    if(p.pending_preroll) return true;
    return gst_element_set_state(p.pipeline, GST_STATE_PLAYING) != GST_STATE_CHANGE_FAILURE;
}
bool Playback::pause() {
    auto& p = *impl_;
    if(p.state.suspended || (p.managed() && p.managed_epoch_sealed)) return false;
    if(p.initial_hdmi_pending) { p.desired_playing = false; p.state.paused = true; return true; }
    if(!p.pipeline) return false;
    p.settle_seek();
    GstState before = GST_STATE_NULL, pending = GST_STATE_VOID_PENDING;
    const bool capture_position = !p.pending_preroll && !p.pending_seek_target() &&
        gst_element_get_state(p.pipeline, &before, &pending, 0) == GST_STATE_CHANGE_SUCCESS &&
        before == GST_STATE_PLAYING && pending == GST_STATE_VOID_PENDING;
    p.desired_playing = false;
    p.state.playing = false;
    p.state.paused = true;
    if(gst_element_set_state(p.pipeline, GST_STATE_PAUSED) == GST_STATE_CHANGE_FAILURE) return false;
    // Caption changes while paused redraw by seeking to the saved position.
    // Refresh it after pausing a stable running graph: the regular 250 ms
    // status sample can otherwise rewind an immediate pause/caption request.
    // An unsettled seek or preroll already has a target that must be retained.
    if(capture_position) {
        gint64 position = 0;
        if(gst_element_query_position(p.pipeline, GST_FORMAT_TIME, &position) && position >= 0)
            p.state.position = static_cast<double>(position) / GST_SECOND;
    }
    return true;
}
bool Playback::toggle_pause() { return impl_->desired_playing ? pause() : play(); }
bool Playback::seek(double seconds) {
    auto& p = *impl_;
    if(p.state.suspended || (p.managed() && p.managed_epoch_sealed)) return false;
    if((!p.pipeline && !p.initial_hdmi_pending) || !std::isfinite(seconds)) return false;
    seconds = std::max(0.0, seconds);
    if(p.state.duration > 0) seconds = std::min(seconds, std::max(0.0, p.state.duration - 0.01));
    if(p.pending_preroll || p.initial_hdmi_pending) {
        p.restart_position = seconds; p.state.position = seconds; p.ended = p.resume_ended = false; return true;
    }
    const bool result = p.issue_seek(seconds);
    // Keep the previous immutable snapshot until the streaming thread replaces
    // it. A fast paused preroll can already be published before seek returns.
    if(result) { p.state.position = seconds; p.ended = p.resume_ended = false; }
    else p.state.notice = "This file does not support seeking";
    return result;
}
bool Playback::seek_relative(double seconds) { return seek(impl_->state.position + seconds); }

bool Playback::set_output(Output output) {
    auto& p = *impl_;
    if(p.managed() && (p.managed_epoch_sealed || output != Output::Lcd)) {
        p.state.error = "Managed HDMI graph admission is not qualified";
        return false;
    }
    if(p.state.suspended || (p.managed() && p.managed_epoch_sealed)) return false;
    p.output_preference = output;
    if(p.initial_hdmi_pending) {
        if(output == Output::Hdmi) return true;
        return p.finish_initial_hdmi(Output::Lcd, "HDMI setup cancelled; using LCD");
    }
    if(output == Output::Lcd) {
        const bool cancelled = p.pending_hdmi;
        p.pending_hdmi = false;
        p.state.output_switching = false;
        const bool result = p.change_output(output);
        if(p.state.output == Output::Lcd) { p.release_hdmi_audio(); p.release_desktop(); }
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
        p.external_cleanup_unconfirmed = true;
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
    if(p.state.suspended || (p.managed() && p.managed_epoch_sealed)) return false;
    if(!p.pipeline || index < 0 || index >= static_cast<int>(p.state.audio_tracks.size())) return false;
    p.state.audio_track = index;
    p.restart_audio_track = index;
    g_object_set(p.pipeline, "current-audio", index, nullptr);
    return true;
}
bool Playback::set_subtitle_track(int index) {
    auto& p = *impl_;
    if(p.state.suspended || (p.managed() && p.managed_epoch_sealed)) return false;
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
    if(!p.desired_playing && !p.pending_preroll && !p.ended) return seek(p.state.position);
    return true;
}
bool Playback::set_audio_sink(const std::string& sink_name) {
    auto& p = *impl_;
    if(p.managed()) {
        if(p.managed_epoch_sealed || !sink_name.empty()) { p.state.error = "Managed audio session access is not qualified"; return false; }
        return true;
    }
    if(p.state.suspended || (p.managed() && p.managed_epoch_sealed)) return false;
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
    if(p.state.suspended || (p.managed() && p.managed_epoch_sealed)) return;
    if(!std::isfinite(volume)) return;
    p.state.volume = std::clamp(volume, 0.0, 1.0);
    if(p.pipeline) g_object_set(p.pipeline, "volume", p.state.volume, nullptr);
}
void Playback::set_muted(bool muted) {
    if(impl_->state.suspended || (impl_->managed() && impl_->managed_epoch_sealed)) return;
    impl_->state.muted = muted;
    if(impl_->pipeline) g_object_set(impl_->pipeline, "mute", muted, nullptr);
}

std::optional<PlaybackCheckpoint> Playback::capture_checkpoint() {
    auto& p = *impl_;
    if(p.state.suspended) return p.saved_checkpoint;
    if(!p.state.loaded) return {};
    if(!p.media_identity || !unchanged_file(*p.media_identity)) {
        p.state.error = "Media identity changed or cannot be verified; checkpoint refused";
        return {};
    }
    if(!p.external_subtitle.empty() && (!p.subtitle_identity || !unchanged_file(*p.subtitle_identity))) {
        p.state.error = "External subtitle identity changed or cannot be verified; checkpoint refused";
        return {};
    }
    PlaybackCheckpoint saved;
    saved.media = *p.media_identity;
    saved.external_subtitle = p.subtitle_identity;
    saved.duration = p.state.duration;
    saved.playing = p.desired_playing;
    saved.ended = p.ended || p.resume_ended;
    saved.volume = p.state.volume;
    saved.muted = p.state.muted;
    saved.preferred_output = p.output_preference;
    saved.actual_output = p.state.output;
    saved.requested_audio_sink = p.requested_audio_sink;
    saved.requested_owned_audio = p.explicit_owned_audio();
    saved.software_video_preferred = p.software_video_preferred;
    saved.video_decode_retry_attempted = p.video_decode_retry_attempted;
    saved.video_decode_fallbacks = p.state.video_decode_fallbacks;
    saved.video_decode_fallback_reason = p.state.video_decode_fallback_reason;
    saved.audio_fallback_attempted = p.audio_fallback_attempted;
    saved.audio_recovery_attempted = p.audio_recovery_attempted;
    p.settle_seek();
    auto target = p.restart_target();
    if(p.initial_hdmi_pending || p.pending_preroll) {
        target = {p.restart_position, p.restart_audio_track, p.restart_subtitle_track};
        saved.position_source = CheckpointPosition::PendingTarget;
    } else if(saved.ended) {
        target.position = p.state.duration;
        saved.position_source = CheckpointPosition::EndOfFile;
    } else if(p.seek_pending) {
        GstState current = GST_STATE_NULL, pending = GST_STATE_VOID_PENDING;
        const bool stable = gst_element_get_state(p.pipeline, &current, &pending, 0) == GST_STATE_CHANGE_SUCCESS &&
                            pending == GST_STATE_VOID_PENDING;
        // Some plugins do not propagate seek sequence numbers. Never carry an
        // old target indefinitely once such a graph may have resumed playing.
        if(stable && std::chrono::steady_clock::now() - p.seek_started >= std::chrono::seconds(1)) {
            p.state.error = "Seek completion is unconfirmed; checkpoint refused";
            return {};
        }
        target.position = p.seek_position;
        saved.position_source = CheckpointPosition::PendingTarget;
    } else {
        gint64 position = 0;
        if(!p.pipeline || !gst_element_query_position(p.pipeline, GST_FORMAT_TIME, &position) || position < 0) {
            p.state.error = "Cannot query a fresh playback position; checkpoint refused";
            return {};
        }
        target.position = static_cast<double>(position) / GST_SECOND;
        p.state.position = target.position;
        saved.position_source = CheckpointPosition::FreshQuery;
    }
    saved.position = target.position;
    saved.audio_track = target.audio_track;
    saved.subtitle_track = target.subtitle_track;
    return saved;
}

bool Playback::suspend() {
    auto& p = *impl_;
    if(p.state.suspended) return true;
    const auto saved = capture_checkpoint();
    if(p.state.loaded && !saved) return false;
    p.close_callbacks();
    if(p.pipeline) {
        const auto result = gst_element_set_state(p.pipeline, GST_STATE_NULL);
        GstState current = GST_STATE_VOID_PENDING, pending = GST_STATE_VOID_PENDING;
        if(result != GST_STATE_CHANGE_SUCCESS ||
           gst_element_get_state(p.pipeline, &current, &pending, 0) != GST_STATE_CHANGE_SUCCESS ||
           current != GST_STATE_NULL || pending != GST_STATE_VOID_PENDING) {
            p.state.error = "Playback stop is unconfirmed; suspension refused";
            return false;
        }
    }
    p.retain_recovery_budgets = true;
    // The state transition above can block while GStreamer joins its tasks.
    p.destroy_pipeline(true);
    p.initial_hdmi_pending = p.pending_hdmi = false;
    p.release_hdmi_audio(true);
    p.release_desktop();
    p.saved_checkpoint = saved;
    p.state.suspended = true;
    p.state.playing = p.state.buffering = p.state.output_switching = false;
    p.state.paused = p.state.loaded;
    return resources().local_handles_released();
}

bool Playback::resume() {
    auto& p = *impl_;
    if(p.managed() && p.managed_epoch_sealed) { p.state.error = "A fresh managed grant is required before resume"; return false; }
    if(!p.state.suspended) return true;
    if(!p.saved_checkpoint) { p.state.suspended = false; return true; }
    const auto saved = *p.saved_checkpoint;
    if(!unchanged_file(saved.media) || (saved.external_subtitle && !unchanged_file(*saved.external_subtitle))) {
        p.state.error = "Media or subtitle identity changed; resume refused";
        return false;
    }
    p.state.path = saved.media.path;
    p.state.duration = saved.duration;
    p.external_subtitle = saved.external_subtitle ? saved.external_subtitle->path : std::string{};
    p.media_identity = saved.media;
    p.subtitle_identity = saved.external_subtitle;
    p.state.volume = saved.volume;
    p.state.muted = saved.muted;
    p.state.error.clear();
    p.state.notice.clear();
    p.output_preference = saved.preferred_output;
    p.requested_audio_sink = saved.requested_owned_audio ? std::string{} : saved.requested_audio_sink;
    p.software_video_preferred = saved.software_video_preferred;
    p.video_decode_retry_attempted = saved.video_decode_retry_attempted;
    p.state.video_decode_fallbacks = saved.video_decode_fallbacks;
    p.state.video_decode_fallback_reason = saved.video_decode_fallback_reason;
    p.audio_fallback_attempted = saved.audio_fallback_attempted;
    p.audio_recovery_attempted = saved.audio_recovery_attempted;
    p.state.position = p.restart_position = saved.position;
    p.state.audio_track = p.restart_audio_track = saved.audio_track;
    p.state.subtitle_track = p.restart_subtitle_track = saved.subtitle_track;
    p.desired_playing = saved.playing;
    p.resume_ended = saved.ended;
    p.state.suspended = false;
    bool accepted;
    if(saved.preferred_output == Output::Hdmi) {
        p.resume_hdmi_fallback_pause = true;
        accepted = p.begin_initial_hdmi(saved.position, saved.playing, saved.audio_track, saved.subtitle_track);
    } else {
        p.state.output = Output::Lcd;
        accepted = p.create_pipeline(saved.position, saved.playing, saved.audio_track, saved.subtitle_track);
    }
    if(accepted) { p.saved_checkpoint.reset(); return true; }
    p.destroy_pipeline();
    p.initial_hdmi_pending = p.pending_hdmi = false;
    p.release_hdmi_audio(true);
    p.release_desktop();
    p.state.suspended = true;
    p.state.playing = p.state.buffering = p.state.output_switching = false;
    p.state.paused = p.state.loaded;
    return false;
}

bool Playback::suspended() const { return impl_->state.suspended; }
std::optional<PlaybackCheckpoint> Playback::checkpoint() const { return impl_->saved_checkpoint; }
PlaybackResources Playback::resources() const {
    const auto& p = *impl_;
    const auto desktop = p.desktop_lease.state();
    const auto audio = p.hdmi_audio.state();
    return {p.pipeline != nullptr, p.hdmi.fd() >= 0,
            desktop == HdmiDesktopLease::State::Waiting || desktop == HdmiDesktopLease::State::Ready,
            audio == HdmiAudioSession::State::Waiting || audio == HdmiAudioSession::State::Ready,
            p.external_cleanup_unconfirmed};
}

std::string GraphDrainEvidence::json() const {
    std::ostringstream out;
    out << "{\"kind\":\"graph-drain-v4\",\"epoch\":" << binding.epoch
        << ",\"resource\":" << json_string(binding.resource) << ",\"generation\":" << json_string(binding.generation)
        << ",\"operation\":" << json_string(operation) << ",\"graph_serial\":" << graph_serial
        << ",\"callbacks_started\":" << callbacks_started << ",\"callbacks_completed\":" << callbacks_completed
        << ",\"active_callbacks\":" << active_callbacks << ",\"callback_contexts\":" << callback_contexts
        << ",\"live_objects\":" << live_objects << ",\"pipeline_finalized\":" << (pipeline_finalized ? "true" : "false")
        << ",\"local_handles_released\":" << (local_handles_released ? "true" : "false")
        << ",\"legacy_helpers_unused\":" << (legacy_helpers_unused ? "true" : "false")
        << ",\"finality_receipt\":" << json_string(finality_receipt) << '}';
    return out.str();
}

std::optional<GraphDrainEvidence> Playback::drain_graph(const std::string& operation) {
    auto& p = *impl_;
    if(!p.managed() || !p.initialized || !graph_id(operation)) {
        p.state.error = "Managed graph drain requires a bound coordinator operation";
        return {};
    }
    if(p.graph_receipt) {
        if(p.graph_receipt->operation == operation) return p.graph_receipt;
        p.state.error = "Conflicting managed graph drain operation";
        return {};
    }
    p.managed_epoch_sealed = true;
    if(!suspend()) return {};
    GraphDrainEvidence evidence;
    evidence.binding = *p.options.managed_graph;
    evidence.operation = operation;
    const auto lifetime = std::atomic_load(&p.graph_lifetime);
    evidence.graph_serial = lifetime->created_graphs;
    evidence.callbacks_started = lifetime->started;
    evidence.callbacks_completed = lifetime->completed;
    evidence.active_callbacks = lifetime->active;
    evidence.callback_contexts = lifetime->contexts;
    {
        std::lock_guard<std::mutex> guard(lifetime->mutex);
        evidence.live_objects = lifetime->objects.size();
    }
    evidence.pipeline_finalized = lifetime->created_graphs.load() == lifetime->finalized_graphs.load();
    const auto handles = resources();
    evidence.local_handles_released = handles.local_handles_released();
    evidence.legacy_helpers_unused = !handles.external_cleanup_unconfirmed && !p.options.desktop_handoff &&
        !p.options.automatic_hdmi_audio && p.options.fake_audio && p.state.output == Output::Lcd;
    if(lifetime->failed || !evidence.pipeline_finalized || evidence.live_objects || evidence.active_callbacks ||
       evidence.callback_contexts || evidence.callbacks_started != evidence.callbacks_completed ||
       !evidence.local_handles_released || !evidence.legacy_helpers_unused) {
        p.state.error = "Managed graph completion remains unknown";
        return {};
    }
    if(!p.options.graph_finality) { p.state.error = "Managed graph backend finality is not qualified"; return {}; }
    try { evidence.finality_receipt = p.options.graph_finality(evidence); }
    catch(...) { p.state.error = "Managed graph backend finality failed"; lifetime->failed = true; return {}; }
    if(!graph_id(evidence.finality_receipt)) {
        p.state.error = "Managed graph backend finality remains unknown";
        lifetime->failed = true;
        return {};
    }
    p.graph_receipt = evidence;
    return evidence;
}

std::optional<GraphDrainEvidence> Playback::graph_drain_evidence() const { return impl_->graph_receipt; }

bool Playback::rebind_managed_graph(const ManagedGraphBinding& binding) {
    auto& p = *impl_;
    if(!p.managed() || !p.graph_receipt || !p.state.suspended || p.pipeline ||
       !graph_binding(binding) || binding.epoch <= p.options.managed_graph->epoch ||
       binding.generation == p.options.managed_graph->generation || binding.resource == p.options.managed_graph->resource ||
       (p.state.loaded && !p.saved_checkpoint)) {
        p.state.error = "Fresh graph grant requires a verified drained checkpoint";
        return false;
    }
    p.options.managed_graph = binding;
    std::atomic_store(&p.graph_lifetime,std::make_shared<GraphLifetime>());
    p.graph_receipt.reset();
    p.managed_epoch_sealed = false;
    p.state.error.clear();
    return true;
}

void Playback::stop() {
    auto& p = *impl_;
    p.saved_checkpoint.reset();
    p.media_identity.reset();
    p.subtitle_identity.reset();
    p.retain_recovery_budgets = false;
    p.resume_hdmi_fallback_pause = p.resume_ended = false;
    p.state.suspended = false;
    p.initial_hdmi_pending = false;
    p.destroy_pipeline();
    p.software_video_preferred = false;
    p.video_decode_retry_attempted = false;
    p.state.software_video_fallback = false;
    p.release_hdmi_audio(true);
    p.release_desktop();
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
    const auto inventory = p.managed() ? OutputInventory{} : inspect_outputs(true);
    std::size_t subtitle_renderer_count = 0;
    {
        std::lock_guard<std::mutex> lock(p.subtitle_mutex);
        subtitle_renderer_count = p.subtitle_overlays.size();
    }
    std::ostringstream out;
    out << "{\"gstreamer\":" << json_string(std::to_string(major) + "." + std::to_string(minor) + "." + std::to_string(micro))
        << ",\"loaded\":" << (p.state.loaded ? "true" : "false") << ",\"playing\":" << (p.state.playing ? "true" : "false")
        << ",\"suspended\":" << (p.state.suspended ? "true" : "false")
        << ",\"checkpoint_available\":" << (p.saved_checkpoint ? "true" : "false")
        << ",\"resources\":{\"pipeline_present\":" << (resources().pipeline_present ? "true" : "false")
        << ",\"drm_fd_open\":" << (resources().drm_fd_open ? "true" : "false")
        << ",\"desktop_lease_open\":" << (resources().desktop_lease_open ? "true" : "false")
        << ",\"audio_lease_open\":" << (resources().audio_lease_open ? "true" : "false")
        << ",\"external_cleanup_unconfirmed\":" << (resources().external_cleanup_unconfirmed ? "true" : "false") << '}'
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
        << ",\"software_video_fallback\":" << (p.state.software_video_fallback ? "true" : "false")
        << ",\"video_decode_fallbacks\":" << p.state.video_decode_fallbacks
        << ",\"video_decode_fallback_reason\":" << json_string(p.state.video_decode_fallback_reason)
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
