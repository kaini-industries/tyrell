#pragma once

#include <memory>
#include <string>

namespace tyrell::game {

enum class Backend { Default, Desktop, Native, Headless };

struct RuntimeOptions {
    int width=320, height=170;
    std::string title="Tyrell Game";
    Backend backend=Backend::Default;
    double fps=30; // Zero disables pacing; otherwise finite [1,1000]. Steady-clock elapsed time.
    int window_scale=3;
    bool integer_scale=true;
    std::string framebuffer="auto", input="auto";
    // Normal M5 close restores its prelaunch frame. A launcher that owns its
    // own restoration must disable this before any native device acquisition,
    // including so failed construction cannot repaint a different owner.
    bool restore_framebuffer_on_close=true;
};

// Owns raylib's process-global graphics context. Construct one Runtime at a
// time, and call it only on its owning/main thread. Game code uses raylib draw
// calls between begin_frame() and end_frame(); poll() precedes input queries.
// Desktop builds use a scaled render texture. Software builds support a native
// 320x170 LCD or configurable headless frames without opening device nodes.
// Unsupported backend combinations fail before creating a graphics context.
class Runtime {
public:
    explicit Runtime(RuntimeOptions options={});
    ~Runtime();
    Runtime(const Runtime&)=delete;
    Runtime& operator=(const Runtime&)=delete;
    Runtime(Runtime&&)=delete;
    Runtime& operator=(Runtime&&)=delete;

    void poll();
    bool closed() const;
    bool pressed(int key) const;
    bool down(int key) const;
    bool released(int key) const;
    // Drain after poll(): preserves the order of taps received between frames.
    // Native backend excludes autorepeat; zero means no more keys this frame.
    int next_key();
    int character();
    double time() const;
    double elapsed() const;
    // Relinquish input/display ownership without changing the monotonic clock.
    // An open frame is cancelled, never presented. Calls are main-thread only.
    void suspend();
    // Reopen/reset native input and request a complete frame. Failure leaves
    // ownership suspended. Syscalls can block; this is not a realtime promise.
    bool resume(std::string* error=nullptr);
    // Terminal ownership loss: close native descriptors without restoring the
    // saved prelaunch screen. resume() can never reclaim an abandoned runtime.
    void abandon_foreground() noexcept;
    bool suspended() const;
    bool foreground() const;
    void request_redraw();
    bool redraw_requested() const;
    void begin_frame();
    void end_frame();
    // Exports the logical rendered frame, not desktop letterboxing/window chrome.
    void screenshot(const std::string& path);
    int width() const;
    int height() const;
    Backend backend() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace tyrell::game
