#pragma once

#include "tyrell/game/input.hpp"
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace tyrell::game::detail {

// Private CardputerZero hardware bridge; the public Runtime owns its lifetime.
class NativeDevices {
public:
    NativeDevices(const std::string& framebuffer, const std::string& input,
                  NativeInputState& state,bool restore_framebuffer_on_close=true);
    ~NativeDevices();
    NativeDevices(const NativeDevices&)=delete;
    NativeDevices& operator=(const NativeDevices&)=delete;
    void poll(NativeInputState& state);
    void present(const std::vector<std::uint16_t>& rgb565);
    void suspend(NativeInputState& state) noexcept;
    bool resume(NativeInputState& state,std::string* error);
    void abandon(NativeInputState& state) noexcept;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace tyrell::game::detail
