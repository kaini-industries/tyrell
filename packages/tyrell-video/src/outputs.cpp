#include "outputs.h"
#include "plane_lease.h"
#include "json_string.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cctype>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <map>
#include <sstream>
#include <set>
#include <stdexcept>
#include <thread>
#include <utility>

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
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/ioctl.h>
#include <poll.h>
#include <linux/sync_file.h>
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
    std::vector<std::string> video_formats;
    std::vector<SavedCrtc> saved;
    std::vector<SavedPlaneProperty> plane_properties;
    DisplayPlaneLease foreign_planes;

    bool hide_foreign_planes(std::uint32_t crtc, std::string& error) {
        // Atomic client capability exposes the exact source/destination plane
        // properties. Legacy GETPLANE alone cannot recover those rectangles.
        // kmssink may still use legacy SetPlane calls with this capability set.
        if(drmSetClientCap(fd, DRM_CLIENT_CAP_ATOMIC, 1) != 0) {
            error = "Cannot save HDMI cursor and overlay state: " + std::string(std::strerror(errno));
            return false;
        }
        auto* resources = drmModeGetPlaneResources(fd);
        if(!resources) { error = "Cannot enumerate HDMI cursor and overlays"; return false; }
        std::vector<DisplayPlaneState> states;
        bool ok = true;
        for(std::uint32_t i = 0; i < resources->count_planes && ok; ++i) {
            auto* item = drmModeGetPlane(fd, resources->planes[i]);
            if(!item) { error = "Cannot inspect HDMI plane state"; ok = false; break; }
            if(item->plane_id == plane || item->crtc_id != crtc || !item->fb_id) {
                drmModeFreePlane(item);
                continue;
            }
            DisplayPlaneState state;
            state.id = item->plane_id;
            state.crtc = item->crtc_id;
            state.framebuffer = item->fb_id;
            drmModeFreePlane(item);
            auto* properties = drmModeObjectGetProperties(fd, state.id, DRM_MODE_OBJECT_PLANE);
            if(!properties) { error = "Cannot save HDMI plane geometry"; ok = false; break; }
            unsigned geometry = 0;
            for(std::uint32_t j = 0; j < properties->count_props; ++j) {
                auto* property = drmModeGetProperty(fd, properties->props[j]);
                if(!property) { ok = false; break; }
                const auto value = static_cast<std::uint32_t>(properties->prop_values[j]);
                // CRTC_X/Y are signed values carried by unsigned properties.
                const auto coordinate = static_cast<std::int32_t>(
                    value <= 0x7fffffff ? static_cast<std::int64_t>(value)
                                        : static_cast<std::int64_t>(value) - 0x100000000LL);
                if(std::strcmp(property->name, "type") == 0) {
                    if(value == DRM_PLANE_TYPE_PRIMARY) state.kind = DisplayPlaneKind::Primary;
                    if(value == DRM_PLANE_TYPE_OVERLAY) state.kind = DisplayPlaneKind::Overlay;
                    if(value == DRM_PLANE_TYPE_CURSOR) state.kind = DisplayPlaneKind::Cursor;
                }
                if(std::strcmp(property->name, "CRTC_X") == 0) { state.x = coordinate; geometry |= 1; }
                if(std::strcmp(property->name, "CRTC_Y") == 0) { state.y = coordinate; geometry |= 2; }
                if(std::strcmp(property->name, "CRTC_W") == 0) { state.width = value; geometry |= 4; }
                if(std::strcmp(property->name, "CRTC_H") == 0) { state.height = value; geometry |= 8; }
                if(std::strcmp(property->name, "SRC_X") == 0) { state.source_x = value; geometry |= 16; }
                if(std::strcmp(property->name, "SRC_Y") == 0) { state.source_y = value; geometry |= 32; }
                if(std::strcmp(property->name, "SRC_W") == 0) { state.source_width = value; geometry |= 64; }
                if(std::strcmp(property->name, "SRC_H") == 0) { state.source_height = value; geometry |= 128; }
                drmModeFreeProperty(property);
            }
            drmModeFreeObjectProperties(properties);
            if(!ok || state.kind == DisplayPlaneKind::Unknown ||
               (state.kind != DisplayPlaneKind::Primary && geometry != 255)) {
                error = "Cannot preserve complete HDMI cursor/overlay geometry";
                ok = false;
                break;
            }
            states.push_back(state);
        }
        drmModeFreePlaneResources(resources);
        if(!ok) return false;
        if(!foreign_planes.hide(states, crtc, plane, [&](std::uint32_t id) {
            return drmModeSetPlane(fd, id, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0) == 0;
        })) {
            error = "Cannot hide desktop cursor/overlay on HDMI: " + std::string(std::strerror(errno));
            return false;
        }
        return true;
    }

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
                std::vector<std::string> formats;
                for(const auto& format : {std::make_pair(DRM_FORMAT_YUV420, "I420"),
                                         std::make_pair(DRM_FORMAT_NV12, "NV12"),
                                         std::make_pair(DRM_FORMAT_YVU420, "YV12")}) {
                    if(candidate->count_formats > 0 && std::find(candidate->formats,
                        candidate->formats + candidate->count_formats, format.first) != candidate->formats + candidate->count_formats)
                        formats.emplace_back(format.second);
                }
                if(overlay && plain && !formats.empty() && !candidate->fb_id && !candidate->crtc_id &&
                   (candidate->possible_crtcs & (std::uint32_t{1} << index))) {
                    plane = candidate->plane_id;
                    crtc_index = index;
                    video_formats = std::move(formats);
                }
                drmModeFreePlane(candidate);
            }
            if(plane) break;
        }
        drmModeFreePlaneResources(planes);
        if(!plane) { error = "HDMI has no unused planar YUV overlay plane"; return false; }
        if(!configure_color(error)) return false;

        // Establish the bounded display mode independently of the movie's
        // dimensions. kmssink can then use the overlay/HVS to scale native
        // YUV frames instead of forcing a software upscale to the mode size.
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
        if(!hide_foreign_planes(resources->crtcs[crtc_index], error)) return false;
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
        impl_->foreign_planes.release([&](const DisplayPlaneState& plane) {
            const bool restored = drmModeSetPlane(impl_->fd, plane.id, plane.crtc, plane.framebuffer, 0,
                plane.x, plane.y, plane.width, plane.height,
                plane.source_x, plane.source_y, plane.source_width, plane.source_height) == 0;
            if(!restored) std::cerr << "Tyrell HDMI: cannot restore desktop plane " << plane.id
                                   << ": " << std::strerror(errno) << '\n';
            return restored;
        });
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
    impl_->video_formats.clear();
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
std::vector<std::string> HdmiSession::video_formats() const { return impl_->video_formats; }
DisplayMode HdmiSession::mode() const { return impl_->selected; }
std::string HdmiSession::device() const { return impl_->path; }

