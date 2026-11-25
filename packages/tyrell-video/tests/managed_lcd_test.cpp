#include "managed_lcd.h"
#include "managed_lcd_fixture.h"

#include <atomic>
#include <cstring>
#include <exception>
#include <future>
#include <iostream>
#include <stdexcept>
#include <thread>

#include <fcntl.h>
#include <poll.h>
#include <unistd.h>
#if defined(__linux__)
#include <linux/fb.h>
#include <sys/sysmacros.h>
#endif

using namespace tyrell::video;
using tyrell::video::test::ControlledLcdBackend;

namespace {
void require(bool condition, const std::string& message) {
    if(!condition) throw std::runtime_error(message);
}
ManagedLcdScope scope() { return {7, std::string(32, 'a'), std::string(32, 'b')}; }
std::string operation(char c) { return std::string(32, c); }
void readable(int fd) {
    pollfd value{fd, POLLIN, 0};
    require(poll(&value, 1, 5000) == 1 && (value.revents & POLLIN), "Managed LCD test gate timed out");
}
struct Pipe {
    int fd[2]{-1, -1};
    Pipe() { require(pipe(fd) == 0, "Cannot create LCD fixture pipe"); }
    ~Pipe() { for(int value : fd) if(value >= 0) close(value); }
    void signal() { require(write(fd[1], "G", 1) == 1, "Cannot release LCD gate"); }
    void wait() { readable(fd[0]); char byte; require(read(fd[0], &byte, 1) == 1, "Cannot read LCD gate"); }
};

void completion_and_suppression() {
    auto backend = std::make_shared<ControlledLcdBackend>(scope());
    ManagedLcd lcd(scope(), backend);
    std::string error;
    require(lcd.create(error), error);
    lv_obj_set_style_bg_opa(lv_display_get_screen_active(lcd.display()), LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(lv_display_get_screen_active(lcd.display()), lv_color_hex(0x123456), 0);
    require(lcd.evidence().acquired && !lcd.evidence().driver_finality, "Creation invented driver finality");
    require(lcd.submit(7, operation('c'), error) && lcd.submit(7, operation('d'), error), error);
    require(!lcd.submit(7, operation('c'), error) && !lcd.submit(8, operation('e'), error), "Duplicate/stale submission accepted");
    require(lcd.refresh(7, error), error);
    require(backend->writes >= 6 && backend->starts == 1 && backend->ends == 1,
            "Production LVGL redraw did not submit partial framebuffer strips");
    require(backend->pixels[0] != 0 && lcd.evidence().inflight == 1 && !lcd.operation_complete(operation('c')),
            "Copy/flush-ready falsely completed the backend operation");
    require(!lcd.release(7, error), "Unsealed framebuffer released");
    require(lcd.seal(7, error), error);
    require(lcd.operation_evidence(operation('d')).state == ManagedLcdOperationState::Suppressed &&
            lcd.operation_complete(operation('d')), "Queued frame was not positively suppressed");
    require(lcd.operation_evidence(operation('c')).state == ManagedLcdOperationState::Pending,
            "Sealing erased started backend work");
    const auto writes = backend->writes.load();
    require(!lcd.submit(7, operation('e'), error) && !lcd.refresh(7, error), "Sealed LCD accepted new work");
    lv_obj_invalidate(lv_display_get_screen_active(lcd.display()));
    lv_refr_now(lcd.display()); // Even a direct queued LVGL refresh cannot write.
    require(backend->writes == writes, "An unpermitted post-seal LVGL callback wrote pixels");
    require(lcd.poll(7, error) && lcd.evidence().future_callbacks_disabled, error);
    require(!lcd.release(7, error), "A held backend completion was ignored");
    backend->allow_complete = true;
    require(lcd.poll(7, error) && lcd.operation_complete(operation('c')) && lcd.evidence().inflight == 0, error);
    require(!lcd.release(7, error), "Frame completion alone claimed final driver quiescence");
    backend->allow_finality = true;
    const auto released = lcd.release(7, error);
    require(released && released->driver_finality && released->sealed && released->queued == 0 &&
            released->inflight == 0 && released->future_callbacks_disabled && released->backend_receipt == scope().generation,
            "Managed LCD release lacks bound driver/callback evidence");
    require(lcd.release(7, error)->backend_receipt == released->backend_receipt && backend->writes == writes,
            "Release replay drew another frame");
    require(!lcd.release(8, error) && backend->writes == writes, "Stale release changed pixels");
}

void failure_and_destructor() {
    auto backend = std::make_shared<ControlledLcdBackend>(scope());
    std::string error;
    {
        ManagedLcd lcd(scope(), backend);
        require(lcd.create(error) && lcd.submit(7, operation('c'), error) && lcd.refresh(7, error), error);
        require(lcd.seal(7, error), error);
        backend->fail = true;
        require(!lcd.poll(7, error) && !lcd.release(7, error) && lcd.evidence().inflight == 1,
                "Backend failure retired unknown work");
        backend->fail = false; backend->allow_complete = backend->allow_finality = true;
        require(!lcd.release(7, error), "Restored backend identity revived an unknown epoch");
    }
    const auto writes = backend->writes.load();
    require(backend->close_calls == 1, "Destructor did not abandon its backend exactly once");
    require(backend->poll_frame(operation('c')).state == LcdCompletionState::Unknown && backend->writes == writes,
            "Late completion after destruction restored or redrew pixels");

    auto second = std::make_shared<ControlledLcdBackend>(scope());
    {
        ManagedLcd lcd(scope(), second);
        require(lcd.create(error) && lcd.submit(7, operation('e'), error) && lcd.refresh(7, error), error);
        require(lcd.evidence().inflight == 1, "Pending destructor fixture did not retain work");
    }
    const auto before = second->writes.load();
    second->allow_complete = second->allow_finality = true;
    require(second->writes == before && second->poll_frame(operation('e')).state == LcdCompletionState::Unknown,
            "Destroying an unsealed owner claimed completion or restored pixels");
}

void held_callback_can_be_sealed() {
    auto backend = std::make_shared<ControlledLcdBackend>(scope());
    Pipe entered, gate, after_refresh, finish;
    std::atomic<bool> held{false};
    backend->before_write = [&] {
        if(!held.exchange(true)) { entered.signal(); gate.wait(); }
    };
    std::promise<ManagedLcd*> ready;
    auto pointer = ready.get_future();
    std::exception_ptr failure;
    ManagedLcdEvidence terminal;
    std::thread owner([&] {
        try {
            ManagedLcd lcd(scope(), backend);
            std::string error;
            require(lcd.create(error) && lcd.submit(7, operation('c'), error), error);
            ready.set_value(&lcd);
            require(lcd.refresh(7, error), error);
            require(lcd.poll(7, error), error);
            after_refresh.signal();
            finish.wait();
            auto released = lcd.release(7, error);
            require(bool(released), error);
            terminal = *released;
        } catch(...) {
            failure = std::current_exception();
            try { ready.set_value(nullptr); } catch(...) {}
        }
    });
    ManagedLcd* lcd = pointer.get();
    try {
        require(lcd != nullptr, "Cannot initialize held LCD fixture");
        entered.wait();
        std::string error;
        require(lcd->seal(7, error), error);
        require(lcd->evidence().inflight == 1 &&
                lcd->operation_evidence(operation('c')).state == ManagedLcdOperationState::Active,
                "A blocked accepted write vanished when control sealed the epoch");
        gate.signal(); after_refresh.wait();
        require(lcd->evidence().inflight == 1 && !lcd->evidence().driver_finality,
                "Returning from a write/flush certified a pending backend");
        backend->allow_complete = backend->allow_finality = true;
        finish.signal(); owner.join();
    } catch(...) {
        gate.signal(); finish.signal(); if(owner.joinable()) owner.join(); throw;
    }
    if(failure) std::rethrow_exception(failure);
    require(terminal.driver_finality && terminal.inflight == 0, "Held LCD could not finish under its original permit");
}

void unpermitted_and_invalid_scope() {
    auto backend = std::make_shared<ControlledLcdBackend>(scope());
    std::string error;
    ManagedLcd lcd(scope(), backend);
    require(lcd.create(error), error);
    lv_refr_now(lcd.display()); // No submit ticket: real callback must refuse.
    require(backend->writes == 0 && !lcd.evidence().error.empty(), "Unregistered flush wrote pixels");
    require(lcd.seal(7, error) && !lcd.release(7, error), "Unpermitted work produced release evidence");
    auto invalid = scope(); invalid.generation = "arbitrary";
    bool rejected = false;
    try { ManagedLcd other(invalid, backend); } catch(const std::invalid_argument&) { rejected = true; }
    require(rejected, "Malformed managed LCD binding accepted");
}

#if defined(__linux__)
struct FramebufferFixture {
    int fd = -1;
    fb_fix_screeninfo fixed{};
    fb_var_screeninfo variable{};
    unsigned writes = 0, syncs = 0;
    bool replace_identity = false, fail_write = false;
    bool complete = false, final = false;
    FramebufferFixture() {
        char pattern[] = "/tmp/tyrell-managed-fb-XXXXXX";
        fd = mkstemp(pattern); require(fd > 2, "Cannot create framebuffer fixture"); unlink(pattern);
        require(ftruncate(fd, 640 * 170) == 0, "Cannot size framebuffer fixture");
        fixed.type = FB_TYPE_PACKED_PIXELS; fixed.visual = FB_VISUAL_TRUECOLOR;
        fixed.line_length = 640; fixed.smem_len = 640 * 170;
        variable.xres = variable.xres_virtual = 320; variable.yres = variable.yres_virtual = 170;
        variable.bits_per_pixel = 16; variable.red.offset = 11; variable.red.length = 5;
        variable.green.offset = 5; variable.green.length = 6; variable.blue.length = 5;
    }
    ~FramebufferFixture() { close(fd); }
    std::shared_ptr<LinuxManagedFramebuffer> backend(bool qualified, std::string& error) {
        LinuxManagedFramebuffer::CompletionProvider provider;
        if(qualified) {
            provider.frame = [&](const std::string& operation) { return complete ? LcdCompletion{LcdCompletionState::Complete, operation, {}} : LcdCompletion{}; };
            provider.finality = [&] { return final ? LcdCompletion{LcdCompletionState::Complete, scope().generation, {}} : LcdCompletion{}; };
        }
        return LinuxManagedFramebuffer::adopt(fd, scope(), std::move(provider), error,
            [&](int, unsigned long request, void* target) {
                if(request == FBIOGET_FSCREENINFO) { std::memcpy(target, &fixed, sizeof(fixed)); return 0; }
                if(request == FBIOGET_VSCREENINFO) { std::memcpy(target, &variable, sizeof(variable)); return 0; }
                return -1;
            },
            [&](int value, const void* bytes, std::size_t count, std::int64_t offset) -> std::ptrdiff_t {
                ++writes;
                return fail_write ? -1 : pwrite(value, bytes, count, offset);
            },
            [&](int value) { ++syncs; return fsync(value); },
            [&](int value, struct stat* target) {
                const int result = fstat(value, target);
                if(result == 0) { target->st_mode = S_IFCHR | 0600; target->st_rdev = makedev(29, 0); if(replace_identity) ++target->st_ino; }
                return result;
            });
    }
};

void linux_descriptor_and_finality() {
    FramebufferFixture fixture;
    std::string error;
    const int flags = fcntl(fixture.fd, F_GETFD);
    require(!LinuxManagedFramebuffer::adopt(fixture.fd, scope(), {}, error), "Regular-file manager descriptor accepted");
    require(fcntl(fixture.fd, F_GETFD) == flags, "Rejected descriptor was mutated");
    auto generic = fixture.backend(false, error);
    require(generic && !generic->supports_finality(), error);
    require(!generic->begin_frame(operation('c'), error) && fixture.writes == 0 && fixture.syncs == 0,
            "Generic fbdev accepted writes without qualified driver finality");
    { ManagedLcd lcd(scope(), generic); require(!lcd.create(error), "Generic fbdev was treated as qualified"); }

    auto backend = fixture.backend(true, error);
    require(bool(backend), error);
    {
        ManagedLcd lcd(scope(), backend);
        require(lcd.create(error) && lcd.submit(7, operation('c'), error) && lcd.refresh(7, error), error);
        require(fixture.writes >= 170 && fixture.syncs == 1, "Actual pwrite/fsync adapter path was omitted");
        require(lcd.seal(7, error) && !lcd.release(7, error), "fsync alone claimed driver completion");
        fixture.complete = true;
        require(!lcd.release(7, error), "Completed writes alone claimed final driver quiescence");
        fixture.final = true;
        require(bool(lcd.release(7, error)), error);
    }
    // The adapter owns only a duplicate; the original manager FD remains open.
    require(fcntl(fixture.fd, F_GETFD) == flags, "Managed owner closed/changed the original manager FD");
}

void linux_identity_and_failure() {
    std::string error;
    FramebufferFixture fixture;
    auto backend = fixture.backend(true, error);
    require(bool(backend), error);
    fixture.variable.xoffset = 1;
    require(!backend->begin_frame(operation('c'), error), "External mode change was ignored");
    fixture.variable.xoffset = 0;
    require(!backend->begin_frame(operation('d'), error) && fixture.writes == 0,
            "Restoring geometry revived known uncertain identity");

    FramebufferFixture failed;
    auto broken = failed.backend(true, error);
    require(bool(broken) && broken->begin_frame(operation('c'), error), error);
    failed.fail_write = true;
    std::uint16_t pixel = 0;
    const lv_area_t area{0, 0, 0, 0};
    require(!broken->write_rect(operation('c'), area, reinterpret_cast<const std::uint8_t*>(&pixel), error),
            "Failed write was reported successful");
    failed.fail_write = false;
    require(!broken->end_frame(operation('c'), error) && broken->poll_frame(operation('c')).state == LcdCompletionState::Unknown,
            "A failed write could later certify completion");

    FramebufferFixture abandoned;
    auto late = abandoned.backend(true, error);
    require(bool(late) && late->begin_frame(operation('c'), error) &&
            late->write_rect(operation('c'), area, reinterpret_cast<const std::uint8_t*>(&pixel), error) &&
            late->end_frame(operation('c'), error), error);
    abandoned.complete = true;
    require(late->poll_frame(operation('c')).state == LcdCompletionState::Complete && late->seal(error), error);
    require(late->poll_finality().state == LcdCompletionState::Pending, "Close finality fixture was not held");
    late->close();
    abandoned.final = true;
    require(late->poll_finality().state == LcdCompletionState::Unknown,
            "A late post-close provider result revived an abandoned backend");
}
#endif
} // namespace

int main() {
    try {
        lv_init();
        completion_and_suppression();
        failure_and_destructor();
        held_callback_can_be_sealed();
        unpermitted_and_invalid_scope();
#if defined(__linux__)
        linux_descriptor_and_finality();
        linux_identity_and_failure();
#endif
        lv_deinit();
        std::cout << "PASS: managed LVGL tickets, held copies, suppression, independent finality, no destructor restoration\n";
        return 0;
    } catch(const std::exception& error) {
        lv_deinit();
        std::cerr << "Managed LCD: " << error.what() << '\n';
        return 1;
    }
}
