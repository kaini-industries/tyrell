#include "game.hpp"
#include "chess_rules.h"
#include <chrono>
#include <cstdio>
#include <fstream>
#include <set>
#include <sstream>
#include <stdexcept>

#define CHECK(expression) do { if (!(expression)) throw std::runtime_error( \
    std::string(__FILE__) + ":" + std::to_string(__LINE__) + ": " #expression); } while (false)

namespace {
std::string read(const std::filesystem::path& file) {
    std::ifstream in(file);return {std::istreambuf_iterator<char>(in), {}};
}
void write(const std::filesystem::path& file,const std::string& data) {std::ofstream(file)<<data;}
void rewriteChecksum(const std::filesystem::path& file,const std::string& body) {
    uint64_t hash=14695981039346656037ull;
    for(unsigned char c:body){hash^=c;hash*=1099511628211ull;}
    write(file,body+std::to_string(hash)+"\n");
}
std::string body(const std::filesystem::path& file) {
    auto data=read(file);return data.substr(0,data.rfind('\n',data.size()-2)+1);
}
void play(chess::Game& game,std::initializer_list<const char*> moves) {
    uint32_t now=1000;
    for(const auto* move:moves) {CHECK(game.moveUci(move,now));now+=1000;}
}
chess::Options local() {chess::Options o;o.mode=chess::Mode::Local;return o;}

void setupAndHistory() {
    chess::Game game;
    auto o=local();o.variant=ChessVariant::Chess960;o.position=0;
    game.start(o,0);CHECK(game.board.variant()==ChessVariant::Chess960);
    CHECK(game.board.at(4,0).type!=PieceType::King);
    game.start(local(),0);
    CHECK(game.board.variant()==ChessVariant::Standard);
    CHECK(game.board.positionIndex()==518);
    CHECK(chess::fen(game.board)=="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
    CHECK(!game.moveUci("e2e5",100));CHECK(game.history.empty());
    play(game,{"e2e4","e7e5","g1f3","b8c6","f1b5"});
    CHECK(game.san==std::vector<std::string>({"e4","e5","Nf3","Nc6","Bb5"}));
    CHECK(game.lines()==std::vector<std::string>({"1. e4  e5","2. Nf3  Nc6","3. Bb5"}));
    CHECK(game.lines(1)==std::vector<std::string>({"1. e4"}));
    CHECK(game.lines(3)==std::vector<std::string>({"1. e4  e5","2. Nf3"}));
    CHECK(game.lines(0).empty());
    CHECK(game.atPly(0).at(4,1).type==PieceType::Pawn);
    CHECK(game.atPly(1).at(4,3).type==PieceType::Pawn);
    CHECK(game.undo(9000));CHECK(game.san.back()=="Nc6");
    CHECK(game.hashes.size()==game.history.size()+1);

    o=local();o.mode=chess::Mode::Computer;o.human=PieceColor::White;
    game.start(o,0);play(game,{"e2e4","e7e5"});
    CHECK(game.undo(9000));CHECK(game.history.empty());
    o.time=TimeControl::Blitz3;game.start(o,0);CHECK(game.moveUci("e2e4",500));CHECK(!game.undo(1000));
}

void endings() {
    chess::Game game;game.start(local(),0);
    play(game,{"f2f3","e7e5","g2g4","d8h4"});
    CHECK(game.result=="0-1"&&game.reason=="Checkmate");CHECK(game.san.back()=="Qh4#");
    CHECK(!game.moveUci("a2a3",9000));CHECK(game.undo(9000));CHECK(!game.over());
    game.start(local(),0);
    play(game,{"g1f3","g8f6","f3g1","f6g8","g1f3","g8f6","f3g1","f6g8"});
    CHECK(game.result=="1/2-1/2"&&game.reason=="Threefold repetition");
    CHECK(game.undo(9000));CHECK(!game.over());
    auto o=local();o.time=TimeControl::Bullet1;game.start(o,10);game.update(60010);
    CHECK(game.result=="0-1"&&game.reason=="Time expired");
}

void materialEndings() {
    auto empty=[](chess::Game& game){
        for(int row=0;row<8;++row)for(int col=0;col<8;++col)game.board.set(col,row,{});
        game.board.setCastleRights(0);game.board.setEnPassantTarget(NO_SQUARE);
        game.board.set(6,5,{PieceType::King,PieceColor::White});
        game.board.set(7,7,{PieceType::King,PieceColor::Black});
    };
    chess::Game game;auto o=local();o.time=TimeControl::Bullet1;
    game.start(o,0);empty(game);
    game.board.set(4,6,{PieceType::Knight,PieceColor::White});
    game.board.set(3,5,{PieceType::Knight,PieceColor::White});
    game.board.setSideToMove(PieceColor::Black);
    CHECK(game.clock.commitMove(PieceColor::White,0)==ChessClockMoveResult::Applied);
    game.update(60000);
    CHECK(game.result=="1-0"&&game.reason=="Time expired");

    game.start(o,0);empty(game);
    game.board.set(1,1,{PieceType::Knight,PieceColor::White});
    game.board.setSideToMove(PieceColor::Black);
    CHECK(game.clock.commitMove(PieceColor::White,0)==ChessClockMoveResult::Applied);
    game.update(60000);
    CHECK(game.result=="1/2-1/2"&&game.reason=="Time / no mating material");

    // A resignation also draws when the nominal winner can never mate.
    game.start(local(),0);empty(game);
    game.board.set(1,1,{PieceType::Queen,PieceColor::Black});
    game.resign(PieceColor::Black,100);
    CHECK(game.result=="1/2-1/2"&&game.reason=="Resigned / no mating material");
    game.start(local(),0);empty(game);
    game.board.set(4,6,{PieceType::Knight,PieceColor::White});
    game.board.set(3,5,{PieceType::Knight,PieceColor::White});
    game.resign(PieceColor::Black,100);
    CHECK(game.result=="1-0"&&game.reason=="Resigned");
    game.start(local(),0);game.resign(PieceColor::White,100);
    CHECK(game.result=="0-1"&&game.reason=="Resigned");
    game.resign(PieceColor::Black,200);CHECK(game.result=="0-1");
}

void sanSpecialMoves() {
    chess::Game game;game.start(local(),0);
    play(game,{"e2e4","a7a6","e4e5","d7d5","e5d6"});
    CHECK(game.san.back()=="exd6");
    game.start(local(),0);
    play(game,{"e2e4","e7e5","g1f3","b8c6","f1c4","g8f6","d2d3","f8c5","e1g1"});
    CHECK(game.san.back()=="O-O");
    ChessBoard board;
    for(int row=0;row<8;++row)for(int col=0;col<8;++col)board.set(col,row,{});
    board.setCastleRights(0);board.set(0,7,{PieceType::King,PieceColor::White});
    board.set(7,7,{PieceType::King,PieceColor::Black});
    board.set(1,0,{PieceType::Knight,PieceColor::White});board.set(5,0,{PieceType::Knight,PieceColor::White});
    Move m;m.from={1,0};m.to={3,1};
    CHECK(chess::notation(board,m)=="Nbd2");
    board.set(5,0,{});board.set(1,2,{PieceType::Knight,PieceColor::White});
    CHECK(chess::notation(board,m)=="N1d2");
    board.set(5,0,{PieceType::Knight,PieceColor::White});
    CHECK(chess::notation(board,m)=="Nb1d2");
    board.set(1,0,{});board.set(1,2,{});board.set(5,0,{});
    board.set(0,7,{});board.set(7,0,{PieceType::King,PieceColor::White});
    board.set(0,6,{PieceType::Pawn,PieceColor::White});
    m.from={0,6};m.to={0,7};m.promotion=PieceType::Queen;
    const auto before=chess::fen(board);
    CHECK(chess::notation(board,m)=="a8=Q+");CHECK(chess::fen(board)==before);
}

void savedGames(const std::filesystem::path& directory) {
    const auto file=directory/"game.save";
    std::string error;
    chess::Game game;game.start(local(),0);play(game,{"e2e4"});
    CHECK(game.save(file,10000,error));
    chess::Game loaded;CHECK(loaded.load(file,20000,error));
    CHECK(chess::fen(loaded.board)==chess::fen(game.board));
    CHECK(loaded.board.sideToMove()==PieceColor::Black);CHECK(loaded.san==game.san);
    const auto before=chess::fen(loaded.board);const auto original=read(file);
    auto corrupt=original;corrupt[15]^=0x04;write(file,corrupt);
    CHECK(!loaded.load(file,20000,error));CHECK(chess::fen(loaded.board)==before);
    write(file,original.substr(0,original.size()/2));
    CHECK(!loaded.load(file,20000,error));CHECK(chess::fen(loaded.board)==before);
    write(file,original);auto contents=body(file);
    const auto moveAt=contents.find("e2e4");CHECK(moveAt!=std::string::npos);contents.replace(moveAt,4,"e2e5");
    rewriteChecksum(file,contents);
    CHECK(!loaded.load(file,20000,error));CHECK(chess::fen(loaded.board)==before);

    game.start(local(),0);play(game,{"f2f3","e7e5","g2g4","d8h4"});
    CHECK(game.save(file,10000,error));CHECK(loaded.load(file,20000,error));CHECK(loaded.reason=="Checkmate");
    contents=body(file);const std::string terminal="\n0-1\nCheckmate\n";const auto resultAt=contents.find(terminal);CHECK(resultAt!=std::string::npos);
    contents.replace(resultAt,terminal.size(),"\n*\n\n");rewriteChecksum(file,contents);
    CHECK(!loaded.load(file,20000,error));CHECK(loaded.reason=="Checkmate");

    // Valid checksum cannot disguise a false no-mating-material draw.
    game.start(local(),0);game.finish("1/2-1/2","Resigned / no mating material",0);
    CHECK(game.save(file,100,error));CHECK(!loaded.load(file,200,error));

    auto o=local();o.time=TimeControl::Blitz3;game.start(o,1000);
    CHECK(game.moveUci("e2e4",2000));CHECK(game.save(file,3000,error));
    CHECK(loaded.load(file,900000,error));
    CHECK(loaded.clock.remainingMsAt(PieceColor::White,900000)==181000);
    CHECK(loaded.clock.remainingMsAt(PieceColor::Black,900000)==179000);
    CHECK(loaded.clock.remainingMsAt(PieceColor::Black,901000)==178000);
    game.finish("1-0","Resigned",4000);
    CHECK(game.save(file,5000,error));CHECK(loaded.load(file,900000,error));
    CHECK(loaded.over()&&loaded.reason=="Resigned"&&!loaded.clock.isRunning());

    o=local();o.time=TimeControl::Bullet1;game.start(o,1000);
    // save() settles a flag even when no UI update occurred at this exact time.
    CHECK(game.save(file,61000,error));CHECK(loaded.load(file,900000,error));
    CHECK(loaded.result=="0-1"&&loaded.reason=="Time expired");

    o=local();o.variant=ChessVariant::Chess960;o.position=0;game.start(o,0);
    CHECK(game.save(file,100,error));CHECK(loaded.load(file,200,error));
    CHECK(loaded.options.position==0&&chess::fen(loaded.board)==chess::fen(game.board));
}

void drawAgreementAtFlag(const std::filesystem::path& directory) {
    const auto file=directory/"draw-at-flag.save";
    auto options=local();options.time=TimeControl::Bullet1;
    std::string error;
    // Agreement before the deadline draws; at/after it the existing timeout
    // wins, even when no intervening UI update has settled the running clock.
    for(const uint32_t timestamp:{60099u,60100u,60101u}) {
        chess::Game game;game.start(options,100);game.agreeDraw(timestamp);
        const bool expired=timestamp>=60100;
        CHECK(game.result==(expired?"0-1":"1/2-1/2"));
        CHECK(game.reason==(expired?"Time expired":"Draw agreed"));
        CHECK(!game.clock.isRunning());
        CHECK(game.save(file,timestamp+1000,error));
        chess::Game loaded;CHECK(loaded.load(file,900000,error));
        CHECK(loaded.result==game.result&&loaded.reason==game.reason);
        CHECK(!loaded.clock.isRunning());
        game.agreeDraw(timestamp+1000);
        CHECK(game.result==loaded.result&&game.reason==loaded.reason);
    }
    chess::Game game;game.start(options,100);game.resign(PieceColor::White,500);
    game.agreeDraw(60101);
    CHECK(game.result=="0-1"&&game.reason=="Resigned");
    game.start(local(),100);game.agreeDraw(60101);
    CHECK(game.result=="1/2-1/2"&&game.reason=="Draw agreed");
}

void pgnAndPuzzles(const std::filesystem::path& directory) {
    chess::Game game;game.start(local(),0);play(game,{"f2f3","e7e5","g2g4","d8h4"});
    const auto file=directory/"game.pgn";std::string error;
    CHECK(game.exportPgn(file,error));auto pgn=read(file);
    CHECK(pgn.find("[Result \"0-1\"]")!=std::string::npos);
    CHECK(pgn.find("1. f3  e5 2. g4  Qh4# 0-1")!=std::string::npos);
    auto o=local();o.variant=ChessVariant::Chess960;o.position=0;game.start(o,0);
    CHECK(game.exportPgn(file,error));pgn=read(file);
    CHECK(pgn.find("[Variant \"Chess960\"]")!=std::string::npos);
    CHECK(pgn.find("[FEN \""+chess::fen(game.initial)+"\"]")!=std::string::npos);
    CHECK(game.puzzle(0,0));CHECK(game.options.mode==chess::Mode::Puzzle);
    CHECK(game.puzzleIndex==0&&game.solutionLength>0&&game.humanTurn());
    CHECK(!game.puzzle(puzzleCount(),0));CHECK(game.puzzleIndex==0);
    // Every puzzle export must carry its nonstandard initial position.
    CHECK(game.exportPgn(file,error));pgn=read(file);
    CHECK(pgn.find("[SetUp \"1\"]")!=std::string::npos);
    CHECK(pgn.find("[FEN \""+chess::fen(game.initial)+"\"]")!=std::string::npos);
    game.initial.setSideToMove(PieceColor::Black);game.initial.setFullmoveNumber(17);
    game.san={"Kh7","Qd8","Kh6"};
    CHECK(game.lines()==std::vector<std::string>({"17... Kh7","18. Qd8  Kh6"}));
    CHECK(game.lines(2)==std::vector<std::string>({"17... Kh7","18. Qd8"}));
    CHECK(game.exportPgn(file,error));pgn=read(file);
    CHECK(pgn.find("17... Kh7 18. Qd8  Kh6 *")!=std::string::npos);
}
void chess960CastleCoordinates(const std::filesystem::path& directory) {
    auto options=local();options.variant=ChessVariant::Chess960;options.position=12;
    chess::Game game;game.start(options,0);
    // A legal game reaches Kf1/Rh1 with g1 clear; both Kg1 and O-O are legal.
    play(game,{"h2h3","a7a6","g1h2","a6a5"});
    MoveList moves;ChessRules::generateLegal(game.board,moves);Move castle;bool found=false;
    for(int i=0;i<moves.count;++i)if(moves.moves[i].isCastle&&moves.moves[i].to.col==6){castle=moves.moves[i];found=true;break;}
    CHECK(found&&chess::uci(castle)=="f1g1c");
    auto kingStep=game;CHECK(kingStep.moveUci("f1g1",100));
    CHECK(!kingStep.history.back().move.isCastle&&kingStep.board.at(7,0).type==PieceType::Rook);
    CHECK(game.moveUci("f1g1c",100));
    CHECK(game.history.back().move.isCastle&&game.board.at(5,0).type==PieceType::Rook);
    std::string error;const auto file=directory/"ambiguous-960.save";
    CHECK(game.save(file,200,error));chess::Game restored;CHECK(restored.load(file,300,error));
    CHECK(chess::fen(restored.board)==chess::fen(game.board));
    CHECK(restored.history.back().move.isCastle&&restored.san.back()=="O-O");

    // Every generated legal move must have a unique persisted token, including
    // stationary-king/rook and adjacent-king Chess960 castling cases.
    for(int position=0;position<960;++position){
        options.position=position;game.start(options,0);
        for(int row=0;row<8;++row)for(int col=0;col<8;++col){
            const auto piece=game.board.at(col,row);
            if(row==0&&(piece.type==PieceType::King||piece.type==PieceType::Rook))continue;
            if(row==7&&piece.type==PieceType::King)continue;
            game.board.set(col,row,{});
        }
        ChessRules::generateLegal(game.board,moves);std::set<std::string> tokens;
        for(int n=0;n<moves.count;++n)CHECK(tokens.insert(chess::uci(moves.moves[n])).second);
    }
}

}
int main() {
    const auto directory=std::filesystem::temp_directory_path()/
        ("tyrell-chess-test-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    try {
        std::filesystem::create_directories(directory);
        setupAndHistory();endings();materialEndings();sanSpecialMoves();savedGames(directory);drawAgreementAtFlag(directory);pgnAndPuzzles(directory);chess960CastleCoordinates(directory);
        std::filesystem::remove_all(directory);
        std::puts("Chess model passed: variant reset, SAN, undo, outcomes, transactional saves, clocks, PGN, puzzles.");
        return 0;
    } catch(const std::exception& error) {
        std::filesystem::remove_all(directory);std::fprintf(stderr,"%s\n",error.what());return 1;
    }
}
