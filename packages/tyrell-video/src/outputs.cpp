#include "outputs.h"
#include "json_string.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cctype>
#include <cstring>
#include <filesystem>
#include <iostream>
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
#include <sys/mman.h>
#include <unistd.h>
#include <drm_fourcc.h>
#include <xf86drm.h>
#include <xf86drmMode.h>
#include <pulse/pulseaudio.h>

namespace tyrell::video {
namespace {

std::string lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return text;
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
        out << "{\"device\":" << json_string(connector.device) << ",\"connector_id\":" << connector.connector_id
            << ",\"name\":" << json_string(connector.name) << ",\"connected\":" << (connector.connected ? "true" : "false") << ",\"modes\":[";
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
        out << "{\"name\":" << json_string(sink.name) << ",\"description\":" << json_string(sink.description)
            << ",\"hdmi\":" << (sink.hdmi ? "true" : "false") << ",\"onboard\":" << (sink.onboard ? "true" : "false")
            << ",\"available\":" << (sink.available ? "true" : "false") << '}';
    }
    out << "],\"audio_error\":" << json_string(inventory.audio_error) << ",\"display_error\":" << json_string(inventory.display_error) << '}';
    return out.str();
}

struct HdmiSession::Impl {
    struct SavedCrtc { drmModeCrtc* crtc = nullptr; std::vector<std::uint32_t> connectors; };
    struct SavedPlaneProperty { std::uint32_t id; std::uint64_t value; };
    int fd = -1;
    std::string path;
    std::uint32_t connector = 0;
    std::uint32_t plane = 0;
    std::uint32_t background_handle = 0;
    std::uint32_t background_fb = 0;
    bool modeset_attempted = false;
    DisplayMode selected;
    std::vector<SavedCrtc> saved;
    std::vector<SavedPlaneProperty> plane_properties;

    bool configure_color(std::string& error) {
        auto* properties = drmModeObjectGetProperties(fd, plane, DRM_MODE_OBJECT_PLANE);
        if(!properties) { error = "Cannot inspect HDMI overlay color properties"; return false; }
        bool ok = true;
        for(std::uint32_t i = 0; i < properties->count_props && ok; ++i) {
            auto* property = drmModeGetProperty(fd, properties->props[i]);
            if(!property) { error = "Cannot inspect HDMI overlay property"; ok = false; break; }
            const char* target = nullptr;
            if(std::strcmp(property->name, "COLOR_ENCODING") == 0) target = "ITU-R BT.709 YCbCr";
            if(std::strcmp(property->name, "COLOR_RANGE") == 0) target = "YCbCr limited range";
            if(target) {
                const auto* value = property->count_enums > 0 ? std::find_if(property->enums, property->enums + property->count_enums,
                    [&](const drm_mode_property_enum& item) { return std::strcmp(item.name, target) == 0; })
                    : nullptr;
                if(!value || value == property->enums + property->count_enums) {
                    error = "HDMI overlay does not support BT.709 limited-range video"; ok = false;
                } else if(value->value != properties->prop_values[i]) {
                    plane_properties.push_back({property->prop_id, properties->prop_values[i]});
                    if(drmModeObjectSetProperty(fd, plane, DRM_MODE_OBJECT_PLANE, property->prop_id, value->value) != 0) {
                        error = "Cannot configure HDMI overlay color: " + std::string(std::strerror(errno)); ok = false;
                    }
                }
            }
            drmModeFreeProperty(property);
        }
        drmModeFreeObjectProperties(properties);
        return ok;
    }

