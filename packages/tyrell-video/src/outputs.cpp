#include "outputs.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cctype>
#include <cstring>
#include <filesystem>
#include <sstream>
#include <set>
#include <thread>

namespace tyrell::video {
AudioRoute choose_audio_route(const std::vector<AudioSink>& sinks, bool prefer_hdmi,
                             const std::string& previous_selection) {
    AudioRoute route;
    if(!previous_selection.empty()) {
        const auto selected = std::find_if(sinks.begin(), sinks.end(), [&](const AudioSink& sink) {
            return sink.available && sink.name == previous_selection;
        });
        if(selected != sinks.end()) { route.sink_name = selected->name; return route; }
        route.reset_previous_selection = true;
    }
    auto selected = std::find_if(sinks.begin(), sinks.end(), [&](const AudioSink& sink) {
        return sink.available && (prefer_hdmi ? sink.hdmi : sink.onboard);
    });
    if(selected == sinks.end() && prefer_hdmi) {
        route.hdmi_fallback = true;
        selected = std::find_if(sinks.begin(), sinks.end(), [](const AudioSink& sink) { return sink.available && sink.onboard; });
    }
    if(selected != sinks.end()) route.sink_name = selected->name;
    return route;
}

std::optional<DisplayMode> choose_hdmi_mode(const std::vector<DisplayMode>& modes) {
    for(int pass = 0; pass < 2; ++pass) {
        std::optional<DisplayMode> selected;
        std::set<std::pair<int, int>> dimensions;
        for(const auto& mode : modes) {
            if(!dimensions.emplace(mode.width, mode.height).second) continue;
            if(mode.width <= 0 || mode.height <= 0 || mode.width > 1920 || mode.height > 1080 || mode.interlaced) continue;
            if(pass == 0 && (mode.width > 1280 || mode.height > 720)) continue;
            const int pixels = mode.width * mode.height;
            if(!selected || pixels > selected->width * selected->height ||
               (pixels == selected->width * selected->height && mode.refresh > selected->refresh)) selected = mode;
        }
        if(selected) return selected;
    }
    return std::nullopt;
}
} // namespace tyrell::video

#if defined(__linux__)
#include <fcntl.h>
#include <unistd.h>
#include <xf86drm.h>
#include <xf86drmMode.h>
#include <pulse/pulseaudio.h>

namespace tyrell::video {
namespace {

std::string lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return text;
}

std::string quoted(const std::string& value) {
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
                if(c < 32) {
                    constexpr char hex[] = "0123456789abcdef";
                    out << "\\u00" << hex[c >> 4] << hex[c & 15];
                } else out << static_cast<char>(c);
        }
    }
    return out.str() + '"';
}

DisplayMode describe(const drmModeModeInfo& mode) {
    return {mode.hdisplay, mode.vdisplay, static_cast<int>(mode.vrefresh),
            (mode.type & DRM_MODE_TYPE_PREFERRED) != 0, (mode.flags & DRM_MODE_FLAG_INTERLACE) != 0};
}

std::vector<std::string> cards() {
    std::vector<std::string> result;
    std::error_code error;
    for(const auto& entry : std::filesystem::directory_iterator("/dev/dri", error)) {
        const auto name = entry.path().filename().string();
        if(name.size() > 4 && name.rfind("card", 0) == 0 &&
           std::all_of(name.begin() + 4, name.end(), [](unsigned char c) { return std::isdigit(c); }))
            result.push_back(entry.path().string());
    }
    std::sort(result.begin(), result.end());
    return result;
}

struct PulseQuery {
    std::vector<AudioSink> sinks;
    bool done = false;
};

void sink_info(pa_context*, const pa_sink_info* info, int end, void* data) {
    auto& query = *static_cast<PulseQuery*>(data);
    if(end != 0) { query.done = true; return; }
    if(!info) return;
    AudioSink sink;
    sink.name = info->name ? info->name : "";
    sink.description = info->description ? info->description : sink.name;
    std::string identity = sink.name + " " + sink.description;
    for(const char* key : {"alsa.card_name", "alsa.name", "device.description", "device.profile.name"}) {
        const char* value = pa_proplist_gets(info->proplist, key);
        if(value) identity += std::string(" ") + value;
    }
    if(info->active_port && info->active_port->name) {
        identity += std::string(" ") + info->active_port->name;
        sink.available = info->active_port->available != PA_PORT_AVAILABLE_NO;
    }
    identity = lower(identity);
    sink.hdmi = identity.find("hdmi") != std::string::npos;
    sink.onboard = identity.find("es8389") != std::string::npos;
    query.sinks.push_back(std::move(sink));
}

