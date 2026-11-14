#include "tyrell/game/runtime.hpp"
#include "raylib.h"
#include <chrono>
#include <csignal>
#include <filesystem>
#include <iostream>
#include <limits>
#include <stdexcept>

namespace {
void require(bool value,const char* message) {if(!value) throw std::runtime_error(message);}
template<class Function> void rejects(Function function,const char* message) {
    try {function();} catch(const std::exception&) {return;}
    throw std::runtime_error(message);
}
volatile std::sig_atomic_t original_signal_count=0;
void original_handler(int) {++original_signal_count;}
struct Directory {
    std::filesystem::path path;
    Directory() {
        path=std::filesystem::temp_directory_path()/
            ("tyrell-runtime-test-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        if(!std::filesystem::create_directory(path)) throw std::runtime_error("Cannot create runtime test directory");
    }
    ~Directory() {std::error_code ignored;std::filesystem::remove_all(path,ignored);}
};
tyrell::game::RuntimeOptions options() {
    tyrell::game::RuntimeOptions options;
    options.backend=tyrell::game::Backend::Headless;
    options.width=96;options.height=64;options.fps=0;
    return options;
}
void render(tyrell::game::Runtime& runtime) {
    runtime.poll();runtime.begin_frame();
    ClearBackground({220,30,40,255});DrawRectangle(0,0,runtime.width(),12,{20,40,220,255});
    DrawText("TYRELL",2,25,10,{240,230,200,255});
    runtime.end_frame();
}
}
int main() {
    using namespace tyrell::game;
    try {
        SetTraceLogLevel(LOG_WARNING);
        Directory directory;
        auto invalid=options();invalid.width=0;
        rejects([&] {Runtime runtime(invalid);},"Zero width accepted");
        invalid=options();invalid.fps=std::numeric_limits<double>::quiet_NaN();
        rejects([&] {Runtime runtime(invalid);},"NaN frame rate accepted");
        invalid=options();invalid.fps=1e-300;
        rejects([&] {Runtime runtime(invalid);},"Unsafe tiny positive frame rate accepted");
        invalid=options();invalid.backend=Backend::Native;
        rejects([&] {Runtime runtime(invalid);},"Unsupported native LCD geometry accepted");
        invalid=options();invalid.backend=Backend::Desktop;
        rejects([&] {Runtime runtime(invalid);},"Desktop backend accepted by a software runtime build");
        require(!IsWindowReady(),"Invalid options allocated a graphics context");
#ifdef __linux__
        auto failed_native=options();failed_native.backend=Backend::Native;
        failed_native.width=320;failed_native.height=170;
        failed_native.framebuffer="/definitely/missing/tyrell-framebuffer";failed_native.input="none";
        rejects([&] {Runtime runtime(failed_native);},"Missing native framebuffer was accepted");
        require(!IsWindowReady(),"Failed native device construction leaked its graphics context");
#endif
        InitWindow(32,24,"External context ownership test");
        require(IsWindowReady(),"Cannot initialize external context for ownership check");
        rejects([&] {Runtime runtime(options());},"Runtime accepted an externally owned raylib context");
        require(IsWindowReady(),"Rejected runtime closed an externally owned context");
        CloseWindow();

        {
            Runtime runtime(options());
            require(runtime.width()==96 && runtime.height()==64 && runtime.backend()==Backend::Headless,
                    "Runtime ignores configured headless geometry/backend");
            rejects([&] {Runtime second(options());},"A second runtime stole the process graphics context");
            require(IsWindowReady(),"Rejected second runtime closed the first context");
            rejects([&] {runtime.screenshot((directory.path/"early.png").string());},"Screenshot before rendering accepted");
            rejects([&] {runtime.end_frame();},"Unpaired end_frame accepted");
            runtime.poll();
            require(!runtime.closed() && runtime.character()==0 && !runtime.down(KEY_W) && !runtime.pressed(KEY_W),
                    "Headless runtime unexpectedly reads input/devices");
            runtime.begin_frame();
            rejects([&] {runtime.begin_frame();},"Nested frame accepted");
            rejects([&] {runtime.poll();},"Polling input during rendering accepted");
            ClearBackground(RED);runtime.end_frame();
            render(runtime);
            const auto image_path=directory.path/"frame.png";
            runtime.screenshot(image_path.string());
            Image image=LoadImage(image_path.string().c_str());
            require(IsImageValid(image) && image.width==96 && image.height==64,"Logical frame screenshot has wrong geometry");
            const Color top=GetImageColor(image,80,3),bottom=GetImageColor(image,80,60);
            UnloadImage(image);
            require(top.b>top.r && bottom.r>bottom.b,"Headless screenshot reverses rows or color channels");
            const auto before=runtime.time();runtime.poll();
            require(runtime.time()>=before && runtime.elapsed()>=0,"Runtime steady clock regressed");
        }
        require(!IsWindowReady(),"Runtime destructor leaked the raylib context");
        // Scope failure while a frame is open must not poison the next context.
        try {Runtime runtime(options());runtime.begin_frame();throw std::runtime_error("consumer failure");}
        catch(const std::runtime_error&) {}
        {Runtime recovered(options());render(recovered);}
        require(!IsWindowReady(),"Exceptional frame teardown leaked the graphics context");
        {
            auto paced=options();paced.fps=60;
            Runtime runtime(paced);
            const auto started=std::chrono::steady_clock::now();
            for(int i=0;i<3;++i) render(runtime);
            const auto seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count();
            require(seconds>=.035,"Runtime ignored configured frame pacing");
            runtime.poll();require(runtime.elapsed()>0,"Paced elapsed time did not advance");
        }

        const auto old_term=std::signal(SIGTERM,original_handler);
        {
            Runtime runtime(options());render(runtime);
            std::raise(SIGTERM);
            require(runtime.closed(),"SIGTERM did not request a clean runtime exit");
        }
        std::raise(SIGTERM);
        require(original_signal_count==1,"Runtime did not restore the caller's signal handler");
        std::signal(SIGTERM,old_term);
#ifdef SIGHUP
        const auto old_hup=std::signal(SIGHUP,original_handler);
        {
            Runtime runtime(options());std::raise(SIGHUP);
            require(runtime.closed(),"SIGHUP did not request a clean runtime exit");
        }
        std::raise(SIGHUP);
        require(original_signal_count==2,"Runtime did not restore the SIGHUP handler");
        std::signal(SIGHUP,old_hup);
#endif
        std::cout<<"Tyrell headless runtime tests passed\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr<<"runtime test: "<<error.what()<<'\n';
        return 1;
    }
}
