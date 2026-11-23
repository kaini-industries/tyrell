#include "game.hpp"
#include "view.hpp"
#include "lan.hpp"
#include "chess_rules.h"
#include "chess960.h"
#include "chess_zobrist.h"
#include "cursor_navigation.h"
#include "tyrell/game/runtime.hpp"
#include "raylib.h"
#include <algorithm>
#include <chrono>
#include <ctime>
#include <fstream>
#include <future>
#include <iostream>
#include <random>
#include <set>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <tuple>
#ifdef __unix__
#include <sys/stat.h>
#endif
using chess::Game;
using chess::Mode;
using chessui::Screen;
namespace {
uint32_t nowMs() {return uint32_t(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());}
std::string scoreName(const Game& g) {return g.result=="1-0"?"White wins":g.result=="0-1"?"Black wins":g.result=="1/2-1/2"?"Draw":"In progress";}
std::string modeName(Mode mode) {switch(mode){case Mode::Local:return "TWO PLAYERS";case Mode::Computer:return "VS COMPUTER";case Mode::Puzzle:return "PUZZLE";default:return "LAN MATCH";}}
const char* times[]={"No timer","1+0 Bullet","3+2 Blitz","5+3 Blitz","10+0 Rapid"};
const char* diffs[]={"None","Easy","Medium","Hard"};
struct Args {
 bool headless=false,demo=false,quick=false;
 int frames=0;
 std::string screenshot,report,moves,script;
 std::filesystem::path state;
 bool explicitState=false;
};
Args arguments(int argc,char** argv) {
 Args a;
 for(int i=1;i<argc;++i){std::string s=argv[i];auto val=[&](){if(++i>=argc)throw std::runtime_error("Missing value for "+s);return std::string(argv[i]);};
 if(s=="--help"){std::cout<<"Tyrell Chess 0.1.1 for Cardputer Zero\nUsage: tyrell-chess [--headless] [--frames N] [--demo] [--quick] [--moves e2e4,e7e5] [--script key,key] [--screenshot PATH] [--report PATH] [--state-dir PATH]\nAutomation requires --state-dir; headless mode also requires --frames. Normal launch opens the menu; Q saves and exits.\n";std::exit(0);}
 else if(s=="--version"){std::cout<<"Tyrell Chess 0.1.1 (upstream 64419e29c828)\n";std::exit(0);}
 else if(s=="--headless")a.headless=true;else if(s=="--demo")a.demo=true;else if(s=="--quick")a.quick=true;
 else if(s=="--frames"){auto text=val();size_t n;long count=std::stol(text,&n);if(n!=text.size()||count<1||count>100000)throw std::runtime_error("Frames must be 1..100000");a.frames=int(count);}
 else if(s=="--screenshot")a.screenshot=val();else if(s=="--report")a.report=val();else if(s=="--state-dir"){a.state=val();if(a.state.empty())throw std::runtime_error("--state-dir must not be empty");a.explicitState=true;}else if(s=="--moves")a.moves=val();else if(s=="--script")a.script=val();else throw std::runtime_error("Unknown argument: "+s);
 }
 if(a.headless&&!a.frames)throw std::runtime_error("Use --frames with --headless");
 if((a.headless||a.demo||a.quick||a.frames||!a.moves.empty()||!a.script.empty())&&!a.explicitState)throw std::runtime_error("Automation requires --state-dir to protect your saved game");
 if(a.state.empty())a.state=chess::stateDirectory();
 return a;
}
std::vector<std::string> split(const std::string& s){std::vector<std::string> out;std::istringstream in(s);std::string item;while(std::getline(in,item,','))out.push_back(item);return out;}
int scriptKey(const std::string& k) {if(k=="enter")return KEY_ENTER;if(k=="esc")return KEY_ESCAPE;if(k=="up")return KEY_UP;if(k=="down")return KEY_DOWN;if(k=="left")return KEY_LEFT;if(k=="right")return KEY_RIGHT;if(k=="space")return KEY_SPACE;if(k=="backspace")return KEY_BACKSPACE;return k.size()==1?std::toupper(k[0]):0;}
class App {
public:
 Game game;
 chessui::View view;
 Screen screen=Screen::Home,previous=Screen::Home,leaveOrigin=Screen::Board;
 std::string leavePage,leaveDialog;int leaveMenu=0;
 chess::Options setup;
 std::filesystem::path dir;
 bool quit=false,flipped=false,contrast=false,motion=true,haveGame=false,review=false;
 size_t reviewPly=0;
 Square cursor{4,1},selected=NO_SQUARE,hintFrom=NO_SQUARE,hintTo=NO_SQUARE;
 MoveList legal;
 int menu=0,homeMenu=0,setupRow=0,promotion=0;
 std::string toast,dialog,networkAddress;
 double toastUntil=0,animationStart=-10,puzzleReplyAt=0,networkStartedAt=0;
 Piece animatedPiece;
 Move animatedMove;
 std::vector<Move> promotions;
 std::future<Move> worker;
 uint32_t workerHash=0;
 bool workerHint=false;
 uint64_t generation=0,workerGeneration=0;
 bool archiveMode=false,drawOffered=false,incomingDrawOffer=false,awaitingMove=false,pendingArchive=false;
 bool pendingProgress=false,pendingSettings=false;
 using InputContext=std::tuple<Screen,std::string,bool,bool,uint64_t,size_t>;
 InputContext repeatContext;int repeatKey=0;double repeatAt=0;
 double pendingSince=0;
 std::set<int> solved;
 chess::Lan lan;
 bool networkHost=false,helloSent=false,localReady=false,remoteReady=false,networkPlaying=false,networkEndSent=false;
 int pairCode=0,puzzleCategory=-1;
 uint32_t lastSave=0,lastSync=0;
 std::vector<std::filesystem::path> archives;
 std::string page="",status;
 double time=0;
 explicit App(std::filesystem::path state):dir(std::move(state)) {
   std::filesystem::create_directories(dir);
   std::ifstream settings(dir/"settings");settings>>contrast>>motion;
   std::ifstream progress(dir/"puzzles");int n;while(progress>>n)if(n>=0&&n<puzzleCount())solved.insert(n);
   std::string error;haveGame=game.load(dir/"active.save",nowMs(),error);
   if(haveGame){pendingArchive=game.over();game.clock.pause(nowMs());flipped=game.options.mode==Mode::Computer&&game.options.human==PieceColor::Black;cursor={4,uint8_t(flipped?6:1)};}
   else if(std::filesystem::exists(dir/"active.save")) {
     const auto backup=dir/("damaged-"+std::to_string(std::chrono::system_clock::now().time_since_epoch().count())+".save");
     std::error_code ec;std::filesystem::rename(dir/"active.save",backup,ec);
     notify(ec?"Saved game damaged; could not back it up":"Saved game damaged; backup kept");
   }
 }
 ~App(){if(worker.valid())worker.wait();}
 void notify(const std::string& text){toast=text;toastUntil=time+3;}
 bool writeState(const char* name,const std::string& value,const char* label){
   try {
     const auto file=dir/name,temporary=dir/(std::string(name)+".tmp");
     {std::ofstream out(temporary,std::ios::binary|std::ios::trunc);out.exceptions(std::ios::failbit|std::ios::badbit);out<<value;out.flush();}
     std::filesystem::rename(temporary,file);return true;
   }catch(const std::exception& error){notify(std::string(label)+" not saved. Retry / free space");std::cerr<<"Tyrell Chess "<<label<<": "<<error.what()<<'\n';return false;}
 }
 bool saveProgress(){std::ostringstream out;for(int n:solved)out<<n<<'\n';if(writeState("puzzles",out.str(),"Progress")){pendingProgress=false;return true;}return false;}
 bool settings(){pendingSettings=true;std::ostringstream out;out<<contrast<<' '<<motion<<'\n';if(writeState("settings",out.str(),"Settings")){pendingSettings=false;return true;}return false;}
 bool persist(){
   lastSave=nowMs();bool sideSaved=true;
   if(pendingProgress&&!saveProgress())sideSaved=false;
   if(pendingSettings&&!settings())sideSaved=false;
   if(!haveGame||archiveMode)return sideSaved;
   const bool archived=!pendingArchive||archive();if(archived)pendingArchive=false;
   std::string error;if(!game.save(dir/"active.save",nowMs(),error)){notify("Save failed. Free space / retry");std::cerr<<"Tyrell Chess save: "<<error<<'\n';return false;}
   return archived&&sideSaved;
 }
 void clearOffers(bool expired=false){
   const bool showing=screen==Screen::Confirm&&dialog=="accept-draw";
   drawOffered=incomingDrawOffer=false;status.clear();
   if(showing){screen=Screen::Board;page.clear();if(expired)notify("Draw offer expired");}
 }
 void resetBoardUI(){clearSelection();hintFrom=hintTo=NO_SQUARE;animatedPiece={};animationStart=-1000;repeatKey=0;}

