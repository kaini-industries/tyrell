#include "tyrell/game/runtime.hpp"
#include "tyrell/game/input.hpp"
#include "native_backend.hpp"
#include "raylib.h"
#include "rlgl.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <stdexcept>
#include <thread>
#include <utility>
#include <vector>

namespace tyrell::game {
namespace {
using Clock=std::chrono::steady_clock;
// raylib is process-global. The framework's rendering/input API is main-thread
// only, so this guard also catches accidental mixed direct/framework ownership.
bool context_owned=false;
volatile std::sig_atomic_t interrupted=0;
void interrupt(int) { interrupted=1; }

class ContextLease {
public:
    ContextLease() {
        if(context_owned || IsWindowReady())
            throw std::logic_error("Only one Tyrell runtime/raylib context may be active");
        context_owned=true;
    }
    ~ContextLease() { context_owned=false; }
};

class SignalGuard {
    using Handler=void(*)(int);
    Handler old_int_=SIG_DFL,old_term_=SIG_DFL;
#ifdef SIGHUP
    Handler old_hup_=SIG_DFL;
#endif
public:
    SignalGuard() {
        interrupted=0;
        old_int_=std::signal(SIGINT,interrupt);old_term_=std::signal(SIGTERM,interrupt);
#ifdef SIGHUP
        old_hup_=std::signal(SIGHUP,interrupt);
#endif
    }
    ~SignalGuard() {
        std::signal(SIGINT,old_int_);std::signal(SIGTERM,old_term_);
#ifdef SIGHUP
        std::signal(SIGHUP,old_hup_);
#endif
    }
};

RuntimeOptions validate(RuntimeOptions options) {
    if(options.backend==Backend::Default) {
#if defined(TYRELL_GAME_HEADLESS_DEFAULT)
        options.backend=Backend::Headless;
#elif defined(TYRELL_GAME_SOFTWARE)
        options.backend=Backend::Native;
#else
        options.backend=Backend::Desktop;
#endif
    }
#if defined(TYRELL_GAME_SOFTWARE)
    if(options.backend!=Backend::Native && options.backend!=Backend::Headless)
        throw std::invalid_argument("This Tyrell graphics build supports Native and Headless runtimes");
#else
    if(options.backend!=Backend::Desktop)
        throw std::invalid_argument("Native/Headless runtimes require the Tyrell software graphics build");
#endif
    if(options.width<1 || options.height<1 || options.width>8192 || options.height>8192 ||
        std::uint64_t(options.width)*options.height>16777216)
        throw std::invalid_argument("Runtime logical resolution is out of range");
    if(!std::isfinite(options.fps) || options.fps<0 || options.fps>1000 ||
        (options.fps>0 && options.fps<1))
        throw std::invalid_argument("Runtime FPS must be zero (unpaced) or finite in [1, 1000]");
    if(options.window_scale<1 || options.window_scale>16 ||
        options.width*options.window_scale>32768 || options.height*options.window_scale>32768)
        throw std::invalid_argument("Runtime desktop window scale is out of range");
    if(options.backend==Backend::Native && (options.width!=320 || options.height!=170))
        throw std::invalid_argument("The CardputerZero native LCD requires 320x170 logical pixels");
#if !defined(__linux__)
    if(options.backend==Backend::Native)
        throw std::invalid_argument("The native CardputerZero runtime requires Linux");
#endif
    return options;
}
}

struct Runtime::Impl {
    // Destruction order keeps signal/context guards alive until devices restore
    // their screen and the raylib context has been released.
    ContextLease context;
    SignalGuard signals;
    RuntimeOptions options;
    NativeInputState input;
    std::unique_ptr<detail::NativeDevices> native;
    std::vector<std::uint8_t> rgba;
    std::vector<std::uint16_t> rgb;
    RenderTexture2D target{};
    bool window_ready=false,drawing=false,has_frame=false,first_poll=true;
    Clock::time_point started{},last_poll{},next_frame{};
    double delta=0;

    explicit Impl(RuntimeOptions selected):options(std::move(selected)) {
        try {
#if defined(TYRELL_GAME_SOFTWARE)
            InitWindow(options.width,options.height,options.title.c_str());
#else
            SetConfigFlags(FLAG_WINDOW_RESIZABLE);
            InitWindow(options.width*options.window_scale,options.height*options.window_scale,
                       options.title.c_str());
#endif
            window_ready=IsWindowReady();
            if(!window_ready) throw std::runtime_error("Cannot initialize Tyrell graphics context");
#if defined(TYRELL_GAME_SOFTWARE)
            if(rlGetVersion()!=RL_OPENGL_SOFTWARE)
                throw std::runtime_error("Tyrell software runtime linked to incompatible raylib backend");
            rgba.resize(std::size_t(options.width)*options.height*4);
            if(options.backend==Backend::Native) {
                rgb.resize(std::size_t(options.width)*options.height);
                native=std::make_unique<detail::NativeDevices>(options.framebuffer,options.input,input);
            }
#else
            SetExitKey(KEY_NULL);SetTargetFPS(0); // Runtime owns steady-clock pacing.
            SetWindowMinSize(options.width,options.height);
            target=LoadRenderTexture(options.width,options.height);
            if(!IsRenderTextureValid(target)) throw std::runtime_error("Cannot allocate Tyrell render texture");
            SetTextureFilter(target.texture,TEXTURE_FILTER_POINT);
#endif
            started=last_poll=next_frame=Clock::now();
            delta=options.fps>0?1/options.fps:0;
        } catch(...) { release();throw; }
    }
    ~Impl() { release(); }
    void release() noexcept {
        if(drawing) {
#if defined(TYRELL_GAME_SOFTWARE)
            EndDrawing();
#else
            EndTextureMode();
#endif
            drawing=false;
        }
        native.reset(); // Restore the native screen before closing raylib.
        if(target.id) {UnloadRenderTexture(target);target={};}
        if(window_ready) {CloseWindow();window_ready=false;}
    }
    void capture() {
#if defined(TYRELL_GAME_SOFTWARE)
        // rlsw exports top-first RGBA with its BGRA output disabled.
        rlCopyFramebuffer(0,0,options.width,options.height,PIXELFORMAT_UNCOMPRESSED_R8G8B8A8,rgba.data());
#endif
    }
    void pace() {
        if(options.fps==0) return;
        next_frame+=std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(1/options.fps));
        const auto now=Clock::now();
        if(now<next_frame) std::this_thread::sleep_until(next_frame);
        else next_frame=now; // Drop pacing debt after a stall.
    }
};