void inspect_audio(OutputInventory& result) {
    pa_mainloop* loop = pa_mainloop_new();
    if(!loop) { result.audio_error = "Cannot allocate PulseAudio main loop"; return; }
    pa_context* context = pa_context_new(pa_mainloop_get_api(loop), "Tyrell Video output discovery");
    if(!context) { pa_mainloop_free(loop); result.audio_error = "Cannot allocate audio context"; return; }
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(700);
    if(pa_context_connect(context, nullptr, PA_CONTEXT_NOAUTOSPAWN, nullptr) < 0) {
        result.audio_error = pa_strerror(pa_context_errno(context));
    } else {
        while(std::chrono::steady_clock::now() < deadline) {
            pa_mainloop_iterate(loop, 0, nullptr);
            const auto state = pa_context_get_state(context);
            if(state == PA_CONTEXT_READY || !PA_CONTEXT_IS_GOOD(state)) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
        }
        if(pa_context_get_state(context) == PA_CONTEXT_READY) {
            PulseQuery query;
            pa_operation* operation = pa_context_get_sink_info_list(context, sink_info, &query);
            if(operation) {
                while(!query.done && pa_operation_get_state(operation) == PA_OPERATION_RUNNING &&
                      std::chrono::steady_clock::now() < deadline) {
                    pa_mainloop_iterate(loop, 0, nullptr);
                    std::this_thread::sleep_for(std::chrono::milliseconds(2));
                }
                if(!query.done) pa_operation_cancel(operation);
                pa_operation_unref(operation);
            }
            result.audio = std::move(query.sinks);
            if(!query.done) result.audio_error = "Audio discovery timed out";
        } else result.audio_error = "PipeWire/PulseAudio is unavailable";
    }
    pa_context_disconnect(context);
    pa_context_unref(context);
    pa_mainloop_free(loop);
}

} // namespace

OutputInventory inspect_outputs(bool include_audio) {
    OutputInventory result;
    for(const auto& path : cards()) {
        const int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC);
        if(fd < 0) { result.display_error = path + ": " + std::strerror(errno); continue; }
        drmModeRes* resources = drmModeGetResources(fd);
        if(!resources) { close(fd); continue; }
        for(int i = 0; i < resources->count_connectors; ++i) {
            drmModeConnector* connector = drmModeGetConnector(fd, resources->connectors[i]);
            if(!connector) continue;
            if(connector->connector_type == DRM_MODE_CONNECTOR_HDMIA || connector->connector_type == DRM_MODE_CONNECTOR_HDMIB) {
                HdmiConnector item;
                item.device = path;
                item.connector_id = connector->connector_id;
                item.name = "HDMI-" + std::to_string(connector->connector_type_id);
                item.connected = connector->connection == DRM_MODE_CONNECTED;
                for(int j = 0; j < connector->count_modes; ++j) item.modes.push_back(describe(connector->modes[j]));
                result.hdmi.push_back(std::move(item));
            }
            drmModeFreeConnector(connector);
        }
        drmModeFreeResources(resources);
        close(fd);
    }
    if(include_audio) inspect_audio(result);
    return result;
}

std::string output_inventory_json(const OutputInventory& inventory) {
    std::ostringstream out;
    out << "{\"hdmi\":[";
    bool comma = false;
    for(const auto& connector : inventory.hdmi) {
        if(comma) out << ',';
        comma = true;
        out << "{\"device\":" << quoted(connector.device) << ",\"connector_id\":" << connector.connector_id
            << ",\"name\":" << quoted(connector.name) << ",\"connected\":" << (connector.connected ? "true" : "false") << ",\"modes\":[";
        bool mode_comma = false;
        for(const auto& mode : connector.modes) {
            if(mode_comma) out << ',';
            mode_comma = true;
            out << "{\"width\":" << mode.width << ",\"height\":" << mode.height << ",\"refresh\":" << mode.refresh
                << ",\"interlaced\":" << (mode.interlaced ? "true" : "false") << '}';
        }
        out << "]}";
    }
    out << "],\"audio\":[";
    comma = false;
    for(const auto& sink : inventory.audio) {
        if(comma) out << ',';
        comma = true;
        out << "{\"name\":" << quoted(sink.name) << ",\"description\":" << quoted(sink.description)
            << ",\"hdmi\":" << (sink.hdmi ? "true" : "false") << ",\"onboard\":" << (sink.onboard ? "true" : "false")
            << ",\"available\":" << (sink.available ? "true" : "false") << '}';
    }
    out << "],\"audio_error\":" << quoted(inventory.audio_error) << ",\"display_error\":" << quoted(inventory.display_error) << '}';
    return out.str();
}

struct HdmiSession::Impl {
    struct SavedCrtc { drmModeCrtc* crtc = nullptr; std::vector<std::uint32_t> connectors; };
    int fd = -1;
    std::string path;
    std::uint32_t connector = 0;
    DisplayMode selected;
    std::vector<SavedCrtc> saved;
};

HdmiSession::HdmiSession() : impl_(std::make_unique<Impl>()) {}
HdmiSession::~HdmiSession() { release(); }

