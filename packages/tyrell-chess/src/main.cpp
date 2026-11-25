#include "game.hpp"
#include "view.hpp"
#include "lan.hpp"
#include "chess_rules.h"
#include "chess960.h"
#include "chess_zobrist.h"
#include "cursor_navigation.h"
#include "tyrell/game/runtime.hpp"
#include "raylib.h"
#include "tyrell/app/commands.hpp"
#include "offline_lifecycle.h"
#include "lan_terminal.h"
#include <atomic>
#include <functional>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
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
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
#include <cerrno>
#include <poll.h>
#endif
#ifdef __unix__
#include <sys/stat.h>
#endif
using chess::Game;
using chess::Mode;
using chessui::Screen;
namespace {
uint64_t monotonicMs() {return uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());}
[[maybe_unused]] uint32_t nowMs() {return uint32_t(monotonicMs());}
std::string scoreName(const Game& g) {return g.result=="1-0"?"White wins":g.result=="0-1"?"Black wins":g.result=="1/2-1/2"?"Draw":"In progress";}
std::string modeName(Mode mode) {switch(mode){case Mode::Local:return "TWO PLAYERS";case Mode::Computer:return "VS COMPUTER";case Mode::Puzzle:return "PUZZLE";default:return "LAN MATCH";}}
enum class Action { Exit, Up, Down, Left, Right, Confirm, Back, Help, Flip, Contrast,
 Review, Export, Hint, NextPuzzle, Undo, AcceptDraw, CycleTargets, AddressCharacter,
 AddressErase, AddressDelete, Return, HelpPrevious, HelpNext };