namespace {
bool managed_id(const std::string& value) {
    return value.size() == 32 && std::all_of(value.begin(), value.end(), [](char c) {
        return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
    });
}

struct ManagedProperty { std::uint32_t id = 0; std::uint64_t value = 0; };
ManagedProperty managed_property(int fd, std::uint32_t object, std::uint32_t type, const char* name) {
    auto* values = drmModeObjectGetProperties(fd, object, type);
    if(!values) throw std::runtime_error("Cannot inspect managed KMS object");
    ManagedProperty result;
    bool invalid = false;
    for(std::uint32_t i = 0; i < values->count_props; ++i) {
        auto* property = drmModeGetProperty(fd, values->props[i]);
        if(!property) { invalid = true; break; }
        if(std::strcmp(property->name, name) == 0) {
            if(result.id) invalid = true;
            result = {property->prop_id, values->prop_values[i]};
        }
        drmModeFreeProperty(property);
    }
    drmModeFreeObjectProperties(values);
    if(invalid || !result.id) throw std::runtime_error("Required managed KMS property unavailable");
    return result;
}
} // namespace

struct ManagedHdmiSession::Impl {
    struct Borrower { std::string operation, proof; };
    int fd;
    ManagedHdmiScope scope;
    ManagedHdmiBinding binding;
    CompletionHook terminal;
    std::atomic<ManagedHdmiState> state{ManagedHdmiState::New};
    std::map<std::string, Borrower> borrowers;
    std::uint64_t device = 0, inode = 0, rdev = 0, mode = 0;
    std::string acquire_operation, cleanup_operation, terminal_proof;
    bool fence = false, neutral = false, suppressed = false;
    std::uint64_t sequence = 0;

    Impl(int value, ManagedHdmiScope fixed, ManagedHdmiBinding identity, CompletionHook completion)
        : fd(value), scope(std::move(fixed)), binding(std::move(identity)), terminal(std::move(completion)) {}

