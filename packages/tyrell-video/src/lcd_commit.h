#pragma once

#include "lvgl.h"

#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <string>

#include <fcntl.h>
#include <unistd.h>

namespace tyrell::video {

struct FramebufferCommitStats {
    // These count completed LVGL redraws and kernel commit requests, not
    // physical panel refreshes. A redraw can contain many partial flushes.
    std::uint64_t completed_frames = 0;
    std::uint64_t fsync_calls = 0;
    double fsync_total_seconds = 0;
    double fsync_max_seconds = 0;
    std::string error;
};

// Native fbdev only. LVGL's mmap copies dirty memory but does not itself wait
// for the kernel's deferred framebuffer update. Commit once, after the final
// partial flush of a redraw, rather than committing each draw-buffer strip.
// All methods and callbacks run on the LVGL/UI thread.
// This legacy cadence helper does not certify managed resource release. The
// separate ManagedLcd path gates writes before submission and retains driver
// work until qualified completion; fsync alone is not that completion.
class FramebufferCommit {
public:
    using Stats = FramebufferCommitStats;
    FramebufferCommit() = default;
    ~FramebufferCommit() { detach(); }
    FramebufferCommit(const FramebufferCommit&) = delete;
    FramebufferCommit& operator=(const FramebufferCommit&) = delete;

    bool attach(lv_display_t* display, const std::string& path) {
        detach();
        stats_ = {};
        if(!display) {
            stats_.error = "Cannot attach framebuffer commits without a display";
            return false;
        }
        display_ = display;
        fd_ = open(path.c_str(), O_RDWR | O_CLOEXEC);
        if(fd_ < 0) record_error("Cannot open framebuffer for commits", errno);
        lv_display_add_event_cb(display_, event, LV_EVENT_FLUSH_FINISH, this);
        lv_display_add_event_cb(display_, event, LV_EVENT_DELETE, this);
        return active();
    }

    void detach() {
        if(display_) {
            lv_display_remove_event_cb_with_user_data(display_, event, this);
            display_ = nullptr;
        }
        close_fd();
    }

    bool active() const { return display_ && fd_ >= 0; }
    bool attached() const { return display_ != nullptr; }
    const FramebufferCommitStats& stats() const { return stats_; }

private:
    using Clock = std::chrono::steady_clock;
    lv_display_t* display_ = nullptr; // Borrowed, invalidated by LV_EVENT_DELETE.
    int fd_ = -1;
    FramebufferCommitStats stats_;

    void close_fd() {
        if(fd_ >= 0) close(fd_);
        fd_ = -1;
    }

    void record_error(const char* operation, int error) noexcept {
        // Error reporting must not throw through an LVGL C callback. A failure
        // disables commits; the original mmap display path remains available.
        try { stats_.error = std::string(operation) + ": " + std::strerror(error); }
        catch(...) {}
    }

    void commit() noexcept {
        ++stats_.completed_frames;
        if(fd_ < 0) return;
        for(;;) {
            const auto started = Clock::now();
            ++stats_.fsync_calls;
            const int result = fsync(fd_);
            const int error = result < 0 ? errno : 0;
            const double seconds = std::chrono::duration<double>(Clock::now() - started).count();
            stats_.fsync_total_seconds += seconds;
            if(seconds > stats_.fsync_max_seconds) stats_.fsync_max_seconds = seconds;
            if(result == 0) return;
            if(error == EINTR) continue;
            record_error("Framebuffer commit failed", error);
            close_fd();
            return;
        }
    }

    static void event(lv_event_t* value) {
        auto& self = *static_cast<FramebufferCommit*>(lv_event_get_user_data(value));
        if(lv_event_get_code(value) == LV_EVENT_DELETE) {
            // Do not remove callbacks from the list currently being deleted.
            self.display_ = nullptr;
            self.close_fd();
        } else if(self.display_ && lv_display_flush_is_last(self.display_)) {
            self.commit();
        }
    }
};

} // namespace tyrell::video
