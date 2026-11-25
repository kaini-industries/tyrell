#include "beacon_session.hpp"
#include "beacon_lifecycle.hpp"
#include <tyrell/game/runtime.hpp>
#include <tyrell/game/render.hpp>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <csignal>
#include <functional>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
#include <cerrno>
#include <poll.h>
#endif

#ifndef BEACON_VERSION
#define BEACON_VERSION "0.1.2"
#endif

namespace {
using Clock=std::chrono::steady_clock;
volatile std::sig_atomic_t stopped=0;
void stop(int) {stopped=1;}
struct Options {
    bool host=false,server=false,headless=false,autoplay=false;
    std::string address,screenshot,report,framebuffer="auto",input="auto";
    int port=28771,frames=0;
    double seconds=0;
    bool framebuffer_supplied=false,input_supplied=false;
};
double number(const std::string& value,double low,double high) {
    std::size_t end=0;const double result=std::stod(value,&end);
    if(end!=value.size()||!std::isfinite(result)||result<low||result>high)
        throw std::runtime_error("Invalid numeric option: "+value);
    return result;
}
Options parse(int argc,char** argv) {
    Options out;
    for(int i=1;i<argc;++i) {
        const std::string key=argv[i];
        auto value=[&] {if(++i>=argc) throw std::runtime_error("Missing value for "+key);return std::string(argv[i]);};
        if(key=="--host") out.host=true;
        else if(key=="--join") out.address=value();
        else if(key=="--server") {out.server=true;out.host=true;}
        else if(key=="--headless") out.headless=true;
        else if(key=="--autoplay") out.autoplay=true;
        else if(key=="--seconds") out.seconds=number(value(),.1,3600);
        else if(key=="--frames"||key=="--port") {
            const double parsed=number(value(),1,key=="--port"?65535:1000000);
            if(std::floor(parsed)!=parsed) throw std::runtime_error(key+" requires an integer");
            (key=="--port"?out.port:out.frames)=static_cast<int>(parsed);
        } else if(key=="--screenshot") out.screenshot=value();
        else if(key=="--report") out.report=value();
        else if(key=="--framebuffer") {out.framebuffer=value();out.framebuffer_supplied=true;}
        else if(key=="--input") {out.input=value();out.input_supplied=true;}
        else if(key=="--help") {
            std::cout<<"Tyrell Beacon Arena " BEACON_VERSION ": independent framework sample\n"
                "  --host | --join HOST   LAN session; default is offline\n"
                "  --server               Headless authoritative host (default 60 seconds)\n"
                "  --port PORT            UDP port, default 28771\n"
                "  --headless             Memory-renderer build without device access\n"
                "  --seconds N --frames N Bound a run\n"
                "  --autoplay             Script movement/pulse for verification\n"
                "  --screenshot FILE.png --report FILE.json\n"
                "  --framebuffer PATH --input PATH  Native devices (auto by default)\n"
                "Controls: WASD move, Space pulse, Escape exit.\n";
            std::exit(0);
        } else throw std::runtime_error("Unknown option: "+key);
    }
    if(out.host&&!out.address.empty()) throw std::runtime_error("Choose --host or --join");
    if(out.server&&(!out.screenshot.empty()||out.frames||out.headless))
        throw std::runtime_error("--server has no renderer; use --seconds and --report");
    if(out.server&&!out.seconds) out.seconds=60;
    if(!out.screenshot.empty()&&!out.frames&&!out.seconds) out.frames=30;
    return out;
}
tyrell::game::RuntimeOptions runtime_options(const Options& options,bool linked=false) {
    tyrell::game::RuntimeOptions settings;settings.title="Tyrell / Beacon Arena";
    if(options.headless) settings.backend=tyrell::game::Backend::Headless;
    settings.framebuffer=options.framebuffer;settings.input=options.input;
    settings.restore_framebuffer_on_close=!linked;
#if TYRELL_ENABLE_SHELL_LINK && TYRELL_BEACON_NATIVE && defined(__linux__)
    if(linked&&!options.headless) {
        if(!options.framebuffer_supplied) {
            if(const auto* device=std::getenv("LV_LINUX_FBDEV_DEVICE");device&&*device)settings.framebuffer=device;
        }
        if(!options.input_supplied) {
            if(const auto* device=std::getenv("LV_LINUX_KEYBOARD_DEVICE");device&&*device)settings.input=device;
        }
    }
#endif
    return settings;
}
Color color(int index) {
    constexpr Color palette[]={{80,192,146,255},{245,153,88,255},{108,163,234,255},{226,194,91,255}};
    return palette[std::clamp(index,0,3)];
}
void draw(const beacon::Session& game,int width,int height) {
    using namespace tyrell::game;
    ClearBackground({30,40,50,255});
    Camera3D camera{{11,15,15},{0,0,0},{0,1,0},18,CAMERA_ORTHOGRAPHIC};
    {
        CameraPass pass(camera);
        draw_box({0,-.15f,0},{16,.3f,12},{62,77,83,255});
        for(int x=-6;x<=6;x+=2) DrawLine3D({float(x),.01f,-5},{float(x),.01f,5},{79,95,100,255});
        for(int z=-4;z<=4;z+=2) DrawLine3D({-7,.01f,float(z)},{7,.01f,float(z)},{79,95,100,255});
        draw_box({0,.12f,-6},{16,.25f,.2f},{100,123,129,255});
        draw_box({0,.12f,6},{16,.25f,.2f},{100,123,129,255});
        draw_box({-8,.12f,0},{.2f,.25f,12},{100,123,129,255});
        draw_box({8,.12f,0},{.2f,.25f,12},{100,123,129,255});
        for(int i=0;i<beacon::kPlayers;++i) {
            const auto& actor=game.state().actors[i];if(!actor.active) continue;
            const float h=.8f+actor.pulse*.7f;
            TransformScope transform({actor.x,0,actor.z},float(game.state().tick)*.008f);
            draw_box({0,h/2,0},{.85f,h,.85f},color(i));
            draw_box({0,h+.08f,0},{.4f,.16f,.4f},{237,230,194,255});
        }
    }
    DrawRectangle(0,0,width,23,{23,31,40,245});
    DrawText("BEACON ARENA",7,5,12,{231,226,204,255});
    const auto label=game.local_player()>=0?"P"+std::to_string(game.local_player()+1):"--";
    DrawText(label.c_str(),width-25,6,10,color(game.local_player()));
    DrawRectangle(0,height-25,width,25,{23,31,40,245});
    DrawText(game.status().c_str(),7,height-22,8,{166,189,190,255});
    DrawText("WASD MOVE / SPACE PULSE / ESC EXIT",7,height-11,8,{231,226,204,255});
}
void report(const std::string& path,const beacon::Session& game,int frames,double elapsed,
            int peak_players,float maximum_movement) {
    if(path.empty()) return;
    std::ofstream out(path);if(!out) throw std::runtime_error("Cannot open report "+path);
    out<<"{\"sample\":\"beacon-arena\",\"frames\":"<<frames<<",\"seconds\":"<<elapsed
        <<",\"connected\":"<<(game.connected()?"true":"false")<<",\"local_player\":"<<game.local_player()
        <<",\"tick\":"<<game.state().tick<<",\"peak_players\":"<<peak_players
        <<",\"maximum_local_movement\":"<<maximum_movement<<",\"actors\":[";
    for(int i=0;i<beacon::kPlayers;++i) {
        if(i) out<<',';
        const auto& actor=game.state().actors[i];
        out<<"{\"active\":"<<(actor.active?"true":"false")<<",\"x\":"<<actor.x
            <<",\"z\":"<<actor.z<<",\"pulse\":"<<actor.pulse<<'}';
    }
    out<<"]}\n";if(!out) throw std::runtime_error("Cannot write report "+path);
}

struct SessionState {
    beacon::Session game;
    bool quit=false,drop_simulation_debt=false,origin_ready=false;
    int frames=0,peak_players=0;
    float movement=0,origin_x=0,origin_z=0;
    double last_wall_elapsed=0,last_simulation_elapsed=0;
    explicit SessionState(const Options&) {}
};
struct SessionHooks {
    SessionState* state=nullptr;
    tyrell::game::Runtime* runtime=nullptr;
    void pause(){if(runtime)runtime->suspend();}
    bool resume(){
        if(runtime) {
            std::string error;
            if(!runtime->resume(&error)){std::cerr<<"Beacon resume: "<<error<<'\n';return false;}
            runtime->request_redraw();
        }
        // Discard only this offline app's next simulation interval. The raw
        // wall clock and shared Runtime/network expiry contracts stay intact.
        if(state)state->drop_simulation_debt=true;
        return true;
    }
    void redraw(){if(runtime)runtime->request_redraw();}
    void abandon(){if(runtime)runtime->abandon_foreground();}
};

#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
void waitShell(tyrell::app::ShellLink& link,int maximumMs=50) {
    pollfd descriptor{link.fd(),short(POLLIN|(link.pending_output()?POLLOUT:0)),0};
    const auto result=::poll(&descriptor,1,maximumMs);
    if(result<0&&errno!=EINTR)throw std::runtime_error("Beacon shell poll failed");
}
int awaitShell(tyrell::app::ShellLink& link,beacon::OfflineLifecycle& lifecycle,
               const std::function<void()>& wait={},SessionHooks* appHooks=nullptr) {
    SessionHooks empty;auto& hooks=appHooks?*appHooks:empty;
    const auto deadline=Clock::now()+std::chrono::milliseconds(100);
    for(;;) {
        const bool drained=beacon::pump_lifecycle(link,lifecycle,hooks);
        if(lifecycle.exiting())return 0;
        if(lifecycle.hello_seen()&&lifecycle.foreground()&&drained)return 1;
        if(!lifecycle.hello_seen()&&Clock::now()>=deadline) {
            std::cerr<<"Beacon: shell hello timed out; devices remain untouched\n";return -1;
        }
        if(!drained)continue;
        if(wait)wait();else waitShell(link);
    }
}
#endif

struct SessionResult {int frames=0;double seconds=0;bool saveAllowed=true;};
struct LinkedExitGuard {
    tyrell::game::Runtime& runtime;bool linked;
    ~LinkedExitGuard(){if(linked)runtime.abandon_foreground();}
};
SessionResult runSession(SessionState& state,const Options& options,tyrell::game::Runtime& runtime,
                         tyrell::app::ShellLink* link=nullptr,beacon::OfflineLifecycle* initialLifecycle=nullptr,
                         const std::function<void()>& waitHidden={},const std::function<void(int)>& afterFrame={}) {
    LinkedExitGuard exitOwnership{runtime,link!=nullptr};
    SessionResult result;
    beacon::OfflineLifecycle local;auto& lifecycle=initialLifecycle?*initialLifecycle:local;
    SessionHooks hooks{&state,&runtime};
    if(link&&(options.host||options.server||!options.address.empty()))
        throw std::runtime_error("LAN play is unavailable in this shell session");
    auto started=Clock::now(),previous=started;
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
    bool lifecycle_drained=false;
    const auto service_foreground=[&] {
        lifecycle_drained=beacon::pump_lifecycle(*link,lifecycle,hooks);
        if(lifecycle.exiting())result.saveAllowed=false;
        return lifecycle_drained&&lifecycle.foreground()&&!lifecycle.exiting()&&!runtime.closed();
    };
#endif
    while(!runtime.closed()&&!state.quit) {
        const auto now=Clock::now();const double elapsed=std::chrono::duration<double>(now-started).count();
        if(stopped||(options.seconds&&elapsed>=options.seconds)||(options.frames&&state.frames>=options.frames)) {
            // A bounded linked run can end while hidden, without waiting for a
            // new resume. No final exported state or device work follows it.
            if(link)result.saveAllowed=false;
            break;
        }
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
        if(link&&!service_foreground()) {
            if(lifecycle.exiting()||runtime.closed())break;
            if(lifecycle_drained&&!lifecycle.foreground()) {
                if(waitHidden)waitHidden();else waitShell(*link);
            }
            continue;
        }
#else
        (void)lifecycle;(void)hooks;(void)waitHidden;
#endif
        runtime.poll();if(runtime.closed())break;
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
        // Native polling may have blocked while a lifecycle packet arrived.
        if(link&&!service_foreground())continue;
#endif
        if(runtime.pressed(KEY_ESCAPE)){state.quit=true;break;}
        beacon::Input input{float(runtime.down(KEY_D))-float(runtime.down(KEY_A)),
                            float(runtime.down(KEY_S))-float(runtime.down(KEY_W)),runtime.down(KEY_SPACE)};
        if(options.autoplay)input={.7f,.3f,true};
        state.last_wall_elapsed=std::chrono::duration<double>(now-previous).count();
        double simulation_elapsed=state.last_wall_elapsed;
        if(link&&state.drop_simulation_debt){simulation_elapsed=0;state.drop_simulation_debt=false;}
        state.last_simulation_elapsed=simulation_elapsed;
        state.game.update(simulation_elapsed,input);previous=now;
        int active=0;for(const auto& actor:state.game.state().actors)active+=actor.active;
        state.peak_players=std::max(state.peak_players,active);
        const int player=state.game.local_player();
        if(player>=0&&state.game.state().actors[player].active) {
            const auto& actor=state.game.state().actors[player];
            if(!state.origin_ready){state.origin_x=actor.x;state.origin_z=actor.z;state.origin_ready=true;}
            state.movement=std::max(state.movement,std::hypot(actor.x-state.origin_x,actor.z-state.origin_z));
        }
        if(runtime.closed())break;
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
        if(link&&!service_foreground())continue;
#endif
        runtime.begin_frame();draw(state.game,runtime.width(),runtime.height());
        if(runtime.closed())break;
        runtime.end_frame();++state.frames;
        if(afterFrame)afterFrame(state.frames);
    }
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
    // A final queued pause/loss or a full batch wins over foreground report IO.
    if(link&&result.saveAllowed&&!runtime.closed()) {
        if(!service_foreground())result.saveAllowed=false;
    }
#endif
    result.frames=state.frames;result.seconds=std::chrono::duration<double>(Clock::now()-started).count();
    if(link&&(lifecycle.exiting()||!lifecycle.foreground()||runtime.closed()||runtime.suspended()||!runtime.foreground()))result.saveAllowed=false;
    if(!link&&!options.screenshot.empty())runtime.screenshot(options.screenshot);
    return result;
}

struct ServerSignals {
    using Handler=void(*)(int);
    Handler interrupt=std::signal(SIGINT,stop),terminate=std::signal(SIGTERM,stop);
    ~ServerSignals(){
        if(interrupt!=SIG_ERR)std::signal(SIGINT,interrupt);
        if(terminate!=SIG_ERR)std::signal(SIGTERM,terminate);
    }
};
}
int main(int argc,char** argv) {
    try {
        const auto options=parse(argc,argv);
        stopped=0;
        tyrell::app::ShellLink* activeLink=nullptr;
        beacon::OfflineLifecycle lifecycle;
#if TYRELL_ENABLE_SHELL_LINK && TYRELL_BEACON_NATIVE && defined(__linux__)
        auto link=!options.headless?tyrell::app::ShellLink::from_environment():tyrell::app::ShellLink{};
        if(link.connected()||link.closed()) {
            if(options.host||options.server||!options.address.empty()) {
                std::cerr<<"Beacon: LAN play is unavailable in this shell session\n";return 2;
            }
            const auto ready=awaitShell(link,lifecycle);
            if(ready<=0)return ready<0?1:0;
            activeLink=&link;
        }
#endif
        SessionState state(options);auto& game=state.game;
        if(options.host&&!game.host(static_cast<std::uint16_t>(options.port)))throw std::runtime_error(game.status());
        if(!options.address.empty()&&!game.join(options.address,static_cast<std::uint16_t>(options.port)))throw std::runtime_error(game.status());
        SessionResult result;
        if(options.server) {
            ServerSignals handlers;
            auto started=Clock::now(),previous=started;
            while(!stopped) {
                const auto now=Clock::now();const double elapsed=std::chrono::duration<double>(now-started).count();
                if(options.seconds&&elapsed>=options.seconds)break;
                game.update(std::chrono::duration<double>(now-previous).count(),options.autoplay?beacon::Input{.7f,.3f,true}:beacon::Input{});previous=now;
                int active=0;for(const auto& actor:game.state().actors)active+=actor.active;
                state.peak_players=std::max(state.peak_players,active);
                const int player=game.local_player();
                if(player>=0&&game.state().actors[player].active) {
                    const auto& actor=game.state().actors[player];
                    if(!state.origin_ready){state.origin_x=actor.x;state.origin_z=actor.z;state.origin_ready=true;}
                    state.movement=std::max(state.movement,std::hypot(actor.x-state.origin_x,actor.z-state.origin_z));
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(2));
            }
            result.seconds=std::chrono::duration<double>(Clock::now()-started).count();
        } else {
#if TYRELL_ENABLE_SHELL_LINK && TYRELL_BEACON_NATIVE && defined(__linux__)
            if(activeLink) {
                SessionHooks startup{&state,nullptr};
                const auto ready=awaitShell(*activeLink,lifecycle,{},&startup);
                if(ready<=0)return ready<0?1:0;
            }
#endif
            tyrell::game::Runtime runtime(runtime_options(options,activeLink!=nullptr));
            result=runSession(state,options,runtime,activeLink,&lifecycle);
        }
        if(result.saveAllowed)report(options.report,game,result.frames,result.seconds,state.peak_players,state.movement);
        std::cout<<"Beacon arena: "<<result.frames<<" frames, "<<state.peak_players<<" peak players, tick "<<game.state().tick<<'\n';
        return 0;
    } catch(const std::exception& error) {std::cerr<<"tyrell-beacon-arena: "<<error.what()<<'\n';return 1;}
}
