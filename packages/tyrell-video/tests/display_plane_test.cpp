#include "plane_lease.h"

#include <iostream>
#include <stdexcept>
#include <vector>

using namespace tyrell::video;

namespace {
void require(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

DisplayPlaneState state(std::uint32_t id, std::uint32_t crtc, std::uint32_t framebuffer,
                        DisplayPlaneKind kind) {
    return {id, crtc, framebuffer, kind, -12, 98, 64, 64, 12u << 16, 0, 52u << 16, 64u << 16};
}

bool equal(const DisplayPlaneState& a, const DisplayPlaneState& b) {
    return a.id == b.id && a.crtc == b.crtc && a.framebuffer == b.framebuffer && a.kind == b.kind &&
        a.x == b.x && a.y == b.y && a.width == b.width && a.height == b.height &&
        a.source_x == b.source_x && a.source_y == b.source_y &&
        a.source_width == b.source_width && a.source_height == b.source_height;
}
} // namespace

int main() {
    try {
        const auto primary = state(1, 7, 100, DisplayPlaneKind::Primary);
        const auto cursor = state(2, 7, 101, DisplayPlaneKind::Cursor);
        const auto overlay = state(3, 7, 102, DisplayPlaneKind::Overlay);
        const auto other_display = state(4, 8, 103, DisplayPlaneKind::Cursor);
        const auto inactive = state(5, 0, 0, DisplayPlaneKind::Overlay);
        const auto owned = state(6, 7, 104, DisplayPlaneKind::Overlay);
        const std::vector<DisplayPlaneState> planes{primary, cursor, overlay, other_display, inactive, owned};
        DisplayPlaneLease lease;
        std::vector<std::uint32_t> hidden;
        auto hide = [&](std::uint32_t id) { hidden.push_back(id); return true; };
        require(lease.hide(planes, 7, owned.id, hide), "Cannot hide foreign HDMI cursor/overlay");
        require(hidden == std::vector<std::uint32_t>{cursor.id, overlay.id},
                "Handoff touched a primary, owned, inactive or unrelated-display plane");
        require(!lease.hide(planes, 7, owned.id, hide), "A second handoff replaced the original rollback state");
        std::vector<DisplayPlaneState> restored;
        require(lease.release([&](const DisplayPlaneState& p) { restored.push_back(p); return true; }),
                "Could not restore hidden planes");
        require(restored.size() == 2 && equal(restored[0], overlay) && equal(restored[1], cursor),
                "Restoration lost framebuffer, negative cursor coordinates or fixed-point source crop");

        hidden.clear(); restored.clear();
        require(!lease.hide(planes, 7, owned.id, [&](std::uint32_t id) {
            hidden.push_back(id); return id != overlay.id;
        }), "A failed second plane mutation was reported as successful");
        require(lease.release([&](const DisplayPlaneState& p) { restored.push_back(p); return true; }),
                "A partial handoff failed to roll back");
        require(restored.size() == 1 && equal(restored[0], cursor),
                "Partial rollback restored an unmodified plane or lost the first successful mutation");

        auto incomplete = cursor;
        incomplete.source_width = 0;
        hidden.clear();
        require(!lease.hide({overlay, incomplete}, 7, owned.id, hide) && hidden.empty(),
                "Incomplete rollback geometry was discovered only after changing another plane");
        incomplete = cursor;
        incomplete.kind = DisplayPlaneKind::Unknown;
        require(!lease.hide({overlay, incomplete}, 7, owned.id, hide) && hidden.empty(),
                "Unknown plane type was hidden without a complete rollback plan");
        require(!lease.hide(planes, 0, owned.id, hide) && !lease.hide(planes, 7, 0, hide) && hidden.empty(),
                "Invalid HDMI ownership identifiers changed display state");

        require(lease.hide(planes, 7, owned.id, hide), "A released plane lease cannot be reused");
        restored.clear();
        require(!lease.release([&](const DisplayPlaneState& p) {
            restored.push_back(p); return p.id != overlay.id;
        }), "A failed framebuffer restoration was hidden");
        require(restored.size() == 2, "A failed restoration prevented remaining planes from being restored");
        restored.clear();
        require(lease.release([&](const DisplayPlaneState& p) { restored.push_back(p); return true; }) && restored.empty(),
                "A completed rollback was replayed on an unrelated later display session");

        std::cout << "PASS: scoped cursor/overlay handoff, exact rectangles, preflight, partial rollback and cleanup failures\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "HDMI plane test: " << error.what() << '\n';
        return 1;
    }
}
