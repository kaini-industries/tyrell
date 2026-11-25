#include "managed_lcd.h"

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <deque>
#include <map>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#if defined(__linux__)
#include <linux/fb.h>
#include <sys/ioctl.h>
#include <sys/sysmacros.h>
#endif

namespace tyrell::video {
namespace {
bool hex_id(const std::string& value) {
    return value.size() == 32 && std::all_of(value.begin(), value.end(), [](char c) {
        return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
    });
}
bool valid_scope(const ManagedLcdScope& scope) {
    return scope.epoch && hex_id(scope.resource) && hex_id(scope.generation);
}
bool same_scope(const ManagedLcdScope& a, const ManagedLcdScope& b) {
    return a.epoch == b.epoch && a.resource == b.resource && a.generation == b.generation;
}
bool valid_area(const lv_area_t& a) {
    return a.x1 >= 0 && a.y1 >= 0 && a.x1 <= a.x2 && a.y1 <= a.y2 && a.x2 < 320 && a.y2 < 170;
}
} // namespace

struct ManagedLcd::Impl {
    using State = ManagedLcdOperationState;
    struct Ticket { State state; std::string receipt; };
    ManagedLcdScope scope;
    std::shared_ptr<ManagedLcdBackend> backend;
    std::thread::id owner = std::this_thread::get_id();
    mutable std::mutex mutex;
    lv_display_t* display = nullptr;
    std::vector<std::uint16_t> pixels = std::vector<std::uint16_t>(320 * 32);
    std::map<std::string, Ticket> tickets;
    std::deque<std::string> queue;
    std::string active, error, final_receipt;
    bool sealed = false, callbacks_disabled = false, backend_sealed = false, released = false;

