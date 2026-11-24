# Portable Cardputer Chess engine

The files listed in `UPSTREAM.json` originate in Kaini Industries'
[Cardputer ADV Chess](https://github.com/kaini-industries/cardputer-chess), at
commit `64419e29c828b77b420221472e8a314778713a07`. The original MIT notice is
retained in `LICENSE.upstream` and must accompany binary distributions.

Rules, board representation, AI, opening book, clocks, puzzles, game records and
save/profile codecs retain their upstream interfaces, with source-compatible
optional AI/book cancellation arguments. Platform adaptations are
the standard C++17 monotonic clock and random source in `chess_platform.h`,
and thread-safe initialization of the otherwise unchanged Zobrist tables.
The engine does not open device nodes, files or network sockets.

Search and opening-book state are process-global. Run at most one AI search at
a time; pass a board copy to a background worker. Access the live game on the
UI thread. `ChessAI::findBestMove` accepts a caller-owned `std::atomic_bool`
token, which must outlive its call. Use a fresh token for each worker and capture
its ownership in the callable. Request cancellation without waiting in a shell
pause handler; discard results using the app's generation and token even if a
search completed just before cancellation. Observed cancellation returns
`Move{}` after reversing every applied move. Ordinary search-budget expiry still
returns its legal best/fallback move.

Cancellation is sampled before work, between opening-book replay/sort steps,
at each search node and between move-ordering/child steps. A partially built
opening book is never published. The longest unchecked chess work is one finite
legal-move generation/evaluation, followed by at most twelve move reversals.
Search and book move-choice randomness use a small monotonic-seeded PRNG, so a
fresh worker does not initialize `std::random_device`; Chess960 and other
randomness are unchanged. This bounds cooperative CPU work, not OS scheduling,
runtime initialization or a hard wall-clock exit deadline.

`ChessClock` instead accepts explicit timestamps and needs no wall
clock. As upstream, history repetition counts use uint8_t; callers with longer
histories should provide the most recent 255 plies (the 50-move rule ends a game
after 100 reversible plies).

The `chess_engine` CMake target exports this directory. With `BUILD_TESTING`,
`chess-engine-test` verifies known perft counts, move reversal, special moves,
all 960 initial positions, end conditions, bounded AI search, deterministic
clocks, puzzle solution legality and save-codec corruption rejection.
`chess-ai-cancellation` verifies canceled startup, subsequent book/search
recovery, active-search board reversal and token ownership. Its twelve software
samples report cancellation-to-join time separately from its 500 ms scheduling
allowance; the generated `a3-ai-cancellation.json` denies hardware qualification
and hard-deadline guarantees.