    void identity() const {
        struct stat info{};
        if(fstat(fd, &info) != 0 || !S_ISCHR(info.st_mode) || major(info.st_rdev) != 226 || minor(info.st_rdev) >= 128 ||
           static_cast<std::uint64_t>(info.st_dev) != device || static_cast<std::uint64_t>(info.st_ino) != inode ||
           static_cast<std::uint64_t>(info.st_rdev) != rdev)
            throw std::runtime_error("Managed DRM descriptor identity changed");
    }

    bool owned_plane(std::uint32_t id) const {
        return std::find(scope.planes.begin(), scope.planes.end(), id) != scope.planes.end();
    }

    // Inspect all routes into the selected CRTC. A second connector or an
    // unregistered plane invalidates this fixed resource scope before mutation.
    void routes(bool expect_neutral) const {
        auto* resources = drmModeGetResources(fd);
        if(!resources) throw std::runtime_error("Cannot inspect managed KMS routes");
        bool crtc_found = false, connector_found = false, invalid = false;
        for(int i = 0; i < resources->count_crtcs; ++i) crtc_found |= resources->crtcs[i] == scope.crtc;
        try {
            for(int i = 0; i < resources->count_connectors; ++i) {
                const auto id = resources->connectors[i];
                const auto route = managed_property(fd, id, DRM_MODE_OBJECT_CONNECTOR, "CRTC_ID").value;
                if(id == scope.connector) {
                    connector_found = true;
                    invalid |= route != (expect_neutral ? 0 : scope.crtc);
                } else if(route == scope.crtc) invalid = true;
            }
        } catch(...) { drmModeFreeResources(resources); throw; }
        drmModeFreeResources(resources);
        if(!crtc_found || !connector_found || invalid) throw std::runtime_error("Managed connector/CRTC scope changed");
        auto* planes = drmModeGetPlaneResources(fd);
        if(!planes) throw std::runtime_error("Cannot inspect managed plane scope");
        std::set<std::uint32_t> found;
        try {
            for(std::uint32_t i = 0; i < planes->count_planes; ++i) {
                const auto id = planes->planes[i];
                const auto crtc = managed_property(fd, id, DRM_MODE_OBJECT_PLANE, "CRTC_ID").value;
                const auto fb = managed_property(fd, id, DRM_MODE_OBJECT_PLANE, "FB_ID").value;
                if(owned_plane(id)) {
                    found.insert(id);
                    invalid |= expect_neutral ? (crtc != 0 || fb != 0) :
                        !((crtc == scope.crtc && fb != 0) || (crtc == 0 && fb == 0));
                } else if(crtc == scope.crtc) invalid = true;
            }
        } catch(...) { drmModeFreePlaneResources(planes); throw; }
        drmModeFreePlaneResources(planes);
        if(invalid || found.size() != scope.planes.size()) throw std::runtime_error("Managed plane scope changed");
        const auto active = managed_property(fd, scope.crtc, DRM_MODE_OBJECT_CRTC, "ACTIVE").value;
        const auto current_mode = managed_property(fd, scope.crtc, DRM_MODE_OBJECT_CRTC, "MODE_ID").value;
        if(expect_neutral ? (active != 0 || current_mode != 0) : (active != 1 || !current_mode || (mode && current_mode != mode)))
            throw std::runtime_error("Managed CRTC state changed");
    }

    void close_local() noexcept {
        if(fd >= 0) close(fd);
        fd = -1;
        suppressed = true;
    }
};

ManagedHdmiSession::ManagedHdmiSession(int fd, ManagedHdmiScope scope, ManagedHdmiBinding binding,
                                     CompletionHook completion) {
    if(fd <= 2 || !binding.epoch || !managed_id(binding.resource) || !managed_id(binding.generation) ||
       !scope.connector || !scope.crtc || scope.planes.empty() || scope.planes.size() > 16 ||
       std::set<std::uint32_t>(scope.planes.begin(), scope.planes.end()).size() != scope.planes.size() ||
       std::any_of(scope.planes.begin(), scope.planes.end(), [](auto id) { return id == 0; }))
        throw std::invalid_argument("Invalid trusted managed HDMI scope");
    struct stat info{};
    if(fstat(fd, &info) != 0 || !S_ISCHR(info.st_mode) || major(info.st_rdev) != 226 || minor(info.st_rdev) >= 128)
        throw std::invalid_argument("Expected an inherited primary DRM descriptor");
    auto result = std::make_unique<Impl>(fd, std::move(scope), std::move(binding), std::move(completion));
    result->device = info.st_dev;
    result->inode = info.st_ino;
    result->rdev = info.st_rdev;
    const auto flags = fcntl(fd, F_GETFD);
    if(flags < 0 || fcntl(fd, F_SETFD, flags | FD_CLOEXEC) < 0)
        throw std::runtime_error("Cannot secure managed DRM descriptor inheritance");
    impl_ = std::move(result); // Ownership transfers only after validation.
}