    bool ui(std::string& output) const {
        if(owner == std::this_thread::get_id()) return true;
        output = "Managed LCD operation requires its UI owner thread";
        return false;
    }
    void fail(const std::string& text) {
        std::lock_guard<std::mutex> guard(mutex);
        if(error.empty()) error = text.empty() ? "Managed LCD completion unknown" : text;
        if(!active.empty()) tickets.at(active).state = State::Unknown;
    }
    ManagedLcdEvidence evidence_locked() const {
        ManagedLcdEvidence result;
        result.epoch = scope.epoch; result.resource = scope.resource; result.generation = scope.generation;
        result.acquired = display != nullptr;
        result.sealed = sealed; result.future_callbacks_disabled = callbacks_disabled;
        result.driver_finality = released; result.backend_receipt = final_receipt; result.error = error;
        for(const auto& [operation, ticket] : tickets) {
            (void)operation;
            if(ticket.state == State::Queued) ++result.queued;
            if(ticket.state == State::Active || ticket.state == State::Pending || ticket.state == State::Unknown)
                ++result.inflight;
        }
        return result;
    }
    static void flush(lv_display_t* display, const lv_area_t* area, std::uint8_t* pixels) noexcept {
        auto& self = *static_cast<Impl*>(lv_display_get_driver_data(display));
        std::string operation;
        try {
            {
                std::lock_guard<std::mutex> guard(self.mutex);
                operation = self.active;
                if(operation.empty()) {
                    // A pending LVGL timer after seal can finish its CPU work,
                    // but cannot submit any new framebuffer write.
                    if(!self.sealed && self.error.empty()) self.error = "Unpermitted managed LCD flush";
                    lv_display_flush_ready(display);
                    return;
                }
                if(self.tickets.at(operation).state == State::Unknown) {
                    lv_display_flush_ready(display);
                    return;
                }
            }
            std::string error;
            if(!area || !pixels || !valid_area(*area) ||
               lv_display_get_rotation(display) != LV_DISPLAY_ROTATION_0 ||
               !self.backend->write_rect(operation, *area, pixels, error))
                self.fail(error.empty() ? "Managed LCD write failed" : error);
        } catch(...) {
            // Never let C++ exceptions cross LVGL's C callback boundary.
            try { self.fail("Managed LCD callback completion unknown"); } catch(...) {}
        }
        // This retires only LVGL's synchronous callback/buffer borrowing. The
        // independent backend operation stays pending until qualified polling.
        lv_display_flush_ready(display);
    }
};

ManagedLcd::ManagedLcd(ManagedLcdScope scope, std::shared_ptr<ManagedLcdBackend> backend)
    : impl_(std::make_unique<Impl>()) {
    if(!valid_scope(scope) || !backend || !same_scope(scope, backend->scope()))
        throw std::invalid_argument("Invalid managed LCD resource binding");
    impl_->scope = std::move(scope); impl_->backend = std::move(backend);
}

ManagedLcd::~ManagedLcd() {
    // Destruction is abandonment, never a release receipt or a last redraw.
    // There is no async callback carrying this pointer; backend completions are
    // polled by operation ID. The owner must not delete during refresh/poll.
    if(impl_->display) lv_display_delete(impl_->display);
    impl_->display = nullptr;
    impl_->backend->close();
}

bool ManagedLcd::create(std::string& error) {
    if(!impl_->ui(error)) return false;
    std::lock_guard<std::mutex> guard(impl_->mutex);
    if(impl_->display || impl_->sealed || !impl_->error.empty() || !impl_->backend->supports_finality()) {
        error = "Managed LCD requires a fresh qualified backend"; return false;
    }
    auto* display = lv_display_create(320, 170);
    if(!display) { error = "Cannot create managed LCD display"; return false; }
    impl_->display = display;
    lv_display_set_driver_data(display, impl_.get());
    lv_display_set_color_format(display, LV_COLOR_FORMAT_RGB565);
    lv_display_set_buffers(display, impl_->pixels.data(), nullptr,
                           impl_->pixels.size() * sizeof(std::uint16_t), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(display, Impl::flush);
    // Only explicit accepted refresh calls may submit. Normal LVGL timer work
    // is kept paused, including invalidations queued before the epoch seals.
    lv_timer_pause(lv_display_get_refr_timer(display));
    return true;
}

lv_display_t* ManagedLcd::display() const { return impl_->display; }

bool ManagedLcd::submit(std::uint64_t epoch, const std::string& operation, std::string& error) {
    if(!impl_->ui(error)) return false;
    std::lock_guard<std::mutex> guard(impl_->mutex);
    if(epoch != impl_->scope.epoch || !hex_id(operation) || !impl_->display || impl_->sealed ||
       !impl_->error.empty() || impl_->tickets.size() >= 128 || impl_->tickets.count(operation)) {
        error = "Managed LCD submission is stale, sealed, duplicate or full"; return false;
    }
    impl_->tickets.emplace(operation, Impl::Ticket{Impl::State::Queued, {}});
    impl_->queue.push_back(operation);
    return true;
}

bool ManagedLcd::refresh(std::uint64_t epoch, std::string& error) {
    if(!impl_->ui(error)) return false;
    std::string operation;
    {
        std::lock_guard<std::mutex> guard(impl_->mutex);
        if(epoch != impl_->scope.epoch || impl_->sealed || !impl_->display || !impl_->error.empty() ||
           !impl_->active.empty() || impl_->queue.empty()) {
            error = "Managed LCD refresh has no accepted ticket"; return false;
        }
        operation = impl_->queue.front(); impl_->queue.pop_front();
        impl_->active = operation;
        impl_->tickets.at(operation).state = Impl::State::Active;
    }
    bool ok = false;
    try {
        if(impl_->backend->begin_frame(operation, error)) {
            lv_obj_invalidate(lv_display_get_screen_active(impl_->display));
            lv_refr_now(impl_->display);
            {
                std::lock_guard<std::mutex> guard(impl_->mutex);
                ok = impl_->tickets.at(operation).state != Impl::State::Unknown;
            }
            if(ok) ok = impl_->backend->end_frame(operation, error);
        }
    } catch(...) { error = "Managed LCD frame completion unknown"; }
    {
        std::lock_guard<std::mutex> guard(impl_->mutex);
        impl_->tickets.at(operation).state = ok ? Impl::State::Pending : Impl::State::Unknown;
        impl_->active.clear();
        if(!ok && impl_->error.empty()) impl_->error = error.empty() ? "Managed LCD frame failed" : error;
    }
    return ok;
}

bool ManagedLcd::seal(std::uint64_t epoch, std::string& error) {
    std::lock_guard<std::mutex> guard(impl_->mutex);
    if(epoch != impl_->scope.epoch) { error = "Stale managed LCD epoch"; return false; }
    impl_->sealed = true;
    for(const auto& operation : impl_->queue) impl_->tickets.at(operation).state = Impl::State::Suppressed;
    impl_->queue.clear();
    return true;
}

bool ManagedLcd::poll(std::uint64_t epoch, std::string& error) {
    if(!impl_->ui(error)) return false;
    std::vector<std::string> pending;
    bool need_seal = false;
    {
        std::lock_guard<std::mutex> guard(impl_->mutex);
        if(epoch != impl_->scope.epoch) { error = "Stale managed LCD epoch"; return false; }
        if(!impl_->error.empty()) { error = impl_->error; return false; }
        for(const auto& [operation, ticket] : impl_->tickets)
            if(ticket.state == Impl::State::Pending) pending.push_back(operation);
        need_seal = impl_->sealed && !impl_->backend_sealed && impl_->active.empty();
    }
    try {
        for(const auto& operation : pending) {
            const auto result = impl_->backend->poll_frame(operation);
            if(result.state == LcdCompletionState::Unknown ||
               (result.state == LcdCompletionState::Complete && !hex_id(result.receipt))) {
                impl_->fail(result.error.empty() ? "Managed LCD backend completion unknown" : result.error);
                std::lock_guard<std::mutex> guard(impl_->mutex);
                impl_->tickets.at(operation).state = Impl::State::Unknown;
                error = impl_->error; return false;
            }
            if(result.state == LcdCompletionState::Complete) {
                std::lock_guard<std::mutex> guard(impl_->mutex);
                impl_->tickets.at(operation) = {Impl::State::Complete, result.receipt};
            }
        }
        if(need_seal) {
            if(!impl_->backend->seal(error)) { impl_->fail(error); return false; }
            if(impl_->display) {
                lv_timer_pause(lv_display_get_refr_timer(impl_->display));
                lv_display_enable_invalidation(impl_->display, false);
            }
            std::lock_guard<std::mutex> guard(impl_->mutex);
            impl_->backend_sealed = impl_->callbacks_disabled = true;
        }
    } catch(...) { error = "Managed LCD poll completion unknown"; impl_->fail(error); return false; }
    return true;
}

std::optional<ManagedLcdEvidence> ManagedLcd::release(std::uint64_t epoch, std::string& error) {
    if(!impl_->ui(error) || !poll(epoch, error)) return std::nullopt;
    {
        std::lock_guard<std::mutex> guard(impl_->mutex);
        if(impl_->released) return impl_->evidence_locked();
        const auto evidence = impl_->evidence_locked();
        if(!evidence.sealed || evidence.queued || evidence.inflight || !evidence.future_callbacks_disabled || !evidence.error.empty()) {
            error = "Managed LCD still has outstanding work"; return std::nullopt;
        }
    }
    try {
        const auto result = impl_->backend->poll_finality();
        if(result.state == LcdCompletionState::Pending) { error = "Managed LCD driver finality pending"; return std::nullopt; }
        if(result.state != LcdCompletionState::Complete || !hex_id(result.receipt)) {
            error = result.error.empty() ? "Managed LCD driver finality unknown" : result.error;
            impl_->fail(error); return std::nullopt;
        }
        std::lock_guard<std::mutex> guard(impl_->mutex);
        impl_->released = true; impl_->final_receipt = result.receipt;
        return impl_->evidence_locked();
    } catch(...) { error = "Managed LCD driver finality unknown"; impl_->fail(error); return std::nullopt; }
}

ManagedLcdEvidence ManagedLcd::evidence() const {
    std::lock_guard<std::mutex> guard(impl_->mutex);
    return impl_->evidence_locked();
}
bool ManagedLcd::operation_complete(const std::string& operation) const {
    std::lock_guard<std::mutex> guard(impl_->mutex);
    const auto found = impl_->tickets.find(operation);
    return found != impl_->tickets.end() && (found->second.state == Impl::State::Complete ||
                                           found->second.state == Impl::State::Suppressed);
}
ManagedLcdOperationEvidence ManagedLcd::operation_evidence(const std::string& operation) const {
    std::lock_guard<std::mutex> guard(impl_->mutex);
    const auto found = impl_->tickets.find(operation);
    if(found == impl_->tickets.end()) return {operation, ManagedLcdOperationState::Missing, {}};
    return {operation, found->second.state, found->second.receipt};
}

struct LinuxManagedFramebuffer::Impl {
    ManagedLcdScope scope;
    CompletionProvider completion;
    Ioctl ioctl;
    Write write;
    Sync sync;
    Inspect inspect;
    struct stat identity{};
    int fd = -1;
    bool sealed = false, unknown = false, finalizing = false;
    std::string final_receipt;
    struct Frame { bool ended = false; std::string receipt; };
    std::map<std::string, Frame> frames;
    ~Impl() { if(fd >= 0) ::close(fd); }
#if defined(__linux__)
    fb_fix_screeninfo fixed{};
    fb_var_screeninfo variable{};
    bool unchanged() {
        fb_fix_screeninfo f{}; fb_var_screeninfo v{};
        struct stat info{};
        return inspect(fd, &info) == 0 && info.st_dev == identity.st_dev && info.st_ino == identity.st_ino &&
            info.st_rdev == identity.st_rdev && S_ISCHR(info.st_mode) &&
            ioctl(fd, FBIOGET_FSCREENINFO, &f) == 0 && ioctl(fd, FBIOGET_VSCREENINFO, &v) == 0 &&
            f.type == fixed.type && f.visual == fixed.visual && f.smem_len == fixed.smem_len &&
            f.line_length == fixed.line_length && f.smem_start == fixed.smem_start &&
            v.xres == variable.xres && v.yres == variable.yres && v.xres_virtual == variable.xres_virtual &&
            v.yres_virtual == variable.yres_virtual && v.xoffset == 0 && v.yoffset == 0 &&
            v.bits_per_pixel == 16 && v.red.offset == 11 && v.red.length == 5 &&
            v.green.offset == 5 && v.green.length == 6 && v.blue.offset == 0 && v.blue.length == 5 &&
            v.red.msb_right == 0 && v.green.msb_right == 0 && v.blue.msb_right == 0 &&
            v.transp.length == 0 && v.rotate == 0 && v.grayscale == 0 && v.nonstd == 0;
    }
#endif
};

LinuxManagedFramebuffer::LinuxManagedFramebuffer(std::unique_ptr<Impl> impl) : impl_(std::move(impl)) {}
LinuxManagedFramebuffer::~LinuxManagedFramebuffer() { close(); }
std::shared_ptr<LinuxManagedFramebuffer> LinuxManagedFramebuffer::adopt(
    int manager_fd, ManagedLcdScope scope, CompletionProvider completion, std::string& error,
    Ioctl ioctl, Write write, Sync sync, Inspect inspect) {
#if defined(__linux__)
    if(!inspect) inspect = [](int fd, struct stat* value) { return ::fstat(fd, value); };
    struct stat info{};
    const int flags = manager_fd > 2 ? fcntl(manager_fd, F_GETFL) : -1;
    if(manager_fd <= 2 || !valid_scope(scope) || inspect(manager_fd, &info) != 0 ||
       !S_ISCHR(info.st_mode) || major(info.st_rdev) != 29 || flags < 0 || (flags & O_ACCMODE) != O_RDWR) {
        error = "Managed framebuffer requires a trusted framebuffer descriptor"; return {};
    }
    auto impl = std::make_unique<Impl>();
    impl->scope = std::move(scope); impl->completion = std::move(completion);
    impl->inspect = std::move(inspect); impl->identity = info;
    impl->ioctl = ioctl ? std::move(ioctl) : Ioctl([](int fd, unsigned long request, void* value) { return ::ioctl(fd, request, value); });
    impl->write = write ? std::move(write) : Write([](int fd, const void* bytes, std::size_t size, std::int64_t offset) { return ::pwrite(fd, bytes, size, static_cast<off_t>(offset)); });
    impl->sync = sync ? std::move(sync) : Sync([](int fd) { return ::fsync(fd); });
    impl->fd = fcntl(manager_fd, F_DUPFD_CLOEXEC, 3);
    if(impl->fd < 0) { error = "Cannot retain managed framebuffer descriptor"; return {}; }
    const bool valid = impl->ioctl(impl->fd, FBIOGET_FSCREENINFO, &impl->fixed) == 0 &&
        impl->ioctl(impl->fd, FBIOGET_VSCREENINFO, &impl->variable) == 0 &&
        impl->fixed.type == FB_TYPE_PACKED_PIXELS && impl->fixed.visual == FB_VISUAL_TRUECOLOR &&
        impl->fixed.line_length >= 640 && impl->fixed.line_length <= 4096 &&
        impl->fixed.smem_len >= impl->fixed.line_length * 170 && impl->fixed.smem_len <= 16 * 1024 * 1024 &&
        impl->variable.xres == 320 && impl->variable.yres == 170 && impl->variable.xres_virtual == 320 &&
        impl->variable.yres_virtual == 170 && impl->unchanged();
    if(!valid) { ::close(impl->fd); impl->fd = -1; error = "Managed framebuffer geometry/identity unsupported"; return {}; }
    return std::shared_ptr<LinuxManagedFramebuffer>(new LinuxManagedFramebuffer(std::move(impl)));
#else
    (void)manager_fd; (void)scope; (void)completion; (void)ioctl; (void)write; (void)sync; (void)inspect;
    error = "Managed framebuffer requires Linux"; return {};
#endif
}
ManagedLcdScope LinuxManagedFramebuffer::scope() const { return impl_->scope; }
bool LinuxManagedFramebuffer::supports_finality() const noexcept {
    return impl_->fd >= 0 && bool(impl_->completion.frame) && bool(impl_->completion.finality);
}
bool LinuxManagedFramebuffer::begin_frame(const std::string& operation, std::string& error) {
#if defined(__linux__)
    if(!supports_finality() || impl_->sealed || impl_->unknown || !hex_id(operation) ||
       impl_->frames.size() >= 128 || impl_->frames.count(operation)) {
        error = "Managed framebuffer admission/finality unavailable"; return false;
    }
    if(!impl_->unchanged()) {
        impl_->unknown = true; error = "Managed framebuffer identity changed"; return false;
    }
    impl_->frames.emplace(operation, Impl::Frame{}); return true;
#else
    (void)operation; error = "Managed framebuffer requires Linux"; return false;
#endif
}
bool LinuxManagedFramebuffer::write_rect(const std::string& operation, const lv_area_t& area,
                                        const std::uint8_t* pixels, std::string& error) {
#if defined(__linux__)
    const auto found = impl_->frames.find(operation);
    if(impl_->unknown || !pixels || !valid_area(area) || found == impl_->frames.end() || found->second.ended) {
        error = "Unpermitted managed framebuffer write"; return false;
    }
    const auto stride = lv_draw_buf_width_to_stride(lv_area_get_width(&area), LV_COLOR_FORMAT_RGB565);
    const auto bytes = static_cast<std::size_t>(lv_area_get_width(&area)) * 2;
    for(int y = area.y1; y <= area.y2; ++y) {
        const std::int64_t offset = std::int64_t(y) * impl_->fixed.line_length + area.x1 * 2;
        if(impl_->write(impl_->fd, pixels, bytes, offset) != static_cast<std::ptrdiff_t>(bytes)) {
            impl_->unknown = true; error = "Managed framebuffer write completion unknown"; return false;
        }
        pixels += stride;
    }
    return true;
#else
    (void)operation; (void)area; (void)pixels; error = "Managed framebuffer requires Linux"; return false;
#endif
}
bool LinuxManagedFramebuffer::end_frame(const std::string& operation, std::string& error) {
#if defined(__linux__)
    const auto found = impl_->frames.find(operation);
    if(impl_->unknown || found == impl_->frames.end() || found->second.ended || impl_->sync(impl_->fd) != 0 || !impl_->unchanged()) {
        impl_->unknown = true; error = "Managed framebuffer sync/readback unknown"; return false;
    }
    found->second.ended = true; return true; // fsync is deliberately not finality.
#else
    (void)operation; error = "Managed framebuffer requires Linux"; return false;
#endif
}
LcdCompletion LinuxManagedFramebuffer::poll_frame(const std::string& operation) {
    const auto found = impl_->frames.find(operation);
    if(impl_->unknown || found == impl_->frames.end() || !found->second.ended)
        return {LcdCompletionState::Unknown, {}, "Managed framebuffer work unconfirmed"};
    if(!found->second.receipt.empty()) return {LcdCompletionState::Complete, found->second.receipt, {}};
    if(!supports_finality()) return {LcdCompletionState::Unknown, {}, "Managed framebuffer completion unavailable"};
    auto result = impl_->completion.frame(operation);
    if(result.state == LcdCompletionState::Unknown ||
       (result.state == LcdCompletionState::Complete && !hex_id(result.receipt))) {
        impl_->unknown = true; return {LcdCompletionState::Unknown, {}, "Managed framebuffer completion receipt invalid"};
    }
    if(result.state == LcdCompletionState::Complete) found->second.receipt = result.receipt;
    return result;
}
bool LinuxManagedFramebuffer::seal(std::string& error) {
    if(impl_->unknown || !supports_finality()) { error = "Managed framebuffer cannot seal"; return false; }
    impl_->sealed = true; return true;
}
LcdCompletion LinuxManagedFramebuffer::poll_finality() {
#if defined(__linux__)
    if(!impl_->final_receipt.empty()) return {LcdCompletionState::Complete, impl_->final_receipt, {}};
    if(impl_->unknown || !impl_->sealed || !impl_->completion.finality) {
        impl_->unknown = true; return {LcdCompletionState::Unknown, {}, "Managed framebuffer finality unavailable"};
    }
    for(const auto& [operation, frame] : impl_->frames) {
        (void)operation;
        if(frame.receipt.empty()) return {};
    }
    if(!impl_->finalizing) {
        if(!impl_->unchanged()) {
            impl_->unknown = true; return {LcdCompletionState::Unknown, {}, "Managed framebuffer identity changed before close"};
        }
        const int fd = impl_->fd; impl_->fd = -1;
        if(::close(fd) != 0) {
            impl_->unknown = true; return {LcdCompletionState::Unknown, {}, "Managed framebuffer close completion unknown"};
        }
        impl_->finalizing = true;
    }
    auto result = impl_->completion.finality();
    if(result.state == LcdCompletionState::Unknown ||
       (result.state == LcdCompletionState::Complete && !hex_id(result.receipt))) {
        impl_->unknown = true; return {LcdCompletionState::Unknown, {}, "Managed framebuffer finality receipt invalid"};
    }
    if(result.state == LcdCompletionState::Complete) impl_->final_receipt = result.receipt;
    return result;
#else
    return {LcdCompletionState::Unknown, {}, "Managed framebuffer requires Linux"};
#endif
}
void LinuxManagedFramebuffer::close() noexcept {
    if(impl_->final_receipt.empty()) impl_->unknown = true;
    if(impl_->fd >= 0) ::close(impl_->fd);
    impl_->fd = -1;
}

} // namespace tyrell::video
