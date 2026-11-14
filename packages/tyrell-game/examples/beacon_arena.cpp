#include "beacon_session.hpp"
#include <tyrell/game/runtime.hpp>
#include <tyrell/game/render.hpp>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <csignal>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>

namespace {
using Clock=std::chrono::steady_clock;
volatile std::sig_atomic_t stopped=0;
void stop(int) {stopped=1;}
struct Options {
    bool host=false,server=false,headless=false,autoplay=false;
    std::string address,screenshot,report;
    int port=28771,frames=0;
    double seconds=0;
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
        else if(key=="--help") {
            std::cout<<"Tyrell Beacon Arena: independent framework sample\n"
                "  --host | --join HOST   LAN session; default is offline\n"
                "  --server               Headless authoritative host (default 60 seconds)\n"
                "  --port PORT            UDP port, default 28771\n"
                "  --headless             Memory-renderer build without device access\n"
                "  --seconds N --frames N Bound a run\n"
                "  --autoplay             Script movement/pulse for verification\n"
                "  --screenshot FILE.png --report FILE.json\n"
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
}
int main(int argc,char** argv) {
    try {
        const auto options=parse(argc,argv);
        beacon::Session game;
        if(options.host&&!game.host(static_cast<std::uint16_t>(options.port))) throw std::runtime_error(game.status());
        if(!options.address.empty()&&!game.join(options.address,static_cast<std::uint16_t>(options.port))) throw std::runtime_error(game.status());
        std::unique_ptr<tyrell::game::Runtime> runtime;
        if(!options.server) {
            tyrell::game::RuntimeOptions settings;settings.title="Tyrell / Beacon Arena";
            if(options.headless) settings.backend=tyrell::game::Backend::Headless;
            runtime=std::make_unique<tyrell::game::Runtime>(settings);
        }
        if(options.server) {std::signal(SIGINT,stop);std::signal(SIGTERM,stop);}
        auto started=Clock::now(),previous=started;
        int frames=0,peak_players=0;float movement=0;
        bool origin_ready=false;float origin_x=0,origin_z=0;
        for(;;) {
            const auto now=Clock::now();const double elapsed=std::chrono::duration<double>(now-started).count();
            if(stopped||(options.seconds&&elapsed>=options.seconds)||(options.frames&&frames>=options.frames)) break;
            beacon::Input input;
            if(runtime) {
                runtime->poll();if(runtime->closed()||runtime->pressed(KEY_ESCAPE)) break;
                input={float(runtime->down(KEY_D))-float(runtime->down(KEY_A)),
                       float(runtime->down(KEY_S))-float(runtime->down(KEY_W)),runtime->down(KEY_SPACE)};
            }
            if(options.autoplay) input={.7f,.3f,true};
            game.update(std::chrono::duration<double>(now-previous).count(),input);previous=now;
            int active=0;for(const auto& actor:game.state().actors) active+=actor.active;
            peak_players=std::max(peak_players,active);
            const int local=game.local_player();
            if(local>=0&&game.state().actors[local].active) {
                const auto& actor=game.state().actors[local];
                if(!origin_ready) {origin_x=actor.x;origin_z=actor.z;origin_ready=true;}
                movement=std::max(movement,std::hypot(actor.x-origin_x,actor.z-origin_z));
            }
            if(runtime) {
                runtime->begin_frame();draw(game,runtime->width(),runtime->height());runtime->end_frame();++frames;
            } else std::this_thread::sleep_for(std::chrono::milliseconds(2));
        }
        if(runtime&&!options.screenshot.empty()) runtime->screenshot(options.screenshot);
        report(options.report,game,frames,std::chrono::duration<double>(Clock::now()-started).count(),peak_players,movement);
        std::cout<<"Beacon arena: "<<frames<<" frames, "<<peak_players<<" peak players, tick "<<game.state().tick<<'\n';
        return 0;
    } catch(const std::exception& error) {std::cerr<<"tyrell-beacon-arena: "<<error.what()<<'\n';return 1;}
}
