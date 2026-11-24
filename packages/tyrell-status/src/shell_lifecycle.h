#pragma once

#include <tyrell/app/shell_link.hpp>

namespace tyrell::status {
// App policy, separate from the transport. Hooks run synchronously on the GUI
// thread. No worker can keep reading input or flushing after pause returns.
class ShellLifecycle {
public:
    bool foreground() const { return foreground_; }
    bool exiting() const { return exiting_; }
    bool hello_seen() const { return hello_seen_; }
    template<class Link, class Hooks>
    void handle(app::ShellEvent event, Link& link, Hooks& hooks) {
        if(exiting_) return;
        switch(event) {
            case app::ShellEvent::Hello: hello_seen_ = true; break;
            case app::ShellEvent::Pause:
                if(foreground_) {
                    foreground_ = false;
                    hooks.pause();
                }
                link.acknowledge_paused();
                break;
            case app::ShellEvent::Resume:
                link.cancel_paused();
                if(!foreground_) {
                    if(hooks.resume()) foreground_ = true;
                    else { exiting_ = true; hooks.pause(); }
                }
                break;
            case app::ShellEvent::Redraw: hooks.redraw(); break;
            case app::ShellEvent::Lost:
                foreground_ = false;
                exiting_ = true;
                link.cancel_paused();
                hooks.pause();
                break;
            case app::ShellEvent::None: case app::ShellEvent::Ignored: break;
        }
    }
private:
    bool foreground_ = true;
    bool exiting_ = false;
    bool hello_seen_ = false;
};
} // namespace tyrell::status