 void clearSelection(){selected=NO_SQUARE;legal.clear();}
 void focusPlayer(){
   const auto side=online()?(networkHost?PieceColor::White:PieceColor::Black):game.options.human;
   if(game.board.sideToMove()!=side)return;
   if(cursor.valid()&&!game.board.at(cursor.col,cursor.row).empty()&&game.board.at(cursor.col,cursor.row).color==side)return;
   MoveList moves;ChessRules::generateLegal(game.board,moves);if(moves.count)cursor=moves.moves[0].from;
 }
 bool online() const {return haveGame&&(game.options.mode==Mode::Host||game.options.mode==Mode::Join);}
 bool canPlay() const {return haveGame&&!game.over()&&!review&&!awaitingMove&&(!online()||networkPlaying)&&(!online()||game.board.sideToMove()==(networkHost?PieceColor::White:PieceColor::Black))&&(game.options.mode!=Mode::Puzzle||game.board.sideToMove()==game.options.human);}
 bool thinking() const {return worker.valid();}
 bool activeComputer() const {return haveGame&&!archiveMode&&!review&&game.options.mode==Mode::Computer&&!game.over()&&(screen==Screen::Board||page=="pause"||(screen==Screen::Help&&previous==Screen::Board));}
 void startGame(){
   if(worker.valid()||((pendingArchive||pendingProgress||pendingSettings)&&!persist()))return;
   setup.position=setup.variant==ChessVariant::Chess960?chess960RandomIndex():518;
   ++generation;pendingArchive=false;archiveMode=false;status.clear();game.start(setup,nowMs());haveGame=true;review=false;page.clear();flipped=setup.mode==Mode::Computer&&setup.human==PieceColor::Black;cursor={4,uint8_t(flipped?6:1)};resetBoardUI();clearOffers();screen=Screen::Board;persist();
 }
 bool archive(){
   if(game.options.mode==Mode::Puzzle||!game.over())return true;
   // Stable content ID makes a retry after restart idempotent.
   uint64_t id=14695981039346656037ull;std::string identity=chess::fen(game.initial)+game.result+std::to_string(int(game.options.mode))+std::to_string(int(game.options.human))+std::to_string(int(game.options.time))+std::to_string(int(game.options.difficulty));
   for(const auto& record:game.history)identity+=chess::uci(record.move);
   for(unsigned char ch:identity){id^=ch;id*=1099511628211ull;}
   std::string error;auto path=dir/"games"/(std::to_string(id)+".pgn");
   if(!game.exportPgn(path,error)||(!online()&&!game.save(dir/"games"/(std::to_string(id)+".save"),nowMs(),error))){notify("Archive failed. Free space / retry");std::cerr<<"Tyrell Chess archive: "<<error<<'\n';return false;}return true;
 }
 void finished(){if(game.over()){clearOffers();screen=Screen::Board;page.clear();review=false;menu=0;resetBoardUI();promotions.clear();pendingArchive=game.options.mode!=Mode::Puzzle;const bool saved=persist();if(saved)notify(scoreName(game)+" - "+game.reason);}}

