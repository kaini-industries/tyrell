#include "network.hpp"
#include "scene.hpp"
#include <tyrell/game/runtime.hpp>
#include <algorithm>
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

namespace {
constexpr Color paper{244,225,184,255}, ink{36,43,40,255}, muted{192,180,149,255};
struct Options {
    std::string address, screenshot, report, framebuffer="auto", input="auto";
    int port=28770, frames=0;
    double seconds=0;
    bool host=false, practice=false, autoplay=false, server=false, demo=false, headless=false;
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
            std::cout<<"SANDLINE / Tyrell Tanks 0.1.0\n"
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
        else if(flag=="--framebuffer") o.framebuffer=value();
        else if(flag=="--input") o.input=value();
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
tyrell::game::RuntimeOptions runtime_options(const Options& options) {
    tyrell::game::RuntimeOptions runtime;
    runtime.title="SANDLINE / Tyrell Tanks";
#ifdef TANKS_SOFTWARE
    runtime.fps=30;
#else
    runtime.fps=60;
#endif
    if(options.headless) runtime.backend=tyrell::game::Backend::Headless;
    runtime.framebuffer=options.framebuffer;runtime.input=options.input;
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
void menu(int selected,bool joining,const std::string& address,const std::string& message,double time) {
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
        const char* labels[]={"DRIVING RANGE","HOST LAN MATCH","JOIN LAN MATCH"};
        for(int i=0;i<3;++i) {
            int y=69+i*25;
            if(i==selected) DrawRectangle(13,y,138,21,{206,154,74,255});
            text(std::to_string(i+1)+"  "+labels[i],18,y+6,8,i==selected?ink:paper);
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
}

int main(int argc,char**argv) {
    try {
        Options o=parse(argc,argv);
        tanks::NetworkGame game;
        if(o.host && !game.host(static_cast<std::uint16_t>(o.port))) throw std::runtime_error(game.status());
        if(!o.address.empty() && !game.join(o.address,static_cast<std::uint16_t>(o.port))) throw std::runtime_error(game.status());
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
        if(o.practice) game.offline();
        tyrell::game::Runtime platform(runtime_options(o));
        tanks::World preview;tanks::reset(preview);
        for(int i=0;i<tanks::kMaxPlayers;++i) tanks::spawn(preview,i);
        bool in_menu=!(o.host||!o.address.empty()||o.practice),enter_address=false,quit=false;
        std::string address=o.address.empty()?"127.0.0.1":o.address,message;
        int selected=0,frame=0;double started=platform.time();
        std::array<std::uint64_t,4> controls{};
        while(!platform.closed()&&!quit) {
            platform.poll();
            if(platform.closed()) break;
            // Network liveness needs wall time; NetworkGame bounds simulation catch-up itself.
            double elapsed=platform.elapsed();
            if(in_menu && !o.demo) {
                if(enter_address) {
                    for(int c=platform.character();c;c=platform.character()) if(c>=33&&c<=126&&address.size()<63) address+=static_cast<char>(c);
                    if(platform.pressed(KEY_BACKSPACE)&&!address.empty()) address.pop_back();
                    if(platform.pressed(KEY_ESCAPE)) enter_address=false;
                    if(platform.pressed(KEY_ENTER)) {
                        if(game.join(address,static_cast<std::uint16_t>(o.port))) {in_menu=false;enter_address=false;}
                        else message=game.status();
                    }
                } else {
                    if(platform.pressed(KEY_DOWN)||platform.pressed(KEY_S)) selected=(selected+1)%3;
                    if(platform.pressed(KEY_UP)||platform.pressed(KEY_W)) selected=(selected+2)%3;
                    for(int k=0;k<3;++k) if(platform.pressed(KEY_ONE+k)) {selected=k;}
                    if(platform.pressed(KEY_ESCAPE)) quit=true;
                    if(platform.pressed(KEY_ENTER)) {
                        if(selected==0) {game.offline();in_menu=false;}
                        else if(selected==1) {if(game.host(static_cast<std::uint16_t>(o.port))) in_menu=false;else message=game.status();}
                        else enter_address=true;
                    }
                }
            } else if(platform.pressed(KEY_ESCAPE)) {game.offline();in_menu=true;message.clear();}
            tanks::Input input;
            if(!in_menu) {
                input.throttle=static_cast<float>(platform.down(KEY_W))-static_cast<float>(platform.down(KEY_S));
                input.turn=static_cast<float>(platform.down(KEY_A))-static_cast<float>(platform.down(KEY_D));
                input.turret=static_cast<float>(platform.down(KEY_Q))-static_cast<float>(platform.down(KEY_E));
                input.fire=platform.down(KEY_SPACE);
                if(o.autoplay) { input.throttle=.7f;input.turn=.3f;input.turret=.5f;input.fire=true; }
                controls[0]+=input.throttle!=0;controls[1]+=input.turn!=0;
                controls[2]+=input.turret!=0;controls[3]+=input.fire;
                game.update(elapsed,input);
            }
            if(in_menu) for(int i=0;i<tanks::kMaxPlayers;++i) preview.tanks[i].turret_yaw=static_cast<float>(platform.time()*.2+i*1.57);
            platform.begin_frame();
            tanks::draw_scene(in_menu?preview:game.world(),in_menu?-1:game.local_player(),platform.time(),in_menu);
            if(in_menu&&!o.demo) menu(selected,enter_address,address,message,platform.time());
            else if(!in_menu) hud(game,platform.down(KEY_TAB));
            platform.end_frame();++frame;
            bool finished=(o.frames&&frame>=o.frames)||(o.seconds&&platform.time()-started>=o.seconds)||quit;
            if(finished && !o.screenshot.empty()) platform.screenshot(o.screenshot);
            if(finished) break;
        }
        std::cout<<"frames="<<frame<<" player="<<game.local_player()<<" tick="<<game.world().tick<<" status="<<game.status()<<'\n';
        write_report(o.report,game,frame,platform.time()-started,controls);
        return 0;
    } catch(const std::exception& error) { std::cerr<<"tyrell-tanks: "<<error.what()<<'\n';return 1; }
}
