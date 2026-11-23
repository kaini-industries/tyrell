#include "game.hpp"
#include "chess_rules.h"
#include "chess960.h"
#include "chess_zobrist.h"
#include <algorithm>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace chess {
namespace {
uint64_t checksum(const std::string& s) { uint64_t h=14695981039346656037ull; for(unsigned char c:s) {h^=c;h*=1099511628211ull;} return h; }
bool atomicWrite(const std::filesystem::path& file,const std::string& data,std::string& error) {
    try {
        if(!file.parent_path().empty())std::filesystem::create_directories(file.parent_path());
        const auto temp=file.string()+".tmp";
        {std::ofstream out(temp,std::ios::binary|std::ios::trunc);out.exceptions(std::ios::badbit|std::ios::failbit);out<<data;out.flush();}
        std::filesystem::rename(temp,file);return true;
    } catch(const std::exception& e) {error=e.what();return false;}
}
}
std::filesystem::path stateDirectory() {
    if(const char* x=std::getenv("XDG_STATE_HOME");x && *x && std::filesystem::path(x).is_absolute()) return std::filesystem::path(x)/"tyrell-chess";
    if(const char* h=std::getenv("HOME");h && *h) return std::filesystem::path(h)/".local/state/tyrell-chess";
    throw std::runtime_error("HOME or an absolute XDG_STATE_HOME is required for saved games");
}
std::string uci(const Move& m) {
    std::string s={m.from.file(),m.from.rank(),m.to.file(),m.to.rank()};
    if(m.promotion!=PieceType::None) s+=char(Piece(m.promotion,PieceColor::Black).toChar());
    // Chess960 can have both a normal king step and castling with the same
    // endpoints. Retain the castle flag in saves and network coordinates.
    if(m.isCastle)s+='c';
    return s;
}
std::string notation(ChessBoard& board,const Move& m) {
    auto p=board.at(m.from.col,m.from.row);bool capture=!board.at(m.to.col,m.to.row).empty()||m.isEnPassant;
    std::string s;
    if(m.isCastle) s=m.to.col==6?"O-O":"O-O-O";
    else {
        if(p.type!=PieceType::Pawn) {
            s+=p.typeChar(); MoveList legal;ChessRules::generateLegal(board,legal);
            bool ambiguous=false,file=false,rank=false;
            for(int i=0;i<legal.count;++i) {auto o=legal.moves[i];if(o.to==m.to && o.from!=m.from && board.at(o.from.col,o.from.row).type==p.type) {ambiguous=true;file|=o.from.col==m.from.col;rank|=o.from.row==m.from.row;}}
            if(ambiguous) {if(!file)s+=m.from.file();else if(!rank)s+=m.from.rank();else {s+=m.from.file();s+=m.from.rank();}}
        } else if(capture) s+=m.from.file();
        if(capture)s+='x';s+=m.to.file();s+=m.to.rank();
        if(m.promotion!=PieceType::None) {s+='=';s+=Piece(m.promotion,PieceColor::White).typeChar();}
    }
    auto rec=board.makeMove(m);
    if(ChessRules::isInCheck(board,board.sideToMove())) s+=ChessRules::isCheckmate(board)?'#':'+';
    board.unmakeMove(rec);return s;
}
std::string fen(const ChessBoard& b) {
    std::ostringstream out;
    for(int r=7;r>=0;--r) {int empty=0;for(int c=0;c<8;++c){auto p=b.at(c,r);if(p.empty())++empty;else{if(empty){out<<empty;empty=0;}out<<p.toChar();}}if(empty)out<<empty;if(r)out<<'/';}
    out<<(b.sideToMove()==PieceColor::White?" w ":" b ");
    std::string rights;
    for(auto color:{PieceColor::White,PieceColor::Black}) {
        char shift=color==PieceColor::White?'A':'a';
        if(b.canCastleKS(color))rights+=b.variant()==ChessVariant::Chess960?char(shift+b.initRookKS()):(color==PieceColor::White?'K':'k');
        if(b.canCastleQS(color))rights+=b.variant()==ChessVariant::Chess960?char(shift+b.initRookQS()):(color==PieceColor::White?'Q':'q');
    }
    out<<(rights.empty()?"-":rights)<<' ';
    if(isNoSquare(b.enPassantTarget()))out<<'-';else out<<b.enPassantTarget().file()<<b.enPassantTarget().rank();
    out<<' '<<int(b.halfmoveClock())<<' '<<b.fullmoveNumber();return out.str();
}
std::string clockText(uint32_t ms,bool enabled) {if(!enabled)return "--:--"; auto seconds=(ms+999)/1000;std::ostringstream o;o<<seconds/60<<':'<<std::setfill('0')<<std::setw(2)<<seconds%60;return o.str();}
void Game::start(const Options& selected,uint32_t now) {
    options=selected;
    if(options.variant==ChessVariant::Standard)options.position=518;
    else if(options.position>=960)options.position=518;
    board=ChessBoard();board.setVariant(options.variant);board.setPositionIndex(options.position);board.reset();
    initial=board;history.clear();san.clear();hashes={ChessZobrist::repetitionHash(board)};result="*";reason.clear();puzzleIndex=-1;puzzleStep=0;
    solutionLength=0;rating=0;
    clock.configure(options.time);clock.start(now);
}
bool Game::puzzle(int index,uint32_t now) {
    ChessBoard loaded;uint8_t n=0,r=0;PuzzleType t;Move sol[MAX_PUZZLE_SOLUTION_MOVES];
    if(index<0 || index>=puzzleCount() || !loadPuzzleIntoBoard(index,loaded,sol,n,t,r)) return false;
    Options o;o.mode=Mode::Puzzle;start(o,now);board=initial=loaded;options.human=board.sideToMove();puzzleIndex=index;solutionLength=n;rating=r;puzzleType=t;std::copy(sol,sol+n,solution);hashes={ChessZobrist::repetitionHash(board)};return true;
}
bool Game::humanTurn() const {return !over() && (options.mode!=Mode::Computer || board.sideToMove()==options.human);}
void Game::finish(const std::string& score,const std::string& why,uint32_t now) {result=score;reason=why;clock.pause(now);}
void Game::resign(PieceColor side,uint32_t now) {
    update(now);if(over())return;
    if(side!=PieceColor::White&&side!=PieceColor::Black)return;
    const bool draw=ChessRules::hasInsufficientMatingMaterial(board,opponent(side));
    finish(draw?"1/2-1/2":side==PieceColor::White?"0-1":"1-0",
           draw?"Resigned / no mating material":"Resigned",now);
}
void Game::detect(uint32_t now) {
    if(ChessRules::isCheckmate(board)) finish(board.sideToMove()==PieceColor::White?"0-1":"1-0","Checkmate",now);
    else if(ChessRules::isStalemate(board))finish("1/2-1/2","Stalemate",now);
    else if(ChessRules::isDraw50Move(board))finish("1/2-1/2","50-move rule",now);
    else if(ChessRules::isInsufficientMaterial(board))finish("1/2-1/2","Insufficient material",now);
    else if(std::count(hashes.begin(),hashes.end(),hashes.back())>=3)finish("1/2-1/2","Threefold repetition",now);
}
void Game::update(uint32_t now) {
    if(over())return;clock.update(now);
    if(clock.hasFlaggedSide()) {
        auto side=clock.flaggedSide();bool draw=ChessRules::hasInsufficientMatingMaterial(board,opponent(side));
        finish(draw?"1/2-1/2":side==PieceColor::White?"0-1":"1-0",draw?"Time / no mating material":"Time expired",now);
    }
}
bool Game::move(const Move& candidate,uint32_t now) {
    update(now);if(over() || history.size()>=10000)return false;
    MoveList legal;ChessRules::generateLegal(board,legal);
    if(!legal.contains(candidate))return false;
    if(clock.enabled() && clock.commitMove(board.sideToMove(),now)!=ChessClockMoveResult::Applied)return false;
    san.push_back(notation(board,candidate));history.push_back(board.makeMove(candidate));hashes.push_back(ChessZobrist::repetitionHash(board));
    if(options.mode==Mode::Puzzle)++puzzleStep;else detect(now);return true;
}
bool Game::moveUci(const std::string& text,uint32_t now) {
    MoveList legal;ChessRules::generateLegal(board,legal);
    for(int i=0;i<legal.count;++i) {
        const Move& candidate=legal.moves[i];const auto token=uci(candidate);
        // Standard e1g1/e1c1 coordinates remain accepted for CLI compatibility;
        // in Chess960 plain king endpoints always mean the ordinary king move.
        const bool standardCastle=candidate.isCastle&&board.variant()==ChessVariant::Standard&&text==token.substr(0,4);
        if(token==text||standardCastle)return move(candidate,now);
    }
    return false;
}
bool Game::undo(uint32_t now) {
    if(history.empty() || clock.enabled() || options.mode==Mode::Host || options.mode==Mode::Join)return false;
    int n=options.mode==Mode::Computer && board.sideToMove()==options.human && history.size()>1?2:1;
    while(n--){board.unmakeMove(history.back());history.pop_back();san.pop_back();hashes.pop_back();if(puzzleStep>0)--puzzleStep;}
    result="*";reason.clear();(void)now;return true;
}
std::vector<std::string> Game::lines(size_t plyCount) const {
    std::vector<std::string> out;
    const size_t count=std::min(plyCount,san.size());size_t i=0;
    unsigned number=initial.fullmoveNumber();
    if(count&&initial.sideToMove()==PieceColor::Black){out.push_back(std::to_string(number)+"... "+san[0]);++i;++number;}
    for(;i<count;i+=2,++number)out.push_back(std::to_string(number)+". "+san[i]+(i+1<count?"  "+san[i+1]:""));
    return out;
}
ChessBoard Game::atPly(size_t ply) const {auto b=initial;for(size_t i=0;i<std::min(ply,history.size());++i)b.makeMove(history[i].move);return b;}
bool Game::save(const std::filesystem::path& file,uint32_t now,std::string& error) const {
    if(options.mode==Mode::Puzzle || options.mode==Mode::Host || options.mode==Mode::Join)return true;
    auto c=clock.snapshot(now);std::ostringstream body;
    body<<"TYRELL_CHESS_1\n"<<int(options.mode)<<' '<<int(options.variant)<<' '<<int(options.time)<<' '<<int(options.difficulty)<<' '<<int(options.human)<<' '<<options.position<<'\n';
    body<<c.whiteRemainingMs<<' '<<c.blackRemainingMs<<' '<<int(c.activeSide)<<' '<<c.started<<' '<<c.paused<<' '<<c.hasFlaggedSide<<' '<<int(c.flaggedSide)<<'\n';
    std::string savedResult=result,savedReason=reason;
    if(!over() && c.hasFlaggedSide) {
        const bool draw=ChessRules::hasInsufficientMatingMaterial(board,opponent(c.flaggedSide));
        savedResult=draw?"1/2-1/2":c.flaggedSide==PieceColor::White?"0-1":"1-0";
        savedReason=draw?"Time / no mating material":"Time expired";
    }
    body<<savedResult<<'\n'<<savedReason<<'\n'<<history.size()<<'\n';for(const auto& h:history)body<<uci(h.move)<<'\n';
    auto data=body.str();return atomicWrite(file,data+std::to_string(checksum(data))+"\n",error);
}
bool Game::load(const std::filesystem::path& file,uint32_t now,std::string& error) {
    try {
        if(std::filesystem::file_size(file)>100000)throw std::runtime_error("Save is too large");
        std::ifstream in(file);std::string data((std::istreambuf_iterator<char>(in)),{});auto end=data.rfind('\n',data.size()>1?data.size()-2:0);
        if(end==std::string::npos)throw std::runtime_error("Incomplete save");auto body=data.substr(0,end+1);
        if(std::to_string(checksum(body))+"\n"!=data.substr(end+1))throw std::runtime_error("Saved game checksum failed");
        std::istringstream s(body);std::string magic;std::getline(s,magic);if(magic!="TYRELL_CHESS_1")throw std::runtime_error("Unsupported saved game");
        int mode,variant,tc,diff,human,pos; s>>mode>>variant>>tc>>diff>>human>>pos;
        if(!s || mode<0||mode>1||variant<0||variant>1||tc<0||tc>4||diff<1||diff>3||human<0||human>1||pos<0||pos>=960||(variant==0&&pos!=518))throw std::runtime_error("Invalid save options");
        ChessClockSnapshot c;int active,flag; s>>c.whiteRemainingMs>>c.blackRemainingMs>>active>>c.started>>c.paused>>c.hasFlaggedSide>>flag;
        if(!s || active<0||active>1||flag<0||flag>1)throw std::runtime_error("Invalid saved clock");
        c.timeControl=TimeControl(tc);c.activeSide=PieceColor(active);c.flaggedSide=PieceColor(flag);
        s.ignore(1000,'\n');std::string resultIn,reasonIn;std::getline(s,resultIn);std::getline(s,reasonIn);size_t count;s>>count;
        if(!s || count>10000 || (resultIn!="*"&&resultIn!="1-0"&&resultIn!="0-1"&&resultIn!="1/2-1/2"))throw std::runtime_error("Invalid saved result/history");
        Game loaded;Options o;o.mode=Mode(mode);o.variant=ChessVariant(variant);o.difficulty=AIDifficulty(diff);o.human=PieceColor(human);o.position=pos;loaded.start(o,now);
        for(size_t i=0;i<count;++i){std::string move;s>>move;if(!s||!loaded.moveUci(move,now))throw std::runtime_error("Illegal move in saved game");}
        s>>std::ws;if(!s.eof())throw std::runtime_error("Unexpected saved data");
        loaded.options.time=TimeControl(tc);loaded.clock.configure(loaded.options.time);
        if(!loaded.clock.loadSnapshot(c,now) || (loaded.clock.enabled() && (!c.started || c.activeSide!=loaded.board.sideToMove())))throw std::runtime_error("Saved clock disagrees with board");
        if(loaded.over()) {
            if(resultIn!=loaded.result || reasonIn!=loaded.reason)throw std::runtime_error("Saved result disagrees with board");
        } else if(loaded.clock.hasFlaggedSide()) {
            loaded.update(now);
            if(resultIn!=loaded.result || reasonIn!=loaded.reason)throw std::runtime_error("Saved result disagrees with clock");
        } else if(resultIn=="*") {
            if(!reasonIn.empty())throw std::runtime_error("Unfinished save has a result reason");
        } else if(reasonIn=="Resigned") {
            if((resultIn!="1-0"&&resultIn!="0-1")||ChessRules::hasInsufficientMatingMaterial(
                    loaded.board,resultIn=="1-0"?PieceColor::White:PieceColor::Black))
                throw std::runtime_error("Saved resignation winner cannot mate");
        } else if(reasonIn=="Resigned / no mating material") {
            if(resultIn!="1/2-1/2"||(!ChessRules::hasInsufficientMatingMaterial(loaded.board,PieceColor::White)&&
                    !ChessRules::hasInsufficientMatingMaterial(loaded.board,PieceColor::Black)))
                throw std::runtime_error("Saved resignation draw disagrees with material");
        } else if(reasonIn!="Draw agreed"||resultIn!="1/2-1/2") {
            throw std::runtime_error("Invalid saved termination");
        }
        loaded.result=resultIn;loaded.reason=reasonIn;if(loaded.over())loaded.clock.pause(now);else loaded.clock.resume(now);
        *this=std::move(loaded);return true;
    } catch(const std::exception& e){error=e.what();return false;}
}
bool Game::exportPgn(const std::filesystem::path& file,std::string& error) const {
    std::ostringstream out;out<<"[Event \"Cardputer Zero Chess\"]\n[Site \"Handheld\"]\n[White \""<<(options.mode==Mode::Computer&&options.human==PieceColor::Black?"Computer":"Player")<<"\"]\n[Black \""<<(options.mode==Mode::Computer&&options.human==PieceColor::White?"Computer":"Player")<<"\"]\n[Result \""<<result<<"\"]\n";
    if(options.variant==ChessVariant::Chess960)out<<"[Variant \"Chess960\"]\n";
    if(options.variant==ChessVariant::Chess960||fen(initial)!=fen(ChessBoard{}))out<<"[SetUp \"1\"]\n[FEN \""<<fen(initial)<<"\"]\n";
    out<<'\n';for(const auto& line:lines())out<<line<<' ';out<<result<<'\n';return atomicWrite(file,out.str(),error);
}
}
