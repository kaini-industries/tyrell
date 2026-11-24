#pragma once

#include <array>
#include <cstddef>

namespace tyrell::status {
// Every command in Status is a press action. A held key, including one held
// while another app owned the seat, must be released before it can act again.
class InputEdges {
public:
    static constexpr std::size_t keys = 768;
    using State = std::array<bool, keys>;
    void reset(const State& held = {}) { down_ = held; dropped_ = false; }
    void dropped() { dropped_ = true; }
    bool needs_sync() const { return dropped_; }
    bool press(unsigned code, int value) {
        if(dropped_ || code >= keys) return false;
        if(value == 0) { down_[code] = false; return false; }
        if(value != 1 || down_[code]) return false;
        down_[code] = true;
        return true;
    }
private:
    State down_{};
    bool dropped_ = false;
};
} // namespace tyrell::status