    bool configure(drmModeConnector* output, drmModeRes* resources, std::string& error) {
        if(output->connection != DRM_MODE_CONNECTED) { error = "HDMI disconnected during setup"; return false; }
        std::vector<DisplayMode> candidates;
        for(int i = 0; i < output->count_modes; ++i) candidates.push_back(describe(output->modes[i]));
        const auto mode = choose_hdmi_mode(candidates);
        if(!mode) { error = "Monitor has no supported progressive mode at or below 1080p"; return false; }
        selected = *mode;
        auto* timing = &output->modes[std::distance(candidates.begin(),
            std::find_if(candidates.begin(), candidates.end(), [&](const DisplayMode& item) {
                return item.width == selected.width && item.height == selected.height;
            }))];

        // Keep every original CRTC and connector route for rollback. The SPI
        // LCD is on a different DRM card and is never touched here.
        for(int i = 0; i < resources->count_crtcs; ++i) {
            auto* crtc = drmModeGetCrtc(fd, resources->crtcs[i]);
            if(!crtc) { error = "Cannot save HDMI CRTC state"; return false; }
            saved.push_back({crtc, {}});
            for(int j = 0; j < resources->count_connectors; ++j) {
                auto* other = drmModeGetConnector(fd, resources->connectors[j]);
                if(!other) { error = "Cannot save HDMI connector state"; return false; }
                auto* encoder = other->encoder_id ? drmModeGetEncoder(fd, other->encoder_id) : nullptr;
                const bool missing_encoder = other->encoder_id && !encoder;
                if(encoder && encoder->crtc_id == crtc->crtc_id) saved.back().connectors.push_back(other->connector_id);
                if(encoder) drmModeFreeEncoder(encoder);
                drmModeFreeConnector(other);
                if(missing_encoder) { error = "Cannot save HDMI encoder state"; return false; }
            }
        }

        std::uint32_t compatible = 0, current = 0;
        for(int i = 0; i < output->count_encoders; ++i) {
            auto* encoder = drmModeGetEncoder(fd, output->encoders[i]);
            if(!encoder) { error = "Cannot inspect HDMI encoder"; return false; }
            compatible |= encoder->possible_crtcs;
            if(encoder->encoder_id == output->encoder_id) current = encoder->crtc_id;
            drmModeFreeEncoder(encoder);
        }
        std::vector<int> crtc_order;
        // Prefer the connector's own CRTC, otherwise use a disabled one. Do
        // not take a CRTC that also drives another connector.
        for(int pass = 0; pass < 2; ++pass) for(int i = 0; i < resources->count_crtcs && i < 32; ++i) {
            const auto& previous = saved[i];
            if(!(compatible & (std::uint32_t{1} << i))) continue;
            if(std::any_of(previous.connectors.begin(), previous.connectors.end(), [&](std::uint32_t id) { return id != connector; })) continue;
            if(pass == 0 ? previous.crtc->crtc_id == current :
               previous.crtc->crtc_id != current && !previous.crtc->mode_valid && previous.crtc->buffer_id == 0)
                crtc_order.push_back(i);
        }
        if(crtc_order.empty()) { error = "HDMI has no unused compatible CRTC"; return false; }
        if(drmSetClientCap(fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1) != 0) {
            error = "Cannot inspect HDMI planes: " + std::string(std::strerror(errno)); return false;
        }
        auto* planes = drmModeGetPlaneResources(fd);
        if(!planes) { error = "Cannot inspect HDMI planes"; return false; }
        int crtc_index = -1;
        for(const int index : crtc_order) {
            for(std::uint32_t i = 0; i < planes->count_planes && !plane; ++i) {
                auto* candidate = drmModeGetPlane(fd, planes->planes[i]);
                if(!candidate) continue;
                bool overlay = false, plain = true;
                auto* properties = drmModeObjectGetProperties(fd, candidate->plane_id, DRM_MODE_OBJECT_PLANE);
                if(properties) {
                    for(std::uint32_t j = 0; j < properties->count_props; ++j) {
                        auto* property = drmModeGetProperty(fd, properties->props[j]);
                        if(property && std::strcmp(property->name, "type") == 0)
                            overlay = properties->prop_values[j] == DRM_PLANE_TYPE_OVERLAY;
                        if(property && std::strcmp(property->name, "rotation") == 0)
                            plain = plain && properties->prop_values[j] == DRM_MODE_ROTATE_0;
                        if(property && std::strcmp(property->name, "alpha") == 0)
                            plain = plain && properties->prop_values[j] == 0xffff;
                        if(property) drmModeFreeProperty(property);
                    }
                    drmModeFreeObjectProperties(properties);
                }
                const bool nv12 = candidate->count_formats > 0 && std::find(candidate->formats, candidate->formats + candidate->count_formats,
                                            DRM_FORMAT_NV12) != candidate->formats + candidate->count_formats;
                if(overlay && plain && nv12 && !candidate->fb_id && !candidate->crtc_id &&
                   (candidate->possible_crtcs & (std::uint32_t{1} << index))) {
                    plane = candidate->plane_id;
                    crtc_index = index;
                }
                drmModeFreePlane(candidate);
            }
            if(plane) break;
        }
        drmModeFreePlaneResources(planes);
        if(!plane) { error = "HDMI has no unused NV12 overlay plane"; return false; }
        if(!configure_color(error)) return false;

        // Establish the bounded display mode independently of the movie's
        // dimensions. kmssink can then use the overlay/HVS to scale native
        // NV12 frames instead of forcing a software upscale to the mode size.
        drm_mode_create_dumb create{};
        create.width = selected.width;
        create.height = selected.height;
        create.bpp = 32;
        if(drmIoctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &create) != 0) {
            error = "Cannot allocate HDMI background: " + std::string(std::strerror(errno)); return false;
        }
        background_handle = create.handle;
        drm_mode_map_dumb map{};
        map.handle = background_handle;
        if(drmIoctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &map) != 0) {
            error = "Cannot map HDMI background: " + std::string(std::strerror(errno)); return false;
        }
        void* pixels = mmap(nullptr, create.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, map.offset);
        if(pixels == MAP_FAILED) {
            error = "Cannot clear HDMI background: " + std::string(std::strerror(errno)); return false;
        }
        std::memset(pixels, 0, create.size);
        munmap(pixels, create.size);
        const std::uint32_t handles[4] = {background_handle, 0, 0, 0};
        const std::uint32_t pitches[4] = {create.pitch, 0, 0, 0};
        const std::uint32_t offsets[4] = {};
        if(drmModeAddFB2(fd, selected.width, selected.height, DRM_FORMAT_XRGB8888,
                         handles, pitches, offsets, &background_fb, 0) != 0) {
            error = "Cannot create HDMI background: " + std::string(std::strerror(errno)); return false;
        }
        modeset_attempted = true;
        if(drmModeSetCrtc(fd, resources->crtcs[crtc_index], background_fb, 0, 0, &connector, 1, timing) != 0) {
            error = "Cannot set HDMI display mode: " + std::string(std::strerror(errno)); return false;
        }
        return true;
    }
};