using Command = tyrell::app::Command<Action>;
using Repeat = tyrell::app::Repeat;
using Confirmation = tyrell::app::Confirmation;
const char* times[]={"No timer","1+0 Bullet","3+2 Blitz","5+3 Blitz","10+0 Rapid"};
const char* diffs[]={"None","Easy","Medium","Hard"};
struct Args {
 bool headless=false,demo=false,quick=false;
 int frames=0;
 double uiTime=-1;
 std::string screenshot,report,moves,script;
 std::filesystem::path state;
 bool explicitState=false;
};
Args arguments(int argc,char** argv) {
 Args a;
 for(int i=1;i<argc;++i){std::string s=argv[i];auto val=[&](){if(++i>=argc)throw std::runtime_error("Missing value for "+s);return std::string(argv[i]);};
 if(s=="--help"){std::cout<<"Tyrell Chess 0.1.6 for Cardputer Zero\nUsage: tyrell-chess [--headless] [--frames N] [--demo] [--quick] [--moves e2e4,e7e5] [--script key,key] [--screenshot PATH] [--report PATH] [--state-dir PATH] [--ui-time SECONDS]\nAutomation requires --state-dir; headless mode also requires --frames. Normal launch opens the menu; Q saves and exits.\n";std::exit(0);}
 else if(s=="--version"){std::cout<<"Tyrell Chess 0.1.6 (upstream 64419e29c828)\n";std::exit(0);}
 else if(s=="--headless")a.headless=true;else if(s=="--demo")a.demo=true;else if(s=="--quick")a.quick=true;
 else if(s=="--ui-time"){auto value=val();size_t n; a.uiTime=std::stod(value,&n);if(n!=value.size()||!std::isfinite(a.uiTime)||a.uiTime<0||a.uiTime>86400)throw std::runtime_error("UI time must be 0..86400 seconds");}
 else if(s=="--frames"){auto text=val();size_t n;long count=std::stol(text,&n);if(n!=text.size()||count<1||count>100000)throw std::runtime_error("Frames must be 1..100000");a.frames=int(count);}
 else if(s=="--screenshot")a.screenshot=val();else if(s=="--report")a.report=val();else if(s=="--state-dir"){a.state=val();if(a.state.empty())throw std::runtime_error("--state-dir must not be empty");a.explicitState=true;}else if(s=="--moves")a.moves=val();else if(s=="--script")a.script=val();else throw std::runtime_error("Unknown argument: "+s);
 }
 if(a.headless&&!a.frames)throw std::runtime_error("Use --frames with --headless");
 if(a.uiTime>=0&&!a.headless)throw std::runtime_error("--ui-time is for headless rendering only");
 if((a.headless||a.demo||a.quick||a.frames||!a.moves.empty()||!a.script.empty()||a.uiTime>=0)&&!a.explicitState)throw std::runtime_error("Automation requires --state-dir to protect your saved game");
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
 size_t helpPage=0;
 std::string toast,dialog,networkAddress;
 double toastUntil=0,animationStart=-10,puzzleReplyAt=0,networkStartedAt=0;
 Piece animatedPiece;
 Move animatedMove;
 std::vector<Move> promotions;
 std::future<Move> worker;
 std::shared_ptr<std::atomic_bool> workerCancel;
 std::function<uint64_t()> monotonicClock;
 bool linkedSession=false,lifecyclePaused=false,lifecycleSavePending=false,lifecycleSaveAttempted=false;
 bool lanForegroundAllowed=true,lanLifecycleAttached=false,lanSealPending=false;
 // The loop owns this synchronous admission hook. The post-poll observer is a
 // controlled fixture seam; neither callback enables linked LAN in main().
 std::function<bool()> lifecycleAdmission;
 std::function<void()> afterLanPoll;
 uint64_t suspendedAt=0;uint32_t suspendedStamp=0,suspendedRemaining=0;bool suspendedClockRunning=false;
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
 chess::LanTerminal lanTerminal;
 Game lastHostConfirmedGame;
 ChessClockSnapshot lastHostConfirmedClock,hostClockCheckpoint;
 bool haveHostConfirmation=false,haveHostClockCheckpoint=false;
 uint64_t lastLanServiceAt=0,lastLanAuthorityAt=0,hostClockCheckpointAt=0;
 std::string pendingLanMove;
 size_t pendingLanPly=0;
 uint64_t pendingLanQueuedAt=0;
 bool networkHost=false,helloSent=false,localReady=false,remoteReady=false,networkPlaying=false,networkEndSent=false;
 int pairCode=0,puzzleCategory=-1;
 uint32_t lastSave=0,lastSync=0;
 std::vector<std::filesystem::path> archives;
 std::string page="",status;
 double time=0,uiTimeOverride=-1;
 explicit App(std::filesystem::path state,double uiTime=-1,bool linked=false,
              std::function<uint64_t()> clock=monotonicMs)
   :dir(std::move(state)),monotonicClock(std::move(clock)),linkedSession(linked),uiTimeOverride(uiTime) {
   std::filesystem::create_directories(dir);
   std::ifstream settings(dir/"settings");settings>>contrast>>motion;
   std::ifstream progress(dir/"puzzles");int n;while(progress>>n)if(n>=0&&n<puzzleCount())solved.insert(n);
   std::string error;haveGame=game.load(dir/"active.save",clockNow(),error);
   if(haveGame){pendingArchive=game.over();game.clock.pause(clockNow());flipped=game.options.mode==Mode::Computer&&game.options.human==PieceColor::Black;cursor={4,uint8_t(flipped?6:1)};}
   else if(std::filesystem::exists(dir/"active.save")) {
     const auto backup=dir/("damaged-"+std::to_string(std::chrono::system_clock::now().time_since_epoch().count())+".save");
     std::error_code ec;std::filesystem::rename(dir/"active.save",backup,ec);
     notify(ec?"Saved game damaged; could not back it up":"Saved game damaged; backup kept");
   }
 }
 ~App(){cancelAI();if(worker.valid())worker.wait();}
 uint32_t clockNow() const {return uint32_t(monotonicClock());}
 uint32_t actionClockNow() const {return lanTerminal.active()?uint32_t(lastLanServiceAt):clockNow();}
 // Explicit opt-in only. Call before opening the transport; a sealed session
 // cannot be recycled until its actual socket/buffer cleanup has completed.
 bool beginLanSession(chess::LanRole role){
   if(linkedSession||lifecyclePaused||!lanForegroundAllowed||lanTerminal.active()||lanTerminal.teardown_pending()||
      lanTerminal.session_id()==UINT64_MAX||(role!=chess::LanRole::Host&&role!=chess::LanRole::Joiner))return false;
   const auto requestGeneration=generation;
   if(lifecycleAdmission&&!lifecycleAdmission())return false;
   if(generation!=requestGeneration||lifecyclePaused||!lanForegroundAllowed)return false;
   if(!lan.begin_session()||!lanTerminal.begin(role))return false;
   const auto now=monotonicClock();lastLanServiceAt=lastLanAuthorityAt=now;lanSealPending=false;
   networkHost=role==chess::LanRole::Host;networkPlaying=false;
   helloSent=localReady=remoteReady=networkEndSent=false;pairCode=0;
   awaitingMove=drawOffered=incomingDrawOffer=false;
   pendingLanMove.clear();pendingLanPly=0;pendingLanQueuedAt=0;pendingSince=0;
   haveHostConfirmation=haveHostClockCheckpoint=false;
   lastHostConfirmedGame=Game{};lastHostConfirmedClock=ChessClockSnapshot{};
   hostClockCheckpoint=ChessClockSnapshot{};hostClockCheckpointAt=now;
   networkStartedAt=time;lastSync=uint32_t(now);resetBoardUI();promotions.clear();
   return true;
 }
 void checkpointLanHost(uint64_t now){
   if(!lanTerminal.active()||!networkHost||!networkPlaying||!online())return;
   if(now<lastLanServiceAt||now-lastLanServiceAt>=chess::LanTerminal::service_gap_limit_ms){sealLanSession(chess::LanEndReason::ServiceGap,now);return;}
   lastLanServiceAt=now;
   hostClockCheckpoint=game.clock.snapshot(uint32_t(now));
   hostClockCheckpointAt=now;haveHostClockCheckpoint=true;
 }
 static void freezeClock(Game& target,const ChessClockSnapshot& snapshot,uint32_t now){
   auto frozen=snapshot;
   if(frozen.started&&!frozen.hasFlaggedSide)frozen.paused=true;
   target.clock.loadSnapshot(frozen,now);
 }
 void confirmLanHostState(uint64_t now,bool clockConfirmed=true){
   if(!lanTerminal.active()||networkHost||!networkPlaying||!online())return;
   if(clockConfirmed)lastHostConfirmedClock=game.clock.snapshot(uint32_t(now));
   lastHostConfirmedGame=game;
   freezeClock(lastHostConfirmedGame,lastHostConfirmedClock,uint32_t(now));
   haveHostConfirmation=true;lastLanAuthorityAt=now;
 }
 bool latchLanSession(chess::LanEndReason reason,uint64_t observedAtMs) noexcept {
   if(!lanTerminal.latch(reason,observedAtMs,networkPlaying&&online()))return false;
   lan.seal();lanSealPending=true;
   return true;
 }
 void finishLanSeal(){
   if(!lanSealPending)return;
   // The shell adapter calls this only after releasing local input/rendering.
   // No persistence, transport close, flush or AI join belongs in this step.
   lanSealPending=false;
   const auto observedAtMs=lanTerminal.interruption().observedAtMs;
   const auto reason=lanTerminal.interruption().reason;
   const bool pending=awaitingMove||!pendingLanMove.empty();
   lanTerminal.retain_request(pending,std::move(pendingLanMove),pendingLanPly,pendingLanQueuedAt);
   const auto boundary=observedAtMs<lastLanServiceAt?lastLanServiceAt:observedAtMs;
   if(lanTerminal.interruption().hadMatch){
     if(networkHost){
       if(!game.over()){
         const auto side=hostClockCheckpoint.activeSide;
         const auto remaining=side==PieceColor::White?hostClockCheckpoint.whiteRemainingMs:hostClockCheckpoint.blackRemainingMs;
         // A uint64 service observation spans even a full uint32 clock cycle.
         // The checkpoint values are projected at this exact checkpoint stamp.
         if(haveHostClockCheckpoint&&hostClockCheckpoint.hasFlaggedSide)
           game.update(uint32_t(hostClockCheckpointAt));
         else if(haveHostClockCheckpoint&&hostClockCheckpoint.started&&!hostClockCheckpoint.paused&&
            !hostClockCheckpoint.hasFlaggedSide&&boundary>=hostClockCheckpointAt&&
            boundary-hostClockCheckpointAt>=remaining)
           game.update(uint32_t(hostClockCheckpointAt+remaining));
         else game.update(uint32_t(boundary));
       }
       const auto clock=game.clock.snapshot(uint32_t(boundary));
       freezeClock(game,clock,uint32_t(boundary));
       lanTerminal.retain(clock,game.result,game.reason);
     }else if(haveHostConfirmation){
       // Detach the retained view without replaying moves or projecting clocks.
       game=std::move(lastHostConfirmedGame);
       lanTerminal.retain(lastHostConfirmedClock,game.result,game.reason);
     }
   }
   ++generation;cancelAI();resetBoardUI();promotions.clear();
   toast.clear();toastUntil=0;
   networkPlaying=false;awaitingMove=drawOffered=incomingDrawOffer=false;
   helloSent=localReady=remoteReady=networkEndSent=false;
   // An interruption is never an ordinary completed-game archive or save.
   if(lanTerminal.interruption().hadMatch)pendingArchive=false;
   screen=lanTerminal.interruption().hadMatch?Screen::Board:Screen::Network;
   page=lanTerminal.interruption().hadMatch?"":"disconnected";review=false;
   status=reason==chess::LanEndReason::ShellPause?"Connection ended on shell suspension":
          reason==chess::LanEndReason::ServiceGap?"Connection ended after a service gap":
          reason==chess::LanEndReason::StaleAuthority?"Host state is no longer current":"Local connection ended";
 }
 bool sealLanSession(chess::LanEndReason reason,uint64_t observedAtMs){
   if(!latchLanSession(reason,observedAtMs))return false;
   finishLanSeal();return true;
 }
 void completeLanTeardown(){
   if(!lanTerminal.teardown_pending())return;
   // Lan::close closes local descriptors and discards buffers; it does not
   // flush, wait for a peer, or promise remote notification.
   lan.close();lanTerminal.teardown_complete();
 }
 bool admitLanWork(){
   const auto admissionGeneration=generation;
   if(lifecycleAdmission&&!lifecycleAdmission())return false;
   // This guard also serves ordinary offline linked callers. A pause+resume
   // may finish foreground in one drain, but work popped before it is stale.
   if(generation!=admissionGeneration)return false;
   if(!lanTerminal.begun())return true;
   if(lanTerminal.sealed())return false;
   const auto now=monotonicClock();
   if(now<lastLanServiceAt||now-lastLanServiceAt>=chess::LanTerminal::service_gap_limit_ms){
     sealLanSession(chess::LanEndReason::ServiceGap,now);return false;
   }
   if(!networkHost&&networkPlaying&&haveHostConfirmation&&
      (now<lastLanAuthorityAt||now-lastLanAuthorityAt>=chess::LanTerminal::stale_authority_limit_ms)){
     sealLanSession(chess::LanEndReason::StaleAuthority,now);return false;
   }
   lastLanServiceAt=now;checkpointLanHost(now);return true;
 }
 bool sendLan(const std::string& line){return admitLanWork()&&lan.send(line);}
 void cancelAI(){if(workerCancel)workerCancel->store(true,std::memory_order_relaxed);}
 void launchAI(ChessBoard board,AIDifficulty difficulty,uint32_t budget){
   workerCancel=std::make_shared<std::atomic_bool>(false);auto cancel=workerCancel;
   worker=std::async(std::launch::async,[board,difficulty,budget,cancel]()mutable{
     return ChessAI::findBestMove(board,difficulty,budget,cancel.get());
   });
 }
 void settleLifecycleClock(bool fullWrap=false){
   if(lanTerminal.sealed()||!haveGame||archiveMode||online()||game.over())return;
   const bool before=game.over();
   if(fullWrap&&suspendedClockRunning)game.update(uint32_t(suspendedStamp+suspendedRemaining));
   else game.update(clockNow());
   if(!before&&game.over())finished(false);
 }
 void suspendSession(){
   if(lifecyclePaused)return;
   ++generation;cancelAI();lifecyclePaused=true;lifecycleSavePending=true;lifecycleSaveAttempted=false;
   repeatKey=0;animatedPiece={};animationStart=-1000;hintFrom=hintTo=NO_SQUARE;
   settleLifecycleClock();suspendedAt=monotonicClock();suspendedStamp=clockNow();
   suspendedClockRunning=haveGame&&!archiveMode&&!online()&&!game.over()&&game.clock.isRunning();
   suspendedRemaining=suspendedClockRunning?game.clock.remainingMs(game.clock.activeSide()):0;
 }
 void resumeSession(double timestamp){
   if(!lifecyclePaused)return;
   const auto now=monotonicClock();
   settleLifecycleClock(now<suspendedAt||now-suspendedAt>=(uint64_t(1)<<32));
   time=timestamp;lifecyclePaused=false;repeatKey=0;animatedPiece={};animationStart=-1000;
   if(haveGame&&game.options.mode==Mode::Puzzle&&game.board.sideToMove()!=game.options.human)
     puzzleReplyAt=time+.5;
 }
 bool linkedOfflineAllowed() const {return !online()&&setup.mode!=Mode::Host&&setup.mode!=Mode::Join;}

 double uiNow() const {return uiTimeOverride>=0?uiTimeOverride:time;}
 void notify(const std::string& text){toast=text;toastUntil=uiNow()+3;}
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
   if(lanTerminal.sealed())return true;
   // Hidden offline work remains deferred. A normal foreground Q may still
   // finish its existing final save after Runtime has released device ownership.
   if(lifecyclePaused)return false;
   lastSave=clockNow();bool sideSaved=true;
   if(pendingProgress&&!saveProgress())sideSaved=false;
   if(pendingSettings&&!settings())sideSaved=false;
   if(!haveGame||archiveMode)return sideSaved;
   const bool archived=!pendingArchive||archive();if(archived)pendingArchive=false;
   std::string error;if(!game.save(dir/"active.save",clockNow(),error)){notify("Save failed. Free space / retry");std::cerr<<"Tyrell Chess save: "<<error<<'\n';return false;}
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
 bool canPlay() const {return !lanTerminal.sealed()&&haveGame&&!game.over()&&!review&&!awaitingMove&&(!online()||networkPlaying)&&(!online()||game.board.sideToMove()==(networkHost?PieceColor::White:PieceColor::Black))&&(game.options.mode!=Mode::Puzzle||game.board.sideToMove()==game.options.human);}
 bool thinking() const {return worker.valid();}
 bool activeComputer() const {return haveGame&&!archiveMode&&!review&&game.options.mode==Mode::Computer&&!game.over()&&(screen==Screen::Board||page=="pause"||(screen==Screen::Help&&previous==Screen::Board));}
 void startGame(){
   if(lanTerminal.begun())return;
   if(linkedSession&&(setup.mode==Mode::Host||setup.mode==Mode::Join)){notify("LAN play is unavailable in this shell session");return;}
   if(worker.valid()||((pendingArchive||pendingProgress||pendingSettings)&&!persist()))return;
   setup.position=setup.variant==ChessVariant::Chess960?chess960RandomIndex():518;
   ++generation;pendingArchive=false;archiveMode=false;status.clear();game.start(setup,clockNow());haveGame=true;review=false;page.clear();flipped=setup.mode==Mode::Computer&&setup.human==PieceColor::Black;cursor={4,uint8_t(flipped?6:1)};resetBoardUI();clearOffers();screen=Screen::Board;persist();
 }
 bool archive(){
   if(lanTerminal.sealed())return true;
   if(game.options.mode==Mode::Puzzle||!game.over())return true;
   // Stable content ID makes a retry after restart idempotent.
   uint64_t id=14695981039346656037ull;std::string identity=chess::fen(game.initial)+game.result+std::to_string(int(game.options.mode))+std::to_string(int(game.options.human))+std::to_string(int(game.options.time))+std::to_string(int(game.options.difficulty));
   for(const auto& record:game.history)identity+=chess::uci(record.move);
   for(unsigned char ch:identity){id^=ch;id*=1099511628211ull;}
   std::string error;auto path=dir/"games"/(std::to_string(id)+".pgn");
   if(!game.exportPgn(path,error)||(!online()&&!game.save(dir/"games"/(std::to_string(id)+".save"),clockNow(),error))){notify("Archive failed. Free space / retry");std::cerr<<"Tyrell Chess archive: "<<error<<'\n';return false;}return true;
 }
 void finished(bool save=true){if(lanTerminal.sealed())return;if(game.over()){clearOffers();screen=Screen::Board;page.clear();review=false;menu=0;resetBoardUI();promotions.clear();pendingArchive=game.options.mode!=Mode::Puzzle;if(save){const bool saved=persist();if(saved)notify(scoreName(game)+" - "+game.reason);}else lifecycleSavePending=true;}}

 void broadcastMove(){if(!admitLanWork())return;std::ostringstream o;auto c=game.clock.snapshot(clockNow());o<<"PLY "<<game.history.size()<<' '<<chess::uci(game.history.back().move)<<' '<<ChessZobrist::hash(game.board)<<' '<<c.whiteRemainingMs<<' '<<c.blackRemainingMs;sendLan(o.str());}
 bool apply(const Move& m,bool remote=false){
   if(!admitLanWork())return false;
   const auto admittedAt=lastLanServiceAt;
   if(online()&&!networkHost&&!remote){if(awaitingMove)return false;std::ostringstream o;o<<"REQ "<<game.history.size()<<' '<<chess::uci(m);if(!sendLan(o.str())){if(!lanTerminal.sealed())notify("Connection unavailable");return false;}awaitingMove=true;pendingSince=time;if(lanTerminal.active()){pendingLanMove=chess::uci(m);pendingLanPly=game.history.size();pendingLanQueuedAt=lastLanServiceAt;}clearSelection();return true;}
   if(game.options.mode==Mode::Puzzle&&!remote){if(game.puzzleStep>=game.solutionLength||!(m==game.solution[game.puzzleStep])){notify("Try another move. H for a hint");cursor=m.from;clearSelection();return false;}}
   const auto oldCursor=cursor;const auto piece=game.board.at(m.from.col,m.from.row);if(!game.move(m,actionClockNow())){if(game.over())finished();return false;}
   // A move changes the active clock. Preserve that transition at the same
   // admitted observation before a later read can detect a stop/service gap.
   checkpointLanHost(admittedAt);
   clearOffers(true);animatedPiece=piece;animatedMove=m;animationStart=uiNow();const bool keepCursor=(game.options.mode==Mode::Computer&&piece.color!=game.options.human)||(remote&&(online()||game.options.mode==Mode::Puzzle));cursor=keepCursor?oldCursor:m.to;if(keepCursor)focusPlayer();clearSelection();hintFrom=hintTo=NO_SQUARE;
   if(online()&&networkHost)broadcastMove();
   if(game.options.mode==Mode::Puzzle){
      if(game.puzzleStep>=game.solutionLength){game.finish(game.options.human==PieceColor::White?"1-0":"0-1","Puzzle solved",clockNow());solved.insert(game.puzzleIndex);pendingProgress=true;if(saveProgress())notify("Solved! X for next puzzle");}
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
 std::vector<Command> commands(Screen context) const {
   if(lanTerminal.sealed()){
     if(lanLifecycleAttached&&lanForegroundAllowed&&!lifecyclePaused)
       return {{Action::Exit,{KEY_Q},"Q","Exit","Exit ended session without saving",true,Repeat::PressOnly,Confirmation::None,{}}};
     return {};
   }
   std::vector<Command> result;
   auto add=[&](Action id,std::initializer_list<int> keys,const std::string& keyLabel,
                const std::string& label,const std::string& help,Repeat repeat=Repeat::PressOnly,
                bool enabled=true,const std::string& reason={},Confirmation confirmation=Confirmation::None){
     result.push_back({id,keys,keyLabel,label,help,enabled,repeat,confirmation,reason});
   };
   const bool live=online()&&networkPlaying&&!game.over();
   add(Action::Exit,{KEY_Q},"Q",live?"Leave":"Exit",live?"Confirm leaving live match":"Save and exit",
       Repeat::PressOnly,true,{},live?Confirmation::Required:Confirmation::None);
   if(context==Screen::Help){
     add(Action::Return,{KEY_ENTER,KEY_KP_ENTER,KEY_ESCAPE,KEY_I},"Enter/Esc/I","Back","Return to previous screen");
     const auto count=contextHelp().size();
     add(Action::HelpPrevious,{KEY_LEFT},"Left","Prev","Previous help page",Repeat::PressOnly,helpPage>0,"First help page");
     add(Action::HelpNext,{KEY_RIGHT},"Right","Next","Next help page",Repeat::PressOnly,(helpPage+1)*8<count,"Last help page");
     return result;
   }
   if(page=="address"){
     std::vector<int> keys{KEY_PERIOD,KEY_KP_DECIMAL};
     for(int k=KEY_ZERO;k<=KEY_NINE;++k)keys.push_back(k);
     for(int k=KEY_KP_0;k<=KEY_KP_9;++k)keys.push_back(k);
     result.push_back({Action::AddressCharacter,std::move(keys),"0-9 / .","Address","Type host IPv4 address",
                       networkAddress.size()<15,Repeat::PressOnly,Confirmation::None,"Address is full"});
     add(Action::Down,{KEY_DOWN,KEY_S},"Down/S","Row","Next address row (hold)",Repeat::Allow);
     add(Action::Up,{KEY_UP,KEY_W},"Up/W","Row","Previous address row (hold)",Repeat::Allow);
     add(Action::Back,{KEY_ESCAPE},"Esc","Back","Return to LAN menu");
     add(Action::AddressErase,{KEY_BACKSPACE},"Backspace","Erase","Erase address digit (hold)",Repeat::Allow,
         !networkAddress.empty(),"Address is empty");
     add(Action::AddressDelete,{KEY_DELETE},"Delete","Erase","Erase address digit",Repeat::PressOnly,
         !networkAddress.empty(),"Address is empty");
     add(Action::Confirm,{KEY_ENTER,KEY_KP_ENTER},"Enter","Connect","Confirm address row");
     return result;
   }
   if(context!=Screen::Promotion){
     const bool vertical=context!=Screen::Board||!review;
     add(Action::Up,{KEY_UP,KEY_W,KEY_SEMICOLON},"Up/W/;","Up",context==Screen::Board?"Move cursor up (hold)":"Previous row (hold)",Repeat::Allow,vertical,"Use Left/Right in replay");
     add(Action::Down,{KEY_DOWN,KEY_S,KEY_PERIOD},"Down/S/.","Down",context==Screen::Board?"Move cursor down (hold)":"Next row (hold)",Repeat::Allow,vertical,"Use Left/Right in replay");
   }
   const bool horizontal=context==Screen::Board||context==Screen::Promotion||page=="setup"||page=="preferences";
   add(Action::Left,{KEY_LEFT,KEY_A,KEY_COMMA},"Left/A/,",context==Screen::Promotion?"Choose":review?"Step":"Left",
       context==Screen::Board?(review?"Previous replay move (hold)":"Move cursor left (hold)"):context==Screen::Promotion?"Previous promotion piece (hold)":"Change setting left (hold)",Repeat::Allow,horizontal,"No horizontal action here");
   add(Action::Right,{KEY_RIGHT,KEY_D,KEY_SLASH},"Right/D//",context==Screen::Promotion?"Choose":review?"Step":"Right",
       context==Screen::Board?(review?"Next replay move (hold)":"Move cursor right (hold)"):context==Screen::Promotion?"Next promotion piece (hold)":"Change setting right (hold)",Repeat::Allow,horizontal,"No horizontal action here");
   const bool confirm=context!=Screen::Board||(!archiveMode&&(game.over()||(!thinking()&&canPlay()&&
       (game.options.mode!=Mode::Computer||game.humanTurn()))));
   add(Action::Confirm,{KEY_ENTER,KEY_KP_ENTER},"Enter",context==Screen::Promotion||context==Screen::Confirm?"Confirm":context!=Screen::Board?"Select":game.over()?"Menu":selected.valid()?"Move":"Pick",
       context==Screen::Confirm?"Confirm selected choice":context==Screen::Promotion?"Confirm promotion piece":context!=Screen::Board?"Activate selected row":game.over()?"Open result menu":selected.valid()?"Confirm selected move":"Select a piece",Repeat::PressOnly,confirm,
       "Move input unavailable",context==Screen::Confirm||context==Screen::Promotion?Confirmation::Required:Confirmation::None);
   add(Action::Back,{KEY_ESCAPE,KEY_BACKSPACE,KEY_DELETE},"Esc/Backspace",context==Screen::Board?(selected.valid()?"Cancel":review?"Return":"Menu"):"Back",
       context==Screen::Board?(selected.valid()?"Cancel selected move":review?"Leave replay":"Open game menu"):"Cancel / return");
   if(context!=Screen::Board)return result;
   add(Action::Help,{KEY_I},"I","Help","Contextual controls");
   add(Action::Flip,{KEY_F},"F","Flip","Flip board");
   add(Action::Contrast,{KEY_T},"T","Contrast","Toggle board contrast");
   const bool canReview=!archiveMode&&!thinking()&&(!game.clock.enabled()||game.over())&&(!online()||game.over());
   add(Action::Review,{KEY_V},"V","Replay","Toggle move replay",Repeat::PressOnly,canReview,"Review available after game");
   add(Action::Export,{KEY_P},"P","Export","Export PGN");
   const bool hint=!review&&!archiveMode&&!thinking()&&!game.over()&&
       (game.options.mode==Mode::Puzzle||(!online()&&!game.clock.enabled()));
   add(Action::Hint,{KEY_H},"H","Hint","Show a hint",Repeat::PressOnly,hint,
       online()||game.clock.enabled()?"Hints are for untimed offline games":"No hint available now");
   add(Action::NextPuzzle,{KEY_X},"X","Next","Next puzzle in category",Repeat::PressOnly,game.options.mode==Mode::Puzzle,"Not a puzzle");
   const bool undo=!review&&!archiveMode&&!thinking()&&!online()&&!game.clock.enabled()&&!game.history.empty();
   add(Action::Undo,{KEY_U},"U","Undo","Undo previous move",Repeat::PressOnly,undo,"Undo needs an untimed offline game");
   add(Action::AcceptDraw,{KEY_Y},"Y","Draw","Review opponent's draw offer",Repeat::PressOnly,
       live&&!review&&incomingDrawOffer,"No pending draw offer",Confirmation::Required);
   add(Action::CycleTargets,{KEY_SPACE},"Space","Cycle","Cycle legal destinations",Repeat::PressOnly,
       selected.valid(),"Select a piece first");
   return result;
 }
 std::vector<Command> commands() const {return commands(screen);}
 std::vector<std::string> contextHelp() const {return tyrell::app::help_lines(commands(previous));}
 std::string commandHelp(Action id) const {
   const auto table=commands();
   const auto found=std::find_if(table.begin(),table.end(),[&](const auto& c){return c.id==id;});
   return found==table.end()||!found->enabled?std::string{}:tyrell::app::help_line(*found);
 }
 std::string commandFooter() const {
   if(lanTerminal.sealed())return lanLifecycleAttached&&lanForegroundAllowed&&!lifecyclePaused?"Read-only  Q Exit":"Read-only ended session";
   auto table=commands();
   // The full aliases live in help. Keep the first key in compact footer hints.
   if(screen!=Screen::Help)for(auto& command:table){const auto at=command.key_label.find('/');if(at!=std::string::npos)command.key_label.resize(at);}
   if(screen==Screen::Help){
     const auto rows=contextHelp().size(),pages=std::max(size_t(1),(rows+7)/8);
     return tyrell::app::footer(table,{Action::HelpPrevious,Action::HelpNext,Action::Return})+"  "+std::to_string(std::min(helpPage+1,pages))+"/"+std::to_string(pages);
   }
   if(screen==Screen::Home)return tyrell::app::footer(table,{Action::Confirm,Action::Exit});
   if(screen==Screen::Promotion){
     const auto left=std::find_if(table.begin(),table.end(),[](const auto& c){return c.id==Action::Left;});
     const auto right=std::find_if(table.begin(),table.end(),[](const auto& c){return c.id==Action::Right;});
     return left->key_label+"/"+right->key_label+" "+left->label+"   "+tyrell::app::footer(table,{Action::Confirm,Action::Back});
   }
   if(screen!=Screen::Board)return tyrell::app::footer(table,{Action::Up,Action::Down,Action::Confirm,Action::Back});
   if(review){
     const auto left=std::find_if(table.begin(),table.end(),[](const auto& c){return c.id==Action::Left;});
     const auto right=std::find_if(table.begin(),table.end(),[](const auto& c){return c.id==Action::Right;});
     return left->key_label+"/"+right->key_label+" "+left->label;
   }
   if(game.options.mode==Mode::Puzzle)return tyrell::app::footer(table,{Action::Hint,Action::NextPuzzle,Action::Help});
   if(selected.valid())return tyrell::app::footer(table,{Action::Confirm,Action::Back});
   if(game.over())return tyrell::app::footer(table,{Action::Confirm,Action::Review});
   const auto confirm=std::find_if(table.begin(),table.end(),[](const auto& c){return c.id==Action::Confirm;});
   if(confirm==table.end()||!confirm->enabled)return tyrell::app::footer(table,{Action::Help,Action::Back});
   return tyrell::app::footer(table,{Action::Confirm,Action::Help});
 }
 bool repeatable(int k) const {
   const auto table=commands();const auto* command=tyrell::app::find_command(table,k,true);
   return command&&command->enabled;
 }
 template<class Input>void input(Input& source,double timestamp){
   if(lifecyclePaused)return;
   if(repeatKey&&repeatContext!=inputContext())repeatKey=0;
   int k;while((k=source.next_key())!=0&&!quit){
     const auto before=inputContext();key(k);
     if(repeatable(k)&&before==inputContext()){repeatKey=k;repeatAt=timestamp+.35;repeatContext=inputContext();}
     else repeatKey=0;
   }
   if(!repeatKey)return;
   if(!source.down(repeatKey)||repeatContext!=inputContext()){repeatKey=0;return;}
   if(timestamp>=repeatAt){const auto before=inputContext();key(repeatKey,true);repeatAt=timestamp+.10;if(before!=inputContext())repeatKey=0;}
 }

 void home(){
   if(lanTerminal.sealed())return;
   const bool temporary=archiveMode||(haveGame&&(game.options.mode==Mode::Puzzle||online()));
   if((!temporary||pendingArchive||pendingProgress||pendingSettings)&&!persist())return;
   clearSelection();review=false;page.clear();status.clear();networkPlaying=false;awaitingMove=false;clearOffers();lan.close();++generation;
   if(temporary){std::string error;Game active;haveGame=active.load(dir/"active.save",clockNow(),error);if(haveGame&&linkedSession&&(active.options.mode==Mode::Host||active.options.mode==Mode::Join)){haveGame=false;notify("LAN games are unavailable in this shell session");}if(haveGame)game=std::move(active);else game=Game{};}
   archiveMode=false;
   if(haveGame){game.clock.pause(clockNow());flipped=game.options.mode==Mode::Computer&&game.options.human==PieceColor::Black;cursor={4,uint8_t(flipped?6:1)};}screen=Screen::Home;menu=homeMenu;persist();
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
 void setupMenu(Mode m){if(linkedSession&&(m==Mode::Host||m==Mode::Join)){notify("LAN play is unavailable in this shell session");return;}setup=chess::Options{};setup.mode=m;screen=Screen::Setup;page="setup";menu=0;}
 void nextPuzzle(){if((pendingProgress||pendingSettings)&&!persist())return;++generation;archiveMode=false;status.clear();int start=game.puzzleIndex>=0?game.puzzleIndex+1:0;std::vector<int> candidates;for(int i=0;i<puzzleCount();++i){int p=(start+i)%puzzleCount();bool category=puzzleCategory<0;for(int n=0;!category&&n<puzzleCountByType(PuzzleType(puzzleCategory));++n)category=p==puzzleIndexByType(PuzzleType(puzzleCategory),n);if(category)candidates.push_back(p);}if(candidates.empty())return;int n=candidates[0];for(int p:candidates)if(!solved.count(p)){n=p;break;}game.puzzle(n,clockNow());haveGame=true;review=false;flipped=game.options.human==PieceColor::Black;cursor=NO_SQUARE;focusPlayer();resetBoardUI();screen=Screen::Board;page.clear();}
 void hint(){
   if(game.options.mode==Mode::Puzzle){int n=game.puzzleStep;if(n<game.solutionLength){hintFrom=game.solution[n].from;hintTo=game.solution[n].to;notify("Look at the marked squares");}return;}
   if(online()||game.clock.enabled()){notify("Hints are for untimed offline games");return;}
   if(thinking()||game.over())return;workerHint=true;workerGeneration=generation;workerHash=ChessZobrist::hash(game.board);auto b=game.board;
   launchAI(b,AIDifficulty::Hard,500);
 }
 std::vector<std::string> homeItems() const {return {haveGame?"Resume game":"Start playing","Vs computer","Two players","Puzzles",linkedSession?"LAN unavailable":"LAN play","Game library","Preferences","Controls","Exit"};}
 std::vector<std::string> items() {
   if(lanTerminal.sealed())return {};
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
   if(!admitLanWork())return;
   if(lifecyclePaused)return;
   if(linkedSession&&screen==Screen::Home&&menu==4){notify("LAN play is unavailable in this shell session");return;}
   if(linkedSession&&(page=="lan"||page=="address"||page=="pair"||page=="waiting"||(page=="setup"&&(setup.mode==Mode::Host||setup.mode==Mode::Join)))){lan.close();notify("LAN play is unavailable in this shell session");return;}
   if(screen==Screen::Home){if((pendingArchive||pendingProgress||pendingSettings)&&menu!=0&&!persist())return;homeMenu=menu;
     if(menu==0){if(haveGame){screen=Screen::Board;review=false;if(!game.over())game.clock.resume(clockNow());}else setupMenu(Mode::Computer);}
     else if(menu==1||menu==2){if(haveGame&&!game.over()){screen=Screen::Confirm;dialog=menu==1?"computer":"local";menu=0;}else setupMenu(menu==1?Mode::Computer:Mode::Local);}
     else if(menu==3){if(!persist())return;screen=Screen::Setup;page="puzzles";menu=0;}
     else if(menu==4){if(!persist())return;screen=Screen::Network;page="lan";menu=0;}
     else if(menu==5)loadLibrary();else if(menu==6){screen=Screen::Setup;page="preferences";menu=0;}else if(menu==7){previous=screen;helpPage=0;screen=Screen::Help;}else {if(persist())quit=true;}return;
   }
   if(screen==Screen::Confirm){
     if(dialog=="leave-menu"||dialog=="leave-exit"){
       const bool exiting=dialog=="leave-exit";if(menu==0){cancelLeave();return;}
       if(!persist())return;lan.close();networkPlaying=false;awaitingMove=false;clearOffers();
       if(exiting)quit=true;else home();return;
     }
     if(dialog=="accept-draw"){
       if(menu==1&&online()&&networkPlaying&&!review&&!game.over()&&incomingDrawOffer){
         if(networkHost){game.agreeDraw(actionClockNow());finished();}
         else {if(!sendLan("ACCEPTDRAW")&&lanTerminal.sealed())return;incomingDrawOffer=false;status="Draw accepted; waiting";}
       }
       screen=Screen::Board;page.clear();return;
     }
     if(dialog=="castle"){for(const auto& m:promotions)if(m.isCastle==(menu==1)){screen=Screen::Board;apply(m);break;}return;}
     if(dialog=="resign"||dialog=="draw"){
       if(menu==1&&!game.over()){
         if(dialog=="draw"){game.agreeDraw(actionClockNow());finished();}
         else {auto side=online()?(networkHost?PieceColor::White:PieceColor::Black):game.options.mode==Mode::Computer?game.options.human:game.board.sideToMove();if(online()&&!networkHost){if(!sendLan("RESIGN")&&lanTerminal.sealed())return;}else{game.resign(side,actionClockNow());finished();}}
       }
       screen=Screen::Board;page.clear();
     }else if(menu==0)home();else setupMenu(dialog=="computer"?Mode::Computer:Mode::Local);return;
   }
   if(screen==Screen::Promotion){const PieceType types[]={PieceType::Queen,PieceType::Knight,PieceType::Rook,PieceType::Bishop};for(auto m:promotions)if(m.promotion==types[promotion]){screen=Screen::Board;apply(m);break;}return;}
   if(screen==Screen::Help){screen=previous;return;}
   if(page=="setup"){if(menu<4)change(1);else if(setup.mode==Mode::Host){setup.position=setup.variant==ChessVariant::Chess960?chess960RandomIndex():518;std::string error;if(lan.host(error)){networkHost=true;networkPlaying=false;helloSent=localReady=remoteReady=networkEndSent=drawOffered=incomingDrawOffer=awaitingMove=false;page="waiting";screen=Screen::Network;networkStartedAt=time;menu=0;}else notify(error);}else startGame();return;}
   if(page=="preferences"){if(menu==2)home();else change(1);return;}
   if(page=="puzzles"){if(menu==4)home();else{puzzleCategory=menu-1;game.puzzleIndex=-1;nextPuzzle();}return;}
   if(page=="library"){if(menu==int(archives.size())){home();return;}std::string error;Game loaded;if(loaded.load(archives[menu],clockNow(),error)){if(linkedSession&&(loaded.options.mode==Mode::Host||loaded.options.mode==Mode::Join)){notify("LAN games are unavailable in this shell session");return;}++generation;archiveMode=true;game=std::move(loaded);flipped=game.options.mode==Mode::Computer&&game.options.human==PieceColor::Black;haveGame=true;review=true;reviewPly=game.history.size();screen=Screen::Board;page="archive";}else notify("Cannot open: "+error);return;}
   if(page=="pause"){
     if(menu==0){screen=Screen::Board;page.clear();}
     else if(menu==1){if(online()&&!game.over()){askLeave(false);}else home();}
     else if(menu==2){if((game.clock.enabled()||online())&&!game.over())notify("Review available after game");else{review=true;reviewPly=game.history.size();screen=Screen::Board;page.clear();}}
     else if(menu==3){std::string error;auto path=dir/"exports/last-game.pgn";notify(game.exportPgn(path,error)?"PGN saved in exports/":"Export failed: "+error);}
     else if(menu==4){if(!game.over()){screen=Screen::Confirm;dialog="resign";menu=0;}else {screen=Screen::Board;page.clear();}}
     else if(menu==5){if(game.over()){screen=Screen::Board;page.clear();return;}if(online()){drawOffered=true;if(!sendLan("DRAW")&&lanTerminal.sealed())return;notify("Draw offered to opponent");screen=Screen::Board;page.clear();}else if(!game.over()){screen=Screen::Confirm;dialog="draw";menu=0;}}
     else {previous=screen;helpPage=0;screen=Screen::Help;}return;
   }
   if(page=="lan"){if(menu==0)setupMenu(Mode::Host);else if(menu==1){page="address";networkAddress.clear();menu=0;}else home();return;}
   if(page=="address"){if(menu==2){page="lan";menu=0;}else{std::string error;if(lan.join(networkAddress,error)){networkHost=false;networkPlaying=false;helloSent=localReady=remoteReady=networkEndSent=drawOffered=incomingDrawOffer=awaitingMove=false;page="waiting";networkStartedAt=time;menu=0;}else notify(error);}return;}
   if(page=="pair"){if(menu==1){lan.close();home();}else {localReady=true;if(!sendLan("READY")&&lanTerminal.sealed())return;}return;}
   if(page=="waiting"||page=="disconnected"){lan.close();home();}
 }
 void key(int k,bool repeated=false){
   const auto inputGeneration=generation;
   if(lifecyclePaused||!lanForegroundAllowed||(lifecycleAdmission&&!lifecycleAdmission()))return;
   // A pause+resume drained here baselines Runtime input, but this key was
   // already popped by the caller. It belongs to the old ownership interval.
   if(generation!=inputGeneration)return;
   if(lanTerminal.sealed()){
     // Only explicit foreground exit is offered by the controlled adapter.
     // It neither persists nor changes the retained match or peer identity.
     if(lanLifecycleAttached&&k==KEY_Q&&!repeated)quit=true;
     return;
   }
   if(!admitLanWork())return;
   const auto table=commands();const auto* command=tyrell::app::find_command(table,k,repeated);
   if(!command)return;
   const auto action=command->id;
   if(!command->enabled){
     // Preserve the established feedback for attempted unavailable operations.
     if(action==Action::Review&&!archiveMode)notify(command->unavailable_reason);
     else if(action==Action::Hint&&!review&&!archiveMode&&(online()||game.clock.enabled()))notify(command->unavailable_reason);
     else if(action==Action::Undo&&!review&&!archiveMode&&!thinking())notify(command->unavailable_reason);
     return;
   }
   if(action==Action::Exit){if(online()&&networkPlaying&&!game.over()){askLeave(true);}else if(persist())quit=true;return;}
   if(screen==Screen::Help){
     if(action==Action::Return)screen=previous;
     else if(action==Action::HelpPrevious&&helpPage)--helpPage;
     else if(action==Action::HelpNext)++helpPage;
     return;
   }
   if(page=="address"){
     if(action==Action::AddressCharacter){menu=0;networkAddress+=k>=KEY_KP_0&&k<=KEY_KP_9?char('0'+k-KEY_KP_0):k==KEY_KP_DECIMAL?'.':char(k);}
     else if(action==Action::Down)menu=(menu+1)%3;
     else if(action==Action::Up)menu=(menu+2)%3;
     else if(action==Action::Back){page="lan";menu=0;}
     else if(action==Action::AddressErase||action==Action::AddressDelete){menu=0;networkAddress.pop_back();}
     else if(action==Action::Confirm)activate();
     return;
   }
   if(screen!=Screen::Board){
     if(action==Action::Back){if(screen==Screen::Confirm&&(dialog=="leave-menu"||dialog=="leave-exit")){cancelLeave();return;}if(screen==Screen::Promotion){screen=Screen::Board;clearSelection();}else if(screen==Screen::Confirm&&(dialog=="resign"||dialog=="draw"||dialog=="castle"||dialog=="accept-draw"||dialog=="leave-menu"||dialog=="leave-exit")){screen=Screen::Board;page.clear();}else if(page=="pause"){screen=Screen::Board;page.clear();}else{lan.close();home();}return;}
     if(screen==Screen::Promotion){if(action==Action::Left)promotion=(promotion+3)%4;if(action==Action::Right)promotion=(promotion+1)%4;if(action==Action::Confirm)activate();return;}
     if(action==Action::Down)menu=(menu+1)%std::max(1,int(items().size()));
     if(action==Action::Up)menu=(menu+std::max(1,int(items().size()))-1)%std::max(1,int(items().size()));
     if(action==Action::Left)change(-1);if(action==Action::Right)change(1);
     if(action==Action::Confirm)activate();return;
   }
   if(action==Action::Help){previous=screen;helpPage=0;screen=Screen::Help;return;}
   if(action==Action::Back){if(selected.valid()){cursor=selected;clearSelection();}else if(review){review=false;if(page=="archive")home();}else pauseMenu();return;}
   if(action==Action::Flip){flipped=!flipped;return;}if(action==Action::Contrast){contrast=!contrast;settings();return;}
   if(action==Action::Review){review=!review;reviewPly=game.history.size();clearSelection();return;}
   if(action==Action::Export){std::string error;notify(game.exportPgn(dir/"exports/last-game.pgn",error)?"PGN saved in exports/":"PGN export failed");return;}
   if(action==Action::Hint){hint();return;}
   if(action==Action::NextPuzzle){nextPuzzle();return;}
   if(action==Action::Undo){if(game.undo(clockNow())){clearSelection();hintFrom=hintTo=NO_SQUARE;persist();}else notify("Undo needs an untimed offline game");return;}
   if(action==Action::AcceptDraw){screen=Screen::Confirm;dialog="accept-draw";menu=0;return;}
   if(action==Action::Left)moveCursor(-1,0);if(action==Action::Right)moveCursor(1,0);
   if(action==Action::Up)moveCursor(0,-1);if(action==Action::Down)moveCursor(0,1);
   if(action==Action::CycleTargets)targetCursor(true);
   if(action==Action::Confirm){if(game.over())pauseMenu();else choose();}
 }
 void disconnect(const std::string& why){
   if(lanTerminal.begun()){
     sealLanSession(chess::LanEndReason::TransportError,monotonicClock());
     completeLanTeardown();status=why;return;
   }
   status=why;lan.close();networkPlaying=false;awaitingMove=false;incomingDrawOffer=drawOffered=false;if(haveGame&&online())game.clock.pause(clockNow());screen=Screen::Network;page="disconnected";menu=0;
 }
 void network(){
   if(!admitLanWork())return;
   lan.poll();
   if(afterLanPoll)afterLanPoll();
   if(!admitLanWork())return;
   if(awaitingMove&&time-pendingSince>10){disconnect("Host did not acknowledge move");return;}
   if(!networkPlaying&&(page=="waiting"||page=="pair")&&time-networkStartedAt>90){disconnect("Pairing timed out");return;}
   if(networkHost&&lan.connected()&&!helloSent){std::random_device r;pairCode=int(r()%900000)+100000;std::ostringstream o;o<<"HELLO 1 "<<int(setup.variant)<<' '<<setup.position<<' '<<int(setup.time)<<' '<<pairCode;if(!sendLan(o.str())&&lanTerminal.sealed())return;helloSent=true;page="pair";menu=0;}
   for(const auto& line:lan.receive()){
     if(!admitLanWork())return;
     const auto receivedAt=lastLanServiceAt;
     const auto packetNow=[&]{return lanTerminal.active()?uint32_t(receivedAt):clockNow();};
     std::istringstream in(line);std::string command;in>>command;
     if(command=="HELLO"&&!networkHost&&!helloSent&&!networkPlaying){int version,variant,pos,tc,code;in>>version>>variant>>pos>>tc>>code;if(!in||version!=1||variant<0||variant>1||pos<0||pos>959||tc<0||tc>4||code<100000||code>999999){disconnect("Incompatible chess peer");break;}setup=chess::Options{};setup.mode=Mode::Join;setup.variant=ChessVariant(variant);setup.position=pos;setup.time=TimeControl(tc);pairCode=code;helloSent=true;page="pair";menu=0;}
     else if(command=="READY"&&helloSent&&!networkPlaying)remoteReady=true;
     else if(command=="START"&&!networkHost&&localReady&&remoteReady&&!networkPlaying){++generation;pendingArchive=false;archiveMode=false;status.clear();game.start(setup,packetNow());networkPlaying=true;haveGame=true;screen=Screen::Board;page.clear();review=false;flipped=true;cursor={4,6};clearSelection();confirmLanHostState(receivedAt);}
     else if(command=="REQ"&&networkHost&&networkPlaying){size_t ply;std::string move;in>>ply>>move;bool ended=game.over();game.update(packetNow());if(!ended&&game.over())finished();if(game.over())continue;if(!in||ply!=game.history.size()||game.board.sideToMove()!=PieceColor::Black){disconnect("Invalid peer move sequence");break;}MoveList moves;ChessRules::generateLegal(game.board,moves);bool applied=false;for(int i=0;i<moves.count;++i)if(chess::uci(moves.moves[i])==move){applied=apply(moves.moves[i],true);break;}if(!applied){if(lanTerminal.sealed())return;if(game.over())finished();else {disconnect("Illegal peer move");break;}}}
     else if(command=="PLY"&&!networkHost&&networkPlaying){
       size_t ply;std::string move;uint32_t hash,w,b;in>>ply>>move>>hash>>w>>b;
       if(!in||ply!=game.history.size()+1){disconnect("Position sequence differs");break;}
       const bool ownMove=game.board.sideToMove()==PieceColor::Black;const auto tc=game.options.time;
       game.clock.configure(TimeControl::None);bool ok=game.moveUci(move,packetNow());
       if(lanTerminal.active()){
         // PLY confirms a position and clock, while END confirms the result.
         game.result=lastHostConfirmedGame.result;game.reason=lastHostConfirmedGame.reason;
       }
       ChessClockSnapshot clock;clock.timeControl=tc;clock.whiteRemainingMs=w;clock.blackRemainingMs=b;
       clock.activeSide=game.board.sideToMove();clock.started=tc!=TimeControl::None;
       clock.paused=tc!=TimeControl::None&&game.over();
       if(!game.clock.loadSnapshot(clock,packetNow()))ok=false;
       if(!ok||ChessZobrist::hash(game.board)!=hash){disconnect("Position differs from host");break;}
       confirmLanHostState(receivedAt);awaitingMove=false;
       if(ownMove&&move==pendingLanMove&&ply-1==pendingLanPly){pendingLanMove.clear();pendingLanPly=0;pendingLanQueuedAt=0;}
       clearOffers(true);if(ownMove)cursor=game.history.back().move.to;else focusPlayer();clearSelection();
     }
     else if(command=="SYNC"&&!networkHost&&networkPlaying){
       uint32_t w,b;size_t ply;in>>ply>>w>>b;
       if(in&&ply==game.history.size()){
         game.clock.synchronizeRemainingMs(w,b,packetNow());confirmLanHostState(receivedAt);
       }
     }
     else if(command=="RESIGN"&&networkHost&&networkPlaying){if(!game.over()){game.resign(PieceColor::Black,packetNow());finished();}}
     else if(command=="DRAW"&&networkPlaying){if(!game.over()){incomingDrawOffer=true;status="Draw offered: Y yes";}}
     else if(command=="ACCEPTDRAW"&&networkHost&&networkPlaying){if(!game.over()&&drawOffered){drawOffered=false;game.agreeDraw(packetNow());finished();}}
     else if(command=="END"&&!networkHost&&networkPlaying){
       std::string score,reason;in>>score;in.get();std::getline(in,reason);
       if(score!="1-0"&&score!="0-1"&&score!="1/2-1/2"){disconnect("Invalid result");break;}
       if(lanTerminal.active()&&haveHostConfirmation)freezeClock(game,lastHostConfirmedClock,packetNow());
       awaitingMove=false;game.finish(score,reason.substr(0,60),packetNow());
       confirmLanHostState(receivedAt,false);finished();
     }
     else {disconnect("Unexpected peer message");break;}
   }
   if(lifecyclePaused||!admitLanWork())return;
   if(!lan.error().empty()&&(networkPlaying||page=="pair"||page=="waiting")){if(networkPlaying&&game.over()&&!lanTerminal.active()){networkPlaying=false;lan.close();status.clear();}else disconnect(lan.error());return;}
   if(networkHost&&localReady&&remoteReady&&!networkPlaying&&page=="pair"){setup.mode=Mode::Host;++generation;pendingArchive=false;archiveMode=false;status.clear();game.start(setup,actionClockNow());haveGame=true;networkPlaying=true;checkpointLanHost(lastLanServiceAt);screen=Screen::Board;page.clear();flipped=false;review=false;cursor={4,1};if(!sendLan("START")&&lanTerminal.sealed())return;clearSelection();}
   if(networkHost&&networkPlaying&&clockNow()-lastSync>=500){auto c=game.clock.snapshot(clockNow());if(!sendLan("SYNC "+std::to_string(game.history.size())+" "+std::to_string(c.whiteRemainingMs)+" "+std::to_string(c.blackRemainingMs))&&lanTerminal.sealed())return;lastSync=clockNow();}
   if(networkHost&&networkPlaying&&game.over()&&!networkEndSent){if(!sendLan("END "+game.result+" "+game.reason)&&lanTerminal.sealed())return;networkEndSent=true;}
 }
 void update(double t){
   if(lifecyclePaused||!admitLanWork())return;
   time=t;if(linkedSession){lan.close();if(online()){quit=true;return;}}else network();
   if(lifecyclePaused||!admitLanWork())return;
   if(lifecycleSavePending&&(!lifecycleSaveAttempted||clockNow()-lastSave>=5000)){lifecycleSaveAttempted=true;lifecycleSavePending=!persist();}
   if(!admitLanWork())return;
   if(haveGame&&screen!=Screen::Home&&page!="library"&&!archiveMode&&(!online()||(networkHost&&networkPlaying))) {bool was=game.over();game.update(actionClockNow());if(!was&&game.over())finished();}
   const auto workGeneration=generation;
   if(worker.valid()&&worker.wait_for(std::chrono::seconds(0))==std::future_status::ready){auto move=worker.get();if(haveGame&&workerGeneration==generation&&((workerHint&&screen==Screen::Board)||(!workerHint&&activeComputer()))&&!review&&!archiveMode&&ChessZobrist::hash(game.board)==workerHash&&!game.over()){if(workerHint){hintFrom=move.from;hintTo=move.to;notify("Try "+chess::uci(move));}else apply(move);}workerHint=false;}
   // A nested move admission may observe pause/loss after harvesting a worker.
   // Do not continue that old ownership interval by launching more work.
   if(lifecyclePaused||generation!=workGeneration||!admitLanWork())return;
   if(((screen==Screen::Board&&!review&&!archiveMode)||activeComputer())&&!worker.valid()&&!game.over()){
     if(game.options.mode==Mode::Computer&&!game.humanTurn()){
       workerGeneration=generation;workerHash=ChessZobrist::hash(game.board);workerHint=false;auto b=game.board;auto d=game.options.difficulty;uint32_t budget=game.clock.enabled()?std::max(1u,std::min(3000u,game.clock.remainingMsAt(game.board.sideToMove(),clockNow())/10)):0;
       launchAI(b,d,budget);
     }
     if(game.options.mode==Mode::Puzzle&&game.board.sideToMove()!=game.options.human&&time>=puzzleReplyAt&&game.puzzleStep<game.solutionLength)apply(game.solution[game.puzzleStep],true);
   }
   // The puzzle reply has its own nested admission. Pause+resume in that drain
   // can return foreground while still invalidating this tick's persistence.
   if(lifecyclePaused||generation!=workGeneration||!admitLanWork())return;
   if(((haveGame&&!online()&&!review&&!archiveMode)||pendingProgress||pendingSettings)&&clockNow()-lastSave>=5000)persist();
   if(lanTerminal.active())checkpointLanHost(monotonicClock());
 }
 void draw(double uiTime=-1,std::vector<chessui::TextBounds>* audit=nullptr){
   if(lifecyclePaused)return;
   const double displayTime=uiTime>=0?uiTime:uiNow();
   view=chessui::View{};view.screen=screen;view.board=review?game.atPly(reviewPly):game.board;view.cursor=cursor;view.selected=selected;view.legal=legal;view.hintFrom=hintFrom;view.hintTo=hintTo;view.flipped=flipped;view.monochrome=contrast;view.review=review;view.gameOver=game.over();view.clocksEnabled=game.clock.enabled();view.clocksRunning=game.clock.isRunning();view.thinking=thinking();view.time=displayTime;view.menu=items();view.menuIndex=screen==Screen::Promotion?promotion:menu;
   view.title=game.over()?(game.result=="1-0"?"WHITE WINS":game.result=="0-1"?"BLACK WINS":"DRAW"):modeName(game.options.mode);view.status=game.over()?game.reason:awaitingMove?"Waiting for host...":!status.empty()?status:ChessRules::isInCheck(view.board,view.board.sideToMove())?"Check!":game.board.sideToMove()==PieceColor::White?"White to move":"Black to move";
   view.whiteClock=chess::clockText(game.clock.remainingMsAt(PieceColor::White,clockNow()),game.clock.enabled());view.blackClock=chess::clockText(game.clock.remainingMsAt(PieceColor::Black,clockNow()),game.clock.enabled());view.moveLines=game.lines(review?reviewPly:game.history.size());
   if(review){view.reviewLabel="REPLAY "+std::to_string(reviewPly)+" / "+std::to_string(game.history.size());}
   view.subtitle=game.options.variant==ChessVariant::Chess960?"Chess960 #"+std::to_string(game.options.position):"Standard chess";
   if(!game.history.empty()){size_t n=review?reviewPly:game.history.size();if(n){view.lastFrom=game.history[n-1].move.from;view.lastTo=game.history[n-1].move.to;}}
   if(motion&&!review){view.animatedPiece=animatedPiece;view.animationFrom=animatedMove.from;view.animationTo=animatedMove.to;view.animationProgress=float(std::clamp((displayTime-animationStart)/.18,0.,1.));}
   if(game.options.mode==Mode::Puzzle){view.title="PUZZLE "+std::to_string(game.puzzleIndex+1);view.subtitle=std::to_string(solved.size())+" / "+std::to_string(puzzleCount())+" solved";if(game.over())view.status="Solved! X for next";else view.status=game.puzzleType==PuzzleType::MateIn1?"Mate in one":game.puzzleType==PuzzleType::MateIn2?"Mate in two":"Find the best move";}
   if(screen==Screen::Home){view.title="YOUR MOVE";view.status=std::to_string(solved.size())+" puzzles solved";}
   if(page=="setup"){view.title=setup.mode==Mode::Host?"HOST A MATCH":"NEW GAME";view.subtitle=modeName(setup.mode);view.status="Choose a row, then change with left/right";}
   if(page=="pause"){view.title=game.over()?scoreName(game):"GAME MENU";view.subtitle=game.over()?game.reason:"Clock continues while this menu is open";view.status=commandHelp(Action::Exit);}
   if(page=="puzzles"){view.title="PUZZLE ARCADE";view.subtitle=std::to_string(solved.size())+" / "+std::to_string(puzzleCount())+" solved - progress saves automatically";view.status=commandHelp(Action::Confirm);}
   if(page=="preferences"){view.title="PREFERENCES";view.subtitle="Make the board your own";view.status="";}
   if(page=="library"){view.title="GAME LIBRARY";view.subtitle="Completed offline games, full move replay";view.status=archives.empty()?"Finish a game to start your collection":"Choose a game to replay";}
   if(screen==Screen::Help){
     view.title="HOW TO PLAY";view.status.clear();view.menu=contextHelp();
     const auto pages=std::max(size_t(1),(view.menu.size()+7)/8);helpPage=std::min(helpPage,pages-1);
     view.helpOffset=helpPage*8;
   }
   if(screen==Screen::Confirm){view.title=dialog=="castle"?"KING MOVE OR CASTLE?":dialog=="resign"?"RESIGN THIS GAME?":dialog=="draw"?"AGREE TO A DRAW?":"REPLACE YOUR SAVED GAME?";view.status=dialog=="castle"?"Same destination, different rook move":dialog=="resign"?"Your opponent will win this match":dialog=="draw"?"The game will end in a draw":"Your current match will be replaced";}
   if(screen==Screen::Confirm&&(dialog=="leave-menu"||dialog=="leave-exit")){view.title="LEAVE THIS LAN MATCH?";view.status="Disconnects the match. It cannot resume.";}
   if(screen==Screen::Confirm&&dialog=="accept-draw"){view.title="ACCEPT OPPONENT'S DRAW?";view.status="Confirm to end this match as a draw.";}
   if(screen==Screen::Promotion){view.title="PROMOTE YOUR PAWN";view.status="Choose your new piece";}
   if(screen==Screen::Network){
      view.title=page=="pair"?"PAIR CODE "+std::to_string(pairCode):page=="disconnected"?"CONNECTION ENDED":"LOCAL WIRELESS";
      view.subtitle=page=="pair"?"Both players must see the same code":page=="address"?"Type the host's IP, then press ENTER":page=="waiting"?networkHost?"Waiting at "+chess::localAddress():"Connecting to "+networkAddress:"Play another Zero on the same Wi-Fi";
      view.status=page=="disconnected"?status:page=="pair"?localReady?"You confirmed. Waiting for opponent":"Confirm together to begin":page=="address"?"IPv4 only, e.g. 192.168.1.42":"Port 28773  /  90 second pairing timeout";
   }
   if(lanTerminal.sealed()){
     view.sessionEnded=true;view.title="CONNECTION ENDED";view.authorityNotice="Remote unconfirmed";
     view.subtitle=!lanTerminal.interruption().hadMatch?"No match established":lanTerminal.role()==chess::LanRole::Joiner?"Host-confirmed view":"Last local view";
     switch(lanTerminal.interruption().reason){
       case chess::LanEndReason::ShellPause:view.status="Shell suspended";break;
       case chess::LanEndReason::ShellLost:view.status="Shell link lost";break;
       case chess::LanEndReason::ServiceGap:view.status="Service gap";break;
       case chess::LanEndReason::StaleAuthority:view.status="Host state stale";break;
       default:view.status="Session ended";break;
     }
     view.clocksRunning=view.thinking=false;
   }
   view.footer=commandFooter();
   if(displayTime<toastUntil&&!toast.empty()){view.notice=toast;view.noticeError=toast.find("failed")!=std::string::npos||toast.find("not saved")!=std::string::npos||toast.find("damaged")!=std::string::npos;}
   chessui::draw(view,audit);
 }
 };

struct SessionHooks {
 App* app=nullptr;
 tyrell::game::Runtime* runtime=nullptr;
 void pause(){
   // Capture terminal intent before any ownership syscall. Clock settlement
   // uses this observation even if releasing native input takes time.
   if(app){app->lanForegroundAllowed=false;app->latchLanSession(chess::LanEndReason::ShellPause,app->monotonicClock());}
   if(runtime)runtime->suspend();
   if(app){app->finishLanSeal();app->cancelAI();app->suspendSession();}
 }
 bool resume(){
   // Resolve clock expiry before reopening input or accepting a move.
   if(app)app->resumeSession(runtime?runtime->time():0);
   if(runtime){std::string error;if(!runtime->resume(&error)){
     std::cerr<<"Tyrell Chess resume: "<<error<<'\n';
     if(app)app->suspendSession();
     return false;
   }runtime->request_redraw();}
   if(app)app->lanForegroundAllowed=true;
   return true;
 }
 void redraw(){if(runtime)runtime->request_redraw();}
 void end(chess::LanEndReason reason){
   if(app){app->lanForegroundAllowed=false;app->latchLanSession(reason,app->monotonicClock());}
   if(runtime)runtime->abandon_foreground();
   if(app){app->finishLanSeal();app->cancelAI();app->suspendSession();}
 }
 void abandon(){end(chess::LanEndReason::ShellLost);}
 void terminate(){end(chess::LanEndReason::LinkedTermination);}
};

#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
void waitShell(tyrell::app::ShellLink& link,int maximumMs=50){
 pollfd descriptor{link.fd(),short(POLLIN|(link.pending_output()?POLLOUT:0)),0};
 const auto result=::poll(&descriptor,1,maximumMs);
 if(result<0&&errno!=EINTR)throw std::runtime_error("Shell link poll failed");
}
// No App, AI worker, raylib context or device exists during startup handoff.
int awaitShell(tyrell::app::ShellLink& link,chess::OfflineLifecycle& lifecycle,
               const std::function<void()>& wait={},SessionHooks* appHooks=nullptr){
 SessionHooks emptyHooks;auto& hooks=appHooks?*appHooks:emptyHooks;
 const auto deadline=std::chrono::steady_clock::now()+std::chrono::milliseconds(100);
 for(;;){
   const bool drained=chess::pump_lifecycle(link,lifecycle,hooks);
   if(lifecycle.exiting())return 0;
   if(lifecycle.hello_seen()&&lifecycle.foreground()&&drained)return 1;
   if(!lifecycle.hello_seen()&&std::chrono::steady_clock::now()>=deadline){
     std::cerr<<"Tyrell Chess: shell hello timed out; devices remain untouched\n";return -1;
   }
   if(!drained)continue;
   if(wait)wait();else waitShell(link);
 }
}
#endif

struct SessionResult {int frames=0;bool saveAllowed=true;};
struct LinkedExitGuard {
 App& app;tyrell::game::Runtime& runtime;bool linked;
 std::function<bool()> previousAdmission;
 ~LinkedExitGuard(){
   app.lifecycleAdmission=std::move(previousAdmission);
   if(!linked)return;
   app.lanForegroundAllowed=false;
   uint64_t observedAt=app.lastLanServiceAt;
   try{observedAt=app.monotonicClock();}catch(...){}
   app.latchLanSession(chess::LanEndReason::LinkedTermination,observedAt);
   runtime.abandon_foreground();
   // Exceptions and signals must still close descriptors without replaying
   // saved pixels, persistence or a wait for the AI worker/remote peer.
   try{app.finishLanSeal();}catch(...){}
   app.completeLanTeardown();
 }
};
SessionResult runSession(App& app,const Args& args,tyrell::game::Runtime& runtime,
                        tyrell::app::ShellLink* link=nullptr,
                        chess::OfflineLifecycle* initialLifecycle=nullptr,
                        const std::function<void()>& wait={},
                        const std::function<void(int)>& afterFrame={},
                        const std::function<void()>& beforeCommit={}){
 LinkedExitGuard exitOwnership{app,runtime,link!=nullptr,std::move(app.lifecycleAdmission)};
 SessionResult result;
 chess::OfflineLifecycle localLifecycle;
 auto& lifecycle=initialLifecycle?*initialLifecycle:localLifecycle;
 SessionHooks hooks{&app,&runtime};
 if(link&&app.lanTerminal.begun())app.lanLifecycleAttached=true;
 const auto observeClosed=[&]{
   if(!runtime.closed())return false;
   // A nested admission check can observe termination while its App caller
   // still has trailing completion/persistence work. Seal and release now so
   // that work sees the terminal boundary, rather than waiting for loop exit.
   if(link){hooks.terminate();app.completeLanTeardown();}
   return true;
 };
 const auto foreground=[&]{
   if(observeClosed())return false;
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
   if(link){
     // Each receive batch is bounded. A full batch is followed immediately by
     // another drain, before taking a LAN message vector or returning to work.
     // This preserves queued packets when unknown shell traffic has no pause.
     for(;;){
       const bool drained=chess::pump_lifecycle(*link,lifecycle,hooks);
       // pump_lifecycle has queued/serviced the local release acknowledgment.
       // Cleanup discards sockets/buffers; it does not flush or await a peer.
       app.completeLanTeardown();
       if(lifecycle.exiting()||observeClosed())return false;
       if(drained)return lifecycle.foreground()&&runtime.foreground();
     }
   }
#endif
   return runtime.foreground();
 };
 if(link)app.lifecycleAdmission=foreground;
 auto scripted=split(args.script);size_t scriptIndex=0;
 while(!runtime.closed()&&!app.quit){
#if TYRELL_ENABLE_SHELL_LINK && defined(__linux__)
   if(link){
     if(!foreground()){
       if(lifecycle.exiting()||runtime.closed()){result.saveAllowed=false;break;}
       if(wait)wait();else waitShell(*link);
       continue;
     }
   }
#else
   (void)link;(void)lifecycle;(void)hooks;(void)wait;
#endif
   // A signal can arrive during polling or application work. Observe it before
   // applying another command or beginning a new native frame.
   runtime.poll();
   if(!foreground())continue;
   app.update(runtime.time());
   if(!foreground())continue;
   if(scriptIndex<scripted.size())app.key(scriptKey(scripted[scriptIndex++]));
   if(!foreground())continue;
   app.input(runtime,runtime.time());
   while(runtime.character()!=0){} // Numeric IP input uses the ordered key stream.
   if(!foreground())continue;
   if(app.quit&&app.lanTerminal.sealed())break;
   const auto frameGeneration=app.generation;
   runtime.begin_frame();app.draw(args.uiTime);
   if(beforeCommit)beforeCommit();
   // A newly observed pause cancels the open frame through Runtime::suspend.
   // Pause+resume can end in foreground in this same drain; its canceled frame
   // still belongs to the prior ownership interval and must never be committed.
   if(!foreground()||frameGeneration!=app.generation)continue;
   runtime.end_frame();++result.frames;
   if(afterFrame)afterFrame(result.frames);
   if(args.frames&&result.frames>=args.frames)break;
 }
 app.cancelAI();
 if(app.lifecyclePaused||runtime.suspended()||!runtime.foreground())result.saveAllowed=false;
 if(link&&runtime.closed())result.saveAllowed=false;
 if(link&&app.lanTerminal.begun())result.saveAllowed=false;
 return result;
}
}
int main(int argc,char** argv){
 try{
#ifdef __unix__
   umask(0077);
#endif
   auto args=arguments(argc,argv);
   tyrell::app::ShellLink* activeLink=nullptr;
   chess::OfflineLifecycle lifecycle;
#if TYRELL_ENABLE_SHELL_LINK && TYRELL_CHESS_NATIVE && defined(__linux__)
   auto link=!args.headless?tyrell::app::ShellLink::from_environment():tyrell::app::ShellLink{};
   if(link.connected()||link.closed()){
     const auto ready=awaitShell(link,lifecycle);
     if(ready<=0)return ready<0?1:0;
     activeLink=&link;
   }
#endif
   App app(args.state,args.uiTime,activeLink!=nullptr);
   if(activeLink&&!app.linkedOfflineAllowed()){
     std::cerr<<"Tyrell Chess: LAN games are unavailable in this shell session\n";return 2;
   }
   if(args.quick||args.demo||!args.moves.empty()) {app.setup.mode=Mode::Local;app.startGame();if(args.demo&&args.moves.empty())args.moves="e2e4,e7e5,g1f3,b8c6,f1c4,g8f6";for(auto& m:split(args.moves))if(!app.game.moveUci(m,app.clockNow()))throw std::runtime_error("Illegal --moves entry: "+m);app.persist();}
#if TYRELL_ENABLE_SHELL_LINK && TYRELL_CHESS_NATIVE && defined(__linux__)
   if(activeLink){
     // App loading may have performed filesystem work. Drain handoff messages
     // again before any device acquisition; a queued pause waits device-free.
     SessionHooks appHooks{&app,nullptr};
     const auto ready=awaitShell(*activeLink,lifecycle,{},&appHooks);
     if(ready<=0)return ready<0?1:0;
   }
#endif
   SessionResult result;
   {
     tyrell::game::RuntimeOptions options;options.title="Tyrell Chess";
     options.restore_framebuffer_on_close=activeLink==nullptr;
#if TYRELL_ENABLE_SHELL_LINK && TYRELL_CHESS_NATIVE && defined(__linux__)
     if(activeLink){
       if(const auto* device=std::getenv("LV_LINUX_FBDEV_DEVICE");device&&*device)options.framebuffer=device;
       if(const auto* device=std::getenv("LV_LINUX_KEYBOARD_DEVICE");device&&*device)options.input=device;
     }
#endif
     if(args.headless){options.backend=tyrell::game::Backend::Headless;options.fps=0;}
     tyrell::game::Runtime runtime(options);
     result=runSession(app,args,runtime,activeLink,&lifecycle);
     if(result.saveAllowed&&!activeLink&&!args.screenshot.empty())runtime.screenshot(args.screenshot);
     // Runtime closes first. Hidden/lost ownership cleanup cannot restore the
     // prelaunch screen; neither save retries nor AI joining can retain devices.
   }
   // Hidden EOF/termination preserves the last successful atomic save. It does
   // not promise persistence of RAM-only moves or perform unbounded hidden I/O.
   if(result.saveAllowed)app.persist();
   if(result.saveAllowed&&!args.report.empty()){std::ofstream out(args.report);if(!out)throw std::runtime_error("Cannot write report");out<<"{\"version\":\"0.1.6\",\"frames\":"<<result.frames<<",\"plies\":"<<app.game.history.size()<<",\"fen\":\""<<chess::fen(app.game.board)<<"\",\"screen\":"<<int(app.screen)<<",\"result\":\""<<app.game.result<<"\"}\n";}
   return 0;
 }catch(const std::exception& e){std::cerr<<"Tyrell Chess: "<<e.what()<<'\n';return 1;}
}
