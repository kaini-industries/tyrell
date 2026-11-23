# Optional independent rules oracle

This development-only differential check compares Tyrell Chess with the
independent [python-chess project](https://github.com/niklasf/python-chess).
The installed **PyPI distribution is `chess==1.11.2`**, imported as `chess` and
reporting `chess.__version__ == "1.11.2"`. The older `python-chess` PyPI
metapackage has a different version scheme; do not substitute a
`python-chess==1.11.2` installation command.

Neither CMake nor the normal tests download or require this package. The oracle
is not linked into or installed with the app. Its library remains in a separate
virtual environment; the application does not acquire a Python dependency.

Run from the Tyrell repository root after building the headless C++ targets:

```sh
python3 scripts/chess build --backend headless
python3 -m venv /tmp/tyrell-chess-oracle-venv
/tmp/tyrell-chess-oracle-venv/bin/pip install chess==1.11.2
c++ -std=c++17 \
  -I packages/tyrell-chess/src -I packages/tyrell-chess/engine \
  packages/tyrell-chess/tests/oracle/adapter.cpp \
  build/chess-headless/libchess_model.a \
  build/chess-headless/engine/libchess_engine.a \
  -o /tmp/tyrell-chess-oracle
/tmp/tyrell-chess-oracle-venv/bin/python \
  packages/tyrell-chess/tests/oracle/oracle.py \
  --adapter /tmp/tyrell-chess-oracle
```

This macOS command uses the already-built static libraries. A Linux toolchain
may also need `-pthread`. The subprocess protocol accepts controlled test inputs,
not arbitrary user FEN files. Tyrell's internal castle-coordinate suffix `c` is
mapped to the oracle's Chess960 king-to-rook move representation explicitly.

## Recorded result

The deterministic review run on November 15, 2026 passed:

- **87,663 positions:** exact legal move sets and both colors' material-based
  mating possibilities. This includes 200 capture-biased Standard games up to
  150 plies, every Chess960 start with 36 random legal plies, simplified
  king/rook castling geometries for every start, and valid positions with
  additional enemy rook/bishop/queen attacks.
- **13,914 played positions:** exact SAN and resulting FEN from 100 Standard
  and 100 Chess960 random games, replayed through the application model.

Expected final JSON:

```json
{"rules_and_material_positions":87663,"san_and_replayed_fen_positions":13914,"chess_distribution_version":"1.11.2","passed":true}
```

The seeds are fixed. Any mismatch prints the exact position, move sequence or
set differences and exits unsuccessfully. This is broad differential evidence,
not a proof of all possible chess positions; it supplements the focused perft,
special-move, clock and save regressions in the normal CTest suite.
