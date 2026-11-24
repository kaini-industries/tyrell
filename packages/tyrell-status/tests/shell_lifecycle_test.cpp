#include "shell_lifecycle.h"
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

using tyrell::app::ShellEvent;
void check(bool value) { if(!value) { std::cerr << "shell lifecycle contract failed\n"; std::exit(1); } }
struct Harness {
    std::vector<std::string> calls;
    bool input = true, allow_resume = true, pending = false;
    void pause() { input = false; calls.push_back("release"); }
    bool resume() { calls.push_back("acquire"); input = allow_resume; return allow_resume; }
    void redraw() { calls.push_back("dirty"); }
    void acknowledge_paused() { check(!input); pending = true; calls.push_back("ack"); }
    void cancel_paused() { pending = false; calls.push_back("cancel"); }
};
int main() {
    tyrell::status::ShellLifecycle state;
    Harness h;
    state.handle(ShellEvent::Hello, h, h);
    check(state.hello_seen() && state.foreground());
    state.handle(ShellEvent::Resume, h, h);
    check(h.calls == std::vector<std::string>{"cancel"});
    state.handle(ShellEvent::Pause, h, h);
    check(!state.foreground() && h.pending);
    check(h.calls == std::vector<std::string>({"cancel", "release", "ack"}));
    state.handle(ShellEvent::Pause, h, h);
    check(h.calls.back() == "ack" && h.calls.size() == 4);
    state.handle(ShellEvent::Redraw, h, h);
    check(!h.input && !state.foreground() && h.calls.back() == "dirty");
    state.handle(ShellEvent::Ignored, h, h);
    state.handle(ShellEvent::Resume, h, h);
    check(state.foreground() && h.input && !h.pending);
    check(h.calls[h.calls.size() - 2] == "cancel" && h.calls.back() == "acquire");
    state.handle(ShellEvent::Lost, h, h);
    check(state.exiting() && !state.foreground() && !h.input);
    const auto calls = h.calls;
    state.handle(ShellEvent::Resume, h, h);
    check(h.calls == calls && !h.input);

    tyrell::status::ShellLifecycle hidden;
    Harness hidden_hooks;
    hidden.handle(ShellEvent::Pause, hidden_hooks, hidden_hooks);
    hidden.handle(ShellEvent::Lost, hidden_hooks, hidden_hooks);
    check(hidden.exiting() && !hidden.foreground() && !hidden_hooks.input);

    tyrell::status::ShellLifecycle failure;
    Harness failed;
    failed.allow_resume = false;
    failure.handle(ShellEvent::Pause, failed, failed);
    failure.handle(ShellEvent::Resume, failed, failed);
    check(failure.exiting() && !failure.foreground() && !failed.input && !failed.pending);
}
