// Exercise the production native bridge and Runtime through fake syscalls.
#include "native_io_fixture.hpp"
#include "raylib.h"
#include <csignal>
#include <iostream>
#include <stdexcept>

using namespace tyrell::game::test;
namespace {
void require(bool value,const char* message) {if(!value)throw std::runtime_error(message);}
template<class Function> void rejects(Function function,const char* message) {
    try {function();}catch(const std::logic_error&){return;}
    throw std::runtime_error(message);
}
tyrell::game::RuntimeOptions options() {return native_fixture_options();}
void frame(tyrell::game::Runtime& runtime,Color color) {
    runtime.begin_frame();ClearBackground(color);runtime.end_frame();
}
}
int main() {
    using tyrell::game::Runtime;
    try {
        SetTraceLogLevel(LOG_WARNING);
        reset_native_fixture();
        {
            Runtime runtime(options());runtime.poll();frame(runtime,RED);
            require(native_io_stats().commits==1&&!foreground_pixels_equal(0x1111),"Production native frame was not presented");
            queue_native_key(17,1);runtime.poll();
            require(runtime.down('W')&&runtime.pressed('W'),"Native input fixture did not reach runtime");
            runtime.suspend();runtime.suspend();
            const auto reads=native_io_stats().reads;
            require(native_io_stats().key_closes==1&&native_io_stats().open_keyboards==0,"Suspend did not close keyboard once");
            runtime.poll();require(native_io_stats().reads==reads&&!runtime.down('W')&&runtime.next_key()==0,"Hidden poll read or leaked input");
            write_foreground_pixels(0x2222);
            rejects([&]{runtime.begin_frame();},"Hidden frame accepted");
            queue_native_resume_key(28,1);queue_native_resume_key(28,0);
            require(runtime.resume()&&runtime.redraw_requested(),"Native resume failed or omitted redraw");runtime.poll();
            require(!runtime.down('W')&&!runtime.pressed('W')&&runtime.next_key()==0&&runtime.character()==0,
                    "Resume replayed held/stale keys");
            queue_native_key(17,2);runtime.poll();require(runtime.character()==0&&!runtime.down('W'),"Resume autorepeat escaped suppression");
            queue_native_sync(3);queue_native_sync(0);runtime.poll();
            require(!runtime.down('W')&&runtime.next_key()==0,"Dropped-event resync lost resume suppression");
            queue_native_key(17,0);queue_native_key(17,1);runtime.poll();
            require(runtime.down('W')&&runtime.pressed('W')&&runtime.next_key()=='W',"Fresh post-release key did not act");
            frame(runtime,GREEN);require(native_io_stats().commits==2&&!runtime.redraw_requested(),"Resume did not complete native redraw");
        }
        require(native_io_stats().commits==3&&foreground_pixels_equal(0x1111),"Normal foreground close did not preserve saved-screen restoration");
        require(native_io_stats().fb_closes==1&&native_io_stats().key_closes==2&&native_io_stats().unmaps==1,"Foreground close leaked native descriptors");
        for(const bool restore:{true,false}) {
            reset_native_fixture();fail_native_keyboard_open(true,0x7777);
            auto selected=options();selected.restore_framebuffer_on_close=restore;
            bool failed=false;try{Runtime runtime(selected);}catch(const std::runtime_error&){failed=true;}
            require(failed&&!IsWindowReady(),"Failed native construction leaked its graphics context");
            require(native_io_stats().fb_closes==1&&native_io_stats().unmaps==1,"Failed native construction leaked framebuffer");
            require(native_io_stats().commits==(restore?1U:0U)&&foreground_pixels_equal(restore?0x1111:0x7777),
                    "Construction-time restore permission did not protect the foreground");
        }
        reset_native_fixture();
        {
            auto selected=options();selected.restore_framebuffer_on_close=false;
            Runtime runtime(selected);frame(runtime,RED);runtime.suspend();
            require(runtime.resume(),"No-restore runtime could not resume");frame(runtime,GREEN);
            write_foreground_pixels(0x8888);
        }
        require(native_io_stats().commits==2&&foreground_pixels_equal(0x8888),"Resume re-enabled forbidden saved-frame restoration");
        reset_native_fixture();
        {
            Runtime runtime(options());frame(runtime,RED);runtime.suspend();
            write_foreground_pixels(0x3333);
        }
        require(native_io_stats().commits==1&&foreground_pixels_equal(0x3333)&&native_io_stats().fb_closes==1&&native_io_stats().unmaps==1,
                "Hidden close repainted the newer foreground");
        reset_native_fixture();
        {
            Runtime runtime(options());runtime.begin_frame();ClearBackground(BLUE);runtime.suspend();
            require(native_io_stats().commits==0&&foreground_pixels_equal(0x1111),"Suspension presented an incomplete frame");
            write_foreground_pixels(0x4444);runtime.abandon_foreground();
            require(runtime.closed()&&!runtime.resume(),"Abandoned runtime reacquired input");
        }
        require(native_io_stats().commits==0&&foreground_pixels_equal(0x4444),"Abandoned pending frame restored old pixels");
        reset_native_fixture();
        {
            Runtime runtime(options());frame(runtime,RED);write_foreground_pixels(0x5555);runtime.abandon_foreground();
            require(native_io_stats().fb_closes==1&&native_io_stats().open_keyboards==0,"Ownership loss did not immediately close native descriptors");
        }
        require(native_io_stats().commits==1&&foreground_pixels_equal(0x5555),"Foreground link loss restored saved pixels");
        for(const bool open_failure:{true,false}) {
            reset_native_fixture();
            {
                Runtime runtime(options());frame(runtime,RED);runtime.suspend();write_foreground_pixels(0x6666);
                fail_native_keyboard_open(open_failure);fail_native_keyboard_resync(!open_failure);
                std::string error;require(!runtime.resume(&error)&&!error.empty()&&runtime.suspended(),"Failed input resume granted foreground");
                require(native_io_stats().open_keyboards==0&&foreground_pixels_equal(0x6666),"Failed resume leaked input or wrote pixels");
            }
            require(native_io_stats().commits==1&&foreground_pixels_equal(0x6666),"Failed resume cleanup restored prelaunch screen");
        }
        reset_native_fixture();
        {
            Runtime runtime(options());fail_native_commit(true);
            bool failed=false;try {frame(runtime,RED);}catch(const std::runtime_error&){failed=true;}
            require(failed&&runtime.redraw_requested(),"Failed native present cleared full-redraw request");
            runtime.abandon_foreground();
        }
        reset_native_fixture();
        {
            Runtime runtime(options());queue_native_key(17,1);signal_on_native_read(SIGTERM);
            runtime.poll();
            require(runtime.closed()&&native_io_stats().reads>0,"Signal during native poll was not observed");
            runtime.abandon_foreground();
        }
        require(native_io_stats().commits==0,"Mid-poll shutdown fixture unexpectedly presented a frame");
        std::cout<<"Native lifecycle ownership/input tests passed\n";return 0;
    } catch(const std::exception& error) {
        std::cerr<<"Native lifecycle: "<<error.what()<<'\n';return 1;
    }
}
