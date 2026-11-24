#pragma once

namespace tyrell::app {

// Ignored consumed one malformed/unknown packet; None means no packet is ready.
enum class ShellEvent { None, Ignored, Hello, Pause, Resume, Redraw, Lost };

// Optional, single-threaded Gh0st protocol-v1 transport. The app retains all
// lifecycle policy. Call from_environment() before starting workers or helpers.
class ShellLink {
public:
    ShellLink() noexcept = default;
    ~ShellLink();
    ShellLink(const ShellLink&) = delete;
    ShellLink& operator=(const ShellLink&) = delete;
    ShellLink(ShellLink&& other) noexcept;
    ShellLink& operator=(ShellLink&& other) noexcept;

    // GH0ST_SHELL_FD explicitly transfers exclusive ownership of an inherited
    // endpoint. Validation cannot prove that no other object holds this fd.
    // Invalid descriptors are never closed or changed. The variable is always
    // removed, including on unsupported platforms and invalid input.
    static ShellLink from_environment() noexcept;

    int fd() const noexcept { return fd_; }
    bool connected() const noexcept { return fd_ >= 0; }
    bool closed() const noexcept { return lost_; }
    bool pending_output() const noexcept { return hello_pending_ || paused_pending_ || awake_pending_; }

    // Reads at most one packet; never blocks. A valid hello\t1 must precede
    // lifecycle events. An unsupported hello version loses the link safely.
    ShellEvent next_event() noexcept;
    // True means accepted for sending, not that the peer has received it.
    // The app must first stop rendering, release/reset input, then acknowledge.
    bool acknowledge_paused() noexcept;
    // Drop an unsent acknowledgment when resume supersedes that pause.
    void cancel_paused() noexcept { paused_pending_ = false; }
    bool send_awake(bool awake) noexcept;
    // At most three sends; retain output on backpressure, close on fatal error.
    void service() noexcept;

private:
    int fd_ = -1;
    bool lost_ = false;
    bool lost_pending_ = false;
    bool hello_received_ = false;
    bool hello_pending_ = false;
    bool paused_pending_ = false;
    bool awake_pending_ = false;
    bool awake_ = false;
    void lose() noexcept;
    void release() noexcept;
    bool send_packet(const char* packet, unsigned length) noexcept;
};

} // namespace tyrell::app
