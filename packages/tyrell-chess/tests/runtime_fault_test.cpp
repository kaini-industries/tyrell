// Regression for completed timed games: reopening the result must never restart
// its clocks or turn a valid resignation save into a flagged/corrupt snapshot.
#define main chess_application_main
#include "../src/main.cpp"
#undef main

#define CHECK(expression) do { if (!(expression)) throw std::runtime_error( \
    std::string(__FILE__) + ":" + std::to_string(__LINE__) + ": " #expression); } while (false)

namespace {
void completedResume(const std::filesystem::path& dir,bool checkmate) {
    App app(dir);app.setup.mode=Mode::Local;app.setup.time=TimeControl::Bullet1;app.startGame();
    if(checkmate) {
        CHECK(app.game.moveUci("f2f3",nowMs()));CHECK(app.game.moveUci("e7e5",nowMs()));
        CHECK(app.game.moveUci("g2g4",nowMs()));
        CHECK(app.game.clock.setRemainingMs(PieceColor::White,100,nowMs()));
        CHECK(app.game.moveUci("d8h4",nowMs()));
        CHECK(app.game.result=="0-1"&&app.game.reason=="Checkmate");
    } else {
        CHECK(app.game.clock.setRemainingMs(PieceColor::White,100,nowMs()));
        app.game.resign(PieceColor::Black,nowMs());
        CHECK(app.game.result=="1-0"&&app.game.reason=="Resigned");
    }
    app.finished();app.home();CHECK(app.screen==Screen::Home);
    const auto result=app.game.result,reason=app.game.reason;
    const auto before=app.game.clock.snapshot(nowMs());
    app.menu=0;app.activate();CHECK(app.screen==Screen::Board&&app.game.over());
    CHECK(!app.game.clock.isRunning());
    std::this_thread::sleep_for(std::chrono::milliseconds(140));app.update(1);
    const auto after=app.game.clock.snapshot(nowMs());
    CHECK(before.whiteRemainingMs==after.whiteRemainingMs&&before.blackRemainingMs==after.blackRemainingMs);
    CHECK(!after.hasFlaggedSide&&app.game.result==result&&app.game.reason==reason);
    CHECK(app.persist());Game loaded;std::string error;
    CHECK(loaded.load(dir/"active.save",nowMs(),error));CHECK(loaded.result==result&&loaded.reason==reason);
    CHECK(!loaded.clock.isRunning());
    // Reopening the app and choosing Resume follows the same stopped path.
    App reopened(dir);CHECK(reopened.haveGame&&reopened.game.over());
    reopened.menu=0;reopened.activate();CHECK(!reopened.game.clock.isRunning());
}
}
int main() {
    const auto directory=std::filesystem::temp_directory_path()/
        ("tyrell-chess-runtime-fault-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    try {
        completedResume(directory/"resignation",false);completedResume(directory/"checkmate",true);
        std::filesystem::remove_all(directory);
        std::puts("Completed timed-game resume preserves stopped clocks, result and reloadable save.");return 0;
    } catch(const std::exception& error) {
        std::filesystem::remove_all(directory);std::fprintf(stderr,"%s\n",error.what());return 1;
    }
}
