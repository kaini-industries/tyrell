#pragma once

#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace tyrell::video {

// Supplied by the trusted launcher before any worker exists. Wire identifiers
// and inherited environment variables cannot construct this registration.
struct Draft2Bootstrap {
    int descriptor = -1;
    std::uint32_t expected_uid = 0;
    int expected_pid = -1;
    std::uint32_t creator_uid = 0;
    std::string app_instance;
};

struct Draft2Grant {
    std::string incarnation, app_instance, lease;
    std::uint64_t epoch = 0, request = 0;
};

enum class Draft2State { AwaitWelcome, AwaitGrant, AwaitReady, Foreground, Draining, Hidden, Lost };
enum class Draft2EventKind { Welcome, Grant, Quiesce, Cancel, Commit, Failed, Lost };
struct Draft2Event {
    Draft2EventKind kind = Draft2EventKind::Lost;
    Draft2Grant grant;
    std::uint64_t request = 0;
    unsigned budget_ms = 0;
    std::string receipt, error;
};

// Single UI/control-thread draft-2 app peer. This transports lifecycle intent;
// it neither creates authority permits nor certifies graph/display release.
// In particular, a grant does not reopen the UI until its caller has rebuilt
// under independently supplied durable permits and calls ready().
class Draft2Link {
public:
    using Clock = std::function<std::uint64_t()>;
    // Invalid adoption leaves the supplied descriptor and flags unchanged.
    // Successful adoption takes exclusive ownership, enables per-message
    // credentials and CLOEXEC, and pins the expected sender with a pidfd.
    static std::unique_ptr<Draft2Link> adopt(const Draft2Bootstrap&, std::string& error, Clock clock = {});
    ~Draft2Link();
    Draft2Link(const Draft2Link&) = delete;
    Draft2Link& operator=(const Draft2Link&) = delete;

    // At most 64 receives and sends, with nonblocking socket operations.
    std::vector<Draft2Event> pump();
    bool ready();
    // Only after the UI's local input/render seal; graph/display participants
    // must independently complete before the authority can issue a receipt.
    bool local_done(const std::string& checkpoint = "none");
    bool progress(const std::string& phase);
    void close() noexcept;

    Draft2State state() const noexcept;
    const std::optional<Draft2Grant>& grant() const noexcept;
    std::uint64_t drain_request() const noexcept;
    bool input_allowed() const noexcept { return state() == Draft2State::Foreground; }
    bool render_allowed() const noexcept { return input_allowed(); }
    bool connected() const noexcept;
    bool pending_output() const noexcept;
    int fd() const noexcept;
    int peer_pidfd() const noexcept;
    const std::string& last_error() const noexcept;

private:
    struct Impl;
    explicit Draft2Link(std::unique_ptr<Impl>);
    std::unique_ptr<Impl> impl_;
};

} // namespace tyrell::video
