// Fresh adversarial regressions: displayed directions must match key intent,
// safe draw controls must not steal WASD, and keyboard aliases must agree.
#define main chess_application_main
#include "../src/main.cpp"
#undef main
#define REQUIRE(expression) do {if(!(expression))throw std::runtime_error( \
 std::string(__FILE__)+":"+std::to_string(__LINE__)+": " #expression);}while(false)
namespace {
void local(App& app){app.setup=chess::Options{};app.setup.mode=Mode::Local;app.startGame();}
void knightDirections(const std::filesystem::path& directory,bool flipped) {
 App app(directory/(flipped?"black":"white"));local(app);app.flipped=flipped;
 if(flipped)app.game.board.setSideToMove(PieceColor::Black);
 const Square source{1,uint8_t(flipped?7:0)};
 app.cursor=source;app.key(KEY_ENTER);app.key(KEY_RIGHT);
 REQUIRE((app.cursor==Square{uint8_t(flipped?0:2),uint8_t(flipped?5:2)}));
 app.key(KEY_ESCAPE);REQUIRE(!app.selected.valid()&&app.cursor==source);
 app.key(KEY_ENTER);app.key(KEY_LEFT);
 REQUIRE((app.cursor==Square{uint8_t(flipped?2:0),uint8_t(flipped?5:2)}));
 app.key(KEY_ESCAPE);app.key(KEY_ENTER);app.key(KEY_SPACE);
 const Square first=app.cursor;app.key(KEY_SPACE);const Square second=app.cursor;
 REQUIRE(first!=second);app.key(KEY_SPACE);REQUIRE(app.cursor==first);
 REQUIRE(first.row==uint8_t(flipped?5:2)&&second.row==first.row);
}
void promotionCycle(const std::filesystem::path& directory) {
 App app(directory);local(app);
 for(int row=0;row<8;++row)for(int col=0;col<8;++col)app.game.board.set(col,row,{});
 app.game.board.setCastleRights(0);app.game.board.set(7,0,{PieceType::King,PieceColor::White});
 app.game.board.set(7,7,{PieceType::King,PieceColor::Black});app.game.board.set(0,6,{PieceType::Pawn,PieceColor::White});
 app.cursor={0,6};app.key(KEY_ENTER);REQUIRE(app.legal.count==4);
 for(int i=0;i<6;++i){app.key(KEY_SPACE);REQUIRE((app.cursor==Square{0,7}));}
 app.key(KEY_ENTER);REQUIRE(app.screen==Screen::Promotion);
 app.key(KEY_RIGHT);app.key(KEY_KP_ENTER);REQUIRE(app.screen==Screen::Board);
 REQUIRE(app.game.board.at(0,7).type==PieceType::Knight);
}
void menuAliases(const std::filesystem::path& directory) {
 App app(directory);local(app);app.setupMenu(Mode::Computer);app.menu=2;
 app.key(KEY_D);REQUIRE(app.setup.difficulty==AIDifficulty::Hard);
 app.key(KEY_A);REQUIRE(app.setup.difficulty==AIDifficulty::Medium);
 app.menu=3;app.key(KEY_D);REQUIRE(app.setup.human==PieceColor::Black);
 app.key(KEY_A);REQUIRE(app.setup.human==PieceColor::White);
 app.screen=Screen::Setup;app.page="preferences";app.menu=0;
 const bool before=app.contrast;app.key(KEY_D);REQUIRE(app.contrast!=before);
 app.key(KEY_A);REQUIRE(app.contrast==before);
 app.home();app.menu=0;app.key(KEY_KP_ENTER);REQUIRE(app.screen==Screen::Board);
 app.key(KEY_KP_ENTER);REQUIRE((app.selected==Square{4,1}));
}
void puzzleRetryFocus(const std::filesystem::path& directory){
 App app(directory);app.cursor={4,6};app.animatedPiece={PieceType::Queen,PieceColor::Black};app.animationStart=0;app.nextPuzzle();
 REQUIRE(app.game.puzzleIndex==0);REQUIRE(!app.game.board.at(app.cursor.col,app.cursor.row).empty());REQUIRE(app.animatedPiece.empty());
 const auto source=app.cursor;const auto before=chess::fen(app.game.board);app.key(KEY_ENTER);app.key(KEY_UP);REQUIRE(app.cursor!=source);app.key(KEY_ENTER);
 REQUIRE(app.game.history.empty()&&chess::fen(app.game.board)==before&&app.cursor==source&&!app.selected.valid());
 REQUIRE(app.toast.find("Try another move")!=std::string::npos);app.key(KEY_ENTER);REQUIRE(app.selected==source);
}

void safeDrawKeys(const std::filesystem::path& directory) {
 App app(directory);app.setup.mode=Mode::Host;app.game.start(app.setup,nowMs());
 app.haveGame=true;app.networkHost=true;app.networkPlaying=true;app.screen=Screen::Board;
 app.incomingDrawOffer=true;app.status="Draw offered: Y yes";app.cursor={4,1};
 app.key(KEY_D);REQUIRE((app.cursor==Square{5,1}));REQUIRE(!app.game.over());
 app.key(KEY_Y);REQUIRE(app.screen==Screen::Confirm&&app.dialog=="accept-draw");
 app.key(KEY_ENTER);REQUIRE(app.screen==Screen::Board&&!app.game.over());
 app.incomingDrawOffer=true;app.status="Draw offered: Y yes";
 app.key(KEY_Y);REQUIRE(app.screen==Screen::Confirm);app.key(KEY_DOWN);app.key(KEY_ENTER);
 REQUIRE(app.game.result=="1/2-1/2"&&app.game.reason=="Draw agreed");
}
}
int main(){
 const auto directory=std::filesystem::temp_directory_path()/("tyrell-chess-navigation-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
 try{knightDirections(directory,false);knightDirections(directory,true);promotionCycle(directory/"promotion");menuAliases(directory/"aliases");safeDrawKeys(directory/"draw");puzzleRetryFocus(directory/"puzzle-focus");std::filesystem::remove_all(directory);std::puts("Keyboard navigation passed: spatial and flipped targets, independent Space cycling, promotion deduplication, safe draw confirmation, WASD and keypad parity.");return 0;}
 catch(const std::exception& error){std::filesystem::remove_all(directory);std::fprintf(stderr,"%s\n",error.what());return 1;}
}
