#pragma once
#include "chess_board.h"
#include "chess_ai.h"
#include "chess_clock.h"
#include "puzzle_data.h"
#include <filesystem>
#include <limits>
#include <string>
#include <vector>

namespace chess {
enum class Mode { Local=0, Computer=1, Puzzle=2, Host=3, Join=4 };
struct Options {
    Mode mode=Mode::Computer;
    ChessVariant variant=ChessVariant::Standard;
    TimeControl time=TimeControl::None;
    AIDifficulty difficulty=AIDifficulty::Medium;
    PieceColor human=PieceColor::White;
    uint16_t position=518;
};
// Coordinate token: standard UCI endpoints/promotion plus c for castling.
// The explicit flag keeps Chess960 king steps distinct from castling.
std::string uci(const Move& move);
std::string notation(ChessBoard& board,const Move& move);
std::string fen(const ChessBoard& board);
std::string clockText(uint32_t ms,bool enabled);
std::filesystem::path stateDirectory();
class Game {
public:
    Options options;
    ChessBoard board,initial;
    ChessClock clock;
    std::vector<MoveRecord> history;
    std::vector<std::string> san;
    std::vector<uint32_t> hashes;
    std::string result="*",reason;
    int puzzleIndex=-1,puzzleStep=0;
    Move solution[MAX_PUZZLE_SOLUTION_MOVES]{};
    uint8_t solutionLength=0,rating=0;
    PuzzleType puzzleType=PuzzleType::MateIn1;
    void start(const Options& selected,uint32_t now);
    bool puzzle(int index,uint32_t now);
    bool move(const Move& candidate,uint32_t now);
    bool moveUci(const std::string& text,uint32_t now);
    bool undo(uint32_t now);
    void update(uint32_t now);
    void finish(const std::string& score,const std::string& why,uint32_t now);
    void agreeDraw(uint32_t now);
    void resign(PieceColor side,uint32_t now);
    bool over() const { return result!="*"; }
    bool humanTurn() const;
    std::vector<std::string> lines(size_t plyCount=std::numeric_limits<size_t>::max()) const;
    ChessBoard atPly(size_t ply) const;
    bool save(const std::filesystem::path& file,uint32_t now,std::string& error) const;
    bool load(const std::filesystem::path& file,uint32_t now,std::string& error);
    bool exportPgn(const std::filesystem::path& file,std::string& error) const;
private:
    void detect(uint32_t now);
};
}