HdmiSession::HdmiSession() : impl_(std::make_unique<Impl>()) {}
HdmiSession::~HdmiSession() { release(); }

bool HdmiSession::acquire(std::string& error) {
    release();
    error.clear();
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
        impl_->fd = fd;
        impl_->path = item.device;
        impl_->connector = item.connector_id;
        const bool configured = impl_->configure(connector, resources, error);
        drmModeFreeConnector(connector);
        drmModeFreeResources(resources);
        if(configured) return true;
        release();
    }
    if(error.empty()) error = inventory.display_error.empty() ? "No connected HDMI display" : inventory.display_error;
    return false;
}

void HdmiSession::release() {
    if(impl_->fd >= 0) {
        if(impl_->modeset_attempted && impl_->plane &&
           drmModeSetPlane(impl_->fd, impl_->plane, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0) != 0)
            std::cerr << "Tyrell HDMI: cannot disable owned overlay: " << std::strerror(errno) << '\n';
        for(const auto& property : impl_->plane_properties) {
            if(drmModeObjectSetProperty(impl_->fd, impl_->plane, DRM_MODE_OBJECT_PLANE, property.id, property.value) != 0)
                std::cerr << "Tyrell HDMI: cannot restore overlay property: " << std::strerror(errno) << '\n';
        }
        for(auto& saved : impl_->saved) {
            const auto* crtc = saved.crtc;
            if(impl_->modeset_attempted) {
                const int result = crtc->mode_valid && !saved.connectors.empty()
                    ? drmModeSetCrtc(impl_->fd, crtc->crtc_id, crtc->buffer_id, crtc->x, crtc->y,
                                      saved.connectors.data(), static_cast<int>(saved.connectors.size()), &saved.crtc->mode)
                    : drmModeSetCrtc(impl_->fd, crtc->crtc_id, 0, 0, 0, nullptr, 0, nullptr);
                if(result != 0) std::cerr << "Tyrell HDMI: cannot restore CRTC " << crtc->crtc_id
                                        << ": " << std::strerror(errno) << '\n';
            }
            drmModeFreeCrtc(saved.crtc);
        }
        if(impl_->background_fb) drmModeRmFB(impl_->fd, impl_->background_fb);
        if(impl_->background_handle) {
            drm_mode_destroy_dumb destroy{};
            destroy.handle = impl_->background_handle;
            drmIoctl(impl_->fd, DRM_IOCTL_MODE_DESTROY_DUMB, &destroy);
        }
        drmDropMaster(impl_->fd);
        close(impl_->fd);
    }
    impl_->saved.clear();
    impl_->plane_properties.clear();
    impl_->fd = -1;
    impl_->connector = 0;
    impl_->plane = impl_->background_handle = impl_->background_fb = 0;
    impl_->modeset_attempted = false;
    impl_->path.clear();
    impl_->selected = {};
}

bool HdmiSession::connected() const {
    if(impl_->fd < 0) return false;
    // Poll the status maintained by kernel hotplug detection. GetConnector()
    // forces a blocking EDID probe while we hold DRM master; doing that every
    // 500 ms can stall the controller and make the HDMI picture flicker.
    drmModeConnector* connector = drmModeGetConnectorCurrent(impl_->fd, impl_->connector);
    const bool result = connector && connector->connection == DRM_MODE_CONNECTED;
    if(connector) drmModeFreeConnector(connector);
    return result;
}
int HdmiSession::fd() const { return impl_->fd; }
std::uint32_t HdmiSession::connector_id() const { return impl_->connector; }
std::uint32_t HdmiSession::plane_id() const { return impl_->plane; }
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
std::uint32_t HdmiSession::plane_id() const { return 0; }
DisplayMode HdmiSession::mode() const { return {}; }
std::string HdmiSession::device() const { return {}; }
} // namespace tyrell::video
#endif
