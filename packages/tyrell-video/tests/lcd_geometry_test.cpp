#include "playback.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

using namespace tyrell::video;
using Clock = std::chrono::steady_clock;

namespace {
void require(bool condition, const std::string& message) {
    if(!condition) throw std::runtime_error(message);
}

struct Pixel {
    unsigned red, green, blue;
    bool white() const { return red >= 28 && green >= 57 && blue >= 28; }
    bool black() const { return red <= 2 && green <= 4 && blue <= 2; }
};

Pixel pixel(const Frame& frame, int x, int y) {
    std::uint16_t value = 0;
    std::memcpy(&value, frame.pixels.data() + y * frame.stride + x * 2, sizeof(value));
    return Pixel{(value >> 11) & 31u, (value >> 5) & 63u, value & 31u};
}

struct Captions {
    std::filesystem::path directory = std::filesystem::temp_directory_path() /
        ("tyrell-lcd-geometry-" + std::to_string(Clock::now().time_since_epoch().count()));
    std::filesystem::path path = directory / "captions.srt";
    Captions() {
        require(std::filesystem::create_directory(directory), "Cannot create caption test directory");
        std::ofstream out(path);
        out << "1\n00:00:00,000 --> 00:01:00,000\nTyrell captions\n\n";
        out.close();
        if(!out) {
            std::error_code error;
            std::filesystem::remove_all(directory, error);
            throw std::runtime_error("Cannot write caption fixture");
        }
    }
    ~Captions() {
        std::error_code error;
        std::filesystem::remove_all(directory, error);
    }
};

std::shared_ptr<const Frame> wait_frame(Playback& player,
        const std::function<bool(const Frame&)>& ready, const std::string& failure) {
    const auto deadline = Clock::now() + std::chrono::seconds(12);
    while(Clock::now() < deadline) {
        player.poll();
        require(player.status().error.empty(), failure + ": " + player.status().error);
        const auto frame = player.latest_frame();
        if(frame && ready(*frame)) return frame;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    throw std::runtime_error(failure);
}

void check(Playback& player, const char* path, const std::string& captions,
           int expected_width, int expected_height,
           const std::string& description) {
    require(player.load(path, captions), description + ": load failed: " + player.status().error);
    auto frame = wait_frame(player, [&](const Frame&) {
        const auto status = player.status();
        return !status.buffering && status.duration > 0 && !status.subtitle_tracks.empty();
    }, description + ": no decoded frame or external SRT track");
    require(player.status().subtitle_track == -1, description + ": captions did not start disabled");
    const auto initial_serial = frame->serial;
    require(player.pause() && player.seek(0.4), description + ": cannot pause and seek");
    frame = wait_frame(player, [&](const Frame& candidate) {
        return candidate.serial > initial_serial && std::abs(candidate.position - 0.4) < 0.03;
    }, description + ": paused seek did not produce its target frame");
    require(frame->width == 320 && frame->height == 170 && frame->stride == 640 &&
            frame->pixels.size() == 320u * 170u * 2u,
            description + ": invalid RGB565 frame dimensions or storage");

    int left = frame->width, right = -1, top = frame->height, bottom = -1;
    for(int y = 0; y < frame->height; ++y) {
        for(int x = 0; x < frame->width; ++x) {
            if(pixel(*frame, x, y).white()) {
                left = std::min(left, x); right = std::max(right, x);
                top = std::min(top, y); bottom = std::max(bottom, y);
            }
        }
    }
    require(right >= left && bottom >= top, description + ": white picture disappeared");
    const int width = right - left + 1, height = bottom - top + 1;
    const std::string geometry = std::to_string(width) + "x" + std::to_string(height) +
        " at " + std::to_string(left) + "," + std::to_string(top);
    // Permit a two-pixel edge difference from chroma alignment and rounding.
    require(std::abs(width - expected_width) <= 2 && std::abs(height - expected_height) <= 2,
            description + ": incorrect aspect fit: " + geometry);
    require(std::abs(left - (320 - width) / 2) <= 1 && std::abs(top - (170 - height) / 2) <= 1,
            description + ": picture is not centered: " + geometry);
    for(int y = 0; y < frame->height; ++y) {
        for(int x = 0; x < frame->width; ++x) {
            const bool interior = x >= left + 2 && x <= right - 2 && y >= top + 2 && y <= bottom - 2;
            const bool border = x < left - 2 || x > right + 2 || y < top - 2 || y > bottom + 2;
            if(interior) require(pixel(*frame, x, y).white(), description + ": nonwhite picture interior");
            if(border) require(pixel(*frame, x, y).black(), description + ": nonblack letterbox border");
        }
    }

    const auto plain = frame;
    require(player.set_subtitle_track(0), description + ": cannot enable external SRT");
    const auto captioned = wait_frame(player, [&](const Frame& candidate) {
        return candidate.serial > plain->serial && std::abs(candidate.position - 0.4) < 0.03 &&
               candidate.pixels != plain->pixels && player.status().lcd_caption_compositions > 0;
    }, description + ": enabling captions did not redraw the paused target");
    require(captioned->width == plain->width && captioned->height == plain->height &&
            captioned->stride == plain->stride && captioned->pixels.size() == plain->pixels.size(),
            description + ": caption rendering changed the frame layout");
    int caption_left = 320, caption_right = -1, caption_top = 170, caption_bottom = -1;
    unsigned changed = 0;
    for(int y = 0; y < frame->height; ++y) {
        for(int x = 0; x < frame->width; ++x) {
            const auto offset = y * frame->stride + x * 2;
            if(captioned->pixels[offset] != plain->pixels[offset] ||
               captioned->pixels[offset + 1] != plain->pixels[offset + 1]) {
                ++changed;
                caption_left = std::min(caption_left, x); caption_right = std::max(caption_right, x);
                caption_top = std::min(caption_top, y); caption_bottom = std::max(caption_bottom, y);
                require(x >= left && x <= right && y >= top && y <= bottom,
                        description + ": captions painted into the letterbox border");
            }
        }
    }
    require(changed > 40, description + ": captions have too few visible glyph/outline pixels");
    const int caption_height = caption_bottom - caption_top + 1;
    require(caption_height >= 10 && caption_height <= 35,
            description + ": caption height is outside readable LCD bounds: " + std::to_string(caption_height));
    require(caption_top >= top + height / 2,
            description + ": captions are not in the lower part of the fitted picture");
    require(caption_left < caption_right,
            description + ": captions have no horizontal extent");
    require(std::abs((caption_left + caption_right) - (left + right)) <= 8,
            description + ": captions are not centered in the fitted picture");
    require(player.set_subtitle_track(-1), description + ": cannot disable captions");
    wait_frame(player, [&](const Frame& candidate) {
        return candidate.serial > captioned->serial && std::abs(candidate.position - 0.4) < 0.03 &&
               candidate.pixels == plain->pixels;
    }, description + ": disabling captions did not restore the exact paused picture");
    player.stop();
    std::cout << description << ": " << geometry << ", caption height " << caption_height << '\n';
}
} // namespace

int main(int argc, char** argv) {
    try {
        require(argc == 4, "Pass white wide, anamorphic and small video fixtures");
        PlaybackOptions options;
        options.fake_audio = true;
        options.desktop_handoff = false;
        options.automatic_hdmi_audio = false;
        Playback player(options);
        Captions captions;
        check(player, argv[1], captions.path.string(), 320, 136, "Wide square-pixel video");
        check(player, argv[2], captions.path.string(), 227, 170, "Anamorphic 4:3 video");
        check(player, argv[3], captions.path.string(), 302, 170, "Small square-pixel video");
        std::cout << "PASS: LCD aspect fit, centering, black bars, RGB565 pixels and caption placement/on/off\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "LCD geometry test: " << error.what() << '\n';
        return 1;
    }
}