ManagedHdmiSession::~ManagedHdmiSession() { abandon(); }

bool ManagedHdmiSession::acquire(const std::string& operation, std::string& error) {
    error.clear();
    if(!managed_id(operation)) { error = "Invalid coordinator acquire permit"; return false; }
    if(state() == ManagedHdmiState::Owned && operation == impl_->acquire_operation) return true;
    if(state() != ManagedHdmiState::New) { error = "Managed HDMI cannot acquire in this state"; return false; }
    impl_->acquire_operation = operation;
    try {
        impl_->identity();
        if(drmIsMaster(impl_->fd) != 1 || drmSetClientCap(impl_->fd, DRM_CLIENT_CAP_ATOMIC, 1) != 0)
            throw std::runtime_error("Managed DRM master/atomic capability unavailable");
        impl_->routes(false);
        impl_->mode = managed_property(impl_->fd, impl_->scope.crtc, DRM_MODE_OBJECT_CRTC, "MODE_ID").value;
        managed_property(impl_->fd, impl_->scope.crtc, DRM_MODE_OBJECT_CRTC, "OUT_FENCE_PTR");
        impl_->state = ManagedHdmiState::Owned;
        return true;
    } catch(const std::exception& failure) {
        impl_->state = ManagedHdmiState::Unknown;
        error = failure.what();
        return false;
    }
}

bool ManagedHdmiSession::register_borrower(const std::string& id, std::string& error) {
    error.clear();
    if(state() != ManagedHdmiState::Owned || !managed_id(id) || impl_->borrowers.size() >= 16 || impl_->borrowers.count(id)) {
        error = "Managed borrower registry is sealed, invalid or full"; return false;
    }
    impl_->borrowers.emplace(id, Impl::Borrower{});
    return true;
}

int ManagedHdmiSession::borrow_fd(const std::string& id, const std::string& operation, std::string& error) {
    error.clear();
    const auto found = impl_->borrowers.find(id);
    if(state() != ManagedHdmiState::Owned || !managed_id(operation) || found == impl_->borrowers.end() ||
       !found->second.operation.empty() || operation == impl_->acquire_operation ||
       std::any_of(impl_->borrowers.begin(), impl_->borrowers.end(), [&](const auto& item) {
           return item.second.operation == operation;
       })) { error = "No registered managed graph permit"; return -1; }
    const int duplicate = fcntl(impl_->fd, F_DUPFD_CLOEXEC, 3);
    if(duplicate < 0) { error = "Cannot duplicate registered managed graph FD"; return -1; }
    found->second.operation = operation;
    return duplicate;
}

bool ManagedHdmiSession::borrower_drained(const std::string& id, const std::string& operation,
                                        const std::string& proof, std::string& error) {
    error.clear();
    const auto found = impl_->borrowers.find(id);
    if((state() != ManagedHdmiState::Owned && state() != ManagedHdmiState::Sealed) || !managed_id(proof) ||
       found == impl_->borrowers.end() || found->second.operation.empty() || found->second.operation != operation ||
       (!found->second.proof.empty() && found->second.proof != proof)) {
        error = "Unverified managed graph completion"; return false;
    }
    found->second.proof = proof;
    return true;
}

bool ManagedHdmiSession::seal(std::string& error) {
    error.clear();
    if(state() == ManagedHdmiState::Sealed) return true;
    if(state() != ManagedHdmiState::Owned) { error = "Managed HDMI is not owned"; return false; }
    impl_->state = ManagedHdmiState::Sealed;
    return true;
}