 void broadcastMove(){std::ostringstream o;auto c=game.clock.snapshot(nowMs());o<<"PLY "<<game.history.size()<<' '<<chess::uci(game.history.back().move)<<' '<<ChessZobrist::hash(game.board)<<' '<<c.whiteRemainingMs<<' '<<c.blackRemainingMs;lan.send(o.str());}
 bool apply(const Move& m,bool remote=false){
   if(online()&&!networkHost&&!remote){if(awaitingMove)return false;std::ostringstream o;o<<"REQ "<<game.history.size()<<' '<<chess::uci(m);if(!lan.send(o.str())){notify("Connection unavailable");return false;}awaitingMove=true;pendingSince=time;clearSelection();return true;}
   if(game.options.mode==Mode::Puzzle&&!remote){if(game.puzzleStep>=game.solutionLength||!(m==game.solution[game.puzzleStep])){notify("Try another move. H for a hint");cursor=m.from;clearSelection();return false;}}
   const auto oldCursor=cursor;const auto piece=game.board.at(m.from.col,m.from.row);if(!game.move(m,nowMs()))return false;
   clearOffers(true);animatedPiece=piece;animatedMove=m;animationStart=time;const bool keepCursor=(game.options.mode==Mode::Computer&&piece.color!=game.options.human)||(remote&&(online()||game.options.mode==Mode::Puzzle));cursor=keepCursor?oldCursor:m.to;if(keepCursor)focusPlayer();clearSelection();hintFrom=hintTo=NO_SQUARE;
   if(online()&&networkHost)broadcastMove();
   if(game.options.mode==Mode::Puzzle){
      if(game.puzzleStep>=game.solutionLength){game.finish(game.options.human==PieceColor::White?"1-0":"0-1","Puzzle solved",nowMs());solved.insert(game.puzzleIndex);pendingProgress=true;if(saveProgress())notify("Solved! X for next puzzle");}
      else if(game.board.sideToMove()!=game.options.human)puzzleReplyAt=time+.5;
   }else {finished();persist();}
   return true;
 }
 void choose(){
   if(!canPlay()||thinking()||(game.options.mode==Mode::Computer&&!game.humanTurn()))return;
   if(!selected.valid()){
      if(game.board.at(cursor.col,cursor.row).empty()||game.board.at(cursor.col,cursor.row).color!=game.board.sideToMove())return;
      selected=cursor;ChessRules::getLegalMovesFrom(game.board,selected.col,selected.row,legal);if(!legal.count){clearSelection();notify("This piece has no legal moves");}return;
   }
   promotions.clear();for(int i=0;i<legal.count;++i)if(legal.moves[i].to==cursor)promotions.push_back(legal.moves[i]);
   if(promotions.size()>1){if(promotions[0].promotion!=PieceType::None){screen=Screen::Promotion;promotion=0;}else{screen=Screen::Confirm;dialog="castle";menu=0;}return;}
   if(promotions.size()==1){apply(promotions[0]);return;}
   clearSelection();choose();
 }
 Square displayed(Square square) const {return {uint8_t(flipped?7-square.col:square.col),uint8_t(flipped?square.row:7-square.row)};}
 void targetCursor(bool cycle,int dx=0,int dy=0){
   Square cells[MoveList::MAX_MOVES];for(int i=0;i<legal.count;++i)cells[i]=displayed(legal.moves[i].to);
   Square next;const auto direction=dx<0?CursorDirection::Left:dx>0?CursorDirection::Right:dy<0?CursorDirection::Up:CursorDirection::Down;
   const bool found=cycle?cycleCursorDestination(cells,uint8_t(legal.count),displayed(cursor),next):chooseCursorDestination(cells,uint8_t(legal.count),displayed(cursor),direction,next);
   if(found)cursor=displayed(next);
 }
 void moveCursor(int dx,int dy){
   if(review){if(dx<0&&reviewPly)--reviewPly;else if(dx>0&&reviewPly<game.history.size())++reviewPly;return;}
   if(selected.valid()&&legal.count){targetCursor(false,dx,dy);return;}
   if(flipped){dx=-dx;dy=-dy;}cursor.col=uint8_t(std::clamp(int(cursor.col)+dx,0,7));cursor.row=uint8_t(std::clamp(int(cursor.row)-dy,0,7));
 }
 InputContext inputContext() const {return {screen,page,review,selected.valid(),generation,game.history.size()};}
 bool repeatable(int k) const {
   if(screen==Screen::Help)return false;
   if(page=="address")return k==KEY_UP||k==KEY_DOWN||k==KEY_W||k==KEY_S||k==KEY_BACKSPACE;
   const bool horizontal=k==KEY_LEFT||k==KEY_RIGHT||k==KEY_A||k==KEY_D||k==KEY_COMMA||k==KEY_SLASH;
   const bool vertical=k==KEY_UP||k==KEY_DOWN||k==KEY_W||k==KEY_S||k==KEY_SEMICOLON||k==KEY_PERIOD;
   if(screen==Screen::Promotion)return horizontal;
   return horizontal||vertical;
 }
 template<class Input>void input(Input& source,double timestamp){
   if(repeatKey&&repeatContext!=inputContext())repeatKey=0;
   int k;while((k=source.next_key())!=0&&!quit){
     const auto before=inputContext();key(k);
     if(repeatable(k)&&before==inputContext()){repeatKey=k;repeatAt=timestamp+.35;repeatContext=inputContext();}
     else repeatKey=0;
   }
   if(!repeatKey)return;
   if(!source.down(repeatKey)||repeatContext!=inputContext()){repeatKey=0;return;}
   if(timestamp>=repeatAt){const auto before=inputContext();key(repeatKey);repeatAt=timestamp+.10;if(before!=inputContext())repeatKey=0;}
 }

