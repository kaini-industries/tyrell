#define main chess_application_main
#include "../src/main.cpp"
#undef main

#include <atomic>

#define REQUIRE(value) do {if(!(value))throw std::runtime_error(std::string("offline lifecycle: ")+#value);} while(false)
namespace {
struct FakeHooks {
 bool owned=true,allowResume=true;unsigned pauses=0,resumes=0,redraws=0,abandons=0;
 void pause(){owned=false;++pauses;}
 bool resume(){++resumes;owned=allowResume;return owned;}
 void redraw(){++redraws;}
 void abandon(){owned=false;++abandons;}
};
struct FakeLink {
 FakeHooks& hooks;unsigned acks=0,cancels=0;
 void acknowledge_paused(){REQUIRE(!hooks.owned);++acks;}
 void cancel_paused(){++cancels;}
};
void ordering(){
 chess::OfflineLifecycle state;FakeHooks hooks;FakeLink link{hooks};
 using E=tyrell::app::ShellEvent;
 state.handle(E::Hello,link,hooks);REQUIRE(state.hello_seen());
 state.handle(E::Resume,link,hooks);REQUIRE(hooks.resumes==0);
 state.handle(E::Pause,link,hooks);state.handle(E::Pause,link,hooks);
 REQUIRE(hooks.pauses==1&&link.acks==2&&!state.foreground());
 state.handle(E::Redraw,link,hooks);REQUIRE(hooks.redraws==1&&!hooks.owned);
 state.handle(E::Resume,link,hooks);REQUIRE(state.foreground()&&hooks.resumes==1);
 state.handle(E::Pause,link,hooks);hooks.allowResume=false;
 state.handle(E::Resume,link,hooks);REQUIRE(state.exiting()&&!state.foreground()&&hooks.abandons==1);
 state.handle(E::Resume,link,hooks);REQUIRE(hooks.resumes==2);
 chess::OfflineLifecycle lost;FakeHooks lostHooks;FakeLink lostLink{lostHooks};
 lost.handle(E::Lost,lostLink,lostHooks);REQUIRE(lost.exiting()&&!lost.foreground()&&!lostHooks.owned);
}
void local(App& app,TimeControl control){app.setup=chess::Options{};app.setup.mode=Mode::Local;app.setup.time=control;app.startGame();}
void clocks(const std::filesystem::path& path){
 uint64_t now=100;
 App app(path,-1,true,[&]{return now;});local(app,TimeControl::Bullet1);
 app.game.clock.setRemainingMs(PieceColor::White,100,app.clockNow());
 now+=30;app.suspendSession();REQUIRE(app.game.clock.remainingMs(PieceColor::White)==70);
 REQUIRE(app.game.clock.isRunning());
 const auto before=chess::fen(app.game.board);now+=69;app.resumeSession(2);
 REQUIRE(!app.game.over()&&app.game.clock.remainingMs(PieceColor::White)==1);
 app.suspendSession();now+=1;app.resumeSession(3);
 REQUIRE(app.game.over()&&app.game.clock.hasFlaggedSide());
 app.key(KEY_ENTER);REQUIRE(chess::fen(app.game.board)==before);

 now=uint64_t(UINT32_MAX)-20;local(app,TimeControl::Bullet1);
 app.game.clock.setRemainingMs(PieceColor::White,50,app.clockNow());app.suspendSession();
 now+=49;app.resumeSession(4);REQUIRE(!app.game.over()&&app.game.clock.remainingMs(PieceColor::White)==1);
 app.suspendSession();now+=(uint64_t(1)<<32)+5;app.resumeSession(5);
 REQUIRE(app.game.over());

 now=100;local(app,TimeControl::Bullet1);app.game.clock.pause(app.clockNow());
 const auto remaining=app.game.clock.remainingMs(PieceColor::White);app.suspendSession();
 now+=(uint64_t(1)<<32)+100;app.resumeSession(6);
 REQUIRE(!app.game.over()&&app.game.clock.isPaused()&&app.game.clock.remainingMs(PieceColor::White)==remaining);
}
void savesAndLan(const std::filesystem::path& path){
 uint64_t now=100;App app(path,-1,true,[&]{return now;});local(app,TimeControl::None);
 const auto bytes=std::filesystem::file_size(path/"active.save");
 const auto good=app.dir;app.dir=path/"blocked";{std::ofstream out(app.dir);out<<"regular file";}
 app.pendingSettings=true;app.suspendSession();REQUIRE(app.lifecyclePaused);
 app.key(KEY_Q);REQUIRE(!app.quit);app.update(100);REQUIRE(!app.lifecycleSaveAttempted);
 now+=1000;app.resumeSession(101);app.update(101);REQUIRE(app.lifecycleSavePending);
 const auto attempted=app.lastSave;now+=20;app.update(102);REQUIRE(app.lastSave==attempted);
 app.dir=good;now+=5000;app.update(107);REQUIRE(!app.lifecycleSavePending);
 REQUIRE(std::filesystem::file_size(path/"active.save")==bytes);
 app.screen=Screen::Home;app.menu=4;app.activate();REQUIRE(app.screen==Screen::Home&&!app.lan.listening());
 app.setupMenu(Mode::Host);REQUIRE(app.setup.mode==Mode::Local);
 app.setup.mode=Mode::Host;app.startGame();REQUIRE(app.game.options.mode==Mode::Local);
 app.page="address";app.screen=Screen::Network;app.networkAddress="127.0.0.1";app.menu=1;app.activate();
 REQUIRE(!app.lan.connected()&&!app.lan.listening());
}
void staleAi(const std::filesystem::path& path){
 App app(path,-1,true);local(app,TimeControl::None);
 app.workerHint=true;app.workerGeneration=app.generation;app.workerHash=ChessZobrist::hash(app.game.board);
 auto promise=std::make_shared<std::promise<Move>>();app.worker=promise->get_future();
 app.suspendSession();MoveList moves;ChessRules::generateLegal(app.game.board,moves);promise->set_value(moves.moves[0]);
 const auto position=chess::fen(app.game.board);app.update(1000);REQUIRE(app.worker.valid());
 app.resumeSession(1000);app.update(1000);REQUIRE(!app.worker.valid());
 REQUIRE(chess::fen(app.game.board)==position&&!app.hintFrom.valid());
 app.repeatKey=KEY_LEFT;app.animatedPiece={PieceType::Queen,PieceColor::White};app.suspendSession();
 REQUIRE(app.repeatKey==0&&app.animatedPiece.empty());
}
}
int main(){
 auto root=std::filesystem::temp_directory_path()/("tyrell-offline-lifecycle-"+std::to_string(monotonicMs()));
 try{ordering();clocks(root/"clock");savesAndLan(root/"save");staleAi(root/"ai");std::filesystem::remove_all(root);return 0;}
 catch(const std::exception& error){std::filesystem::remove_all(root);std::cerr<<error.what()<<'\n';return 1;}
}
