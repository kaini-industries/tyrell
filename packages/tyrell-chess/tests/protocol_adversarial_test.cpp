// Exercise independent peers with legitimate packets crossing in flight.
#define main chess_application_main
#include "../src/main.cpp"
#undef main
#define CHECK(expression) do { if (!(expression)) throw std::runtime_error( \
    std::string(__FILE__)+":"+std::to_string(__LINE__)+": " #expression); } while(false)
namespace {
template<class Predicate> void until(Predicate predicate) {
 const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(3);
 while(!predicate()) {
  if(std::chrono::steady_clock::now()>deadline)throw std::runtime_error("Protocol test timeout");
  std::this_thread::sleep_for(std::chrono::milliseconds(2));
 }
}
void connect(App& host,App& joiner,TimeControl tc) {
 host.setup=chess::Options{};host.setup.mode=Mode::Host;host.setup.time=tc;
 std::string error;CHECK(host.lan.host(error));host.networkHost=true;
 host.page="waiting";host.screen=Screen::Network;host.networkStartedAt=1;
 CHECK(joiner.lan.join("127.0.0.1",error));joiner.networkHost=false;
 joiner.page="waiting";joiner.screen=Screen::Network;joiner.networkStartedAt=1;
 until([&]{host.update(2);joiner.update(2);return host.page=="pair"&&joiner.page=="pair";});
 host.menu=0;host.activate();joiner.menu=0;joiner.activate();
 until([&]{host.update(2);joiner.update(2);return host.networkPlaying&&joiner.networkPlaying;});
}
void whiteMove(App& host,const std::string& text) {
 MoveList legal;ChessRules::generateLegal(host.game.board,legal);
 for(unsigned i=0;i<legal.count;++i)if(chess::uci(legal.moves[i])==text){CHECK(host.apply(legal.moves[i]));return;}
 throw std::runtime_error("Illegal test move");
}
void crossedDrawAcceptance(const std::filesystem::path& dir) {
 App host(dir/"draw-host"),joiner(dir/"draw-joiner");connect(host,joiner,TimeControl::None);
 host.pauseMenu();host.menu=5;host.activate();
 until([&]{host.update(2);joiner.update(2);return joiner.incomingDrawOffer;});
 // The receiver accepts using the visible prompt. Before that TCP packet is
 // processed, the offerer makes a move. Neither peer has violated the protocol.
 joiner.key(KEY_Y);joiner.key(KEY_DOWN);joiner.key(KEY_ENTER);whiteMove(host,"e2e4");
 for(int i=0;i<20;++i){joiner.lan.poll();host.update(2);joiner.update(2);std::this_thread::sleep_for(std::chrono::milliseconds(2));}
 CHECK(host.page!="disconnected"&&joiner.page!="disconnected");
 CHECK(host.game.result==joiner.game.result);
 CHECK(chess::fen(host.game.board)==chess::fen(joiner.game.board));
}
void moveArrivesAfterFlag(const std::filesystem::path& dir) {
 App host(dir/"flag-host"),joiner(dir/"flag-joiner");connect(host,joiner,TimeControl::Blitz3);
 whiteMove(host,"e2e4");
 until([&]{host.update(2);joiner.update(2);return joiner.game.history.size()==1;});
 CHECK(host.game.clock.setRemainingMs(PieceColor::Black,1,nowMs()));
 CHECK(joiner.lan.send("REQ 1 e7e5"));joiner.lan.poll();
 std::this_thread::sleep_for(std::chrono::milliseconds(5));
 host.network();
 until([&]{host.update(2);joiner.update(2);return joiner.game.over()||joiner.page=="disconnected";});
 CHECK(host.screen==Screen::Board&&joiner.screen==Screen::Board);
 CHECK(host.game.result=="1-0"&&joiner.game.result=="1-0");
 CHECK(host.game.reason=="Time expired"&&joiner.game.reason=="Time expired");
 CHECK(host.game.history.size()==1&&joiner.game.history.size()==1);
 CHECK(!host.game.clock.isRunning()&&!joiner.game.clock.isRunning());
}
void drawAcceptanceArrivesAfterFlag(const std::filesystem::path& dir) {
 App host(dir/"draw-flag-host"),joiner(dir/"draw-flag-joiner");connect(host,joiner,TimeControl::Bullet1);
 host.pauseMenu();host.menu=5;host.activate();
 until([&]{host.update(2);joiner.update(2);return joiner.incomingDrawOffer;});
 CHECK(host.game.clock.setRemainingMs(PieceColor::White,1,nowMs()));
 joiner.key(KEY_Y);joiner.key(KEY_DOWN);joiner.key(KEY_ENTER);joiner.lan.poll();
 std::this_thread::sleep_for(std::chrono::milliseconds(5));
 // network() precedes the frame's general clock update. Acceptance must settle
 // the authoritative host clock itself instead of overwriting its elapsed flag.
 host.network();
 until([&]{host.update(2);joiner.update(2);return joiner.game.over()||joiner.page=="disconnected";});
 CHECK(host.screen==Screen::Board&&joiner.screen==Screen::Board);
 CHECK(host.game.result=="0-1"&&joiner.game.result=="0-1");
 CHECK(host.game.reason=="Time expired"&&joiner.game.reason=="Time expired");
 CHECK(host.game.history.empty()&&joiner.game.history.empty());
 CHECK(!host.game.clock.isRunning()&&!joiner.game.clock.isRunning());
}
void terminalDrawAction(const std::filesystem::path& dir) {
 App host(dir/"terminal-host"),joiner(dir/"terminal-joiner");connect(host,joiner,TimeControl::None);
 host.game.finish("1/2-1/2","Draw agreed",nowMs());host.finished();
 until([&]{host.update(2);joiner.update(2);return joiner.game.over();});
 host.pauseMenu();host.menu=5;host.activate();
 for(int i=0;i<10;++i){host.update(2);joiner.update(2);std::this_thread::sleep_for(std::chrono::milliseconds(2));}
 CHECK(host.page!="disconnected"&&joiner.page!="disconnected");
 CHECK(host.game.result=="1/2-1/2"&&joiner.game.result=="1/2-1/2");
}

}
int main() {
 const auto dir=std::filesystem::temp_directory_path()/("tyrell-chess-protocol-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
 try {
  crossedDrawAcceptance(dir);moveArrivesAfterFlag(dir);drawAcceptanceArrivesAfterFlag(dir);terminalDrawAction(dir);
  std::filesystem::remove_all(dir);std::puts("Protocol adversarial tests passed: crossed acceptance, flag fall during move/draw transit, terminal menu actions.");return 0;
 }catch(const std::exception& e){std::filesystem::remove_all(dir);std::fprintf(stderr,"%s\n",e.what());return 1;}
}