bool ManagedHdmiSession::release(const std::string& operation, std::string& error) {
    error.clear();
    if(state() == ManagedHdmiState::Released && operation == impl_->cleanup_operation) return true;
    if(state() != ManagedHdmiState::Sealed || !managed_id(operation) || operation == impl_->acquire_operation ||
       std::any_of(impl_->borrowers.begin(), impl_->borrowers.end(), [&](const auto& value) {
           return value.second.operation == operation;
       }) ||
       std::any_of(impl_->borrowers.begin(), impl_->borrowers.end(), [](const auto& value) {
           return value.second.operation.empty() || value.second.proof.empty();
       })) { error = "Managed HDMI still has an unsealed or undrained graph"; return false; }
    impl_->cleanup_operation = operation;
    impl_->state = ManagedHdmiState::Releasing;
    int fence_fd = -1;
    drmModeAtomicReq* request = nullptr;
    try {
        impl_->identity();
        impl_->routes(false);
        if(!impl_->terminal) throw std::runtime_error("No qualified terminal KMS completion hook");
        request = drmModeAtomicAlloc();
        if(!request) throw std::runtime_error("Cannot allocate managed KMS transaction");
        auto add = [&](std::uint32_t object, std::uint32_t type, const char* name, std::uint64_t value) {
            const auto property = managed_property(impl_->fd, object, type, name);
            if(drmModeAtomicAddProperty(request, object, property.id, value) < 0)
                throw std::runtime_error("Cannot prepare managed KMS neutralization");
        };
        for(const auto plane : impl_->scope.planes) {
            add(plane, DRM_MODE_OBJECT_PLANE, "FB_ID", 0);
            add(plane, DRM_MODE_OBJECT_PLANE, "CRTC_ID", 0);
        }
        add(impl_->scope.connector, DRM_MODE_OBJECT_CONNECTOR, "CRTC_ID", 0);
        add(impl_->scope.crtc, DRM_MODE_OBJECT_CRTC, "ACTIVE", 0);
        add(impl_->scope.crtc, DRM_MODE_OBJECT_CRTC, "MODE_ID", 0);
        add(impl_->scope.crtc, DRM_MODE_OBJECT_CRTC, "OUT_FENCE_PTR", reinterpret_cast<std::uintptr_t>(&fence_fd));
        if(drmModeAtomicCommit(impl_->fd, request, DRM_MODE_ATOMIC_TEST_ONLY | DRM_MODE_ATOMIC_ALLOW_MODESET, nullptr) != 0)
            throw std::runtime_error("Managed KMS neutral transaction rejected");
        if(fence_fd != -1) throw std::runtime_error("Unexpected test-only KMS fence");
        if(drmModeAtomicCommit(impl_->fd, request, DRM_MODE_ATOMIC_NONBLOCK | DRM_MODE_ATOMIC_ALLOW_MODESET, nullptr) != 0)
            throw std::runtime_error("Managed KMS neutral commit failed");
        ++impl_->sequence;
        drmModeAtomicFree(request);
        request = nullptr;
        if(fence_fd <= 2 || fcntl(fence_fd, F_SETFD, FD_CLOEXEC) < 0)
            throw std::runtime_error("Managed KMS completion fence unavailable");
        pollfd wait{fence_fd, POLLIN, 0};
        int result;
        do { result = poll(&wait, 1, -1); } while(result < 0 && errno == EINTR);
        if(result != 1 || !(wait.revents & POLLIN) || (wait.revents & (POLLERR | POLLHUP | POLLNVAL)))
            throw std::runtime_error("Managed KMS fence failed");
        sync_file_info info{};
        if(ioctl(fence_fd, SYNC_IOC_FILE_INFO, &info) != 0 || info.status != 1)
            throw std::runtime_error("Managed KMS fence did not confirm successful completion");
        impl_->fence = true;
        close(fence_fd); fence_fd = -1;
        impl_->identity();
        impl_->routes(true);
        impl_->neutral = true;
        if(drmDropMaster(impl_->fd) != 0) throw std::runtime_error("Managed DRM master release failed");
        impl_->close_local();
        impl_->terminal_proof = impl_->terminal(impl_->binding, impl_->scope);
        if(!managed_id(impl_->terminal_proof)) throw std::runtime_error("Terminal KMS backend completion remains unknown");
        impl_->state = ManagedHdmiState::Released;
        return true;
    } catch(const std::exception& failure) {
        if(request) drmModeAtomicFree(request);
        if(fence_fd >= 0) close(fence_fd);
        impl_->state = ManagedHdmiState::Unknown;
        error = failure.what();
        return false;
    }
}

void ManagedHdmiSession::abandon() noexcept {
    if(!impl_) return;
    if(state() != ManagedHdmiState::Released) impl_->state = ManagedHdmiState::Unknown;
    impl_->close_local();
}

ManagedHdmiState ManagedHdmiSession::state() const noexcept { return impl_->state.load(); }

