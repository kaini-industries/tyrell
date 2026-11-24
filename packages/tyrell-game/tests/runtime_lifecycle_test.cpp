#include "tyrell/game/runtime.hpp"
#include "raylib.h"

#include <chrono>
#include <cmath>
#include <csignal>
#include <iostream>
#include <stdexcept>
#include <thread>

namespace {
void require(bool value,const char* message) {if(!value)throw std::runtime_error(message);}
template<class Function> void rejects(Function function,const char* message) {
    try {function();} catch(const std::logic_error&) {return;}
    throw std::runtime_error(message);
}
tyrell::game::RuntimeOptions options() {
    tyrell::game::RuntimeOptions result;
    result.backend=tyrell::game::Backend::Headless;
    result.width=64;result.height=48;result.fps=0;
    return result;
}
void frame(tyrell::game::Runtime& runtime) {
    runtime.begin_frame();ClearBackground(RED);runtime.end_frame();
}
}
int main() {
    using namespace tyrell::game;
    using namespace std::chrono;
    try {
        SetTraceLogLevel(LOG_WARNING);
        {
            Runtime runtime(options());
            require(runtime.foreground()&&!runtime.suspended()&&runtime.redraw_requested(),"Initial ownership/redraw state");
            runtime.poll();frame(runtime);
            require(!runtime.redraw_requested(),"Successful full frame did not clear redraw request");
            runtime.request_redraw();
            runtime.begin_frame();ClearBackground(BLUE);
            runtime.suspend(); // Cancels, never presents, the incomplete frame.
            require(!runtime.foreground()&&runtime.suspended()&&runtime.redraw_requested(),"Suspension ownership/redraw state");
            rejects([&]{runtime.end_frame();},"Cancelled frame could still end/present");
            rejects([&]{runtime.begin_frame();},"Hidden runtime accepted drawing");
            require(!runtime.down(KEY_W)&&!runtime.pressed(KEY_W)&&!runtime.released(KEY_W)&&
                    runtime.next_key()==0&&runtime.character()==0,"Hidden runtime leaked input");
            const auto before=runtime.time();
            std::this_thread::sleep_for(milliseconds(70));
            std::string error;
            require(runtime.resume(&error)&&error.empty(),"Headless resume failed");
            runtime.poll();
            require(runtime.elapsed()>=.06&&runtime.time()-before>=.06,"Suspension gap was reset or clamped");
            require(runtime.redraw_requested(),"Resume did not request a complete frame");
            frame(runtime);
            require(!runtime.redraw_requested()&&runtime.resume(),"Foreground resume is not idempotent");
            runtime.suspend();runtime.suspend();
            runtime.abandon_foreground();runtime.abandon_foreground();
            require(runtime.closed()&&runtime.suspended()&&!runtime.foreground(),"Abandon did not terminate ownership");
            require(!runtime.resume(&error)&&!error.empty(),"Abandoned runtime reclaimed foreground");
            rejects([&]{runtime.begin_frame();},"Abandoned runtime accepted drawing");
        }
        {
            Runtime runtime(options());
            runtime.suspend(); // Include the gap even before a first poll.
            std::this_thread::sleep_for(milliseconds(30));
            require(runtime.resume(),"Initial resume failed");runtime.poll();
            require(runtime.elapsed()>=.02,"First poll hid the suspension interval");
        }
        {
            Runtime runtime(options());
            std::thread worker([] {std::raise(SIGTERM);});
            worker.join();
            require(runtime.closed(),"Signal on another thread was not observed by owner");
        }
        require(!IsWindowReady(),"Lifecycle left a raylib context active");
        std::cout<<"Runtime lifecycle/monotonic clock tests passed\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr<<"Runtime lifecycle: "<<error.what()<<'\n';return 1;
    }
}
