#pragma once

#include "playback.h"
#include "lvgl.h"
#include "src/misc/cache/instance/lv_image_cache.h"

#include <limits>
#include <memory>
#include <utility>

namespace tyrell::video {

// UI-thread-only bridge. The retained sample owns every byte referenced by
// LVGL, including while a newly assigned source waits for the next refresh.
// clear() must run before deleting the widget or deinitializing LVGL.
class FrameImage {
public:
    bool show(lv_obj_t* widget, std::shared_ptr<const Frame> next) {
        if(!widget || !valid(next) || next == frame_) return false;
        // The descriptor's address is stable, so a stride-conversion decoder
        // could otherwise reuse pixels cached for the previous frame.
        lv_image_cache_drop(&description_);
        auto previous = std::move(frame_);
        frame_ = std::move(next);
        description_ = {};
        description_.header.magic = LV_IMAGE_HEADER_MAGIC;
        description_.header.cf = LV_COLOR_FORMAT_RGB565;
        description_.header.w = frame_->width;
        description_.header.h = frame_->height;
        description_.header.stride = frame_->stride;
        description_.data_size = static_cast<std::uint32_t>(frame_->pixels.size());
        description_.data = frame_->pixels.data();
        lv_image_set_src(widget, &description_);
        lv_obj_invalidate(widget);
        // `previous` stays alive until the image no longer references it.
        return true;
    }

    void clear(lv_obj_t* widget) {
        if(widget) lv_image_set_src(widget, nullptr);
        lv_image_cache_drop(&description_);
        frame_.reset();
        description_ = {};
    }

private:
    static bool valid(const std::shared_ptr<const Frame>& frame) {
        if(!frame || frame->width <= 0 || frame->width > 320 || frame->height <= 0 || frame->height > 170) return false;
        if(frame->stride < frame->width * 2 || frame->stride > 65535) return false;
        return frame->pixels.size() >= static_cast<std::size_t>(frame->stride) * frame->height &&
               frame->pixels.size() <= std::numeric_limits<std::uint32_t>::max();
    }
    std::shared_ptr<const Frame> frame_;
    lv_image_dsc_t description_{};
};

} // namespace tyrell::video
