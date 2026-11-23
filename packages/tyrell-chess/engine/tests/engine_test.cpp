#include "chess960.h"
#include "chess_ai.h"
#include "chess_clock.h"
#include "chess_storage_codec.h"
#include "chess_zobrist.h"
#include "puzzle_data.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

#define CHECK(expression) do { if (!(expression)) throw std::runtime_error( \
    std::string(__FILE__) + ":" + std::to_string(__LINE__) + ": " #expression); } while (false)

namespace {
ChessBoard position(const std::string& fen) {
    ChessBoard board;
    for (int row = 0; row < 8; ++row)
        for (int col = 0; col < 8; ++col) board.set(col, row, {});
    int row = 7, col = 0;
    std::size_t offset = 0;
    for (; offset < fen.size() && fen[offset] != ' '; ++offset) {
        const unsigned char ch = static_cast<unsigned char>(fen[offset]);
        if (ch == '/') { --row; col = 0; }
        else if (std::isdigit(ch)) col += ch - '0';
        else {
            const char* types = "PNBRQK";
            const auto at = std::strchr(types, std::toupper(ch));
            CHECK(at && col < 8 && row >= 0);
            board.set(col++, row, {static_cast<PieceType>(at - types + 1),
                std::islower(ch) ? PieceColor::Black : PieceColor::White});
        }
    }
    CHECK(offset + 1 < fen.size());
    board.setSideToMove(fen[offset + 1] == 'b' ? PieceColor::Black : PieceColor::White);
    board.setCastleRights(0);
    board.setEnPassantTarget(NO_SQUARE);
    return board;
}

std::string identity(const ChessBoard& board) {
    std::string result;
    for (int row = 0; row < 8; ++row)
        for (int col = 0; col < 8; ++col) result += board.at(col, row).toChar();
    result += std::to_string(static_cast<int>(board.sideToMove())) + ":";
    result += std::to_string(board.castleRights()) + ":";
    result += std::to_string(board.enPassantTarget().col) + ":";
    result += std::to_string(board.enPassantTarget().row) + ":";
    result += std::to_string(board.halfmoveClock()) + ":";
    result += std::to_string(board.fullmoveNumber()) + ":";
    result += std::to_string(board.positionIndex()) + ":";
    result += std::to_string(static_cast<int>(board.variant()));
    return result;
}

Move move(ChessBoard& board, const char* text) {
    MoveList legal;
    ChessRules::generateLegal(board, legal);
    for (uint16_t i = 0; i < legal.count; ++i) {
        const auto& candidate = legal.moves[i];
        if (candidate.from == Square(text[0] - 'a', text[1] - '1') &&
            candidate.to == Square(text[2] - 'a', text[3] - '1') &&
            (!text[4] || candidate.promotion == PieceType::Queen)) return candidate;
    }
    throw std::runtime_error(std::string("Illegal test move: ") + text);
}

uint64_t perft(ChessBoard& board, int depth) {
    if (!depth) return 1;
    MoveList legal;
    ChessRules::generateLegal(board, legal);
    uint64_t count = 0;
    for (uint16_t i = 0; i < legal.count; ++i) {
        auto record = board.makeMove(legal.moves[i]);
        count += perft(board, depth - 1);
        board.unmakeMove(record);
    }
    return count;
}

void treeAndReversal() {
    ChessBoard board;
    const auto before = identity(board);
    for (const auto [depth, expected] : {std::pair{1, 20ULL}, {2, 400ULL},
            {3, 8902ULL}, {4, 197281ULL}}) {
        CHECK(perft(board, depth) == expected);
        CHECK(identity(board) == before);
    }
    // Kiwipete exercises checks, captures and both castling directions.
    auto kiwi = position("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w");
    kiwi.setCastleRights(CastleRights::All);
    const auto kiwiBefore = identity(kiwi);
    CHECK(perft(kiwi, 1) == 48);
    CHECK(perft(kiwi, 2) == 2039);
    CHECK(perft(kiwi, 3) == 97862);
    CHECK(identity(kiwi) == kiwiBefore);
}

void specialMoves() {
    ChessBoard board;
    for (const auto* text : {"e2e4", "a7a6", "e4e5", "d7d5"}) board.makeMove(move(board, text));
    const auto before = identity(board);
    const auto ep = move(board, "e5d6");
    CHECK(ep.isEnPassant);
    const auto record = board.makeMove(ep);
    CHECK(board.at(3, 4).empty());
    CHECK(board.at(3, 5) == Piece(PieceType::Pawn, PieceColor::White));
    board.unmakeMove(record);
    CHECK(identity(board) == before);

    auto pinned = position("k3r3/8/8/3pP3/8/8/8/4K3 w");
    pinned.setEnPassantTarget({3, 5});
    MoveList legal;
    ChessRules::generateLegal(pinned, legal);
    for (uint16_t i = 0; i < legal.count; ++i) CHECK(!legal.moves[i].isEnPassant);
    auto withoutEp = pinned;
    withoutEp.setEnPassantTarget(NO_SQUARE);
    CHECK(ChessZobrist::repetitionHash(pinned) == ChessZobrist::repetitionHash(withoutEp));

    auto promotion = position("7k/P7/8/8/8/8/8/7K w");
    ChessRules::getLegalMovesFrom(promotion, 0, 6, legal);
    CHECK(legal.count == 4);
    std::set<PieceType> promotions;
    const auto promoBefore = identity(promotion);
    for (uint16_t i = 0; i < legal.count; ++i) {
        const auto promotedType = legal.moves[i].promotion;
        promotions.insert(promotedType);
        const auto undo = promotion.makeMove(legal.moves[i]);
        CHECK(promotion.at(0, 7).type == promotedType);
        promotion.unmakeMove(undo);
        CHECK(identity(promotion) == promoBefore);
    }
    CHECK(promotions == std::set<PieceType>({PieceType::Queen, PieceType::Rook,
        PieceType::Bishop, PieceType::Knight}));

    auto castle = position("r3k2r/8/8/8/8/8/8/R3K2R w");
    castle.setCastleRights(CastleRights::All);
    const auto castleBefore = identity(castle);
    const auto ks = move(castle, "e1g1");
    CHECK(ks.isCastle);
    const auto undo = castle.makeMove(ks);
    CHECK(castle.at(6, 0).type == PieceType::King);
    CHECK(castle.at(5, 0).type == PieceType::Rook);
    CHECK(!castle.canCastleKS(PieceColor::White));
    castle.unmakeMove(undo);
    CHECK(identity(castle) == castleBefore);
}

void variants() {
    std::set<std::string> ranks;
    for (unsigned index = 0; index < 960; ++index) {
        const auto generated = chess960Generate(index);
        CHECK(generated.rookQS < generated.kingCol && generated.kingCol < generated.rookKS);
        int bishopParity = 0, bishopCount = 0;
        std::string rank;
        for (int col = 0; col < 8; ++col) {
            rank += Piece(generated.backRank[col], PieceColor::White).typeChar();
            if (generated.backRank[col] == PieceType::Bishop) { bishopParity += col % 2; ++bishopCount; }
        }
        CHECK(bishopParity == 1 && bishopCount == 2);
        CHECK(ranks.insert(rank).second);
        ChessBoard board;
        board.setVariant(ChessVariant::Chess960);
        board.setPositionIndex(index);
        board.reset();
        const auto before = identity(board);
        MoveList legal;
        ChessRules::generateLegal(board, legal);
        CHECK(legal.count > 0 && !ChessRules::isInCheck(board, PieceColor::White));
        for (uint16_t i = 0; i < legal.count; ++i) {
            const auto undo = board.makeMove(legal.moves[i]);
            board.unmakeMove(undo);
            CHECK(identity(board) == before);
        }
    }
    CHECK(ranks.size() == 960);
    CHECK(chess960Generate(518).kingCol == 4);
    CHECK(chess960Generate(65535).kingCol == 4);
}

void endingsAndAI() {
    auto mate = position("7k/6Q1/5K2/8/8/8/8/8 b");
    CHECK(ChessRules::isCheckmate(mate));
    CHECK(!ChessRules::isStalemate(mate));
    auto stale = position("7k/5K2/6Q1/8/8/8/8/8 b");
    CHECK(ChessRules::isStalemate(stale));
    auto loneBishop = position("7k/8/8/8/8/8/1B6/K7 w");
    CHECK(ChessRules::isInsufficientMaterial(loneBishop));
    CHECK(ChessRules::hasInsufficientMatingMaterial(loneBishop, PieceColor::White));
    // Constructive counterexample: two knights can legally mate, so a
    // preceding position cannot be declared dead just because mate is unforced.
    auto twoKnights = position("7k/4N3/3N2K1/8/8/8/8/8 w");
    CHECK(!ChessRules::isInsufficientMaterial(twoKnights));
    CHECK(!ChessRules::hasInsufficientMatingMaterial(twoKnights, PieceColor::White));
    twoKnights.makeMove(move(twoKnights, "d6f7"));
    CHECK(ChessRules::isCheckmate(twoKnights));
    auto oneKnight = position("7k/8/6K1/8/8/8/1N6/8 w");
    CHECK(ChessRules::isInsufficientMaterial(oneKnight));
    CHECK(ChessRules::hasInsufficientMatingMaterial(oneKnight, PieceColor::White));
    ChessBoard board;
    board.setHalfmoveClock(100);
    CHECK(ChessRules::isDraw50Move(board));
    board.reset();
    std::vector<MoveRecord> history;
    for (const auto* text : {"g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6", "f3g1", "f6g8"})
        history.push_back(board.makeMove(move(board, text)));
    CHECK(ChessRules::isThreefoldRepetition(board, history.data(), history.size()));

    // No opening-book hit: exercise the actual bounded search and board reversal.
    auto search = position("7k/8/5KQ1/8/8/8/8/8 w");
    const auto before = identity(search);
    MoveList legal;
    ChessRules::generateLegal(search, legal);
    const auto start = std::chrono::steady_clock::now();
    const auto best = ChessAI::findBestMove(search, AIDifficulty::Hard, 80);
    CHECK(std::chrono::steady_clock::now() - start < std::chrono::seconds(2));
    CHECK(legal.contains(best));
    CHECK(identity(search) == before);
    search.makeMove(best);
    CHECK(ChessRules::isCheckmate(search));
}

void clocks() {
    ChessClock clock(TimeControl::Blitz3);
    CHECK(clock.start(1000));
    CHECK(clock.remainingMsAt(PieceColor::White, 2000) == 179000);
    CHECK(clock.commitMove(PieceColor::Black, 2000) == ChessClockMoveResult::WrongSide);
    CHECK(clock.commitMove(PieceColor::White, 2000) == ChessClockMoveResult::Applied);
    CHECK(clock.remainingMs(PieceColor::White) == 181000);
    CHECK(clock.activeSide() == PieceColor::Black);
    CHECK(clock.pause(3000));
    CHECK(clock.remainingMsAt(PieceColor::Black, 9000) == 179000);
    const auto snapshot = clock.snapshot(9000);
    ChessClock loaded;
    CHECK(loaded.loadSnapshot(snapshot, 10000));
    CHECK(loaded.isPaused());
    CHECK(loaded.resume(10000));
    CHECK(loaded.remainingMsAt(PieceColor::Black, 11000) == 178000);
    loaded.update(200000);
    CHECK(loaded.flagged(PieceColor::Black));
    ChessClock wrap(TimeControl::Bullet1);
    CHECK(wrap.start(UINT32_MAX - 9));
    CHECK(wrap.remainingMsAt(PieceColor::White, 10) == 59980);
}

void puzzles() {
    CHECK(puzzleCount() > 0);
    unsigned total = 0;
    for (const auto type : {PuzzleType::MateIn1, PuzzleType::MateIn2, PuzzleType::Tactic}) {
        const auto count = puzzleCountByType(type);
        total += count;
        CHECK(puzzleIndexByType(type, count) == 0xffff);
    }
    CHECK(total == puzzleCount());
    for (uint16_t index = 0; index < puzzleCount(); ++index) {
        ChessBoard board;
        Move solution[MAX_PUZZLE_SOLUTION_MOVES];
        uint8_t length = 0, rating = 0;
        PuzzleType type;
        CHECK(loadPuzzleIntoBoard(index, board, solution, length, type, rating));
        CHECK(length > 0 && length <= MAX_PUZZLE_SOLUTION_MOVES && length % 2 == 1 && rating > 0);
        CHECK(!ChessRules::isInCheck(board, opponent(board.sideToMove())));
        for (uint8_t step = 0; step < length; ++step) {
            MoveList legal;
            ChessRules::generateLegal(board, legal);
            unsigned matches = 0;
            Move resolved;
            for (uint16_t i = 0; i < legal.count; ++i) {
                const auto& candidate = legal.moves[i];
                if (candidate.from == solution[step].from && candidate.to == solution[step].to &&
                        candidate.promotion == solution[step].promotion) { ++matches; resolved = candidate; }
            }
            CHECK(matches == 1);
            board.makeMove(resolved);
        }
        if (type != PuzzleType::Tactic) CHECK(ChessRules::isCheckmate(board));
    }
}

void codecs() {
    ChessBoard board;
    std::array<MoveRecord, 4> history;
    unsigned count = 0;
    for (const auto* text : {"e2e4", "e7e5", "g1f3", "b8c6"})
        history[count++] = board.makeMove(move(board, text));
    std::array<uint8_t, ChessStorageCodec::MAX_ENCODED_BYTES> bytes;
    ChessStorageCodec::SaveMetadata metadata;
    metadata.participants.valid = true;
    metadata.participants.gameId = 42;
    std::strcpy(metadata.participants.whiteName, "White");
    std::strcpy(metadata.participants.blackName, "Black");
    std::size_t size = 0;
    CHECK(ChessStorageCodec::encode(board, history.data(), count, metadata, bytes.data(), bytes.size(), size));
    ChessBoard decoded;
    std::array<MoveRecord, 250> decodedHistory;
    uint8_t decodedCount = 0;
    ChessStorageCodec::SaveMetadata decodedMetadata;
    CHECK(ChessStorageCodec::decode(bytes.data(), size, decoded, decodedHistory.data(), decodedHistory.size(),
        decodedCount, decodedMetadata) == ChessStorageCodec::DecodeStatus::Ok);
    CHECK(identity(decoded) == identity(board) && decodedCount == count);
    const auto before = identity(decoded);
    bytes[size / 2] ^= 0x80;
    CHECK(ChessStorageCodec::decode(bytes.data(), size, decoded, decodedHistory.data(), decodedHistory.size(),
        decodedCount, decodedMetadata) == ChessStorageCodec::DecodeStatus::Corrupt);
    CHECK(identity(decoded) == before);
}
}

int main() {
    try {
        treeAndReversal(); specialMoves(); variants(); endingsAndAI(); clocks(); puzzles(); codecs();
        std::puts("Chess engine passed: perft, reversal, special moves, 960 positions, endings, AI, clock, puzzles, codec.");
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "%s\n", error.what());
        return 1;
    }
}
