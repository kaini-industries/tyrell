#include "draft2_link.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <deque>
#include <limits>
#include <utility>

#if defined(__linux__)
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/un.h>
#include <unistd.h>
#endif

namespace tyrell::video {
namespace {
constexpr char capabilities[] = "async-drain,epoch-barrier,helper-receipts";
constexpr unsigned maximum_packet = 512, maximum_batch = 64, maximum_output = 16;
bool identifier(const std::string& text) {
    return text.size() == 32 && text.find_first_not_of("0123456789abcdef") == std::string::npos;
}
bool decimal(const std::string& text, std::uint64_t& result) {
    if(text.empty() || text.size() > 20 || text.front() == '0') return false;
    result = 0;
    for(unsigned char value : text) {
        if(value < '0' || value > '9' || result > (std::numeric_limits<std::uint64_t>::max() - (value - '0')) / 10) return false;
        result = result * 10 + (value - '0');
    }
    return result != 0;
}
std::vector<std::string> fields(const std::string& packet) {
    if(packet.empty() || packet.size() > maximum_packet ||
       std::any_of(packet.begin(), packet.end(), [](unsigned char c) { return c != '\t' && (c < 32 || c > 126); })) return {};
    std::vector<std::string> result;
    std::size_t start = 0;
    for(;;) {
        const auto end = packet.find('\t', start);
        result.push_back(packet.substr(start, end == std::string::npos ? end : end - start));
        if(end == std::string::npos) break;
        start = end + 1;
    }
    return result;
}
bool same(const Draft2Grant& a, const Draft2Grant& b) {
    return a.incarnation == b.incarnation && a.app_instance == b.app_instance && a.epoch == b.epoch &&
        a.request == b.request && a.lease == b.lease;
}
} // namespace

struct Draft2Link::Impl {
    int descriptor = -1, pidfd = -1, expected_pid = -1;
    std::uint32_t expected_uid = 0;
    std::string app, incarnation, error, checkpoint, committed_receipt;
    Draft2State state = Draft2State::AwaitWelcome;
    std::optional<Draft2Grant> current;
    std::uint64_t request = 0, highest_request = 0;
    unsigned budget = 0;
    bool done = false, cancelled = false, lost_event = false;
    Clock clock;
    std::uint64_t started = 0, last_time = 0;
    std::deque<std::string> output;