 void home(){
   const bool temporary=archiveMode||(haveGame&&(game.options.mode==Mode::Puzzle||online()));
   if((!temporary||pendingArchive||pendingProgress||pendingSettings)&&!persist())return;
   clearSelection();review=false;page.clear();status.clear();networkPlaying=false;awaitingMove=false;clearOffers();lan.close();++generation;
   if(temporary){std::string error;Game active;haveGame=active.load(dir/"active.save",nowMs(),error);if(haveGame)game=std::move(active);else game=Game{};}
   archiveMode=false;
   if(haveGame){game.clock.pause(nowMs());flipped=game.options.mode==Mode::Computer&&game.options.human==PieceColor::Black;cursor={4,uint8_t(flipped?6:1)};}screen=Screen::Home;menu=homeMenu;persist();
 }
 void pauseMenu(){clearSelection();screen=Screen::Setup;page="pause";menu=0;}
 void askLeave(bool exiting){
   if(!(screen==Screen::Confirm&&(dialog=="leave-menu"||dialog=="leave-exit"))){leaveOrigin=screen;leavePage=page;leaveDialog=dialog;leaveMenu=menu;}
   screen=Screen::Confirm;dialog=exiting?"leave-exit":"leave-menu";menu=0;
 }
 void cancelLeave(){
   screen=leaveOrigin;page=leavePage;dialog=leaveDialog;menu=leaveMenu;
   if(screen==Screen::Confirm&&dialog=="accept-draw"&&!incomingDrawOffer){screen=Screen::Board;page.clear();notify("Draw offer expired");}
 }
 void setupMenu(Mode m){setup=chess::Options{};setup.mode=m;screen=Screen::Setup;page="setup";menu=0;}
 void nextPuzzle(){if((pendingProgress||pendingSettings)&&!persist())return;++generation;archiveMode=false;status.clear();int start=game.puzzleIndex>=0?game.puzzleIndex+1:0;std::vector<int> candidates;for(int i=0;i<puzzleCount();++i){int p=(start+i)%puzzleCount();bool category=puzzleCategory<0;for(int n=0;!category&&n<puzzleCountByType(PuzzleType(puzzleCategory));++n)category=p==puzzleIndexByType(PuzzleType(puzzleCategory),n);if(category)candidates.push_back(p);}if(candidates.empty())return;int n=candidates[0];for(int p:candidates)if(!solved.count(p)){n=p;break;}game.puzzle(n,nowMs());haveGame=true;review=false;flipped=game.options.human==PieceColor::Black;cursor=NO_SQUARE;focusPlayer();resetBoardUI();screen=Screen::Board;page.clear();}
 void hint(){
   if(game.options.mode==Mode::Puzzle){int n=game.puzzleStep;if(n<game.solutionLength){hintFrom=game.solution[n].from;hintTo=game.solution[n].to;notify("Look at the marked squares");}return;}
   if(online()||game.clock.enabled()){notify("Hints are for untimed offline games");return;}
   if(thinking()||game.over())return;workerHint=true;workerGeneration=generation;workerHash=ChessZobrist::hash(game.board);auto b=game.board;
   worker=std::async(std::launch::async,[b]()mutable{return ChessAI::findBestMove(b,AIDifficulty::Hard,500);});
 }
 std::vector<std::string> homeItems() const {return {haveGame?"Resume game":"Start playing","Vs computer","Two players","Puzzles","LAN play","Game library","Preferences","Controls","Exit"};}
 std::vector<std::string> items() {
   if(screen==Screen::Home)return homeItems();
   if(screen==Screen::Promotion)return {"Queen","Knight","Rook","Bishop"};
   if(screen==Screen::Confirm){if(dialog=="leave-menu"||dialog=="leave-exit")return {"Stay in match","Leave match"};if(dialog=="accept-draw")return {"Keep playing","Accept draw"};}
   if(screen==Screen::Confirm)return dialog=="castle"?std::vector<std::string>{"Move king","Castle"}:dialog=="resign"?std::vector<std::string>{"Keep playing","Resign game"}:dialog=="draw"?std::vector<std::string>{"Keep playing","Agree a draw"}:std::vector<std::string>{"Keep game","Replace game"};
   if(screen==Screen::Help)return {};
   if(page=="puzzles")return {"Next unsolved","Mate in one","Mate in two","Tactics","Back"};
   if(page=="pause")return {"Continue",online()&&!game.over()?"Leave match":"Save and menu","Review moves","Export PGN","Resign","Offer / agree draw","Controls"};
   if(page=="preferences")return {std::string("Board: ")+(contrast?"High contrast":"Color"),std::string("Motion: ")+(motion?"On":"Off"),"Back"};
   if(page=="library"){std::vector<std::string> rows;for(size_t i=0;i<archives.size();++i)rows.push_back("Game "+std::to_string(archives.size()-i));rows.push_back("Back");return rows;}
   if(page=="lan")return {"Host a game","Join by IP","Back"};
   if(page=="address")return {networkAddress.empty()?"Type host IPv4 address":networkAddress,"Connect","Back"};
   if(page=="pair")return {localReady?"Waiting for opponent":"Confirm code matches","Cancel"};
   if(page=="waiting")return {"Cancel"};
   if(page=="disconnected")return {"Return to menu"};
   if(page=="setup"){
     return {std::string("Variant: ")+(setup.variant==ChessVariant::Standard?"Standard":"Chess960"),std::string("Clock: ")+times[int(setup.time)],setup.mode==Mode::Computer?std::string("Level: ")+diffs[int(setup.difficulty)]:setup.mode==Mode::Host?"Mode: LAN host":"Mode: two players",setup.mode==Mode::Computer?std::string("You play: ")+(setup.human==PieceColor::White?"White":"Black"):setup.mode==Mode::Host?"Host plays White":"Pass and play","Start game"};
   }
   return {};
 }
 void change(int delta){if(page=="setup"){
   if(menu==0)setup.variant=setup.variant==ChessVariant::Standard?ChessVariant::Chess960:ChessVariant::Standard;
   if(menu==1)setup.time=TimeControl((int(setup.time)+delta+5)%5);
   if(menu==2&&setup.mode==Mode::Computer)setup.difficulty=AIDifficulty((int(setup.difficulty)-1+delta+3)%3+1);
   if(menu==3&&setup.mode==Mode::Computer)setup.human=opponent(setup.human);
 }else if(page=="preferences"){if(menu==0)contrast=!contrast;if(menu==1)motion=!motion;settings();}}
 void loadLibrary(){archives.clear();std::error_code ec;for(std::filesystem::directory_iterator i(dir/"games",ec),end;!ec&&i!=end;i.increment(ec))if(i->path().extension()==".save")archives.push_back(i->path());std::sort(archives.rbegin(),archives.rend());if(archives.size()>50)archives.resize(50);screen=Screen::Setup;page="library";menu=0;}
 void activate(){
   if(screen==Screen::Home){if((pendingArchive||pendingProgress||pendingSettings)&&menu!=0&&!persist())return;homeMenu=menu;
     if(menu==0){if(haveGame){screen=Screen::Board;review=false;if(!game.over())game.clock.resume(nowMs());}else setupMenu(Mode::Computer);}
     else if(menu==1||menu==2){if(haveGame&&!game.over()){screen=Screen::Confirm;dialog=menu==1?"computer":"local";menu=0;}else setupMenu(menu==1?Mode::Computer:Mode::Local);}
     else if(menu==3){if(!persist())return;screen=Screen::Setup;page="puzzles";menu=0;}
     else if(menu==4){if(!persist())return;screen=Screen::Network;page="lan";menu=0;}
     else if(menu==5)loadLibrary();else if(menu==6){screen=Screen::Setup;page="preferences";menu=0;}else if(menu==7){previous=screen;screen=Screen::Help;}else {if(persist())quit=true;}return;
   }
   if(screen==Screen::Confirm){
     if(dialog=="leave-menu"||dialog=="leave-exit"){
       const bool exiting=dialog=="leave-exit";if(menu==0){cancelLeave();return;}
       if(!persist())return;lan.close();networkPlaying=false;awaitingMove=false;clearOffers();
       if(exiting)quit=true;else home();return;
     }
     if(dialog=="accept-draw"){
       if(menu==1&&online()&&networkPlaying&&!review&&!game.over()&&incomingDrawOffer){
         if(networkHost){game.finish("1/2-1/2","Draw agreed",nowMs());finished();}
         else {lan.send("ACCEPTDRAW");incomingDrawOffer=false;status="Draw accepted; waiting";}
       }
       screen=Screen::Board;page.clear();return;
     }
     if(dialog=="castle"){for(const auto& m:promotions)if(m.isCastle==(menu==1)){screen=Screen::Board;apply(m);break;}return;}
     if(dialog=="resign"||dialog=="draw"){
       if(menu==1&&!game.over()){
         if(dialog=="draw"){game.finish("1/2-1/2","Draw agreed",nowMs());finished();}
         else {auto side=online()?(networkHost?PieceColor::White:PieceColor::Black):game.options.mode==Mode::Computer?game.options.human:game.board.sideToMove();if(online()&&!networkHost)lan.send("RESIGN");else{game.resign(side,nowMs());finished();}}
       }
       screen=Screen::Board;page.clear();
     }else if(menu==0)home();else setupMenu(dialog=="computer"?Mode::Computer:Mode::Local);return;
   }
   if(screen==Screen::Promotion){const PieceType types[]={PieceType::Queen,PieceType::Knight,PieceType::Rook,PieceType::Bishop};for(auto m:promotions)if(m.promotion==types[promotion]){screen=Screen::Board;apply(m);break;}return;}
   if(screen==Screen::Help){screen=previous;return;}
   if(page=="setup"){if(menu<4)change(1);else if(setup.mode==Mode::Host){setup.position=setup.variant==ChessVariant::Chess960?chess960RandomIndex():518;std::string error;if(lan.host(error)){networkHost=true;networkPlaying=false;helloSent=localReady=remoteReady=networkEndSent=drawOffered=incomingDrawOffer=awaitingMove=false;page="waiting";screen=Screen::Network;networkStartedAt=time;menu=0;}else notify(error);}else startGame();return;}
   if(page=="preferences"){if(menu==2)home();else change(1);return;}
   if(page=="puzzles"){if(menu==4)home();else{puzzleCategory=menu-1;game.puzzleIndex=-1;nextPuzzle();}return;}
   if(page=="library"){if(menu==int(archives.size())){home();return;}std::string error;Game loaded;if(loaded.load(archives[menu],nowMs(),error)){++generation;archiveMode=true;game=std::move(loaded);flipped=game.options.mode==Mode::Computer&&game.options.human==PieceColor::Black;haveGame=true;review=true;reviewPly=game.history.size();screen=Screen::Board;page="archive";}else notify("Cannot open: "+error);return;}
   if(page=="pause"){
     if(menu==0){screen=Screen::Board;page.clear();}
     else if(menu==1){if(online()&&!game.over()){askLeave(false);}else home();}
     else if(menu==2){if((game.clock.enabled()||online())&&!game.over())notify("Review available after game");else{review=true;reviewPly=game.history.size();screen=Screen::Board;page.clear();}}
     else if(menu==3){std::string error;auto path=dir/"exports/last-game.pgn";notify(game.exportPgn(path,error)?"PGN saved in exports/":"Export failed: "+error);}
     else if(menu==4){if(!game.over()){screen=Screen::Confirm;dialog="resign";menu=0;}else {screen=Screen::Board;page.clear();}}
     else if(menu==5){if(game.over()){screen=Screen::Board;page.clear();return;}if(online()){drawOffered=true;lan.send("DRAW");notify("Draw offered to opponent");screen=Screen::Board;page.clear();}else if(!game.over()){screen=Screen::Confirm;dialog="draw";menu=0;}}
     else {previous=screen;screen=Screen::Help;}return;
   }
   if(page=="lan"){if(menu==0)setupMenu(Mode::Host);else if(menu==1){page="address";networkAddress.clear();menu=0;}else home();return;}
   if(page=="address"){if(menu==2){page="lan";menu=0;}else{std::string error;if(lan.join(networkAddress,error)){networkHost=false;networkPlaying=false;helloSent=localReady=remoteReady=networkEndSent=drawOffered=incomingDrawOffer=awaitingMove=false;page="waiting";networkStartedAt=time;menu=0;}else notify(error);}return;}
   if(page=="pair"){if(menu==1){lan.close();home();}else {localReady=true;lan.send("READY");}return;}
   if(page=="waiting"||page=="disconnected"){lan.close();home();}
 }
 void key(int k){
   if(k==KEY_KP_ENTER)k=KEY_ENTER;if(k==KEY_DELETE)k=KEY_BACKSPACE;
   if(k==KEY_Q){if(online()&&networkPlaying&&!game.over()){askLeave(true);}else if(persist())quit=true;return;}
   if(page=="address"){
     if(((k>=KEY_ZERO&&k<=KEY_NINE)||k==KEY_PERIOD)&&networkAddress.size()<15){menu=0;networkAddress+=char(k);}
     else if(k>=KEY_KP_0&&k<=KEY_KP_9&&networkAddress.size()<15){menu=0;networkAddress+=char('0'+k-KEY_KP_0);}
     else if(k==KEY_KP_DECIMAL&&networkAddress.size()<15){menu=0;networkAddress+='.';}
     else if(k==KEY_DOWN||k==KEY_S)menu=(menu+1)%3;
     else if(k==KEY_UP||k==KEY_W)menu=(menu+2)%3;
     else if(k==KEY_ESCAPE){page="lan";menu=0;}
     else if(k==KEY_BACKSPACE&&!networkAddress.empty()){menu=0;networkAddress.pop_back();}
     else if(k==KEY_ENTER||k==KEY_KP_ENTER)activate();
     return;
   }
   if(screen==Screen::Help){if(k==KEY_ENTER||k==KEY_ESCAPE||k==KEY_I)screen=previous;return;}
   if(screen!=Screen::Board){
     if(k==KEY_ESCAPE||k==KEY_BACKSPACE){if(screen==Screen::Confirm&&(dialog=="leave-menu"||dialog=="leave-exit")){cancelLeave();return;}if(screen==Screen::Promotion){screen=Screen::Board;clearSelection();}else if(screen==Screen::Confirm&&(dialog=="resign"||dialog=="draw"||dialog=="castle"||dialog=="accept-draw"||dialog=="leave-menu"||dialog=="leave-exit")){screen=Screen::Board;page.clear();}else if(page=="pause"){screen=Screen::Board;page.clear();}else{lan.close();home();}return;}
     if(screen==Screen::Promotion){if(k==KEY_LEFT||k==KEY_A||k==KEY_COMMA)promotion=(promotion+3)%4;if(k==KEY_RIGHT||k==KEY_D||k==KEY_SLASH)promotion=(promotion+1)%4;if(k==KEY_ENTER)activate();return;}
     if(k==KEY_DOWN||k==KEY_S||k==KEY_PERIOD)menu=(menu+1)%std::max(1,int(items().size()));
     if(k==KEY_UP||k==KEY_W||k==KEY_SEMICOLON)menu=(menu+std::max(1,int(items().size()))-1)%std::max(1,int(items().size()));
     if(k==KEY_LEFT||k==KEY_A||k==KEY_COMMA)change(-1);if(k==KEY_RIGHT||k==KEY_D||k==KEY_SLASH)change(1);
     if(k==KEY_ENTER)activate();return;
   }
   if(k==KEY_I){previous=screen;screen=Screen::Help;return;}
   if(k==KEY_ESCAPE||k==KEY_BACKSPACE){if(selected.valid()){cursor=selected;clearSelection();}else if(review){review=false;if(page=="archive")home();}else pauseMenu();return;}
   if(k==KEY_F){flipped=!flipped;return;}if(k==KEY_T){contrast=!contrast;settings();return;}
   if(k==KEY_V){if(archiveMode)return;if(!thinking()&&(!game.clock.enabled()||game.over())&&(!online()||game.over())){review=!review;reviewPly=game.history.size();clearSelection();}else notify("Review available after game");return;}
   if(k==KEY_P){std::string error;notify(game.exportPgn(dir/"exports/last-game.pgn",error)?"PGN saved in exports/":"PGN export failed");return;}
   if(k==KEY_H&&!review&&!archiveMode){hint();return;}
   if(k==KEY_X&&game.options.mode==Mode::Puzzle){nextPuzzle();return;}
   if(k==KEY_U&&!review&&!archiveMode){if(thinking())return;if(game.undo(nowMs())){clearSelection();hintFrom=hintTo=NO_SQUARE;persist();}else notify("Undo needs an untimed offline game");return;}
   if(k==KEY_Y&&online()&&networkPlaying&&!review&&!game.over()&&incomingDrawOffer){screen=Screen::Confirm;dialog="accept-draw";menu=0;return;}
   if(k==KEY_LEFT||k==KEY_A||k==KEY_COMMA)moveCursor(-1,0);
   if(k==KEY_RIGHT||k==KEY_D||k==KEY_SLASH)moveCursor(1,0);
   if(k==KEY_UP||k==KEY_W||k==KEY_SEMICOLON)moveCursor(0,-1);
   if(k==KEY_DOWN||k==KEY_S||k==KEY_PERIOD)moveCursor(0,1);
   if(k==KEY_SPACE&&selected.valid())targetCursor(true);
   if(k==KEY_ENTER){if(archiveMode)return;if(game.over())pauseMenu();else choose();}
 }
 void disconnect(const std::string& why){status=why;lan.close();networkPlaying=false;awaitingMove=false;incomingDrawOffer=drawOffered=false;if(haveGame&&online())game.clock.pause(nowMs());screen=Screen::Network;page="disconnected";menu=0;}
 void network(){
   lan.poll();
   if(awaitingMove&&time-pendingSince>10){disconnect("Host did not acknowledge move");return;}
   if(!networkPlaying&&(page=="waiting"||page=="pair")&&time-networkStartedAt>90){disconnect("Pairing timed out");return;}
   if(networkHost&&lan.connected()&&!helloSent){std::random_device r;pairCode=int(r()%900000)+100000;std::ostringstream o;o<<"HELLO 1 "<<int(setup.variant)<<' '<<setup.position<<' '<<int(setup.time)<<' '<<pairCode;lan.send(o.str());helloSent=true;page="pair";menu=0;}
   for(const auto& line:lan.receive()){
     std::istringstream in(line);std::string command;in>>command;
     if(command=="HELLO"&&!networkHost&&!helloSent&&!networkPlaying){int version,variant,pos,tc,code;in>>version>>variant>>pos>>tc>>code;if(!in||version!=1||variant<0||variant>1||pos<0||pos>959||tc<0||tc>4||code<100000||code>999999){disconnect("Incompatible chess peer");break;}setup=chess::Options{};setup.mode=Mode::Join;setup.variant=ChessVariant(variant);setup.position=pos;setup.time=TimeControl(tc);pairCode=code;helloSent=true;page="pair";menu=0;}
     else if(command=="READY"&&helloSent&&!networkPlaying)remoteReady=true;
     else if(command=="START"&&!networkHost&&localReady&&remoteReady&&!networkPlaying){++generation;pendingArchive=false;archiveMode=false;status.clear();game.start(setup,nowMs());networkPlaying=true;haveGame=true;screen=Screen::Board;page.clear();review=false;flipped=true;cursor={4,6};clearSelection();}
     else if(command=="REQ"&&networkHost&&networkPlaying){size_t ply;std::string move;in>>ply>>move;bool ended=game.over();game.update(nowMs());if(!ended&&game.over())finished();if(game.over())continue;if(!in||ply!=game.history.size()||game.board.sideToMove()!=PieceColor::Black){disconnect("Invalid peer move sequence");break;}MoveList moves;ChessRules::generateLegal(game.board,moves);bool applied=false;for(int i=0;i<moves.count;++i)if(chess::uci(moves.moves[i])==move){applied=apply(moves.moves[i],true);break;}if(!applied){if(game.over())finished();else {disconnect("Illegal peer move");break;}}}
     else if(command=="PLY"&&!networkHost&&networkPlaying){size_t ply;std::string move;uint32_t hash,w,b;in>>ply>>move>>hash>>w>>b;if(!in||ply!=game.history.size()+1){disconnect("Position sequence differs");break;}const bool ownMove=game.board.sideToMove()==PieceColor::Black;const auto tc=game.options.time;game.clock.configure(TimeControl::None);bool ok=game.moveUci(move,nowMs());ChessClockSnapshot clock;clock.timeControl=tc;clock.whiteRemainingMs=w;clock.blackRemainingMs=b;clock.activeSide=game.board.sideToMove();clock.started=tc!=TimeControl::None;clock.paused=tc!=TimeControl::None&&game.over();if(!game.clock.loadSnapshot(clock,nowMs()))ok=false;if(!ok||ChessZobrist::hash(game.board)!=hash){disconnect("Position differs from host");break;}awaitingMove=false;clearOffers(true);if(ownMove)cursor=game.history.back().move.to;else focusPlayer();clearSelection();}
     else if(command=="SYNC"&&!networkHost&&networkPlaying){uint32_t w,b;size_t ply;in>>ply>>w>>b;if(in&&ply==game.history.size())game.clock.synchronizeRemainingMs(w,b,nowMs());}
     else if(command=="RESIGN"&&networkHost&&networkPlaying){if(!game.over()){game.resign(PieceColor::Black,nowMs());finished();}}
     else if(command=="DRAW"&&networkPlaying){if(!game.over()){incomingDrawOffer=true;status="Draw offered: Y yes";}}
     else if(command=="ACCEPTDRAW"&&networkHost&&networkPlaying){if(!game.over()&&drawOffered){drawOffered=false;game.finish("1/2-1/2","Draw agreed",nowMs());finished();}}
     else if(command=="END"&&!networkHost&&networkPlaying){std::string score,reason;in>>score;in.get();std::getline(in,reason);if(score!="1-0"&&score!="0-1"&&score!="1/2-1/2"){disconnect("Invalid result");break;}awaitingMove=false;game.finish(score,reason.substr(0,60),nowMs());finished();}
     else {disconnect("Unexpected peer message");break;}
   }
   if(!lan.error().empty()&&(networkPlaying||page=="pair"||page=="waiting")){if(networkPlaying&&game.over()){networkPlaying=false;lan.close();status.clear();}else disconnect(lan.error());return;}
   if(networkHost&&localReady&&remoteReady&&!networkPlaying&&page=="pair"){setup.mode=Mode::Host;++generation;pendingArchive=false;archiveMode=false;status.clear();game.start(setup,nowMs());haveGame=true;networkPlaying=true;screen=Screen::Board;page.clear();flipped=false;review=false;cursor={4,1};lan.send("START");clearSelection();}
   if(networkHost&&networkPlaying&&nowMs()-lastSync>=500){auto c=game.clock.snapshot(nowMs());lan.send("SYNC "+std::to_string(game.history.size())+" "+std::to_string(c.whiteRemainingMs)+" "+std::to_string(c.blackRemainingMs));lastSync=nowMs();}
   if(networkHost&&networkPlaying&&game.over()&&!networkEndSent){lan.send("END "+game.result+" "+game.reason);networkEndSent=true;}
 }
 void update(double t){
   time=t;network();
   if(haveGame&&screen!=Screen::Home&&page!="library"&&!archiveMode&&(!online()||(networkHost&&networkPlaying))) {bool was=game.over();game.update(nowMs());if(!was&&game.over())finished();}
   if(worker.valid()&&worker.wait_for(std::chrono::seconds(0))==std::future_status::ready){auto move=worker.get();if(haveGame&&workerGeneration==generation&&((workerHint&&screen==Screen::Board)||(!workerHint&&activeComputer()))&&!review&&!archiveMode&&ChessZobrist::hash(game.board)==workerHash&&!game.over()){if(workerHint){hintFrom=move.from;hintTo=move.to;notify("Try "+chess::uci(move));}else apply(move);}workerHint=false;}
   if(((screen==Screen::Board&&!review&&!archiveMode)||activeComputer())&&!worker.valid()&&!game.over()){
     if(game.options.mode==Mode::Computer&&!game.humanTurn()){
       workerGeneration=generation;workerHash=ChessZobrist::hash(game.board);workerHint=false;auto b=game.board;auto d=game.options.difficulty;uint32_t budget=game.clock.enabled()?std::max(1u,std::min(3000u,game.clock.remainingMsAt(game.board.sideToMove(),nowMs())/10)):0;
       worker=std::async(std::launch::async,[b,d,budget]()mutable{return ChessAI::findBestMove(b,d,budget);});
     }
     if(game.options.mode==Mode::Puzzle&&game.board.sideToMove()!=game.options.human&&time>=puzzleReplyAt&&game.puzzleStep<game.solutionLength)apply(game.solution[game.puzzleStep],true);
   }
   if(((haveGame&&!online()&&!review&&!archiveMode)||pendingProgress||pendingSettings)&&nowMs()-lastSave>=5000)persist();
 }
 void draw(){
   view=chessui::View{};view.screen=screen;view.board=review?game.atPly(reviewPly):game.board;view.cursor=cursor;view.selected=selected;view.legal=legal;view.hintFrom=hintFrom;view.hintTo=hintTo;view.flipped=flipped;view.monochrome=contrast;view.review=review;view.gameOver=game.over();view.clocksEnabled=game.clock.enabled();view.clocksRunning=game.clock.isRunning();view.thinking=thinking();view.time=time;view.menu=items();view.menuIndex=screen==Screen::Promotion?promotion:menu;
   view.title=game.over()?(game.result=="1-0"?"WHITE WINS":game.result=="0-1"?"BLACK WINS":"DRAW"):modeName(game.options.mode);view.status=game.over()?game.reason:awaitingMove?"Waiting for host...":!status.empty()?status:ChessRules::isInCheck(view.board,view.board.sideToMove())?"Check!":game.board.sideToMove()==PieceColor::White?"White to move":"Black to move";
   view.whiteClock=chess::clockText(game.clock.remainingMsAt(PieceColor::White,nowMs()),game.clock.enabled());view.blackClock=chess::clockText(game.clock.remainingMsAt(PieceColor::Black,nowMs()),game.clock.enabled());view.moveLines=game.lines(review?reviewPly:game.history.size());
   if(review){view.reviewLabel="REPLAY "+std::to_string(reviewPly)+" / "+std::to_string(game.history.size());view.footer="Left / right step";}
   else view.footer=game.over()?"ENTER menu  V replay":selected.valid()?"ENTER move ESC cancel":thinking()||awaitingMove?"I help ESC menu":"ENTER pick I help";
   view.subtitle=game.options.variant==ChessVariant::Chess960?"Chess960 #"+std::to_string(game.options.position):"Standard chess";
   if(!game.history.empty()){size_t n=review?reviewPly:game.history.size();if(n){view.lastFrom=game.history[n-1].move.from;view.lastTo=game.history[n-1].move.to;}}
   if(motion&&!review){view.animatedPiece=animatedPiece;view.animationFrom=animatedMove.from;view.animationTo=animatedMove.to;view.animationProgress=float(std::clamp((time-animationStart)/.18,0.,1.));}
   if(game.options.mode==Mode::Puzzle){view.title="PUZZLE "+std::to_string(game.puzzleIndex+1);view.subtitle=std::to_string(solved.size())+" / "+std::to_string(puzzleCount())+" solved";view.footer="H hint  X next  I help";if(game.over())view.status="Solved! X for next";else view.status=game.puzzleType==PuzzleType::MateIn1?"Mate in one":game.puzzleType==PuzzleType::MateIn2?"Mate in two":"Find the best move";}
   if(screen==Screen::Home){view.title="YOUR MOVE";view.status=std::to_string(solved.size())+" puzzles solved";view.footer="ENTER select  Q exit";}
   if(page=="setup"){view.title=setup.mode==Mode::Host?"HOST A MATCH":"NEW GAME";view.subtitle=modeName(setup.mode);view.status="Choose a row, then change with left/right";view.footer="UP/DOWN row  ENTER change / start";}
   if(page=="pause"){view.title=game.over()?scoreName(game):"GAME MENU";view.subtitle=game.over()?game.reason:"Clock continues while this menu is open";view.status="Q saves and exits";view.footer="ENTER select  ESC return";}
   if(page=="puzzles"){view.title="PUZZLE ARCADE";view.subtitle=std::to_string(solved.size())+" / "+std::to_string(puzzleCount())+" solved - progress saves automatically";view.status="H reveals a hint. X skips to the next puzzle";view.footer="ENTER select  ESC back";}
   if(page=="preferences"){view.title="PREFERENCES";view.subtitle="Make the board your own";view.status="";view.footer="ENTER change  ESC back";}
   if(page=="library"){view.title="GAME LIBRARY";view.subtitle="Completed offline games, full move replay";view.status=archives.empty()?"Finish a game to start your collection":"Choose a game to replay";view.footer="ENTER replay  ESC back";}
   if(screen==Screen::Help){
     view.title="HOW TO PLAY";view.status.clear();view.footer="ENTER / ESC return";
     if(previous==Screen::Home)view.menu={"UP/DOWN or W/S   Select menu row","LEFT/RIGHT or A/D   Change setting","ENTER / keypad Enter   Confirm","ESC / Backspace   Back or cancel","Hold directions to repeat","I opens controls during play","Q saves and exits"};
     else if(game.options.mode==Mode::Puzzle)view.menu={"Arrows / WASD / ; , . /   Navigate","ENTER   Select piece / make move","SPACE   Cycle legal destinations","ESC / Backspace   Cancel / menu","H hint   X next puzzle   I help","F flip   T contrast   V replay","Q exit (offline game is preserved)","Progress saves automatically"};
     else view.menu={"Arrows / WASD / ; , . /   Navigate","ENTER / keypad Enter   Select / move","SPACE   Cycle legal destinations","ESC / Backspace   Cancel / game menu",online()?"Y reviews a draw offer, then confirm":"U undo / H hint (untimed games)","F flip   T contrast   V replay","P export PGN   Hold arrows to repeat",online()?(game.over()?"Q exits the completed match":"Q confirms leaving the live match"):"Q saves and exits"};
   }
   if(screen==Screen::Confirm){view.footer="UP/DOWN select ENTER confirm ESC back";view.title=dialog=="castle"?"KING MOVE OR CASTLE?":dialog=="resign"?"RESIGN THIS GAME?":dialog=="draw"?"AGREE TO A DRAW?":"REPLACE YOUR SAVED GAME?";view.status=dialog=="castle"?"Same destination, different rook move":dialog=="resign"?"Your opponent will win this match":dialog=="draw"?"The game will end in a draw":"Your current match will be replaced";}
   if(screen==Screen::Confirm&&(dialog=="leave-menu"||dialog=="leave-exit")){view.title="LEAVE THIS LAN MATCH?";view.status="Disconnects the match. It cannot resume.";}
   if(screen==Screen::Confirm&&dialog=="accept-draw"){view.title="ACCEPT OPPONENT'S DRAW?";view.status="Confirm to end this match as a draw.";}
   if(screen==Screen::Promotion){view.title="PROMOTE YOUR PAWN";view.status="Choose your new piece";view.footer="LEFT/RIGHT choose ENTER confirm ESC back";}
   if(screen==Screen::Network){
      view.title=page=="pair"?"PAIR CODE "+std::to_string(pairCode):page=="disconnected"?"CONNECTION ENDED":"LOCAL WIRELESS";
      view.subtitle=page=="pair"?"Both players must see the same code":page=="address"?"Type the host's IP, then press ENTER":page=="waiting"?networkHost?"Waiting at "+chess::localAddress():"Connecting to "+networkAddress:"Play another Zero on the same Wi-Fi";
      view.status=page=="disconnected"?status:page=="pair"?localReady?"You confirmed. Waiting for opponent":"Confirm together to begin":page=="address"?"IPv4 only, e.g. 192.168.1.42":"Port 28773  /  90 second pairing timeout";view.footer="ENTER select  ESC cancel";
   }
   if(time<toastUntil&&!toast.empty()){view.notice=toast;view.noticeError=toast.find("failed")!=std::string::npos||toast.find("not saved")!=std::string::npos||toast.find("damaged")!=std::string::npos;}
   chessui::draw(view);
 }
};
}
int main(int argc,char** argv){
 try{
#ifdef __unix__
   umask(0077);
#endif
   auto args=arguments(argc,argv);App app(args.state);
   if(args.quick||args.demo||!args.moves.empty()) {app.setup.mode=Mode::Local;app.startGame();if(args.demo&&args.moves.empty())args.moves="e2e4,e7e5,g1f3,b8c6,f1c4,g8f6";for(auto& m:split(args.moves))if(!app.game.moveUci(m,nowMs()))throw std::runtime_error("Illegal --moves entry: "+m);app.persist();}
   tyrell::game::RuntimeOptions options;options.title="Tyrell Chess";if(args.headless){options.backend=tyrell::game::Backend::Headless;options.fps=0;}
   tyrell::game::Runtime runtime(options);auto scripted=split(args.script);size_t scriptIndex=0;int frames=0;
   while(!runtime.closed()&&!app.quit){runtime.poll();app.update(runtime.time());
     if(scriptIndex<scripted.size())app.key(scriptKey(scripted[scriptIndex++]));
     app.input(runtime,runtime.time());
     while(runtime.character()!=0){} // This app accepts numeric IP input from the ordered key stream.

     runtime.begin_frame();app.draw();runtime.end_frame();++frames;if(args.frames&&frames>=args.frames)break;
   }
   app.persist();if(!args.screenshot.empty())runtime.screenshot(args.screenshot);
   if(!args.report.empty()){std::ofstream out(args.report);if(!out)throw std::runtime_error("Cannot write report");out<<"{\"version\":\"0.1.1\",\"frames\":"<<frames<<",\"plies\":"<<app.game.history.size()<<",\"fen\":\""<<chess::fen(app.game.board)<<"\",\"screen\":"<<int(app.screen)<<",\"result\":\""<<app.game.result<<"\"}\n";}
   return 0;
 }catch(const std::exception& e){std::cerr<<"Tyrell Chess: "<<e.what()<<'\n';return 1;}
}
