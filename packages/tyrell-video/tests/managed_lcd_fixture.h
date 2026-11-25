#pragma once

#include "managed_lcd.h"

#include <atomic>
#include <cstring>
#include <functional>
#include <map>
#include <mutex>
#include <vector>

namespace tyrell::video::test {

// Explicit disposable backend: real LVGL partial flushes copy into this memory
// framebuffer. Completion gates model a separately registered driver operation;
// they are not claims about the physical panel or generic fbdev fsync.
class ControlledLcdBackend final : public ManagedLcdBackend {
public:
    explicit ControlledLcdBackend(ManagedLcdScope binding) : binding_(std::move(binding)), pixels(320 * 170) {}
    std::atomic<bool> allow_complete{false}, allow_finality{false}, fail{false};
    std::atomic<unsigned> writes{0}, starts{0}, ends{0}, close_calls{0};
    std::function<void()> before_write;
    std::vector<std::uint16_t> pixels;

    ManagedLcdScope scope() const override { return binding_; }
    bool supports_finality() const noexcept override { return !closed_.load(); }
    bool begin_frame(const std::string& operation, std::string& error) override {
        std::lock_guard<std::mutex> guard(mutex_);
        if(closed_ || sealed_ || fail || frames_.count(operation)) { error = "Fixture frame admission failed"; return false; }
        frames_[operation] = false; ++starts; return true;
    }
    bool write_rect(const std::string& operation, const lv_area_t& area,
                    const std::uint8_t* bytes, std::string& error) override {
        {
            std::lock_guard<std::mutex> guard(mutex_);
            if(closed_ || fail || !frames_.count(operation) || frames_.at(operation)) { error = "Fixture write failed"; return false; }
        }
        ++writes;
        if(before_write) before_write();
        const auto width = lv_area_get_width(&area);
        const auto stride = lv_draw_buf_width_to_stride(width, LV_COLOR_FORMAT_RGB565);
        for(int y = area.y1; y <= area.y2; ++y) {
            std::memcpy(pixels.data() + y * 320 + area.x1, bytes, static_cast<std::size_t>(width) * 2);
            bytes += stride;
        }
        return true;
    }
    bool end_frame(const std::string& operation, std::string& error) override {
        std::lock_guard<std::mutex> guard(mutex_);
        if(closed_ || fail || !frames_.count(operation)) { error = "Fixture frame end failed"; return false; }
        frames_[operation] = true; ++ends; return true;
    }
    LcdCompletion poll_frame(const std::string& operation) override {
        std::lock_guard<std::mutex> guard(mutex_);
        if(closed_ || fail || !frames_.count(operation) || !frames_.at(operation))
            return {LcdCompletionState::Unknown, {}, "Fixture completion unknown"};
        return allow_complete ? LcdCompletion{LcdCompletionState::Complete, operation, {}} : LcdCompletion{};
    }
    bool seal(std::string& error) override {
        std::lock_guard<std::mutex> guard(mutex_);
        if(closed_ || fail) { error = "Fixture seal failed"; return false; }
        sealed_ = true; return true;
    }
    LcdCompletion poll_finality() override {
        std::lock_guard<std::mutex> guard(mutex_);
        if(closed_ || fail) return {LcdCompletionState::Unknown, {}, "Fixture finality unknown"};
        for(const auto& [operation, ended] : frames_) { (void)operation; if(!ended) return {}; }
        return sealed_ && allow_complete && allow_finality
            ? LcdCompletion{LcdCompletionState::Complete, binding_.generation, {}} : LcdCompletion{};
    }
    void close() noexcept override { closed_ = true; ++close_calls; }

private:
    ManagedLcdScope binding_;
    mutable std::mutex mutex_;
    std::map<std::string, bool> frames_;
    std::atomic<bool> closed_{false};
    bool sealed_ = false;
};

} // namespace tyrell::video::test
