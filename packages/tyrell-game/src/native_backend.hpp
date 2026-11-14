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
                  NativeInputState& state);
    ~NativeDevices();
    NativeDevices(const NativeDevices&)=delete;
    NativeDevices& operator=(const NativeDevices&)=delete;
    void poll(NativeInputState& state);
    void present(const std::vector<std::uint16_t>& rgb565);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace tyrell::game::detail
