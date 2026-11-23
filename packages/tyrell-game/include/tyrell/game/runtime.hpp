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
