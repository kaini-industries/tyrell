#define main chess_application_main
#include "../src/main.cpp"
#undef main

#include <sys/socket.h>
#include <csignal>
#include <fcntl.h>
#include <unistd.h>
#ifdef TYRELL_CHESS_NATIVE_TEST_IO
#include "native_io_fixture.hpp"
#endif

#define REQUIRE(value) do {if(!(value))throw std::runtime_error(std::string("shell lifecycle: ")+#value);} while(false)
namespace {
struct Pair {
 int peer=-1,endpoint=-1;
 Pair(){int descriptors[2];REQUIRE(socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0,descriptors)==0);peer=descriptors[0];endpoint=descriptors[1];}
 ~Pair(){if(peer>=0)close(peer);if(endpoint>=0)close(endpoint);unsetenv("GH0ST_SHELL_FD");}
 void send(const std::string& value){REQUIRE(::send(peer,value.data(),value.size(),MSG_NOSIGNAL|MSG_DONTWAIT)==ssize_t(value.size()));}
 tyrell::app::ShellLink adopt(){setenv("GH0ST_SHELL_FD",std::to_string(endpoint).c_str(),1);auto link=tyrell::app::ShellLink::from_environment();REQUIRE(link.connected()||link.closed());endpoint=-1;return link;}
 bool ack(){bool found=false;char data[512];for(unsigned n=0;n<16;++n){auto count=recv(peer,data,sizeof(data),MSG_DONTWAIT);if(count<0&&(errno==EAGAIN||errno==EWOULDBLOCK))return found;REQUIRE(count>0);std::string packet(data,size_t(count));REQUIRE(packet=="hello\t1"||packet=="paused");if(packet=="paused")found=true;}throw std::runtime_error("unbounded output");}
 void disconnect(){close(peer);peer=-1;}
};
void startup(){
 Pair pair;pair.send("hello\t1");for(unsigned n=0;n<40;++n)pair.send("future");pair.send("pause");
 auto link=pair.adopt();chess::OfflineLifecycle lifecycle;unsigned waits=0;
 REQUIRE(awaitShell(link,lifecycle,[&]{++waits;REQUIRE(pair.ack());pair.disconnect();})==0);
 REQUIRE(waits==1&&lifecycle.exiting()&&!lifecycle.foreground());
 Pair closed;closed.disconnect();auto dead=closed.adopt();chess::OfflineLifecycle lost;
 REQUIRE(awaitShell(dead,lost)==0);
}
void afterAppStartupGate(const std::filesystem::path& directory){
 Pair pair;pair.send("hello\t1");auto link=pair.adopt();chess::OfflineLifecycle lifecycle;
 REQUIRE(awaitShell(link,lifecycle)==1);
 App app(directory,-1,true);SessionHooks hooks{&app,nullptr};
#ifdef TYRELL_CHESS_NATIVE_TEST_IO
 tyrell::game::test::reset_native_fixture();
#endif
 // App loading can take time; this packet arrives after the first hello gate.
 pair.send("pause");unsigned waits=0;
 REQUIRE(awaitShell(link,lifecycle,[&]{
   ++waits;REQUIRE(app.lifecyclePaused&&pair.ack());pair.disconnect();
 },&hooks)==0);
 REQUIRE(waits==1&&lifecycle.exiting()&&app.lifecyclePaused);
#ifdef TYRELL_CHESS_NATIVE_TEST_IO
 const auto stats=tyrell::game::test::native_io_stats();
 REQUIRE(stats.key_opens==0&&stats.commits==0&&stats.fb_closes==0);
#endif
}
void backpressure(){
 Pair pair;pair.send("hello\t1");auto link=pair.adopt();chess::OfflineLifecycle lifecycle;
 REQUIRE(awaitShell(link,lifecycle)==1);
 int buffer=1024;REQUIRE(setsockopt(link.fd(),SOL_SOCKET,SO_SNDBUF,&buffer,sizeof(buffer))==0);
 unsigned filled=0;
 for(;filled<8192;++filled){
   const auto count=::send(link.fd(),"hello\t1",7,MSG_NOSIGNAL|MSG_DONTWAIT);
   if(count<0&&(errno==EAGAIN||errno==EWOULDBLOCK))break;
   REQUIRE(count==7);
 }
 REQUIRE(filled>0&&filled<8192);
 SessionHooks hooks;pair.send("pause");REQUIRE(chess::pump_lifecycle(link,lifecycle,hooks));
 REQUIRE(!lifecycle.foreground()&&link.pending_output());
 // Resume supersedes an acknowledgment that could not reach the shell.
 pair.send("resume");REQUIRE(chess::pump_lifecycle(link,lifecycle,hooks));
 REQUIRE(lifecycle.foreground()&&!link.pending_output());
 char packet[16];unsigned drained=0;
 for(;;){const auto count=recv(pair.peer,packet,sizeof(packet),MSG_DONTWAIT);
   if(count<0&&(errno==EAGAIN||errno==EWOULDBLOCK))break;
   REQUIRE(count==7&&std::string(packet,size_t(count))=="hello\t1");++drained;
 }
 REQUIRE(drained==filled+1);
 pair.disconnect();chess::pump_lifecycle(link,lifecycle,hooks);
 REQUIRE(lifecycle.exiting()&&!lifecycle.foreground());
}
#if TYRELL_CHESS_NATIVE && defined(TYRELL_CHESS_NATIVE_TEST_IO)
void nativeStartupEntry(const std::filesystem::path& directory){
 tyrell::game::test::reset_native_fixture();
 Pair pair;pair.send("hello\t1");pair.send("pause");
 setenv("GH0ST_SHELL_FD",std::to_string(pair.endpoint).c_str(),1);
 std::thread shell([&]{
   bool paused=false;const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(1);
   while(!paused&&std::chrono::steady_clock::now()<deadline){paused=pair.ack();if(!paused)std::this_thread::sleep_for(std::chrono::milliseconds(1));}
   REQUIRE(paused);pair.disconnect();
 });
 std::vector<std::string> values{"tyrell-chess","--state-dir",directory.string()};
 std::vector<char*> args;for(auto& value:values)args.push_back(value.data());
 REQUIRE(chess_application_main(int(args.size()),args.data())==0);pair.endpoint=-1;shell.join();
 const auto stats=tyrell::game::test::native_io_stats();
 REQUIRE(!std::filesystem::exists(directory)&&stats.key_opens==0&&stats.commits==0&&stats.fb_closes==0);
}
void nativeForegroundEntry(const std::filesystem::path& directory){
 tyrell::game::test::reset_native_fixture();Pair pair;pair.send("hello\t1");
 setenv("GH0ST_SHELL_FD",std::to_string(pair.endpoint).c_str(),1);
 setenv("LV_LINUX_FBDEV_DEVICE","/fixture/lcd",1);setenv("LV_LINUX_KEYBOARD_DEVICE","/fixture/keyboard",1);
 std::vector<std::string> values{"tyrell-chess","--state-dir",directory.string(),"--frames","1"};
 std::vector<char*> args;for(auto& value:values)args.push_back(value.data());
 REQUIRE(chess_application_main(int(args.size()),args.data())==0);pair.endpoint=-1;
 unsetenv("LV_LINUX_FBDEV_DEVICE");unsetenv("LV_LINUX_KEYBOARD_DEVICE");
 const auto stats=tyrell::game::test::native_io_stats();
 REQUIRE(std::filesystem::exists(directory)&&stats.key_opens==1&&stats.key_closes==1&&stats.commits==1&&stats.fb_closes==1);
}
#endif
void actualLoop(const std::filesystem::path& directory,bool native=false){
 uint64_t clock=100;App app(directory,-1,true,[&]{return clock;});app.setup.mode=Mode::Local;app.setup.time=TimeControl::Bullet1;app.startGame();
 app.game.clock.setRemainingMs(PieceColor::White,100,app.clockNow());
 const auto before=chess::fen(app.game.board);
 Pair pair;pair.send("hello\t1");auto link=pair.adopt();chess::OfflineLifecycle lifecycle;
 REQUIRE(awaitShell(link,lifecycle)==1);
 tyrell::game::RuntimeOptions options;options.backend=tyrell::game::Backend::Headless;options.fps=0;
 options.restore_framebuffer_on_close=false;
#ifdef TYRELL_CHESS_NATIVE_TEST_IO
 if(native){tyrell::game::test::reset_native_fixture();options=tyrell::game::test::native_fixture_options();options.restore_framebuffer_on_close=false;}
#else
 (void)native;
#endif
 Args args;unsigned waits=0,seenFrames=0;
 {
 tyrell::game::Runtime runtime(options);
 const auto result=runSession(app,args,runtime,&link,&lifecycle,[&]{
   ++waits;REQUIRE(!runtime.foreground()&&runtime.suspended()&&app.lifecyclePaused);REQUIRE(pair.ack());
   if(waits==1){
#ifdef TYRELL_CHESS_NATIVE_TEST_IO
     if(native)tyrell::game::test::write_foreground_pixels(0xbeef);
#endif
     clock+=100;pair.send("pause");pair.send("redraw");}
   else if(waits==2){REQUIRE(seenFrames==1);
#ifdef TYRELL_CHESS_NATIVE_TEST_IO
     if(native){tyrell::game::test::set_native_held({16});tyrell::game::test::queue_native_resume_key(16,1);} // evdev Q
#endif
     pair.send("resume");}
   else if(waits==3){REQUIRE(seenFrames==2);
#ifdef TYRELL_CHESS_NATIVE_TEST_IO
     if(native)tyrell::game::test::write_foreground_pixels(0x5a5a);
#endif
     pair.disconnect();}
   else throw std::runtime_error("unexpected wait");
 },[&](int frame){
   seenFrames=unsigned(frame);
   if(frame==1){pair.send("pause");}
   else if(frame==2){REQUIRE(!app.quit&&app.game.over()&&app.game.clock.hasFlaggedSide());REQUIRE(chess::fen(app.game.board)==before);pair.send("pause");}
   else throw std::runtime_error("rendered an extra foreground frame");
 });
 REQUIRE(result.frames==2&&!result.saveAllowed&&waits==3);
 REQUIRE(!runtime.foreground()&&runtime.suspended()&&app.lifecyclePaused);
 }
#ifdef TYRELL_CHESS_NATIVE_TEST_IO
 if(native){
   REQUIRE(tyrell::game::test::foreground_pixels_equal(0x5a5a));
   const auto stats=tyrell::game::test::native_io_stats();
   REQUIRE(stats.fb_closes==1&&stats.unmaps==1&&stats.key_closes>=1);
 }
#endif
}
#ifdef TYRELL_CHESS_NATIVE_TEST_IO
void signalDuringInputPoll(const std::filesystem::path& directory){
 App app(directory,-1,true);app.setup.mode=Mode::Local;app.startGame();
 const auto position=chess::fen(app.game.board);const auto flipped=app.flipped,contrast=app.contrast;
 Pair pair;pair.send("hello\t1");auto link=pair.adopt();chess::OfflineLifecycle lifecycle;
 REQUIRE(awaitShell(link,lifecycle)==1);
 tyrell::game::test::reset_native_fixture();auto options=tyrell::game::test::native_fixture_options();
 options.restore_framebuffer_on_close=false;
 {
   tyrell::game::Runtime runtime(options);Args args;args.script="f";
   tyrell::game::test::write_foreground_pixels(0x369c);
   tyrell::game::test::queue_native_key(20,1); // evdev T: would change contrast.
   tyrell::game::test::signal_on_native_read(SIGTERM);
   const auto result=runSession(app,args,runtime,&link,&lifecycle);
   REQUIRE(result.frames==0&&!result.saveAllowed&&!runtime.foreground());
   REQUIRE(app.time==0&&app.flipped==flipped&&app.contrast==contrast);
   REQUIRE(chess::fen(app.game.board)==position&&app.game.history.empty());
 }
 REQUIRE(tyrell::game::test::foreground_pixels_equal(0x369c));
 REQUIRE(tyrell::game::test::native_io_stats().commits==0);
}
void exceptionalAndQueuedSignalExit(const std::filesystem::path& directory){
 for(bool throwing:{false,true}){
   App app(directory/(throwing?"throw":"signal"),-1,true);Pair pair;pair.send("hello\t1");
   auto link=pair.adopt();chess::OfflineLifecycle lifecycle;REQUIRE(awaitShell(link,lifecycle)==1);
   tyrell::game::test::reset_native_fixture();auto options=tyrell::game::test::native_fixture_options();
   options.restore_framebuffer_on_close=false;
   {
     tyrell::game::Runtime runtime(options);Args args;bool caught=false;
     try{
       const auto result=runSession(app,args,runtime,&link,&lifecycle,{},[&](int frame){
         REQUIRE(frame==1);tyrell::game::test::write_foreground_pixels(0x2468);pair.send("pause");
         if(throwing)throw std::runtime_error("injected session failure");
         std::raise(SIGTERM);
       });
       REQUIRE(!throwing&&!result.saveAllowed);
     }catch(const std::runtime_error&){caught=true;REQUIRE(throwing);}
     REQUIRE(caught==throwing&&!runtime.foreground());
   }
   REQUIRE(tyrell::game::test::foreground_pixels_equal(0x2468));
   REQUIRE(tyrell::game::test::native_io_stats().commits==1);
 }
}
#endif
}
int main(){
 auto path=std::filesystem::temp_directory_path()/("tyrell-shell-chess-"+std::to_string(monotonicMs()));
 try{startup();afterAppStartupGate(path/"loaded-before-pause");backpressure();
#if TYRELL_CHESS_NATIVE && defined(TYRELL_CHESS_NATIVE_TEST_IO)
 nativeStartupEntry(path/"not-created");
 nativeForegroundEntry(path/"native-entry");
#endif
 actualLoop(path/"headless");
#ifdef TYRELL_CHESS_NATIVE_TEST_IO
 actualLoop(path/"native",true);
 signalDuringInputPoll(path/"poll-signal");
 exceptionalAndQueuedSignalExit(path/"exits");
#endif
 std::filesystem::remove_all(path);return 0;}
 catch(const std::exception& error){std::filesystem::remove_all(path);std::cerr<<error.what()<<'\n';return 1;}
}