Runtime::Runtime(RuntimeOptions options):impl_(std::make_unique<Impl>(validate(std::move(options)))) {}
Runtime::~Runtime()=default;
void Runtime::poll() {
    if(impl_->drawing) throw std::logic_error("Poll input before beginning the frame");
    const auto now=Clock::now();
    if(impl_->first_poll) impl_->first_poll=false;
    else impl_->delta=std::chrono::duration<double>(now-impl_->last_poll).count();
    impl_->last_poll=now;
#if defined(TYRELL_GAME_SOFTWARE)
    impl_->input.begin_frame();
    if(impl_->native) impl_->native->poll(impl_->input);
#endif
}
bool Runtime::closed() const {
#if defined(TYRELL_GAME_SOFTWARE)
    return interrupted!=0;
#else
    return interrupted!=0 || WindowShouldClose();
#endif
}
bool Runtime::pressed(int key) const {
#if defined(TYRELL_GAME_SOFTWARE)
    return impl_->input.key_pressed(key);
#else
    return IsKeyPressed(key);
#endif
}
bool Runtime::down(int key) const {
#if defined(TYRELL_GAME_SOFTWARE)
    return impl_->input.key_down(key);
#else
    return IsKeyDown(key);
#endif
}
bool Runtime::released(int key) const {
#if defined(TYRELL_GAME_SOFTWARE)
    return impl_->input.key_released(key);
#else
    return IsKeyReleased(key);
#endif
}
int Runtime::next_key() {
#if defined(TYRELL_GAME_SOFTWARE)
    return impl_->input.next_key();
#else
    return GetKeyPressed();
#endif
}
int Runtime::character() {
#if defined(TYRELL_GAME_SOFTWARE)
    return impl_->input.next_char();
#else
    return GetCharPressed();
#endif
}
double Runtime::time() const {return std::chrono::duration<double>(Clock::now()-impl_->started).count();}
double Runtime::elapsed() const {return impl_->delta;}
void Runtime::begin_frame() {
    if(impl_->drawing) throw std::logic_error("A Tyrell frame is already in progress");
#if defined(TYRELL_GAME_SOFTWARE)
    BeginDrawing();
#else
    BeginTextureMode(impl_->target);
#endif
    impl_->drawing=true;
}
void Runtime::end_frame() {
    if(!impl_->drawing) throw std::logic_error("Begin a Tyrell frame before ending it");
#if defined(TYRELL_GAME_SOFTWARE)
    EndDrawing();
    impl_->drawing=false;
    if(impl_->native) {
        impl_->capture();
        for(std::size_t i=0;i<impl_->rgb.size();++i) {
            const auto* p=impl_->rgba.data()+i*4;
            impl_->rgb[i]=std::uint16_t(((p[0]&0xf8)<<8)|((p[1]&0xfc)<<3)|(p[2]>>3));
        }
        impl_->native->present(impl_->rgb);
    }
#else
    EndTextureMode();impl_->drawing=false;
    BeginDrawing();ClearBackground({30,33,31,255});
    float scale=std::min(GetScreenWidth()/float(impl_->options.width),GetScreenHeight()/float(impl_->options.height));
    if(impl_->options.integer_scale && scale>=1) scale=std::floor(scale);
    const float width=impl_->options.width*scale,height=impl_->options.height*scale;
    DrawTexturePro(impl_->target.texture,{0,0,float(impl_->options.width),-float(impl_->options.height)},
        {(GetScreenWidth()-width)/2,(GetScreenHeight()-height)/2,width,height},{0,0},0,WHITE);
    EndDrawing();
#endif
    impl_->has_frame=true;
    impl_->pace();
}
void Runtime::screenshot(const std::string& path) {
    if(impl_->drawing || !impl_->has_frame)
        throw std::logic_error("Screenshot requires a completed Tyrell frame");
    if(path.empty()) throw std::invalid_argument("Screenshot path is empty");
#if defined(TYRELL_GAME_SOFTWARE)
    impl_->capture();
    const Image image{impl_->rgba.data(),impl_->options.width,impl_->options.height,1,PIXELFORMAT_UNCOMPRESSED_R8G8B8A8};
    if(!ExportImage(image,path.c_str())) throw std::runtime_error("Cannot write Tyrell screenshot: "+path);
#else
    Image image=LoadImageFromTexture(impl_->target.texture);
    if(!IsImageValid(image)) throw std::runtime_error("Cannot read Tyrell render texture");
    ImageFlipVertical(&image);
    const bool saved=ExportImage(image,path.c_str());UnloadImage(image);
    if(!saved) throw std::runtime_error("Cannot write Tyrell screenshot: "+path);
#endif
}
int Runtime::width() const {return impl_->options.width;}
int Runtime::height() const {return impl_->options.height;}
Backend Runtime::backend() const {return impl_->options.backend;}

} // namespace tyrell::game