    ~Impl() { release(); }
    void release() noexcept {
#if defined(__linux__)
        if(descriptor >= 0) ::close(descriptor);
        if(pidfd >= 0) ::close(pidfd);
#endif
        descriptor = pidfd = -1;
    }
    void lose(const std::string& reason) {
        error = reason; state = Draft2State::Lost; output.clear(); lost_event = true; release();
    }
    bool alive() const {
#if defined(__linux__)
        if(pidfd < 0) return false;
        pollfd item{pidfd, POLLIN | POLLHUP | POLLERR, 0};
        const int result = ::poll(&item, 1, 0);
        return result == 0 || (result < 0 && errno == EINTR);
#else
        return false;
#endif
    }
    void flush() {
#if defined(__linux__)
        for(unsigned count = 0; descriptor >= 0 && !output.empty() && count < maximum_batch; ++count) {
            const auto& packet = output.front();
            const auto result = ::send(descriptor, packet.data(), packet.size(), MSG_DONTWAIT | MSG_NOSIGNAL);
            if(result < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return;
            if(result != static_cast<ssize_t>(packet.size())) { lose("bounded-output-lost"); return; }
            output.pop_front();
        }
#endif
    }
    bool queue(std::string packet) {
        if(descriptor < 0 || output.size() >= maximum_output || packet.size() > maximum_packet) return false;
        output.push_back(std::move(packet)); flush(); return descriptor >= 0;
    }
    std::string scope(std::uint64_t value) const {
        return incarnation + '\t' + app + '\t' + std::to_string(current->epoch) + '\t' + std::to_string(value);
    }
    std::optional<Draft2Event> receive() {
#if defined(__linux__)
        std::array<char, maximum_packet> bytes{};
        alignas(cmsghdr) std::array<unsigned char, CMSG_SPACE(sizeof(ucred)) + CMSG_SPACE(32 * sizeof(int))> control{};
        iovec vector{bytes.data(), bytes.size()};
        msghdr message{}; message.msg_iov = &vector; message.msg_iovlen = 1;
        message.msg_control = control.data(); message.msg_controllen = control.size();
        const auto count = recvmsg(descriptor, &message, MSG_DONTWAIT | MSG_CMSG_CLOEXEC);
        if(count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return {};
        if(count <= 0) { lose("peer-or-channel-lost"); return {}; }
        bool invalid = (message.msg_flags & (MSG_TRUNC | MSG_CTRUNC)) != 0;
        unsigned credentials = 0;
        for(auto* item = CMSG_FIRSTHDR(&message); item; item = CMSG_NXTHDR(&message, item)) {
            if(item->cmsg_level == SOL_SOCKET && item->cmsg_type == SCM_RIGHTS) {
                if(item->cmsg_len >= CMSG_LEN(0)) {
                    const auto length = (item->cmsg_len - CMSG_LEN(0)) / sizeof(int);
                    const auto* received = reinterpret_cast<const int*>(CMSG_DATA(item));
                    for(std::size_t n = 0; n < length; ++n) ::close(received[n]);
                }
                invalid = true;
            } else if(item->cmsg_level == SOL_SOCKET && item->cmsg_type == SCM_CREDENTIALS && item->cmsg_len == CMSG_LEN(sizeof(ucred))) {
                const auto* sender = reinterpret_cast<const ucred*>(CMSG_DATA(item)); ++credentials;
                invalid = invalid || sender->pid != expected_pid || sender->uid != expected_uid;
            } else invalid = true;
        }
        if(invalid || credentials != 1) { lose("sender-identity-or-ancillary-mismatch"); return {}; }
        if(!alive()) { lose("registered-process-exited"); return {}; }
        return accept(std::string(bytes.data(), static_cast<std::size_t>(count)));
#else
        return {};
#endif
    }
    std::optional<Draft2Event> accept(const std::string& packet) {
        const auto words = fields(packet);
        auto invalid = [&]() -> std::optional<Draft2Event> { lose("malformed-or-stale-lifecycle"); return {}; };
        if(words.size() < 2 || words[0] != "TVH2") return invalid();
        const auto& verb = words[1];
        if(verb == "welcome") {
            if(words.size() != 5 || !identifier(words[2]) || words[3] != app || words[4] != capabilities) return invalid();
            if(state != Draft2State::AwaitWelcome) {
                if(incarnation == words[2]) return {};
                return invalid();
            }
            incarnation = words[2]; state = Draft2State::AwaitGrant;
            Draft2Event event; event.kind = Draft2EventKind::Welcome; return event;
        }
        if(state == Draft2State::AwaitWelcome || words.size() < 6 || words[2] != incarnation || words[3] != app) return invalid();
        std::uint64_t epoch = 0, incoming_request = 0;
        if(!decimal(words[4], epoch) || !decimal(words[5], incoming_request)) return invalid();
        if(verb == "grant") {
            if(words.size() != 7 || !identifier(words[6])) return invalid();
            Draft2Grant incoming{incarnation, app, words[6], epoch, incoming_request};
            if(current && same(incoming, *current) && (state == Draft2State::AwaitReady || state == Draft2State::Foreground)) return {};
            if((state != Draft2State::AwaitGrant && state != Draft2State::Hidden) || incoming_request <= highest_request ||
               (current && (epoch <= current->epoch || incoming.lease == current->lease))) return invalid();
            current = incoming; highest_request = incoming_request; request = 0; budget = 0;
            done = cancelled = false; checkpoint.clear(); committed_receipt.clear(); state = Draft2State::AwaitReady;
            Draft2Event event; event.kind = Draft2EventKind::Grant; event.grant = incoming; event.request = incoming_request; return event;
        }
        if(!current || epoch != current->epoch) return invalid();
        Draft2Event event; event.grant = *current; event.request = incoming_request;
        if(verb == "quiesce") {
            std::uint64_t incoming_budget = 0;
            if(words.size() != 7 || !decimal(words[6], incoming_budget) || incoming_budget > 30000) return invalid();
            if(state == Draft2State::Draining && request == incoming_request && budget == incoming_budget) return {};
            if((state != Draft2State::Foreground && state != Draft2State::AwaitReady) || incoming_request <= highest_request) return invalid();
            request = highest_request = incoming_request; budget = static_cast<unsigned>(incoming_budget);
            state = Draft2State::Draining; event.kind = Draft2EventKind::Quiesce; event.budget_ms = budget; return event;
        }
        if(verb == "cancel") {
            if(words.size() != 6 || state != Draft2State::Draining || incoming_request != request) return invalid();
            if(cancelled) return {};
            cancelled = true; event.kind = Draft2EventKind::Cancel; return event;
        }
        if(verb == "commit") {
            if(words.size() != 7 || !identifier(words[6]) || incoming_request != request) return invalid();
            if(state == Draft2State::Hidden && committed_receipt == words[6]) return {};
            if(state != Draft2State::Draining || !done) return invalid();
            committed_receipt = words[6]; state = Draft2State::Hidden;
            event.kind = Draft2EventKind::Commit; event.receipt = committed_receipt; return event;
        }
        if(verb == "failed") {
            if(words.size() != 7 || incoming_request != request ||
               (words[6] != "unsupported" && words[6] != "malformed" && words[6] != "stale" && words[6] != "busy" && words[6] != "expired" &&
                words[6] != "identity-lost" && words[6] != "backend-failed" && words[6] != "completion-unknown")) return invalid();
            event.kind = Draft2EventKind::Failed; event.error = words[6]; lose("authority-failed-" + words[6]); return event;
        }
        return invalid();
    }
};

Draft2Link::Draft2Link(std::unique_ptr<Impl> impl) : impl_(std::move(impl)) {}
Draft2Link::~Draft2Link() = default;
std::unique_ptr<Draft2Link> Draft2Link::adopt(const Draft2Bootstrap& bootstrap, std::string& error, Clock clock) {
    error.clear();
#if defined(__linux__) && defined(SYS_pidfd_open)
    if(bootstrap.descriptor <= 2 || bootstrap.expected_pid <= 0 || !identifier(bootstrap.app_instance)) {
        error = "invalid-trusted-bootstrap"; return {};
    }
    int socket_type = 0, family = 0, old_flags = 0, old_passcred = 0;
    socklen_t length = sizeof(socket_type); ucred creator{}; sockaddr_un peer{};
    if(getsockopt(bootstrap.descriptor, SOL_SOCKET, SO_TYPE, &socket_type, &length) != 0 || socket_type != SOCK_SEQPACKET) {
        error = "expected-sequenced-packet"; return {};
    }
    length = sizeof(family);
    if(getsockopt(bootstrap.descriptor, SOL_SOCKET, SO_DOMAIN, &family, &length) != 0 || family != AF_UNIX) {
        error = "expected-unix-endpoint"; return {};
    }
    length = sizeof(peer);
    if(getpeername(bootstrap.descriptor, reinterpret_cast<sockaddr*>(&peer), &length) != 0) { error = "unconnected-endpoint"; return {}; }
    length = sizeof(creator);
    if(getsockopt(bootstrap.descriptor, SOL_SOCKET, SO_PEERCRED, &creator, &length) != 0 ||
       length != sizeof(creator) || creator.pid <= 0 || creator.uid != bootstrap.creator_uid) { error = "creator-identity-mismatch"; return {}; }
    old_flags = fcntl(bootstrap.descriptor, F_GETFD); length = sizeof(old_passcred);
    if(old_flags < 0 || getsockopt(bootstrap.descriptor, SOL_SOCKET, SO_PASSCRED, &old_passcred, &length) != 0) {
        error = "descriptor-validation-failed"; return {};
    }
    auto impl = std::make_unique<Impl>();
    impl->clock = clock ? std::move(clock) : [] {
        return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count());
    };
    impl->started = impl->last_time = impl->clock();
    impl->pidfd = static_cast<int>(syscall(SYS_pidfd_open, bootstrap.expected_pid, 0));
    if(impl->pidfd < 0 || fcntl(impl->pidfd, F_SETFD, FD_CLOEXEC) != 0 || !impl->alive()) { error = "registered-process-unavailable"; return {}; }
    const int enabled = 1;
    if(setsockopt(bootstrap.descriptor, SOL_SOCKET, SO_PASSCRED, &enabled, sizeof(enabled)) != 0 ||
       fcntl(bootstrap.descriptor, F_SETFD, old_flags | FD_CLOEXEC) != 0) {
        setsockopt(bootstrap.descriptor, SOL_SOCKET, SO_PASSCRED, &old_passcred, sizeof(old_passcred));
        fcntl(bootstrap.descriptor, F_SETFD, old_flags); error = "descriptor-configuration-failed"; return {};
    }
    impl->descriptor = bootstrap.descriptor; impl->expected_pid = bootstrap.expected_pid;
    impl->expected_uid = bootstrap.expected_uid; impl->app = bootstrap.app_instance;
    if(!impl->queue("TVH2\thello\t" + bootstrap.app_instance + '\t' + capabilities)) { error = impl->error; return {}; }
    return std::unique_ptr<Draft2Link>(new Draft2Link(std::move(impl)));
#else
    (void)bootstrap; (void)clock; error = "linux-authenticated-channel-required"; return {};
#endif
}

std::vector<Draft2Event> Draft2Link::pump() {
    std::vector<Draft2Event> events;
    if(impl_->descriptor >= 0) {
        const auto now = impl_->clock();
        if(now < impl_->last_time) impl_->lose("invalid-monotonic-clock");
        else if(impl_->state == Draft2State::AwaitWelcome && now - impl_->started >= 1000000000ULL) impl_->lose("negotiation-expired");
        impl_->last_time = now;
    }
    if(impl_->descriptor >= 0 && !impl_->alive()) impl_->lose("registered-process-exited");
    impl_->flush();
    for(unsigned count = 0; impl_->descriptor >= 0 && count < maximum_batch; ++count) {
        const auto event = impl_->receive();
        if(event) events.push_back(*event);
        else {
#if defined(__linux__)
            // receive() also returns empty for an idempotent packet. A socket
            // readiness check keeps processing bounded without spinning empty.
            pollfd item{impl_->descriptor, POLLIN, 0};
            if(impl_->descriptor < 0 || ::poll(&item, 1, 0) <= 0) break;
#else
            break;
#endif
        }
    }
    if(impl_->lost_event) {
        impl_->lost_event = false; Draft2Event event; event.kind = Draft2EventKind::Lost; event.error = impl_->error;
        if(impl_->current) event.grant = *impl_->current;
        event.request = impl_->request; events.push_back(std::move(event));
    }
    return events;
}
bool Draft2Link::ready() {
    if(impl_->state == Draft2State::Foreground) return true;
    if(impl_->state != Draft2State::AwaitReady || !impl_->current || !impl_->queue("TVH2\tready\t" + impl_->scope(impl_->current->request) + '\t' + impl_->current->lease)) return false;
    impl_->state = Draft2State::Foreground; return true;
}
bool Draft2Link::local_done(const std::string& checkpoint) {
    if(impl_->state != Draft2State::Draining || (checkpoint != "none" && !identifier(checkpoint))) return false;
    if(impl_->done) return impl_->checkpoint == checkpoint;
    if(!impl_->queue("TVH2\tlocal-done\t" + impl_->scope(impl_->request) + '\t' + checkpoint)) return false;
    impl_->done = true; impl_->checkpoint = checkpoint; return true;
}
bool Draft2Link::progress(const std::string& phase) {
    if(impl_->state != Draft2State::Draining || (phase != "local" && phase != "graph" && phase != "display" && phase != "audio" && phase != "stop-post")) return false;
    return impl_->queue("TVH2\tquiescing\t" + impl_->scope(impl_->request) + '\t' + phase);
}
void Draft2Link::close() noexcept {
    impl_->state = Draft2State::Lost; impl_->output.clear(); impl_->release();
}
Draft2State Draft2Link::state() const noexcept { return impl_->state; }
const std::optional<Draft2Grant>& Draft2Link::grant() const noexcept { return impl_->current; }
std::uint64_t Draft2Link::drain_request() const noexcept { return impl_->request; }
bool Draft2Link::connected() const noexcept { return impl_->descriptor >= 0; }
bool Draft2Link::pending_output() const noexcept { return !impl_->output.empty(); }
int Draft2Link::fd() const noexcept { return impl_->descriptor; }
int Draft2Link::peer_pidfd() const noexcept { return impl_->pidfd; }
const std::string& Draft2Link::last_error() const noexcept { return impl_->error; }

} // namespace tyrell::video
