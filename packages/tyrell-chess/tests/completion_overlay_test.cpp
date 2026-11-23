// Independent regressions: authoritative completion must replace obsolete
// action dialogs, and canceling a live-match exit must restore its origin.
#define main chess_application_main
#include "../src/main.cpp"
#undef main
#define CHECK(expression) do {if(!(expression))throw std::runtime_error( \
 std::string(__FILE__)+":"+std::to_string(__LINE__)+": " #expression);}while(false)
namespace {
void start(App& app,bool online=false) {
 app.setup.mode=online?Mode::Host:Mode::Local;app.setup.time=TimeControl::Bullet1;
 app.startGame();if(online){app.networkHost=true;app.networkPlaying=true;}
}
void completedBoard(App& app,const std::string& score,const std::string& reason) {
 CHECK(app.game.over()&&app.game.result==score&&app.game.reason==reason);
 CHECK(app.screen==Screen::Board&&app.page.empty()&&!app.review&&!app.quit);
 CHECK(!app.selected.valid()&&!app.legal.count&&app.promotions.empty());
 CHECK(!app.hintFrom.valid()&&!app.hintTo.valid()&&app.animatedPiece.empty());
 CHECK(app.repeatKey==0&&app.menu==0&&!app.game.clock.isRunning());
 // The old confirmation's Enter can no longer commit a candidate or change
 // the terminal outcome; it opens the result menu and Continue returns safely.
 const auto count=app.game.history.size();app.key(KEY_ENTER);app.key(KEY_ENTER);
 CHECK(app.game.result==score&&app.game.reason==reason&&app.game.history.size()==count);
 CHECK(!app.game.clock.isRunning());
}
void expire(App& app) {
 app.repeatKey=KEY_RIGHT;app.hintFrom={0,0};app.hintTo={1,1};
 CHECK(app.game.clock.setRemainingMs(PieceColor::White,1,nowMs()));
 std::this_thread::sleep_for(std::chrono::milliseconds(4));app.update(1);
 completedBoard(app,"0-1","Time expired");
}
void promotion(const std::filesystem::path& dir,bool submitAtFlag=false) {
 App app(dir);start(app);
 for(const auto* move:{"a2a4","h7h5","a4a5","h5h4","a5a6","h4h3","a6b7","h3g2"})CHECK(app.game.moveUci(move,nowMs()));
 app.cursor={1,6};app.key(KEY_ENTER);app.key(KEY_SPACE);app.key(KEY_ENTER);
 CHECK(app.screen==Screen::Promotion&&app.promotions.size()==4);
 if(submitAtFlag) {
  // Input can cross the deadline after the frame's update. The rejected move
  // still needs the same completion cleanup and archive as update-driven expiry.
  CHECK(app.game.clock.setRemainingMs(PieceColor::White,1,nowMs()));
  std::this_thread::sleep_for(std::chrono::milliseconds(4));app.key(KEY_ENTER);
  app.update(1);completedBoard(app,"0-1","Time expired");
  CHECK(app.game.history.size()==8);
  std::string error;Game saved;CHECK(saved.load(dir/"active.save",nowMs(),error));
  CHECK(saved.result=="0-1"&&saved.reason=="Time expired");
  bool archived=false;
  for(const auto& entry:std::filesystem::directory_iterator(dir/"games"))archived|=entry.path().extension()==".save";
  CHECK(archived);
 } else expire(app);
 CHECK(app.game.board.at(1,6).type==PieceType::Pawn);
}
void castle(const std::filesystem::path& dir) {
 App app(dir);chess::Options options;options.mode=Mode::Local;options.time=TimeControl::Bullet1;
 options.variant=ChessVariant::Chess960;options.position=12;
 app.game.start(options,nowMs());app.haveGame=true;app.screen=Screen::Board;
 for(const auto* move:{"h2h3","a7a6","g1h2","a6a5"})CHECK(app.game.moveUci(move,nowMs()));
 app.cursor={5,0};app.key(KEY_ENTER);app.cursor={6,0};app.key(KEY_ENTER);
 CHECK(app.screen==Screen::Confirm&&app.dialog=="castle"&&app.promotions.size()==2);
 expire(app);CHECK(app.game.board.at(5,0).type==PieceType::King);
}
void timedDialogs(const std::filesystem::path& dir) {
 for(const std::string action:{"resign","draw","accept-draw","leave-menu","leave-exit","help"}) {
  const bool online=action=="accept-draw"||action=="leave-menu"||action=="leave-exit";
  App app(dir/action);start(app,online);
  if(action=="help"){app.key(KEY_ENTER);app.key(KEY_I);CHECK(app.screen==Screen::Help);}
  else if(action=="accept-draw"){app.incomingDrawOffer=true;app.key(KEY_Y);CHECK(app.dialog==action);}
  else if(action=="leave-exit"){app.key(KEY_Q);CHECK(app.dialog==action);}
  else {app.pauseMenu();app.menu=action=="resign"?4:action=="draw"?5:1;app.activate();CHECK(app.dialog==action);}
  app.promotions.push_back({});expire(app);CHECK(!app.incomingDrawOffer&&!app.drawOffered);
 }
}
void acceptDrawAtFlag(const std::filesystem::path& dir) {
 for(const bool online:{false,true}) {
  App app(dir/(online?"lan":"local"));start(app,online);
  if(online){app.incomingDrawOffer=true;app.key(KEY_Y);}
  else {app.pauseMenu();app.menu=5;app.activate();}
  CHECK(app.screen==Screen::Confirm);app.menu=1;
  CHECK(app.game.clock.setRemainingMs(PieceColor::White,1,nowMs()));
  std::this_thread::sleep_for(std::chrono::milliseconds(4));app.key(KEY_ENTER);
  completedBoard(app,"0-1","Time expired");
  if(!online) {
   std::string error;Game saved;CHECK(saved.load(app.dir/"active.save",nowMs(),error));
   CHECK(saved.result=="0-1"&&saved.reason=="Time expired");
  }
 }
}
void remoteCompletion(const std::filesystem::path& dir) {
 App app(dir);start(app,true);app.pauseMenu();app.menu=4;app.activate();CHECK(app.dialog=="resign");
 app.game.resign(PieceColor::Black,nowMs());app.finished();completedBoard(app,"1-0","Resigned");
}
void cancelRestoresOrigin(const std::filesystem::path& dir) {
 App app(dir);start(app,true);app.key(KEY_ENTER);CHECK(app.selected.valid());
 const auto cursor=app.cursor,selected=app.selected;const auto legal=app.legal.count;
 app.key(KEY_Q);CHECK(app.dialog=="leave-exit");app.key(KEY_ESCAPE);
 CHECK(app.screen==Screen::Board&&app.cursor==cursor&&app.selected==selected&&app.legal.count==legal&&app.networkPlaying);
 app.key(KEY_I);CHECK(app.screen==Screen::Help);app.key(KEY_Q);app.key(KEY_ESCAPE);
 CHECK(app.screen==Screen::Help&&app.selected==selected&&app.networkPlaying);app.key(KEY_ESCAPE);
 CHECK(app.screen==Screen::Board);
 app.pauseMenu();app.menu=3;app.key(KEY_Q);app.key(KEY_ENTER);
 CHECK(app.screen==Screen::Setup&&app.page=="pause"&&app.menu==3&&app.networkPlaying);
 app.menu=1;app.activate();CHECK(app.dialog=="leave-menu");app.key(KEY_ESCAPE);
 CHECK(app.screen==Screen::Setup&&app.page=="pause"&&app.menu==1);
 app.menu=4;app.activate();CHECK(app.dialog=="resign");app.key(KEY_Q);app.key(KEY_ESCAPE);
 CHECK(app.screen==Screen::Confirm&&app.dialog=="resign"&&app.menu==0);
 CHECK(app.networkPlaying&&!app.quit&&!app.game.over());
}
}
int main(){
 const auto dir=std::filesystem::temp_directory_path()/("chess-completion-overlay-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
 try{promotion(dir/"promotion");promotion(dir/"promotion-input",true);castle(dir/"castle");timedDialogs(dir/"dialogs");acceptDrawAtFlag(dir/"draw-input");remoteCompletion(dir/"remote");cancelRestoresOrigin(dir/"restore");std::filesystem::remove_all(dir);std::puts("Completion overlays passed: promotion/castle/confirm/Help expiry, input crossing flag fall, remote completion, cleared candidate/repeat state and exact canceled-leave origin.");return 0;}
 catch(const std::exception& e){std::filesystem::remove_all(dir);std::fprintf(stderr,"%s\n",e.what());return 1;}
}
