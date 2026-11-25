#pragma once

#include <tyrell/app/shell_link.hpp>

namespace tanks {
// Sandline policy is deliberately offline-only. Transport acknowledgments
// follow input/render release; they do not promise a kernel/device deadline.
class OfflineLifecycle {
public:
    bool foreground() const { return foreground_; }
    bool exiting() const { return exiting_; }
    bool hello_seen() const { return hello_seen_; }
    template<class Link, class Hooks>
    void handle(tyrell::app::ShellEvent event, Link& link, Hooks& hooks) {
        if(exiting_) return;
        switch(event) {
        case tyrell::app::ShellEvent::Hello: hello_seen_ = true; break;
        case tyrell::app::ShellEvent::Pause:
            if(foreground_) { foreground_ = false; hooks.pause(); }
            link.acknowledge_paused(); break;
        case tyrell::app::ShellEvent::Resume:
            link.cancel_paused();
            if(!foreground_) {
                if(hooks.resume()) foreground_ = true;
                else { exiting_ = true; hooks.abandon(); }
            }
            break;
        case tyrell::app::ShellEvent::Redraw: hooks.redraw(); break;
        case tyrell::app::ShellEvent::Lost:
            foreground_ = false; exiting_ = true; link.cancel_paused(); hooks.abandon(); break;
        case tyrell::app::ShellEvent::None: case tyrell::app::ShellEvent::Ignored: break;
        }
    }
private:
    bool foreground_ = true, exiting_ = false, hello_seen_ = false;
};

// A full batch asks the caller to drain again before input, update or drawing.
// This prevents a pause behind unknown traffic from being stranded by a frame.
template<class Link, class Hooks>
bool pump_lifecycle(Link& link, OfflineLifecycle& state, Hooks& hooks) {
    bool drained = false;
    for(unsigned n = 0; n < 32; ++n) {
        const auto event = link.next_event();
        if(event == tyrell::app::ShellEvent::None) { drained = true; break; }
        state.handle(event, link, hooks);
        if(state.exiting()) break;
    }
    link.service();
    if(link.closed()) state.handle(tyrell::app::ShellEvent::Lost, link, hooks);
    return drained;
}
} // namespace tanks
