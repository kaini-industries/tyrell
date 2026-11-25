// Exercise the actual Sandline session and native entry point. The syscall
// fixture wraps Runtime's production device path; no device node is opened.
#define main tanks_application_main
#include "../src/main.cpp"
#undef main

#include <csignal>
#include <filesystem>
#include <functional>
#include <optional>
#include <sys/socket.h>
#include <netinet/in.h>
#include <fcntl.h>
#include <unistd.h>
#ifdef TYRELL_TANKS_NATIVE_TEST_IO
#include "native_io_fixture.hpp"
#endif

#define REQUIRE(value) do { if(!(value)) throw std::runtime_error(std::string("Sandline lifecycle: ") + #value); } while(false)
namespace {
unsigned completed_cases=0;
struct Pair {
    int peer=-1,endpoint=-1;
    Pair() { int descriptors[2];REQUIRE(socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0,descriptors)==0);peer=descriptors[0];endpoint=descriptors[1]; }
    ~Pair() { if(peer>=0)close(peer);if(endpoint>=0)close(endpoint);unsetenv("GH0ST_SHELL_FD"); }
    void send(const std::string& packet) { REQUIRE(::send(peer,packet.data(),packet.size(),MSG_NOSIGNAL|MSG_DONTWAIT)==ssize_t(packet.size())); }
    tyrell::app::ShellLink adopt() {
        REQUIRE(setenv("GH0ST_SHELL_FD",std::to_string(endpoint).c_str(),1)==0);
        auto link=tyrell::app::ShellLink::from_environment();REQUIRE(link.connected()||link.closed());endpoint=-1;return link;
    }
    bool ack() {
        bool found=false;char bytes[512];
        for(unsigned n=0;n<32;++n) {
            const auto count=recv(peer,bytes,sizeof(bytes),MSG_DONTWAIT);
            if(count<0&&(errno==EAGAIN||errno==EWOULDBLOCK))return found;
            REQUIRE(count>0);const std::string packet(bytes,std::size_t(count));
            REQUIRE(packet=="hello\t1"||packet=="paused");found=found||packet=="paused";
        }
        throw std::runtime_error("Unbounded shell output");
    }
    void disconnect() { if(peer>=0)close(peer);peer=-1; }
};
struct Environment {
    std::vector<std::pair<std::string,std::optional<std::string>>> previous;
    void set(const char* key,const std::string& value) {
        const char* old=getenv(key);previous.push_back({key,old?std::optional<std::string>(old):std::nullopt});
        REQUIRE(setenv(key,value.c_str(),1)==0);
    }
    ~Environment(){for(auto it=previous.rbegin();it!=previous.rend();++it){if(it->second)setenv(it->first.c_str(),it->second->c_str(),1);else unsetenv(it->first.c_str());}}
};
Options arguments(std::vector<std::string> values) {
    std::vector<char*> args;for(auto& value:values)args.push_back(value.data());return parse(int(args.size()),args.data());
}
int entry(std::vector<std::string> values) {
    std::vector<char*> args;for(auto& value:values)args.push_back(value.data());return tanks_application_main(int(args.size()),args.data());
}
void same_world(const tanks::World& a,const tanks::World& b) {
    REQUIRE(a.tick==b.tick);
    for(std::size_t n=0;n<a.tanks.size();++n) {
        const auto& x=a.tanks[n];const auto& y=b.tanks[n];
        REQUIRE(x.active==y.active&&x.position.x==y.position.x&&x.position.z==y.position.z&&x.yaw==y.yaw&&
            x.turret_yaw==y.turret_yaw&&x.health==y.health&&x.kills==y.kills&&x.cooldown==y.cooldown&&x.respawn==y.respawn);
    }
    for(std::size_t n=0;n<a.shells.size();++n) {
        const auto& x=a.shells[n];const auto& y=b.shells[n];
        REQUIRE(x.active==y.active&&x.position.x==y.position.x&&x.position.z==y.position.z&&x.velocity.x==y.velocity.x&&
            x.velocity.z==y.velocity.z&&x.owner==y.owner&&x.lifetime==y.lifetime);
    }
}
struct Hooks {
    bool owned=true,allow_resume=true;unsigned pauses=0,resumes=0,redraws=0,abandons=0;
    void pause(){owned=false;++pauses;}
    bool resume(){++resumes;owned=allow_resume;return owned;}
    void redraw(){++redraws;}
    void abandon(){owned=false;++abandons;}
};
struct Link {
    Hooks& hooks;unsigned acks=0,cancels=0;
    void acknowledge_paused(){REQUIRE(!hooks.owned);++acks;}
    void cancel_paused(){++cancels;}
};
void policy_ordering() {
    using E=tyrell::app::ShellEvent;tanks::OfflineLifecycle state;Hooks hooks;Link link{hooks};
    state.handle(E::Hello,link,hooks);state.handle(E::Resume,link,hooks);REQUIRE(state.hello_seen()&&hooks.resumes==0);
    state.handle(E::Pause,link,hooks);state.handle(E::Pause,link,hooks);
    REQUIRE(!state.foreground()&&hooks.pauses==1&&link.acks==2);
    state.handle(E::Redraw,link,hooks);REQUIRE(hooks.redraws==1&&!hooks.owned);
    state.handle(E::Resume,link,hooks);REQUIRE(state.foreground()&&hooks.resumes==1);
    state.handle(E::Pause,link,hooks);hooks.allow_resume=false;state.handle(E::Resume,link,hooks);
    REQUIRE(state.exiting()&&!state.foreground()&&hooks.abandons==1);
    state.handle(E::Resume,link,hooks);REQUIRE(hooks.resumes==2);
    tanks::OfflineLifecycle lost;Hooks other;Link other_link{other};lost.handle(E::Lost,other_link,other);
    REQUIRE(lost.exiting()&&!lost.foreground()&&!other.owned);++completed_cases;
}
void startup_and_batches() {
    Pair pair;pair.send("hello\t1");for(unsigned n=0;n<40;++n)pair.send("future");pair.send("pause");
    auto link=pair.adopt();tanks::OfflineLifecycle lifecycle;unsigned waits=0;
    REQUIRE(awaitShell(link,lifecycle,[&]{++waits;REQUIRE(pair.ack());pair.disconnect();})==0);
    REQUIRE(waits==1&&lifecycle.exiting()&&!lifecycle.foreground());
    Pair closed;closed.disconnect();auto dead=closed.adopt();tanks::OfflineLifecycle dead_state;
    REQUIRE(awaitShell(dead,dead_state)==0);
    Pair malformed;malformed.send("hello\t2");auto bad=malformed.adopt();tanks::OfflineLifecycle bad_state;
    REQUIRE(awaitShell(bad,bad_state)==0&&!bad_state.foreground());
    Pair oversized;oversized.send(std::string(513,'x'));auto ignored=oversized.adopt();tanks::OfflineLifecycle incomplete;
    REQUIRE(awaitShell(ignored,incomplete)==-1&&!incomplete.hello_seen());++completed_cases;
}
void after_state_startup() {
    Pair pair;pair.send("hello\t1");auto link=pair.adopt();tanks::OfflineLifecycle lifecycle;REQUIRE(awaitShell(link,lifecycle)==1);
    Options options;options.practice=true;SessionState state(options);const auto world=state.game.world();SessionHooks hooks{&state,nullptr};
    pair.send("pause");unsigned waits=0;
    REQUIRE(awaitShell(link,lifecycle,[&]{++waits;REQUIRE(pair.ack());same_world(state.game.world(),world);pair.disconnect();},&hooks)==0);
    REQUIRE(waits==1&&lifecycle.exiting());++completed_cases;
}
void backpressure() {
    Pair pair;pair.send("hello\t1");auto link=pair.adopt();tanks::OfflineLifecycle lifecycle;REQUIRE(awaitShell(link,lifecycle)==1);
    int size=1024;REQUIRE(setsockopt(link.fd(),SOL_SOCKET,SO_SNDBUF,&size,sizeof(size))==0);
    unsigned filled=0;for(;filled<8192;++filled){const auto n=::send(link.fd(),"hello\t1",7,MSG_NOSIGNAL|MSG_DONTWAIT);if(n<0&&(errno==EAGAIN||errno==EWOULDBLOCK))break;REQUIRE(n==7);}
    REQUIRE(filled>0&&filled<8192);SessionHooks hooks;
    pair.send("pause");REQUIRE(tanks::pump_lifecycle(link,lifecycle,hooks));REQUIRE(!lifecycle.foreground()&&link.pending_output());
    pair.send("resume");REQUIRE(tanks::pump_lifecycle(link,lifecycle,hooks));REQUIRE(lifecycle.foreground()&&!link.pending_output());
    unsigned drained=0;char bytes[16];for(;;){const auto n=recv(pair.peer,bytes,sizeof(bytes),MSG_DONTWAIT);if(n<0&&(errno==EAGAIN||errno==EWOULDBLOCK))break;REQUIRE(n==7&&std::string(bytes,std::size_t(n))=="hello\t1");++drained;}
    REQUIRE(drained==filled+1);pair.disconnect();tanks::pump_lifecycle(link,lifecycle,hooks);REQUIRE(lifecycle.exiting());++completed_cases;
}
void path_precedence_and_headless() {
    Environment env;env.set("LV_LINUX_FBDEV_DEVICE","/environment/lcd");env.set("LV_LINUX_KEYBOARD_DEVICE","/environment/keys");
    const auto defaults=arguments({"tyrell-tanks"});
    const auto linked=runtime_options(defaults,true);REQUIRE(!linked.restore_framebuffer_on_close);
#if TYRELL_TANKS_NATIVE
    REQUIRE(linked.framebuffer=="/environment/lcd"&&linked.input=="/environment/keys");
#else
    REQUIRE(linked.framebuffer=="auto"&&linked.input=="auto");
#endif
    const auto normal=runtime_options(defaults,false);REQUIRE(normal.framebuffer=="auto"&&normal.input=="auto"&&normal.restore_framebuffer_on_close);
    const auto explicit_paths=arguments({"tyrell-tanks","--framebuffer","/explicit/lcd","--input","/explicit/keys"});
    const auto chosen=runtime_options(explicit_paths,true);REQUIRE(chosen.framebuffer=="/explicit/lcd"&&chosen.input=="/explicit/keys");
    const auto explicit_auto=runtime_options(arguments({"tyrell-tanks","--framebuffer","auto","--input","auto"}),true);
    REQUIRE(explicit_auto.framebuffer=="auto"&&explicit_auto.input=="auto");
    Pair pair;pair.send("hello\t1");env.set("GH0ST_SHELL_FD",std::to_string(pair.endpoint));
    REQUIRE(entry({"tyrell-tanks","--headless","--practice","--frames","1"})==0);
    REQUIRE(getenv("GH0ST_SHELL_FD")!=nullptr&&fcntl(pair.endpoint,F_GETFD)>=0);++completed_cases;
}
void actual_loop(bool native=false) {
    Options options;options.practice=true;SessionState state(options);
    state.game.update(.05,{.7f,.4f,.3f,true});
    REQUIRE(state.game.world().tick>0&&state.game.world().tanks[0].cooldown>0);
    Pair pair;pair.send("hello\t1");auto link=pair.adopt();tanks::OfflineLifecycle lifecycle;REQUIRE(awaitShell(link,lifecycle)==1);
    tyrell::game::RuntimeOptions selected;selected.backend=tyrell::game::Backend::Headless;selected.fps=60;selected.restore_framebuffer_on_close=false;
#ifdef TYRELL_TANKS_NATIVE_TEST_IO
    if(native){tyrell::game::test::reset_native_fixture();selected=tyrell::game::test::native_fixture_options();selected.fps=60;selected.restore_framebuffer_on_close=false;}
#else
    (void)native;
#endif
    unsigned waits=0,frames=0;std::optional<tanks::World> saved;std::array<std::uint64_t,4> controls{};double paused_at=0;
    {
        tyrell::game::Runtime runtime(selected);
        const auto result=runSession(state,options,runtime,&link,&lifecycle,[&]{
            ++waits;REQUIRE(!runtime.foreground()&&runtime.suspended()&&pair.ack()&&state.frames==int(frames));
            REQUIRE(saved.has_value());same_world(state.game.world(),*saved);REQUIRE(state.controls==controls);
#ifdef TYRELL_TANKS_NATIVE_TEST_IO
            auto before=tyrell::game::test::native_io_stats();if(native)REQUIRE(before.open_keyboards==0);
#endif
            if(waits==1){
                std::this_thread::sleep_for(std::chrono::milliseconds(220));
                REQUIRE(runtime.time()-paused_at>=.2);same_world(state.game.world(),*saved);
#ifdef TYRELL_TANKS_NATIVE_TEST_IO
                if(native){const auto after=tyrell::game::test::native_io_stats();REQUIRE(before.commits==after.commits&&before.reads==after.reads);tyrell::game::test::write_foreground_pixels(0xbeef);}
#endif
                pair.send("pause");pair.send("redraw");
            }else if(waits==2){
#ifdef TYRELL_TANKS_NATIVE_TEST_IO
                if(native){tyrell::game::test::set_native_held({17,57});tyrell::game::test::queue_native_resume_key(17,1);tyrell::game::test::queue_native_resume_key(57,1);}
#endif
                pair.send("resume");
            }else if(waits==3){
#ifdef TYRELL_TANKS_NATIVE_TEST_IO
                if(native)tyrell::game::test::write_foreground_pixels(0x5a5a);
#endif
                pair.disconnect();
            }else throw std::runtime_error("Unexpected hidden wait");
        },[&](int frame){
            frames=unsigned(frame);
            if(frame==1){saved=state.game.world();controls=state.controls;paused_at=runtime.time();pair.send("pause");}
            else if(frame==2){same_world(state.game.world(),*saved);REQUIRE(runtime.elapsed()>=.2&&state.last_simulation_elapsed==0&&state.controls==controls);pair.send("pause");}
            else throw std::runtime_error("Rendered extra foreground frame");
        });
        REQUIRE(result.frames==2&&!result.saveAllowed&&waits==3&&state.frames==2&&!runtime.foreground()&&runtime.suspended());
    }
#ifdef TYRELL_TANKS_NATIVE_TEST_IO
    if(native){const auto stats=tyrell::game::test::native_io_stats();REQUIRE(stats.commits==2&&stats.fb_closes==1&&stats.unmaps==1&&tyrell::game::test::foreground_pixels_equal(0x5a5a));}
#endif
    ++completed_cases;
}
#ifdef TYRELL_TANKS_NATIVE_TEST_IO
volatile std::sig_atomic_t pause_sender=-1;
void queue_pause_on_signal(int) {
    if(pause_sender>=0) {const char packet[]="pause";(void)::write(int(pause_sender),packet,5);}
}
void failed_resume() {
    for(bool resync:{false,true}) {
        Options options;options.practice=true;SessionState state(options);std::optional<tanks::World> paused_world;
        Pair pair;pair.send("hello\t1");auto link=pair.adopt();tanks::OfflineLifecycle lifecycle;REQUIRE(awaitShell(link,lifecycle)==1);
        tyrell::game::test::reset_native_fixture();auto selected=tyrell::game::test::native_fixture_options();selected.restore_framebuffer_on_close=false;
        {
            tyrell::game::Runtime runtime(selected);unsigned waits=0;
            const auto result=runSession(state,options,runtime,&link,&lifecycle,[&]{
                ++waits;REQUIRE(waits==1&&pair.ack());tyrell::game::test::write_foreground_pixels(0x7281);
                if(resync)tyrell::game::test::fail_native_keyboard_resync(true);else tyrell::game::test::fail_native_keyboard_open(true);
                pair.send("resume");
            },[&](int frame){REQUIRE(frame==1);paused_world=state.game.world();pair.send("pause");});
            REQUIRE(result.frames==1&&!result.saveAllowed&&!runtime.foreground()&&lifecycle.exiting());
            REQUIRE(paused_world.has_value());same_world(state.game.world(),*paused_world);
        }
        REQUIRE(tyrell::game::test::native_io_stats().commits==1&&tyrell::game::test::foreground_pixels_equal(0x7281));
    }
    ++completed_cases;
}
void signal_during_poll() {
    for(bool terminate:{false,true}) {
        Options options;options.practice=true;options.autoplay=true;SessionState state(options);const auto original=state.game.world();
        Pair pair;pair.send("hello\t1");auto link=pair.adopt();tanks::OfflineLifecycle lifecycle;REQUIRE(awaitShell(link,lifecycle)==1);
        tyrell::game::test::reset_native_fixture();auto selected=tyrell::game::test::native_fixture_options();selected.restore_framebuffer_on_close=false;
        const auto previous=std::signal(SIGUSR1,queue_pause_on_signal);pause_sender=pair.peer;
        try {
            tyrell::game::Runtime runtime(selected);tyrell::game::test::write_foreground_pixels(0x369c);tyrell::game::test::queue_native_key(17,1);
            tyrell::game::test::signal_on_native_read(terminate?SIGTERM:SIGUSR1);
            const auto result=runSession(state,options,runtime,&link,&lifecycle,[&]{REQUIRE(!terminate&&pair.ack());pair.disconnect();});
            REQUIRE(result.frames==0&&!result.saveAllowed&&!runtime.foreground());same_world(state.game.world(),original);REQUIRE((state.controls==std::array<std::uint64_t,4>{}));
        }catch(...){pause_sender=-1;std::signal(SIGUSR1,previous);throw;}
        pause_sender=-1;std::signal(SIGUSR1,previous);
        REQUIRE(tyrell::game::test::native_io_stats().commits==0&&tyrell::game::test::foreground_pixels_equal(0x369c));
    }
    ++completed_cases;
}
void linked_menu_is_offline() {
    for(unsigned short code:{3,4}) { // evdev 2/3 accelerators, then ENTER.
        Options options;SessionState state(options);Pair pair;pair.send("hello\t1");auto link=pair.adopt();tanks::OfflineLifecycle lifecycle;REQUIRE(awaitShell(link,lifecycle)==1);
        tyrell::game::test::reset_native_fixture();auto selected=tyrell::game::test::native_fixture_options();selected.restore_framebuffer_on_close=false;
        {
            tyrell::game::Runtime runtime(selected);tyrell::game::test::queue_native_key(code,1);tyrell::game::test::queue_native_key(28,1);
            const auto result=runSession(state,options,runtime,&link,&lifecycle,{},[&](int frame){REQUIRE(frame==1);REQUIRE(state.in_menu&&!state.enter_address&&!state.game.is_host()&&!state.game.connected());pair.disconnect();});
            REQUIRE(result.frames==1&&!result.saveAllowed);
        }
    }
    ++completed_cases;
}
#if TYRELL_TANKS_NATIVE
void native_startup_entry() {
    tyrell::game::test::reset_native_fixture();Pair pair;pair.send("hello\t1");pair.send("pause");
    REQUIRE(setenv("GH0ST_SHELL_FD",std::to_string(pair.endpoint).c_str(),1)==0);
    std::exception_ptr shell_error;
    std::thread shell([&]{try{bool paused=false;const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(2);while(!paused&&std::chrono::steady_clock::now()<deadline){paused=pair.ack();if(!paused)std::this_thread::sleep_for(std::chrono::milliseconds(1));}REQUIRE(paused);pair.disconnect();}catch(...){shell_error=std::current_exception();pair.disconnect();}});
    const int result=entry({"tyrell-tanks","--practice"});pair.endpoint=-1;shell.join();if(shell_error)std::rethrow_exception(shell_error);
    REQUIRE(result==0);const auto stats=tyrell::game::test::native_io_stats();REQUIRE(stats.key_opens==0&&stats.commits==0&&stats.fb_closes==0);++completed_cases;
}
void native_entry_paths_and_lan() {
    for(bool invalid_link:{false,true}) {
        tyrell::game::test::reset_native_fixture();Environment env;
        char pattern[]="/tmp/sandline-unrelated-fd-XXXXXX";const int unrelated=mkstemp(pattern);REQUIRE(unrelated>2);unlink(pattern);
        const int flags=fcntl(unrelated,F_GETFD),status=fcntl(unrelated,F_GETFL);
        if(invalid_link)env.set("GH0ST_SHELL_FD",std::to_string(unrelated));else unsetenv("GH0ST_SHELL_FD");
        REQUIRE(entry({"tyrell-tanks","--practice","--framebuffer","/fixture/lcd","--input","/fixture/keyboard","--frames","1"})==0);
        const auto stats=tyrell::game::test::native_io_stats();
        REQUIRE(stats.commits==2&&stats.key_opens==1&&stats.fb_closes==1&&tyrell::game::test::foreground_pixels_equal(0x1111));
        REQUIRE(fcntl(unrelated,F_GETFD)==flags&&fcntl(unrelated,F_GETFL)==status);close(unrelated);
    }
    {
        tyrell::game::test::reset_native_fixture();Pair pair;pair.send("hello\t1");Environment env;
        env.set("GH0ST_SHELL_FD",std::to_string(pair.endpoint));env.set("LV_LINUX_FBDEV_DEVICE","/fixture/lcd");env.set("LV_LINUX_KEYBOARD_DEVICE","/fixture/keyboard");
        REQUIRE(entry({"tyrell-tanks","--practice","--frames","1"})==0);pair.endpoint=-1;
        const auto stats=tyrell::game::test::native_io_stats();REQUIRE(stats.key_opens==1&&stats.commits==1&&stats.fb_closes==1);
    }
    {
        tyrell::game::test::reset_native_fixture();tyrell::game::test::fail_native_keyboard_open(true,0x4a4a);
        Pair pair;pair.send("hello\t1");Environment env;env.set("GH0ST_SHELL_FD",std::to_string(pair.endpoint));
        REQUIRE(entry({"tyrell-tanks","--practice","--framebuffer","/fixture/lcd","--input","/fixture/keyboard","--frames","1"})==1);pair.endpoint=-1;
        const auto stats=tyrell::game::test::native_io_stats();REQUIRE(stats.commits==0&&stats.fb_closes==1&&stats.unmaps==1&&tyrell::game::test::foreground_pixels_equal(0x4a4a));
    }
    int socket_fd=socket(AF_INET,SOCK_DGRAM|SOCK_CLOEXEC,0);REQUIRE(socket_fd>=0);
    sockaddr_in address{};address.sin_family=AF_INET;address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);address.sin_port=0;
    REQUIRE(bind(socket_fd,reinterpret_cast<sockaddr*>(&address),sizeof(address))==0);socklen_t size=sizeof(address);REQUIRE(getsockname(socket_fd,reinterpret_cast<sockaddr*>(&address),&size)==0);
    const auto port=std::to_string(ntohs(address.sin_port));
    for(const auto& flags:std::vector<std::vector<std::string>>{{"--host","--frames","1"},{"--join","127.0.0.1","--frames","1"},{"--server","--seconds","1"}}){
        tyrell::game::test::reset_native_fixture();Pair pair;pair.send("hello\t1");Environment env;env.set("GH0ST_SHELL_FD",std::to_string(pair.endpoint));
        std::vector<std::string> values{"tyrell-tanks","--port",port};values.insert(values.end(),flags.begin(),flags.end());
        REQUIRE(entry(values)==2);pair.endpoint=-1;const auto stats=tyrell::game::test::native_io_stats();REQUIRE(stats.key_opens==0&&stats.commits==0&&stats.fb_closes==0);
    }
    close(socket_fd);++completed_cases;
}
#endif
#endif
}
int main(int argc,char** argv) {
    try {
        SetTraceLogLevel(LOG_WARNING);
        std::string report;
        for(int n=1;n<argc;++n){REQUIRE(std::string(argv[n])=="--report"&&n+1<argc);report=argv[++n];}
        policy_ordering();startup_and_batches();after_state_startup();backpressure();path_precedence_and_headless();actual_loop();
#ifdef TYRELL_TANKS_NATIVE_TEST_IO
        actual_loop(true);failed_resume();signal_during_poll();linked_menu_is_offline();
#if TYRELL_TANKS_NATIVE
        native_startup_entry();native_entry_paths_and_lan();
#endif
#endif
        if(!report.empty()) {
            std::ofstream out(report);REQUIRE(bool(out<<"{\"schema\":1,\"kind\":\"a3-sandline-lifecycle\",\"status\":\"passed\",\"cases\":"<<completed_cases
                <<",\"actual_session_loop\":true,\"hidden_world_preserved\":true,\"resume_simulation_debt_dropped\":true,\"native_fixture\":"
#ifdef TYRELL_TANKS_NATIVE_TEST_IO
                <<"true"
#else
                <<"false"
#endif
                <<",\"hardware_qualified\":false,\"physical_testing\":\"pending\"}\n"));
        }
        std::cout<<"PASS: Sandline offline lifecycle, actual session, hidden state/input/render gates and bounded shell batches ("<<completed_cases<<" cases)\n";
        return 0;
    }catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}
}
