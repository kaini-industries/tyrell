// Independent adversarial UI regressions for distinct moves sharing a square.
#define main chess_application_main
#include "../src/main.cpp"
#undef main
#define REQUIRE(expression) do {if(!(expression))throw std::runtime_error( \
 std::string(__FILE__)+":"+std::to_string(__LINE__)+": " #expression);}while(false)
namespace {
void emptyStateArgument() {
 char program[]="tyrell-chess",demo[]="--demo",flag[]="--state-dir",empty[]="";
 char* argv[]={program,demo,flag,empty};bool rejected=false;
 try{(void)arguments(4,argv);}catch(const std::runtime_error&){rejected=true;}
 REQUIRE(rejected);
}
void archiveFailure(const std::filesystem::path& dir) {
 const auto state=dir/"archive-failure";App app(state);
 app.setup.mode=Mode::Local;app.startGame();
 {std::ofstream blocked(state/"games");blocked<<"blocked";}
 app.game.resign(PieceColor::White,nowMs());app.finished();
 REQUIRE(app.toast.find("Archive")!=std::string::npos);
 REQUIRE(app.pendingArchive);app.key(KEY_Q);REQUIRE(!app.quit);
 Game recovered;std::string error;REQUIRE(recovered.load(state/"active.save",nowMs(),error));
 REQUIRE(recovered.over()&&recovered.reason=="Resigned");
 // The completed active save remains the recovery copy after a restart too.
 App restarted(state);REQUIRE(restarted.pendingArchive&&restarted.game.over());
 restarted.menu=1;restarted.activate();restarted.menu=4;restarted.activate();
 REQUIRE(restarted.game.over());REQUIRE(restarted.pendingArchive);
 std::filesystem::remove(state/"games");app.key(KEY_Q);REQUIRE(app.quit&&!app.pendingArchive);
 int saves=0,pgns=0;for(const auto& entry:std::filesystem::directory_iterator(state/"games")) {
  saves+=entry.path().extension()==".save";pgns+=entry.path().extension()==".pgn";
 }
 REQUIRE(saves==1&&pgns==1);
 // Loading/retrying the same completed active save must not duplicate archive.
 App retry(state);retry.key(KEY_Q);REQUIRE(retry.quit);
 int count=0;for(const auto& entry:std::filesystem::directory_iterator(state/"games")){(void)entry;++count;}
 REQUIRE(count==2);
}
void castleChoice(const std::filesystem::path& dir,bool castle) {
 App app(dir/(castle?"castle":"king"));
 chess::Options options;options.mode=Mode::Local;
 options.variant=ChessVariant::Chess960;options.position=12;
 app.game.start(options,nowMs());app.haveGame=true;app.screen=Screen::Board;
 for(const std::string token:{"h2h3","a7a6","g1h2","a6a5"})REQUIRE(app.game.moveUci(token,nowMs()));
 app.cursor={5,0};app.key(KEY_ENTER); // Select king f1.
 app.cursor={6,0};app.key(KEY_ENTER); // g1 is both an ordinary move and a castle.
 REQUIRE(app.screen==Screen::Confirm);
 REQUIRE(app.dialog=="castle");
 app.menu=castle?1:0;app.key(KEY_ENTER);
 REQUIRE(app.screen==Screen::Board);
 REQUIRE(app.game.history.size()==5);
 REQUIRE(app.game.history.back().move.isCastle==castle);
 REQUIRE(app.game.board.at(6,0).type==PieceType::King);
 REQUIRE(app.game.board.at(castle?5:7,0).type==PieceType::Rook);
 REQUIRE(app.game.san.back()==(castle?"O-O":"Kg1"));
}
}
int main(){
 const auto dir=std::filesystem::temp_directory_path()/
  ("tyrell-chess-faults-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
 try{emptyStateArgument();archiveFailure(dir);castleChoice(dir,false);castleChoice(dir,true);std::filesystem::remove_all(dir);
  std::puts("Chess faults passed: archive failures retain recovery, empty automation state rejected, Chess960 king/castle independently selectable.");return 0;
 }catch(const std::exception& e){std::filesystem::remove_all(dir);std::fprintf(stderr,"%s\n",e.what());return 1;}
}
