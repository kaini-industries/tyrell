#pragma once

#include "lvgl.h"

#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <sys/stat.h>

namespace tyrell::video {

struct ManagedLcdScope {
    std::uint64_t epoch = 0;
    std::string resource;
    std::string generation;
};

enum class LcdCompletionState { Pending, Complete, Unknown };
struct LcdCompletion {
    LcdCompletionState state = LcdCompletionState::Pending;
    std::string receipt;
    std::string error;
};

// A trusted backend is registered with this exact scope before side effects.
// Poll methods must not block. Complete means qualified backend finality, not
// just a completed userspace copy, fsync, unmap or LVGL flush-ready callback.
// Finality must also cover completion or suppression of future close effects.
class ManagedLcdBackend {
public:
    virtual ~ManagedLcdBackend() = default;
    virtual ManagedLcdScope scope() const = 0;
    virtual bool supports_finality() const noexcept = 0;
    virtual bool begin_frame(const std::string& operation, std::string& error) = 0;
    virtual bool write_rect(const std::string& operation, const lv_area_t& area,
                            const std::uint8_t* pixels, std::string& error) = 0;
    virtual bool end_frame(const std::string& operation, std::string& error) = 0;
    virtual LcdCompletion poll_frame(const std::string& operation) = 0;
    virtual bool seal(std::string& error) = 0;
    virtual LcdCompletion poll_finality() = 0;
    // Resource abandonment only. This must never restore/redraw/reopen.
    virtual void close() noexcept = 0;
};

struct ManagedLcdEvidence {
    std::uint64_t epoch = 0;
    std::string resource;
    std::string generation;
    bool acquired = false;
    bool sealed = false;
    std::size_t queued = 0;
    std::size_t inflight = 0;
    bool driver_finality = false;
    bool future_callbacks_disabled = false;
    std::string backend_receipt;
    std::string error;
};

enum class ManagedLcdOperationState { Missing, Queued, Active, Pending, Complete, Suppressed, Unknown };
struct ManagedLcdOperationEvidence {
    std::string operation;
    ManagedLcdOperationState state = ManagedLcdOperationState::Missing;
    std::string backend_receipt;
};

// Explicit managed LVGL path, independent of the legacy fbdev driver and
// FramebufferCommit. The coordinator journals each submit operation before
// calling this class. One accepted ticket covers an entire partial-buffer
// redraw. create/submit/refresh/poll/release/destruction run on one UI owner
// thread; seal and evidence are safe from the separate control thread.
class ManagedLcd {
public:
    ManagedLcd(ManagedLcdScope scope, std::shared_ptr<ManagedLcdBackend> backend);
    ~ManagedLcd();
    ManagedLcd(const ManagedLcd&) = delete;
    ManagedLcd& operator=(const ManagedLcd&) = delete;

    bool create(std::string& error);
    lv_display_t* display() const;
    bool submit(std::uint64_t epoch, const std::string& operation, std::string& error);
    bool refresh(std::uint64_t epoch, std::string& error);
    bool seal(std::uint64_t epoch, std::string& error);
    bool poll(std::uint64_t epoch, std::string& error);
    std::optional<ManagedLcdEvidence> release(std::uint64_t epoch, std::string& error);
    ManagedLcdEvidence evidence() const;
    // True for a ticket removed before begin_frame, or positively completed by
    // the backend. This is a fact for the coordinator, never an authority grant.
    bool operation_complete(const std::string& operation) const;
    ManagedLcdOperationEvidence operation_evidence(const std::string& operation) const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

// The generic Linux framebuffer API provides no portable proof that deferred
// panel work has finished. Without an explicitly qualified completion provider
// this adapter refuses managed submission before modifying pixels. Device
// identity/geometry/readback do not themselves establish manager provenance.
class LinuxManagedFramebuffer final : public ManagedLcdBackend {
public:
    struct CompletionProvider {
        std::function<LcdCompletion(const std::string&)> frame;
        // Polled only after the owned framebuffer FD has been closed. This
        // provider must outlive that FD and qualify any release-side work.
        std::function<LcdCompletion()> finality;
    };
    using Ioctl = std::function<int(int, unsigned long, void*)>;
    using Write = std::function<std::ptrdiff_t(int, const void*, std::size_t, std::int64_t)>;
    using Sync = std::function<int(int)>;
    using Inspect = std::function<int(int, struct stat*)>;

    static std::shared_ptr<LinuxManagedFramebuffer> adopt(
        int manager_fd, ManagedLcdScope scope, CompletionProvider completion,
        std::string& error, Ioctl ioctl = {}, Write write = {}, Sync sync = {}, Inspect inspect = {});
    ~LinuxManagedFramebuffer() override;
    ManagedLcdScope scope() const override;
    bool supports_finality() const noexcept override;
    bool begin_frame(const std::string&, std::string&) override;
    bool write_rect(const std::string&, const lv_area_t&, const std::uint8_t*, std::string&) override;
    bool end_frame(const std::string&, std::string&) override;
    LcdCompletion poll_frame(const std::string&) override;
    bool seal(std::string&) override;
    LcdCompletion poll_finality() override;
    void close() noexcept override;

private:
    struct Impl;
    explicit LinuxManagedFramebuffer(std::unique_ptr<Impl>);
    std::unique_ptr<Impl> impl_;
};

} // namespace tyrell::video
