#include "outputs.h"

#include <iostream>
#include <stdexcept>

using namespace tyrell::video;

static void require(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

int main() {
    try {
        const AudioSink onboard{"onboard", "ES8389-Audio", false, true, true};
        const AudioSink hdmi{"hdmi", "HDMI monitor", true, false, true};
        const AudioSink usb{"usb", "USB headset", false, false, true};

        auto route = choose_audio_route({onboard, hdmi, usb}, false, "hdmi");
        require(route.sink_name == "hdmi" && !route.reset_previous_selection,
                "An available explicit HDMI audio route must survive a video switch to LCD");
        route = choose_audio_route({onboard, usb}, false, "hdmi");
        require(route.sink_name == "onboard" && route.reset_previous_selection,
                "HDMI disappearance must recover LCD playback through onboard audio");
        auto disconnected_hdmi = hdmi;
        disconnected_hdmi.available = false;
        route = choose_audio_route({onboard, disconnected_hdmi}, false, "hdmi");
        require(route.sink_name == "onboard" && route.reset_previous_selection,
                "A retained but unavailable HDMI sink must also recover to onboard audio");
        route = choose_audio_route({onboard, hdmi, usb}, true, "usb");
        require(route.sink_name == "usb" && !route.reset_previous_selection,
                "A valid explicit headset route must override automatic HDMI audio");
        route = choose_audio_route({onboard, hdmi}, true, "usb");
        require(route.sink_name == "hdmi" && route.reset_previous_selection,
                "A disconnected headset must recover through the available automatic HDMI route");
        route = choose_audio_route({onboard}, true, {});
        require(route.sink_name == "onboard" && route.hdmi_fallback && !route.reset_previous_selection,
                "Automatic HDMI audio must fall back to onboard without inventing an explicit preference");
        route = choose_audio_route({}, false, "hdmi");
        require(route.sink_name.empty() && route.reset_previous_selection,
                "An empty inventory must release the stale preference and use the session default");
        std::cout << "PASS: disconnected explicit audio outputs recover automatically; available preferences remain selected\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "Audio route test: " << error.what() << '\n';
        return 1;
    }
}
