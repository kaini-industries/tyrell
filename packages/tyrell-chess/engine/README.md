# Portable Cardputer Chess engine

The files listed in `UPSTREAM.json` originate in Kaini Industries'
[Cardputer ADV Chess](https://github.com/kaini-industries/cardputer-chess), at
commit `64419e29c828b77b420221472e8a314778713a07`. The original MIT notice is
retained in `LICENSE.upstream` and must accompany binary distributions.

Rules, board representation, AI, opening book, clocks, puzzles, game records and
save/profile codecs retain their upstream APIs. Platform adaptations are
the standard C++17 monotonic clock and random source in `chess_platform.h`,
and thread-safe initialization of the otherwise unchanged Zobrist tables.
The engine does not open device nodes, files or network sockets.

Search and opening-book state are process-global. Run at most one AI search at
a time; pass a board copy to a background worker. Access the live game on the
UI thread. `ChessClock` instead accepts explicit timestamps and needs no wall
clock. As upstream, history repetition counts use uint8_t; callers with longer
histories should provide the most recent 255 plies (the 50-move rule ends a game
after 100 reversible plies).

The `chess_engine` CMake target exports this directory. With `BUILD_TESTING`,
`chess-engine-test` verifies known perft counts, move reversal, special moves,
all 960 initial positions, end conditions, bounded AI search, deterministic
clocks, puzzle solution legality and save-codec corruption rejection.
