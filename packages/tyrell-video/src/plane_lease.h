#pragma once

#include <cstdint>
#include <vector>

namespace tyrell::video {

enum class DisplayPlaneKind { Primary, Overlay, Cursor, Unknown };

// A plane has independent scanout state: replacing a CRTC's primary framebuffer
// does not remove its cursor or overlays. Preserve the complete rectangles, not
// just the framebuffer ID, so an off-screen cursor can be restored correctly.
struct DisplayPlaneState {
    std::uint32_t id = 0, crtc = 0, framebuffer = 0;
    DisplayPlaneKind kind = DisplayPlaneKind::Unknown;
    std::int32_t x = 0, y = 0;
    std::uint32_t width = 0, height = 0;
    std::uint32_t source_x = 0, source_y = 0, source_width = 0, source_height = 0;
};

// Only the chosen HDMI CRTC is leased. The primary is covered by the saved CRTC
// and our black background; unrelated displays and inactive planes stay alone.
// Retaining each successful mutation makes partial acquisition reversible.
class DisplayPlaneLease {
public:
    DisplayPlaneLease() = default;
    DisplayPlaneLease(const DisplayPlaneLease&) = delete;
    DisplayPlaneLease& operator=(const DisplayPlaneLease&) = delete;

    template<class Disable>
    bool hide(const std::vector<DisplayPlaneState>& planes, std::uint32_t crtc,
              std::uint32_t owned_plane, Disable disable) {
        if(!crtc || !owned_plane || !saved_.empty()) return false;
        for(const auto& plane : planes) {
            if(plane.id == owned_plane || plane.crtc != crtc || !plane.framebuffer ||
               plane.kind == DisplayPlaneKind::Primary) continue;
            if(plane.kind == DisplayPlaneKind::Unknown || !plane.width || !plane.height ||
               !plane.source_width || !plane.source_height) return false;
        }
        for(const auto& plane : planes) {
            if(plane.id == owned_plane || plane.crtc != crtc || !plane.framebuffer ||
               plane.kind == DisplayPlaneKind::Primary) continue;
            if(!disable(plane.id)) return false;
            saved_.push_back(plane);
        }
        return true;
    }

    template<class Restore>
    bool release(Restore restore) {
        bool ok = true;
        for(auto it = saved_.rbegin(); it != saved_.rend(); ++it)
            if(!restore(*it)) ok = false;
        saved_.clear();
        return ok;
    }

private:
    std::vector<DisplayPlaneState> saved_;
};

} // namespace tyrell::video
