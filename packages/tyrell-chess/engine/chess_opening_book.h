#ifndef CHESS_OPENING_BOOK_H
#define CHESS_OPENING_BOOK_H

#include "chess_types.h"
#include "chess_board.h"
#include <atomic>

// =====================================================================
// Opening book for the AI.
// Stores common opening positions and recommended moves.
// Only used for Standard variant.
// =====================================================================

namespace ChessOpeningBook {
    // Initialize the book (computes position hashes). Call once at startup.
    // Cancellation leaves no published partial book; a later call retries.
    void init(const std::atomic_bool* cancel = nullptr);

    // Probe the book for the current position.
    // Returns true and fills 'move' if a book move is found.
    // Randomly selects among multiple moves for variety.
    bool probe(ChessBoard& board, Move& move, const std::atomic_bool* cancel = nullptr);
}

#endif // CHESS_OPENING_BOOK_H
