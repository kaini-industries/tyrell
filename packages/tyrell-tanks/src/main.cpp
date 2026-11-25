#include "network.hpp"
#include "scene.hpp"
#include "offline_lifecycle.h"
#include <tyrell/game/runtime.hpp>
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <fstream>
#include <memory>
#include <sstream>
#include <iomanip>
#include <stdexcept>
#include <string>
#include <thread>
#include <functional>
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
#include <cerrno>
#include <poll.h>
#endif

#ifndef TANKS_VERSION
#define TANKS_VERSION "0.1.1"
#endif

namespace {
constexpr Color paper{244,225,184,255}, ink{36,43,40,255}, muted{192,180,149,255};
struct Options {
    std::string address, screenshot, report, framebuffer="auto", input="auto";
    int port=28770, frames=0;
    double seconds=0;
    bool host=false, practice=false, autoplay=false, server=false, demo=false, headless=false;
    bool framebuffer_supplied=false, input_supplied=false;
};
int integer(const std::string& text,int low,int high) {
    std::size_t end=0; int value=std::stoi(text,&end);
    if(end!=text.size() || value<low || value>high) throw std::runtime_error("Number out of range: "+text);
    return value;
}
Options parse(int argc,char**argv) {
    Options o;
    for(int i=1;i<argc;++i) {
        std::string flag=argv[i];
        auto value=[&]() { if(++i>=argc) throw std::runtime_error("Missing value for "+flag); return std::string(argv[i]); };
        if(flag=="--help") {
            std::cout<<"SANDLINE / Tyrell Tanks " TANKS_VERSION "\n"
                "  --host                Host a LAN match (UDP 28770)\n"
                "  --join HOST           Join a host by name or IPv4 address\n"
                "  --port PORT           UDP port, 1..65535\n"
                "  --practice            Single-player driving range\n"
                "  --server              Host without opening a window\n"
                "  --seconds N           Stop after N seconds (1..3600)\n"
                "  --frames N            Stop after N rendered frames\n"
                "  --autoplay            Script local controls for smoke testing\n"
                "  --demo                Preview four tanks without networking\n"
                "  --screenshot FILE.png Save the last rendered frame\n"
                "  --report FILE.json    Save final simulation state and input counters\n"
#ifdef TANKS_SOFTWARE
                "  --framebuffer PATH    Native LCD (auto by default)\n"
                "  --input PATH          Native evdev keyboard (auto by default)\n"
                "  --headless            Native renderer without device access\n"
#endif
                "Controls: W/S drive, A/D hull, Q/E turret, Space fire, Tab scores, Esc menu.\n";
            std::exit(0);
        } else if(flag=="--host") o.host=true;
        else if(flag=="--join") o.address=value();
        else if(flag=="--port") o.port=integer(value(),1,65535);
        else if(flag=="--frames") o.frames=integer(value(),1,1000000);
        else if(flag=="--seconds") o.seconds=integer(value(),1,3600);
        else if(flag=="--practice") o.practice=true;
        else if(flag=="--autoplay") o.autoplay=true;
        else if(flag=="--server") {o.server=true;o.host=true;}
        else if(flag=="--demo") o.demo=true;
        else if(flag=="--screenshot") o.screenshot=value();
        else if(flag=="--report") o.report=value();
#ifdef TANKS_SOFTWARE
        else if(flag=="--framebuffer") {o.framebuffer=value();o.framebuffer_supplied=true;}
        else if(flag=="--input") {o.input=value();o.input_supplied=true;}
        else if(flag=="--headless") o.headless=true;
#endif
        else throw std::runtime_error("Unknown option: "+flag);
    }
    if(o.host && !o.address.empty()) throw std::runtime_error("Choose hosting or joining");
    if(o.practice && (o.host || !o.address.empty())) throw std::runtime_error("Practice cannot be combined with networking");
    if(o.server && (!o.screenshot.empty() || o.frames)) throw std::runtime_error("Server uses --seconds, without screenshots or rendered frames");
    if(!o.screenshot.empty() && !o.frames && !o.seconds) o.frames=60;
    return o;
}
tyrell::game::RuntimeOptions runtime_options(const Options& options,bool linked=false) {
    tyrell::game::RuntimeOptions runtime;
    runtime.title="SANDLINE / Tyrell Tanks";
#ifdef TANKS_SOFTWARE
    runtime.fps=30;
#else
    runtime.fps=60;
#endif
    if(options.headless) runtime.backend=tyrell::game::Backend::Headless;
    runtime.framebuffer=options.framebuffer;runtime.input=options.input;
    runtime.restore_framebuffer_on_close=!linked;
#if TYRELL_ENABLE_SHELL_LINK && TYRELL_TANKS_NATIVE && defined(__linux__)
    if(linked&&!options.headless) {
        if(!options.framebuffer_supplied) {
            if(const auto* device=std::getenv("LV_LINUX_FBDEV_DEVICE");device&&*device)runtime.framebuffer=device;
        }
        if(!options.input_supplied) {
            if(const auto* device=std::getenv("LV_LINUX_KEYBOARD_DEVICE");device&&*device)runtime.input=device;
        }
    }
#endif
    return runtime;
}
std::string json_string(const std::string& value) {
    std::ostringstream out;out<<'"';
    for(unsigned char c:value) {
        if(c=='"'||c=='\\') out<<'\\'<<c;
        else if(c<32) out<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<int(c)<<std::dec;
        else out<<c;
    }
    out<<'"';return out.str();
}
void write_report(const std::string& path,const tanks::NetworkGame& game,int frames,double seconds,
                  const std::array<std::uint64_t,4>& controls) {
    if(path.empty()) return;
    std::ofstream out(path);
    if(!out) throw std::runtime_error("Cannot open report "+path);
    out<<"{\"frames\":"<<frames<<",\"seconds\":"<<seconds<<",\"player\":"<<game.local_player()
       <<",\"tick\":"<<game.world().tick<<",\"status\":"<<json_string(game.status())
       <<",\"control_frames\":{\"drive\":"<<controls[0]<<",\"hull\":"<<controls[1]
       <<",\"turret\":"<<controls[2]<<",\"fire\":"<<controls[3]<<"},\"players\":[";
    for(int i=0;i<tanks::kMaxPlayers;++i) {
        const auto& t=game.world().tanks[i];
        if(i) out<<',';
        out<<"{\"active\":"<<(t.active?"true":"false")<<",\"x\":"<<t.position.x
           <<",\"z\":"<<t.position.z<<",\"yaw\":"<<t.yaw<<",\"turret_yaw\":"<<t.turret_yaw
           <<",\"health\":"<<t.health<<",\"kills\":"<<t.kills<<'}';
    }
    out<<"]}\n";
    if(!out) throw std::runtime_error("Cannot write report "+path);
}
void text(const std::string& s,int x,int y,int size,Color color=paper) { DrawText(s.c_str(),x,y,size,color); }
void menu(int selected,bool joining,const std::string& address,const std::string& message,double time,bool linked=false) {
    DrawRectangle(0,0,320,55,{36,43,40,243});
    text("SANDLINE",12,9,28);
    text("A POCKET TANK SKIRMISH",13,39,8,{221,167,83,255});
    DrawRectangle(8,64,148,87,{36,43,40,243});
    if(joining) {
        text("HOST ADDRESS",15,70,8,muted);
        DrawRectangle(14,83,136,17,{66,73,61,255});
        BeginScissorMode(15,84,134,15);
        text(address+(static_cast<int>(time*2)%2?"_":""),18,87,10);
        EndScissorMode();
        text("ENTER CONNECT / ESC BACK",15,110,8,muted);
    } else {
        const char* labels[]={"DRIVING RANGE",linked?"HOST LAN UNAVAILABLE":"HOST LAN MATCH",linked?"JOIN LAN UNAVAILABLE":"JOIN LAN MATCH"};
        for(int i=0;i<3;++i) {
            int y=69+i*25;
            if(i==selected) DrawRectangle(13,y,138,21,{206,154,74,255});
            text(std::to_string(i+1)+"  "+labels[i],18,y+6,8,i==selected?ink:(linked&&i?muted:paper));
        }
    }
    text("W/S DRIVE  A/D HULL",170,121,8,ink);
    text("Q/E AIM    SPACE FIRE",170,133,8,ink);
    DrawRectangle(0,155,320,15,ink);
    text(message.empty()?"ARROWS + ENTER TO SELECT / ESC QUIT":message.substr(0,57),8,159,8,muted);
}
void hud(const tanks::NetworkGame& game,bool scores) {
    DrawRectangle(0,0,320,22,{36,43,40,235});
    text("SANDLINE",7,5,12);
    int local=game.local_player();
    if(local>=0 && local<tanks::kMaxPlayers) {
        const auto& t=game.world().tanks[local];
        text("P"+std::to_string(local+1),111,6,10,tanks::player_color(local));
        text("ARMOR "+std::to_string(t.health),137,7,8);
        text("KILLS "+std::to_string(t.kills),221,7,8);
        if(t.health<=0) {
            DrawRectangle(78,69,164,31,{36,43,40,235});
            text("TANK DESTROYED",95,74,12);
            text("RETURNING IN "+std::to_string(static_cast<int>(std::ceil(t.respawn))),112,90,8,muted);
        }
        DrawRectangle(8,140,42,5,{36,43,40,220});
        float ready=std::clamp(1.0f-t.cooldown/.6f,0.0f,1.0f);
        DrawRectangle(9,141,static_cast<int>(40*ready),3,{225,177,94,255});
    }
    DrawRectangle(0,156,320,14,{36,43,40,240});
    text(game.status().substr(0,39),6,160,8,muted);
    text("ESC MENU",270,160,8,muted);
    if(scores) {
        DrawRectangle(82,40,156,87,{36,43,40,245});
        text("FIELD REPORT",95,47,12);
        for(int i=0;i<tanks::kMaxPlayers;++i) if(game.world().tanks[i].active) {
            text("P"+std::to_string(i+1)+"  "+std::to_string(game.world().tanks[i].kills)+" KILLS",98,66+i*12,8,tanks::player_color(i));
        }
    }
}

struct SessionState {
    tanks::NetworkGame game;
    tanks::World preview;
    bool in_menu=true,enter_address=false,quit=false,drop_simulation_debt=false;
    std::string address,message;
    int selected=0,frames=0;
    double last_simulation_elapsed=0;
    std::array<std::uint64_t,4> controls{};
    explicit SessionState(const Options& options)
        :in_menu(!(options.host||!options.address.empty()||options.practice)),
         address(options.address.empty()?"127.0.0.1":options.address) {
        if(options.practice)game.offline();
        tanks::reset(preview);
        for(int i=0;i<tanks::kMaxPlayers;++i)tanks::spawn(preview,i);
    }
};
struct SessionHooks {
    SessionState* state=nullptr;
    tyrell::game::Runtime* runtime=nullptr;
    void pause(){if(runtime)runtime->suspend();}
    bool resume(){
        if(runtime) {
            std::string error;
            if(!runtime->resume(&error)) {std::cerr<<"Sandline resume: "<<error<<'\n';return false;}
            runtime->request_redraw();
        }
        // This policy applies only to the offline practice update. Runtime's
        // monotonic elapsed clock and all unlinked LAN expiry remain truthful.
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
    if(result<0&&errno!=EINTR)throw std::runtime_error("Sandline shell poll failed");
}
int awaitShell(tyrell::app::ShellLink& link,tanks::OfflineLifecycle& lifecycle,
               const std::function<void()>& wait={},SessionHooks* appHooks=nullptr) {
    SessionHooks empty;auto& hooks=appHooks?*appHooks:empty;
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::milliseconds(100);
    for(;;) {
        const bool drained=tanks::pump_lifecycle(link,lifecycle,hooks);
        if(lifecycle.exiting())return 0;
        if(lifecycle.hello_seen()&&lifecycle.foreground()&&drained)return 1;
        if(!lifecycle.hello_seen()&&std::chrono::steady_clock::now()>=deadline) {
            std::cerr<<"Sandline: shell hello timed out; devices remain untouched\n";return -1;
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
SessionResult runSession(SessionState& state,const Options& options,tyrell::game::Runtime& platform,
                         tyrell::app::ShellLink* link=nullptr,tanks::OfflineLifecycle* initialLifecycle=nullptr,
                         const std::function<void()>& waitHidden={},const std::function<void(int)>& afterFrame={}) {
    LinkedExitGuard exitOwnership{platform,link!=nullptr};
    SessionResult result;
    tanks::OfflineLifecycle localLifecycle;auto& lifecycle=initialLifecycle?*initialLifecycle:localLifecycle;
    SessionHooks hooks{&state,&platform};
    const double started=platform.time();
    if(link&&(options.host||options.server||!options.address.empty()))
        throw std::runtime_error("LAN play is unavailable in this shell session");
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
    bool lifecycle_drained=false;
    const auto service_foreground=[&] {
        lifecycle_drained=tanks::pump_lifecycle(*link,lifecycle,hooks);
        if(lifecycle.exiting())result.saveAllowed=false;
        return lifecycle_drained&&lifecycle.foreground()&&!lifecycle.exiting()&&!platform.closed();
    };
#endif
    while(!platform.closed()&&!state.quit) {
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
        if(link) {
            if(!service_foreground()) {
                if(lifecycle.exiting()||platform.closed())break;
                if(lifecycle_drained&&!lifecycle.foreground()) {
                    if(waitHidden)waitHidden();else waitShell(*link);
                }
                continue;
            }
        }
#else
        (void)lifecycle;(void)hooks;(void)waitHidden;
#endif
        platform.poll();
        if(platform.closed())break;
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
        // Input polling may have blocked while a handoff packet arrived. Drain
        // it before applying keys or advancing the offline world.
        if(link&&!service_foreground())continue;
#endif
        // Network liveness needs wall time. Only a linked offline resume drops
        // one hidden simulation interval; NetworkGame's LAN path is untouched.
        double elapsed=platform.elapsed();
        if(link&&state.drop_simulation_debt){elapsed=0;state.drop_simulation_debt=false;}
        state.last_simulation_elapsed=elapsed;
        if(state.in_menu&&!options.demo) {
            if(link&&state.enter_address) {
                state.enter_address=false;state.message="LAN play is unavailable in this shell session";
            }
            if(state.enter_address) {
                for(int c=platform.character();c;c=platform.character())if(c>=33&&c<=126&&state.address.size()<63)state.address+=static_cast<char>(c);
                if(platform.pressed(KEY_BACKSPACE)&&!state.address.empty())state.address.pop_back();
                if(platform.pressed(KEY_ESCAPE))state.enter_address=false;
                if(platform.pressed(KEY_ENTER)) {
                    if(state.game.join(state.address,static_cast<std::uint16_t>(options.port))){state.in_menu=false;state.enter_address=false;}
                    else state.message=state.game.status();
                }
            } else {
                if(platform.pressed(KEY_DOWN)||platform.pressed(KEY_S))state.selected=(state.selected+1)%3;
                if(platform.pressed(KEY_UP)||platform.pressed(KEY_W))state.selected=(state.selected+2)%3;
                for(int k=0;k<3;++k)if(platform.pressed(KEY_ONE+k))state.selected=k;
                if(platform.pressed(KEY_ESCAPE))state.quit=true;
                if(platform.pressed(KEY_ENTER)) {
                    if(state.selected==0){state.game.offline();state.in_menu=false;}
                    else if(link)state.message="LAN play is unavailable in this shell session";
                    else if(state.selected==1){if(state.game.host(static_cast<std::uint16_t>(options.port)))state.in_menu=false;else state.message=state.game.status();}
                    else state.enter_address=true;
                }
            }
        } else if(platform.pressed(KEY_ESCAPE)){state.game.offline();state.in_menu=true;state.message.clear();}
        if(platform.closed())break;
        tanks::Input input;
        if(!state.in_menu) {
            input.throttle=static_cast<float>(platform.down(KEY_W))-static_cast<float>(platform.down(KEY_S));
            input.turn=static_cast<float>(platform.down(KEY_A))-static_cast<float>(platform.down(KEY_D));
            input.turret=static_cast<float>(platform.down(KEY_Q))-static_cast<float>(platform.down(KEY_E));
            input.fire=platform.down(KEY_SPACE);
            if(options.autoplay){input.throttle=.7f;input.turn=.3f;input.turret=.5f;input.fire=true;}
            state.controls[0]+=input.throttle!=0;state.controls[1]+=input.turn!=0;
            state.controls[2]+=input.turret!=0;state.controls[3]+=input.fire;
            state.game.update(elapsed,input);
        }
        if(platform.closed())break;
        if(state.in_menu)for(int i=0;i<tanks::kMaxPlayers;++i)state.preview.tanks[i].turret_yaw=static_cast<float>(platform.time()*.2+i*1.57);
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
        if(link&&!service_foreground())continue;
#endif
        platform.begin_frame();
        tanks::draw_scene(state.in_menu?state.preview:state.game.world(),state.in_menu?-1:state.game.local_player(),platform.time(),state.in_menu);
        if(state.in_menu&&!options.demo)menu(state.selected,state.enter_address,state.address,state.message,platform.time(),link!=nullptr);
        else if(!state.in_menu)hud(state.game,platform.down(KEY_TAB));
        if(platform.closed())break;
        platform.end_frame();++state.frames;
        if(afterFrame)afterFrame(state.frames);
        const bool finished=(options.frames&&state.frames>=options.frames)||(options.seconds&&platform.time()-started>=options.seconds)||state.quit;
        if(finished&&!link&&!options.screenshot.empty())platform.screenshot(options.screenshot);
        if(finished)break;
    }
    result.frames=state.frames;result.seconds=platform.time()-started;
    if(link&&(lifecycle.exiting()||!lifecycle.foreground()||platform.closed()||platform.suspended()||!platform.foreground()))result.saveAllowed=false;
    return result;
}
}

int main(int argc,char**argv) {
    try {
        Options o=parse(argc,argv);
        tyrell::app::ShellLink* activeLink=nullptr;
        tanks::OfflineLifecycle lifecycle;
#if TYRELL_ENABLE_SHELL_LINK && TYRELL_TANKS_NATIVE && defined(__linux__)
        auto link=!o.headless?tyrell::app::ShellLink::from_environment():tyrell::app::ShellLink{};
        if(link.connected()||link.closed()) {
            if(o.host||o.server||!o.address.empty()) {
                std::cerr<<"Sandline: LAN play is unavailable in this shell session\n";return 2;
            }
            const auto ready=awaitShell(link,lifecycle);
            if(ready<=0)return ready<0?1:0;
            activeLink=&link;
        }
#endif
        SessionState state(o);auto& game=state.game;
        if(o.host&&!game.host(static_cast<std::uint16_t>(o.port)))throw std::runtime_error(game.status());
        if(!o.address.empty()&&!game.join(o.address,static_cast<std::uint16_t>(o.port)))throw std::runtime_error(game.status());
        if(o.server) {
            using Clock=std::chrono::steady_clock;
            auto started=Clock::now(),last=started;
            std::cout<<"Hosting UDP "<<o.port<<" (host slot reserved)\n";
            while(!o.seconds || std::chrono::duration<double>(Clock::now()-started).count()<o.seconds) {
                auto now=Clock::now();game.update(std::chrono::duration<double>(now-last).count(),{});last=now;
                std::this_thread::sleep_for(std::chrono::milliseconds(2));
            }
            write_report(o.report,game,0,std::chrono::duration<double>(Clock::now()-started).count(),{});
            return 0;
        }
#if TYRELL_ENABLE_SHELL_LINK && TYRELL_TANKS_NATIVE && defined(__linux__)
        if(activeLink) {
            SessionHooks startup{&state,nullptr};
            const auto ready=awaitShell(*activeLink,lifecycle,{},&startup);
            if(ready<=0)return ready<0?1:0;
        }
#endif
        SessionResult result;
        {
            tyrell::game::Runtime platform(runtime_options(o,activeLink!=nullptr));
            result=runSession(state,o,platform,activeLink,&lifecycle);
        }
        std::cout<<"frames="<<result.frames<<" player="<<game.local_player()<<" tick="<<game.world().tick<<" status="<<game.status()<<'\n';
        if(result.saveAllowed)write_report(o.report,game,result.frames,result.seconds,state.controls);
        return 0;
    } catch(const std::exception& error) { std::cerr<<"tyrell-tanks: "<<error.what()<<'\n';return 1; }
}
