#pragma once

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace tyrell::video {

struct DisplayMode {
    int width = 0;
    int height = 0;
    int refresh = 0;
    bool preferred = false;
    bool interlaced = false;
};

// Retain the conservative first-mode-per-dimensions EDID policy. Prefer
// <=720p; accept <=1080p only when no smaller progressive mode is available.
std::optional<DisplayMode> choose_hdmi_mode(const std::vector<DisplayMode>& modes);

struct HdmiConnector {
    std::string device;
    std::uint32_t connector_id = 0;
    std::string name;
    bool connected = false;
    std::vector<DisplayMode> modes;
};

struct AudioSink {
    std::string name;
    std::string description;
    bool hdmi = false;
    bool onboard = false;
    bool available = true;
};

struct AudioRoute {
    std::string sink_name; // Empty asks PulseAudio for its current default.
    bool reset_previous_selection = false;
    bool hdmi_fallback = false;
};

// Recreate a stream with a previously validated preference. A sink that has
// disappeared must not prevent recovery to the available onboard/system output.
AudioRoute choose_audio_route(const std::vector<AudioSink>& sinks, bool prefer_hdmi,
                             const std::string& previous_selection);

struct OutputInventory {
    std::vector<HdmiConnector> hdmi;
    std::vector<AudioSink> audio;
    std::string audio_error;
    std::string display_error;
};

// Read-only enumeration; never changes a system-wide display or audio default.
OutputInventory inspect_outputs(bool include_audio = true);
std::string output_inventory_json(const OutputInventory& inventory);

class HdmiSession {
public:
    HdmiSession();
    ~HdmiSession();
    HdmiSession(const HdmiSession&) = delete;
    HdmiSession& operator=(const HdmiSession&) = delete;
    // Prepare a bounded display mode and an unused NV12 overlay under DRM
    // master. Stop every sink borrowing fd() before release() restores it.
    bool acquire(std::string& error);
    void release();
    bool connected() const;
    int fd() const;
    std::uint32_t connector_id() const;
    std::uint32_t plane_id() const;
    DisplayMode mode() const;
    std::string device() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace tyrell::video
