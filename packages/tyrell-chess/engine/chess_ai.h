#ifndef CHESS_AI_H
#define CHESS_AI_H

#include "chess_board.h"
#include "chess_rules.h"
#include <cstdint>
#include <atomic>

// =====================================================================
// Chess AI: minimax with alpha-beta pruning, iterative deepening,
// and quiescence search. Designed for ESP32-S3 memory constraints.
// =====================================================================

enum class AIDifficulty : uint8_t {
    None   = 0,
    Easy   = 1,  // Depth 2, 200ms, random blunders
    Medium = 2,  // Depth 4, 1s
    Hard   = 3   // Depth 6+, 3s
};

namespace ChessAI {

    // Find the best move for the current side to move.
    // Returns a legal move. Difficulty chooses the depth and the time budget
    // (Easy 200ms, Medium 1000ms, Hard 3000ms). maxTimeMs == 0 keeps that budget;
    // otherwise the budget is the minimum of the two.
    // The optional token is owned by the caller and must outlive this call.
    // Observed cancellation returns Move{}; callers must also discard a result
    // completed before their cancellation/generation change. Cancellation
    // restores the supplied board before returning. It is
    // checked between finite search work units, not a wall-clock guarantee.
    Move findBestMove(ChessBoard& board, AIDifficulty difficulty, uint32_t maxTimeMs = 0,
                      const std::atomic_bool* cancel = nullptr);

    // Static evaluation of the position from the perspective of the side to move.
    // Positive = good for side to move, negative = bad.
    int16_t evaluate(const ChessBoard& board);

} // namespace ChessAI

#endif // CHESS_AI_H
