#pragma once

#include "chess_clock.h"
#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>

namespace chess {
enum class LanRole { Host, Joiner };
enum class LanEndReason {
    ShellPause, ShellLost, LinkedTermination, ServiceGap, StaleAuthority,
    TransportError, UserLeft
};

// Local history only: neither a seal nor a queued request establishes what the
// peer received. In particular, interruption is separate from the game result.
struct LanInterruption {
    LanEndReason reason=LanEndReason::ShellPause;
    uint64_t observedAtMs=0;
    bool hadMatch=false,pendingMoveRequest=false;
    std::string pendingMove;
    size_t pendingPly=0;
    uint64_t pendingQueuedAtMs=0;
    ChessClockSnapshot clock;
    std::string confirmedResult="*",confirmedReason;
};

// Opt-in controller primitive. Production shell/loop integration is deferred;
// constructing an App does not enable this policy or admit linked LAN play.
class LanTerminal {
public:
    static constexpr uint64_t service_gap_limit_ms=10000;
    static constexpr uint64_t stale_authority_limit_ms=10000;
    bool begun() const {return sessionId_!=0;}
    bool active() const {return begun()&&!sealed_;}
    bool sealed() const {return sealed_;}
    bool teardown_pending() const {return teardownPending_;}
    uint64_t session_id() const {return sessionId_;}
    LanRole role() const {return role_;}
    const LanInterruption& interruption() const {return interruption_;}
    bool begin(LanRole role) {
        if(active()||teardownPending_||sessionId_==UINT64_MAX)return false;
        ++sessionId_;role_=role;sealed_=false;interruption_=LanInterruption{};
        return true;
    }
    bool latch(LanInterruption interruption) {
        if(!active())return false;
        sealed_=teardownPending_=true;interruption_=std::move(interruption);
        return true;
    }
    void retain(const ChessClockSnapshot& clock,const std::string& result,const std::string& reason) {
        interruption_.clock=clock;interruption_.confirmedResult=result;interruption_.confirmedReason=reason;
    }
    void teardown_complete() {if(sealed_)teardownPending_=false;}
private:
    uint64_t sessionId_=0;
    LanRole role_=LanRole::Host;
    bool sealed_=false,teardownPending_=false;
    LanInterruption interruption_;
};
} // namespace chess
