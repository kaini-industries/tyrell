#pragma once

#include <tyrell/app/shell_link.hpp>

namespace chess {
// Transport-independent app policy. Hooks are synchronous on the UI thread;
// pause must release input/rendering before the acknowledgment is queued.
class OfflineLifecycle {
public:
    bool foreground() const { return foreground_; }
    bool exiting() const { return exiting_; }
    bool hello_seen() const { return hello_seen_; }
    template<class Link,class Hooks>
    void handle(tyrell::app::ShellEvent event,Link& link,Hooks& hooks) {
        if(exiting_)return;
        switch(event) {
        case tyrell::app::ShellEvent::Hello: hello_seen_=true;break;
        case tyrell::app::ShellEvent::Pause:
            if(foreground_) {foreground_=false;hooks.pause();}
            link.acknowledge_paused();break;
        case tyrell::app::ShellEvent::Resume:
            link.cancel_paused();
            if(!foreground_) {
                if(hooks.resume())foreground_=true;
                else {exiting_=true;hooks.abandon();}
            }
            break;
        case tyrell::app::ShellEvent::Redraw:
            hooks.redraw();break;
        case tyrell::app::ShellEvent::Lost:
            foreground_=false;exiting_=true;link.cancel_paused();hooks.abandon();break;
        case tyrell::app::ShellEvent::None: case tyrell::app::ShellEvent::Ignored:break;
        }
    }
private:
    bool foreground_=true,exiting_=false,hello_seen_=false;
};

// Returning false means a bounded batch was consumed; immediately try again
// before drawing or waiting so a pause behind unknown traffic is not stranded.
template<class Link,class Hooks>
bool pump_lifecycle(Link& link,OfflineLifecycle& state,Hooks& hooks) {
    bool drained=false;
    for(unsigned n=0;n<32;++n) {
        const auto event=link.next_event();
        if(event==tyrell::app::ShellEvent::None){drained=true;break;}
        state.handle(event,link,hooks);
        if(state.exiting())break;
    }
    link.service();
    if(link.closed())state.handle(tyrell::app::ShellEvent::Lost,link,hooks);
    return drained;
}
} // namespace chess