std::string ManagedHdmiSession::evidence_json() const {
    if(state() != ManagedHdmiState::Released) throw std::runtime_error("Managed HDMI has no terminal evidence");
    std::ostringstream planes;
    for(std::size_t i = 0; i < impl_->scope.planes.size(); ++i) { if(i) planes << ','; planes << impl_->scope.planes[i]; }
    std::ostringstream result;
    result << "{\"kind\":\"kms-v2\",\"epoch\":" << impl_->binding.epoch
           << ",\"resource\":" << json_string(impl_->binding.resource) << ",\"generation\":" << json_string(impl_->binding.generation)
           << ",\"connector\":" << impl_->scope.connector << ",\"crtc\":" << impl_->scope.crtc
           << ",\"planes\":" << json_string(planes.str()) << ",\"device\":" << impl_->device
           << ",\"inode\":" << impl_->inode << ",\"rdev\":" << impl_->rdev
           << ",\"acquire_operation\":" << json_string(impl_->acquire_operation)
           << ",\"cleanup_operation\":" << json_string(impl_->cleanup_operation)
           << ",\"borrowers\":0,\"atomic_sequence\":" << impl_->sequence
           << ",\"fence\":true,\"neutral\":true,\"terminal_proof\":" << json_string(impl_->terminal_proof)
           << ",\"future_writes_suppressed\":true}";
    return result.str();
}

std::string ManagedHdmiSession::identity_json() const {
    std::ostringstream planes;
    for(std::size_t i = 0; i < impl_->scope.planes.size(); ++i) { if(i) planes << ','; planes << impl_->scope.planes[i]; }
    std::ostringstream result;
    result << "{\"kind\":\"kms-v2\",\"epoch\":" << impl_->binding.epoch
           << ",\"resource\":" << json_string(impl_->binding.resource) << ",\"generation\":" << json_string(impl_->binding.generation)
           << ",\"connector\":" << impl_->scope.connector << ",\"crtc\":" << impl_->scope.crtc
           << ",\"planes\":" << json_string(planes.str()) << ",\"device\":" << impl_->device
           << ",\"inode\":" << impl_->inode << ",\"rdev\":" << impl_->rdev << '}';
    return result.str();
}

std::string ManagedHdmiSession::acquired_json() const {
    if(state() != ManagedHdmiState::Owned && state() != ManagedHdmiState::Sealed)
        throw std::runtime_error("Managed HDMI has no verified acquisition");
    auto identity = identity_json();
    identity.pop_back();
    return identity + ",\"acquire_operation\":" + json_string(impl_->acquire_operation) + ",\"verified\":true}";
}

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
std::vector<std::string> HdmiSession::video_formats() const { return {}; }
DisplayMode HdmiSession::mode() const { return {}; }
std::string HdmiSession::device() const { return {}; }
struct ManagedHdmiSession::Impl { std::atomic<ManagedHdmiState> state{ManagedHdmiState::Unknown}; };
ManagedHdmiSession::ManagedHdmiSession(int, ManagedHdmiScope, ManagedHdmiBinding, CompletionHook) {
    throw std::runtime_error("Managed HDMI requires Linux DRM");
}
ManagedHdmiSession::~ManagedHdmiSession() = default;
bool ManagedHdmiSession::acquire(const std::string&, std::string& error) { error = "Managed HDMI requires Linux DRM"; return false; }
bool ManagedHdmiSession::register_borrower(const std::string&, std::string& error) { error = "Managed HDMI requires Linux DRM"; return false; }
int ManagedHdmiSession::borrow_fd(const std::string&, const std::string&, std::string& error) { error = "Managed HDMI requires Linux DRM"; return -1; }
bool ManagedHdmiSession::borrower_drained(const std::string&, const std::string&, const std::string&, std::string& error) { error = "Managed HDMI requires Linux DRM"; return false; }
bool ManagedHdmiSession::seal(std::string& error) { error = "Managed HDMI requires Linux DRM"; return false; }
bool ManagedHdmiSession::release(const std::string&, std::string& error) { error = "Managed HDMI requires Linux DRM"; return false; }
void ManagedHdmiSession::abandon() noexcept {}
ManagedHdmiState ManagedHdmiSession::state() const noexcept { return ManagedHdmiState::Unknown; }
std::string ManagedHdmiSession::identity_json() const { throw std::runtime_error("Managed HDMI requires Linux DRM"); }
std::string ManagedHdmiSession::acquired_json() const { throw std::runtime_error("Managed HDMI requires Linux DRM"); }
std::string ManagedHdmiSession::evidence_json() const { throw std::runtime_error("Managed HDMI requires Linux DRM"); }
} // namespace tyrell::video
#endif
