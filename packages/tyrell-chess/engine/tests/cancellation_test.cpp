#include "chess_ai.h"
#include "chess_opening_book.h"

#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstring>
#include <fstream>
#include <future>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using Clock = std::chrono::steady_clock;
namespace {
void require(bool value, const std::string& message) {
    if (!value) throw std::runtime_error(message);
}
ChessBoard position(const std::string& fen) {
    ChessBoard board;
    for (unsigned row = 0; row < 8; ++row)
        for (unsigned col = 0; col < 8; ++col) board.set(col, row, {});
    unsigned row = 7, col = 0;
    std::size_t offset = 0;
    for (; offset < fen.size() && fen[offset] != ' '; ++offset) {
        const auto c = fen[offset];
        if (c == '/') { --row; col = 0; }
        else if (c >= '1' && c <= '8') col += static_cast<unsigned>(c - '0');
        else {
            const auto* types = "PNBRQK";
            const auto upper = c >= 'a' && c <= 'z' ? c - 'a' + 'A' : c;
            const auto* type = std::strchr(types, upper);
            require(type && col < 8 && row < 8, "Invalid cancellation position");
            board.set(col++, row, {static_cast<PieceType>(type - types + 1),
                      c >= 'a' && c <= 'z' ? PieceColor::Black : PieceColor::White});
        }
    }
    require(offset + 1 < fen.size(), "Missing side in cancellation position");
    board.setSideToMove(fen[offset + 1] == 'b' ? PieceColor::Black : PieceColor::White);
    board.setCastleRights(0); board.setEnPassantTarget(NO_SQUARE);
    // Skip opening-book shortcuts while exercising a full, genuinely active
    // search. Castling is disabled, so this retains the position's legal play.
    board.setVariant(ChessVariant::Chess960);
    return board;
}
std::string identity(const ChessBoard& board) {
    std::string value;
    for (unsigned row = 0; row < 8; ++row)
        for (unsigned col = 0; col < 8; ++col) value += board.at(col, row).toChar();
    for (const auto part : {unsigned(board.sideToMove()), unsigned(board.castleRights()),
         unsigned(board.enPassantTarget().col), unsigned(board.enPassantTarget().row),
         unsigned(board.halfmoveClock()), unsigned(board.fullmoveNumber()), unsigned(board.variant()),
         unsigned(board.positionIndex()), unsigned(board.initKingCol()), unsigned(board.initRookKS()), unsigned(board.initRookQS())})
        value += ':' + std::to_string(part);
    return value;
}
void canceled_before_work_and_book_recovery() {
    ChessBoard board;
    const auto before = identity(board);
    std::atomic_bool canceled{true};
    require(ChessAI::findBestMove(board, AIDifficulty::Hard, 0, &canceled) == Move{},
            "Already-canceled search did work or invented a result");
    Move book;
    require(!ChessOpeningBook::probe(board, book, &canceled), "Canceled cold book probe returned a choice");
    require(identity(board) == before, "Already-canceled work changed the board");
    canceled = false;
    require(ChessOpeningBook::probe(board, book, &canceled), "Book did not initialize after cancellation");
    MoveList legal; ChessRules::generateLegal(board, legal);
    require(legal.contains(book), "Recovered opening book returned an illegal move");
    // An old true token must not contaminate a later call with another token
    // or an ordinary source-compatible caller without a token.
    canceled = true;
    (void)ChessAI::findBestMove(board, AIDifficulty::Hard, 0, &canceled);
    const auto normal = ChessAI::findBestMove(board, AIDifficulty::Easy, 1);
    require(legal.contains(normal) && identity(board) == before, "Cancellation leaked into a later search");

    auto bounded = position("r3k2r/p1ppqpb1/bn2pnp1/2pP4/1p2P3/2N2N2/PPQBBPPP/R3K2R w");
    const auto unchanged = identity(bounded);
    ChessRules::generateLegal(bounded, legal);
    std::atomic_bool running{false};
    require(legal.contains(ChessAI::findBestMove(bounded, AIDifficulty::Hard, 1, &running)),
            "Ordinary time-budget expiry was confused with external cancellation");
    require(identity(bounded) == unchanged, "Timed search failed to restore the board");
}
double cancel_active(const std::string& fen) {
    auto board = position(fen);
    const auto before = identity(board);
    auto cancel = std::make_shared<std::atomic_bool>(false);
    std::weak_ptr<std::atomic_bool> lifetime = cancel;
    std::promise<void> entered;
    auto entry = entered.get_future();
    auto worker = std::async(std::launch::async, [&, token = cancel] {
        entered.set_value();
        return ChessAI::findBestMove(board, AIDifficulty::Hard, 3000, token.get());
    });
    entry.get();
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    require(worker.wait_for(std::chrono::seconds(0)) == std::future_status::timeout,
            "Cancellation fixture finished before the cancellation request");
    cancel.reset(); // The callable must keep the token alive while searching.
    auto signal = lifetime.lock();
    require(bool(signal), "Worker did not retain its cancellation token");
    const auto requested = Clock::now();
    signal->store(true, std::memory_order_relaxed);
    signal.reset();
    const auto finished = worker.wait_for(std::chrono::milliseconds(500));
    require(finished == std::future_status::ready, "Cooperative cancellation exceeded the test's scheduling allowance");
    require(worker.get() == Move{}, "Active search did not observe cancellation");
    const auto elapsed = std::chrono::duration<double, std::milli>(Clock::now() - requested).count();
    require(identity(board) == before, "Mid-search cancellation failed to undo a move");
    require(lifetime.expired(), "Completed search retained its token");
    return elapsed;
}
} // namespace

int main(int argc, char** argv) {
    try {
        std::string report;
        if (argc == 3 && std::string(argv[1]) == "--report") report = argv[2];
        else require(argc == 1, "Expected optional --report PATH");
        canceled_before_work_and_book_recovery();
        const std::array<std::string, 3> positions{{
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w",
            "r3k2r/p1ppqpb1/bn2pnp1/2pP4/1p2P3/2N2N2/PPQBBPPP/R3K2R w",
            "r1b1r1k1/pp2qppp/2n2n2/2bp4/4P3/2NP1N2/PPPQBPPP/R1B1K2R w"}};
        std::vector<double> samples;
        for (unsigned repeat = 0; repeat < 4; ++repeat)
            for (const auto& fen : positions) samples.push_back(cancel_active(fen));
        std::string json = "{\"schema\":1,\"scope\":\"cooperative-ai-cancellation-software-test\",\"status\":\"passed\",\"hardware_qualified\":false,\"hard_deadline_guaranteed\":false,\"sample_count\":" + std::to_string(samples.size()) + ",\"cancel_to_join_ms\":[";
        for (std::size_t i = 0; i < samples.size(); ++i) { if (i) json += ','; json += std::to_string(samples[i]); }
        json += "],\"test_scheduling_allowance_ms\":500,\"longest_unchecked_work\":\"one finite legal-move generation/evaluation followed by at most twelve move undos; scheduler and runtime initialization are not hard-bounded\"}\n";
        if (!report.empty()) { std::ofstream output(report); require(bool(output << json), "Cannot write cancellation report"); }
        std::cout << json;
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "AI cancellation test: " << error.what() << '\n';
        return 1;
    }
}
