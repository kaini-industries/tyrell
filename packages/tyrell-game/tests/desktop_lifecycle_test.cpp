#include "tyrell/game/runtime.hpp"
#include "raylib.h"

#include <iostream>
#include <stdexcept>

namespace {
void require(bool value,const char* message) {if(!value)throw std::runtime_error(message);}
void frame(tyrell::game::Runtime& runtime) {
    runtime.poll();runtime.begin_frame();ClearBackground(BLUE);runtime.end_frame();
}
}
int main() {
    using namespace tyrell::game;
    try {
        SetTraceLogLevel(LOG_WARNING);
        RuntimeOptions options;options.backend=Backend::Desktop;
        options.width=96;options.height=64;options.window_scale=1;options.fps=0;
        {
            Runtime runtime(options);frame(runtime);
            require(runtime.foreground()&&!runtime.redraw_requested(),"Desktop first frame failed");
            runtime.suspend();runtime.poll();
            require(runtime.suspended()&&!runtime.foreground()&&runtime.next_key()==0&&runtime.character()==0&&
                    !runtime.down(KEY_W)&&!runtime.pressed(KEY_W),"Desktop suspension exposed input/ownership");
            bool blocked=false;try{runtime.begin_frame();}catch(const std::logic_error&){blocked=true;}
            require(blocked,"Desktop hidden frame was accepted");
            require(runtime.resume()&&runtime.redraw_requested(),"Desktop resume omitted full redraw");
            frame(runtime);require(!runtime.redraw_requested(),"Desktop redraw did not complete");
            runtime.abandon_foreground();
            require(runtime.closed()&&!runtime.resume(),"Desktop abandoned runtime reclaimed ownership");
        }
        require(!IsWindowReady(),"Desktop lifecycle leaked its window");
        std::cout<<"Desktop lifecycle smoke passed\n";return 0;
    } catch(const std::exception& error) {std::cerr<<"Desktop lifecycle: "<<error.what()<<'\n';return 1;}
}