bool HdmiSession::acquire(std::string& error) {
    release();
    const auto inventory = inspect_outputs(false);
    for(const auto& item : inventory.hdmi) {
        if(!item.connected) continue;
        const int fd = open(item.device.c_str(), O_RDWR | O_CLOEXEC);
        if(fd < 0) { error = "Cannot open HDMI device: " + std::string(std::strerror(errno)); continue; }
        if(drmSetMaster(fd) != 0) {
            error = "HDMI is owned by another display session; close that session to use HDMI";
            close(fd);
            continue;
        }
        drmModeConnector* connector = drmModeGetConnector(fd, item.connector_id);
        drmModeRes* resources = drmModeGetResources(fd);
        if(!connector || !resources) {
            if(connector) drmModeFreeConnector(connector);
            if(resources) drmModeFreeResources(resources);
            drmDropMaster(fd); close(fd); error = "Cannot inspect HDMI resources"; continue;
        }
        std::vector<DisplayMode> candidates;
        for(int i = 0; i < connector->count_modes; ++i) candidates.push_back(describe(connector->modes[i]));
        const auto selected = choose_hdmi_mode(candidates);
        if(!selected) {
            drmModeFreeConnector(connector); drmModeFreeResources(resources);
            drmDropMaster(fd); close(fd); error = "Monitor has no supported progressive mode at or below 1080p"; continue;
        }
        impl_->fd = fd;
        impl_->path = item.device;
        impl_->connector = item.connector_id;
        impl_->selected = *selected;
        // Snapshot every CRTC on this HDMI card because kmssink selects a CRTC
        // from the connector's encoder mask. The SPI LCD is a different card.
        for(int i = 0; i < resources->count_crtcs; ++i) {
            Impl::SavedCrtc saved;
            saved.crtc = drmModeGetCrtc(fd, resources->crtcs[i]);
            if(!saved.crtc) continue;
            for(int j = 0; j < resources->count_connectors; ++j) {
                drmModeConnector* other = drmModeGetConnector(fd, resources->connectors[j]);
                if(other) {
                    drmModeEncoder* encoder = other->encoder_id ? drmModeGetEncoder(fd, other->encoder_id) : nullptr;
                    if(encoder && encoder->crtc_id == saved.crtc->crtc_id) saved.connectors.push_back(other->connector_id);
                    if(encoder) drmModeFreeEncoder(encoder);
                    drmModeFreeConnector(other);
                }
            }
            impl_->saved.push_back(std::move(saved));
        }
        drmModeFreeConnector(connector);
        drmModeFreeResources(resources);
        return true;
    }
    if(error.empty()) error = inventory.display_error.empty() ? "No connected HDMI display" : inventory.display_error;
    return false;
}

void HdmiSession::release() {
    if(impl_->fd >= 0) {
        for(auto& saved : impl_->saved) {
            const auto* crtc = saved.crtc;
            if(crtc->mode_valid && !saved.connectors.empty())
                drmModeSetCrtc(impl_->fd, crtc->crtc_id, crtc->buffer_id, crtc->x, crtc->y,
                               saved.connectors.data(), static_cast<int>(saved.connectors.size()), &saved.crtc->mode);
            else drmModeSetCrtc(impl_->fd, crtc->crtc_id, 0, 0, 0, nullptr, 0, nullptr);
            drmModeFreeCrtc(saved.crtc);
        }
        drmDropMaster(impl_->fd);
        close(impl_->fd);
    }
    impl_->saved.clear();
    impl_->fd = -1;
    impl_->connector = 0;
    impl_->path.clear();
    impl_->selected = {};
}

bool HdmiSession::connected() const {
    if(impl_->fd < 0) return false;
    drmModeConnector* connector = drmModeGetConnector(impl_->fd, impl_->connector);
    const bool result = connector && connector->connection == DRM_MODE_CONNECTED;
    if(connector) drmModeFreeConnector(connector);
    return result;
}
int HdmiSession::fd() const { return impl_->fd; }
std::uint32_t HdmiSession::connector_id() const { return impl_->connector; }
DisplayMode HdmiSession::mode() const { return impl_->selected; }
std::string HdmiSession::device() const { return impl_->path; }

} // namespace tyrell::video
#else
namespace tyrell::video {
OutputInventory inspect_outputs(bool) {
    OutputInventory inventory;
    inventory.display_error = "Direct HDMI output requires Linux DRM";
    return inventory;
}
std::string output_inventory_json(const OutputInventory&) {
    return "{\"hdmi\":[],\"audio\":[],\"display_error\":\"Direct HDMI output requires Linux DRM\",\"audio_error\":\"\"}";
}
struct HdmiSession::Impl {};
HdmiSession::HdmiSession() : impl_(std::make_unique<Impl>()) {}
HdmiSession::~HdmiSession() = default;
bool HdmiSession::acquire(std::string& error) { error = "Direct HDMI output requires Linux DRM"; return false; }
void HdmiSession::release() {}
bool HdmiSession::connected() const { return false; }
int HdmiSession::fd() const { return -1; }
std::uint32_t HdmiSession::connector_id() const { return 0; }
DisplayMode HdmiSession::mode() const { return {}; }
std::string HdmiSession::device() const { return {}; }
} // namespace tyrell::video
#endif
