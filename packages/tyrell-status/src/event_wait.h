#pragma once

#include <chrono>
#include <climits>
#include <optional>
#include <system_error>

#if defined(__linux__)
#include <cerrno>
#include <poll.h>
#endif

namespace tyrell::status {
using SteadyClock = std::chrono::steady_clock;

struct WaitRequest {
    int input_fd = -1;
    int link_fd = -1;
    int shutdown_fd = -1;
    bool link_output = false;
    std::optional<SteadyClock::time_point> deadline;
};

// Round up so a fractional final millisecond never becomes a busy poll. A
// distant deadline is capped to poll's range and reconsidered after that wait.
inline int deadline_timeout_ms(const std::optional<SteadyClock::time_point>& deadline,
                               SteadyClock::time_point now = SteadyClock::now()) {
    if(!deadline) return -1;
    if(*deadline <= now) return 0;
    const auto remaining = *deadline - now;
    if(remaining >= std::chrono::milliseconds(INT_MAX)) return INT_MAX;
    return static_cast<int>(std::chrono::ceil<std::chrono::milliseconds>(remaining).count());
}

#if defined(__linux__)
inline void wait_for_events(const WaitRequest& request) {
    pollfd descriptors[3]{};
    nfds_t count = 0;
    const auto add = [&](int fd, short events) {
        if(fd >= 0) descriptors[count++] = pollfd{fd, events, 0};
    };
    add(request.input_fd, POLLIN);
    add(request.link_fd, static_cast<short>(POLLIN | (request.link_output ? POLLOUT : 0)));
    add(request.shutdown_fd, POLLIN);
    const int ready = ::poll(descriptors, count, deadline_timeout_ms(request.deadline));
    if(ready < 0) {
        // Return to the caller's shutdown fence, including for a signal whose
        // self-pipe byte could not be written. Never restart with a stale wait.
        if(errno == EINTR) return;
        throw std::system_error(errno, std::generic_category(), "Status poll");
    }
    for(nfds_t i = 0; i < count; ++i) {
        if(descriptors[i].revents & POLLNVAL)
            throw std::system_error(EBADF, std::generic_category(), "Status wake descriptor");
    }
}
#endif
} // namespace tyrell::status
