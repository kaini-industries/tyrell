#include "outputs.h"

#include <iostream>
#include <stdexcept>

using namespace tyrell::video;

static void require(bool value, const char* message) {
    if(!value) throw std::runtime_error(message);
}

int main() {
    try {
        auto mode = choose_hdmi_mode({{1920,1080,60,true,false}, {1280,720,60,false,false}, {640,480,60,false,false}});
        require(mode && mode->width == 1280 && mode->height == 720, "Prefer 720p over a native 1080p mode");
        mode = choose_hdmi_mode({{1920,1080,30,true,true}, {1920,1080,60,false,false}, {640,480,60,false,false}});
        require(mode && mode->width == 640, "Reject dimensions whose first EDID match is interlaced");
        mode = choose_hdmi_mode({{1280,720,50,true,false}, {1280,720,60,false,false}});
        require(mode && mode->refresh == 50, "Describe the actual first dimension match, not an unused later refresh");
        mode = choose_hdmi_mode({{1920,1080,30,true,false}});
        require(mode && mode->height == 1080, "Allow progressive 1080p when no smaller mode exists");
        require(!choose_hdmi_mode({{3840,2160,60,true,false}, {1920,1080,30,false,true}}), "Reject 4K and interlaced-only outputs");
        require(!choose_hdmi_mode({{0,0,60,false,false}}), "Reject invalid dimensions");
        std::cout << "PASS: HDMI mode selection follows kmssink dimension matching\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
