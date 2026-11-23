// Independent keyboard/UI flow regressions from the second adversarial round.
#define main chess_application_main
#include "../src/main.cpp"
#undef main
#define CHECK(expression) do { if (!(expression)) throw std::runtime_error( \
    std::string(__FILE__)+":"+std::to_string(__LINE__)+": " #expression); } while(false)
namespace {
void onlineGame(App& app,TimeControl tc=TimeControl::None) {
    app.setup=chess::Options{};app.setup.mode=Mode::Host;app.setup.time=tc;
    app.startGame();app.networkHost=true;app.networkPlaying=true;
    app.screen=Screen::Board;
}
void offer(App& app) {
    app.incomingDrawOffer=true;
    app.status="Draw offered - Y accepts";
}
void terminalOffers(const std::filesystem::path& dir) {
    {
        App app(dir/"resigned");onlineGame(app);offer(app);
        app.game.resign(PieceColor::Black,nowMs());app.finished();
        CHECK(app.game.result=="1-0"&&app.game.reason=="Resigned");
        CHECK(!app.incomingDrawOffer);
        app.key(KEY_Y);app.key(KEY_D);
        CHECK(app.screen==Screen::Board&&app.game.result=="1-0"&&app.game.reason=="Resigned");
        app.key(KEY_V);CHECK(app.review);app.key(KEY_Y);app.key(KEY_D);
        CHECK(app.game.result=="1-0"&&app.game.reason=="Resigned");
    }
    {
        App app(dir/"flagged");onlineGame(app,TimeControl::Bullet1);offer(app);
        CHECK(app.game.clock.setRemainingMs(PieceColor::White,1,nowMs()));
        std::this_thread::sleep_for(std::chrono::milliseconds(3));app.update(1);
        CHECK(app.game.result=="0-1"&&app.game.reason=="Time expired");
        CHECK(!app.incomingDrawOffer);app.key(KEY_Y);app.key(KEY_D);
        CHECK(app.game.result=="0-1"&&app.game.reason=="Time expired");
    }
}
void liveLeave(const std::filesystem::path& dir) {
    App app(dir/"menu");onlineGame(app);
    app.pauseMenu();CHECK(app.items()[1]=="Leave match");app.menu=1;app.activate();
    CHECK(app.screen==Screen::Confirm&&app.dialog=="leave-menu"&&app.menu==0);
    app.key(KEY_ESCAPE);CHECK(app.networkPlaying&&!app.quit&&app.screen!=Screen::Home);
    app.pauseMenu();app.menu=1;app.activate();app.key(KEY_ENTER);
    CHECK(app.networkPlaying&&!app.quit&&app.screen!=Screen::Home);
    app.pauseMenu();app.menu=1;app.activate();app.key(KEY_DOWN);app.key(KEY_ENTER);
    CHECK(app.screen==Screen::Home&&!app.networkPlaying&&!app.quit);

    App quitter(dir/"quit");onlineGame(quitter);quitter.key(KEY_Q);
    CHECK(quitter.screen==Screen::Confirm&&quitter.dialog=="leave-exit"&&quitter.menu==0&&!quitter.quit);
    quitter.key(KEY_ESCAPE);CHECK(quitter.networkPlaying&&!quitter.quit);
    quitter.key(KEY_Q);quitter.key(KEY_ENTER);CHECK(quitter.networkPlaying&&!quitter.quit);
    quitter.key(KEY_Q);quitter.key(KEY_DOWN);quitter.key(KEY_ENTER);
    CHECK(quitter.quit&&!quitter.networkPlaying);

    App complete(dir/"completed");onlineGame(complete);complete.game.resign(PieceColor::Black,nowMs());complete.finished();
    complete.key(KEY_Q);CHECK(complete.quit);
}
std::string contents(const std::filesystem::path& file) {
    std::ifstream in(file);return std::string((std::istreambuf_iterator<char>(in)),{});
}
void progressAndSettingsRetry(const std::filesystem::path& directory) {
    const auto progressDir=directory/"progress";std::filesystem::create_directories(progressDir);
    {std::ofstream old(progressDir/"puzzles");old<<"1\n";}
    App puzzle(progressDir);puzzle.nextPuzzle();CHECK(puzzle.game.puzzleIndex==0);
    std::filesystem::create_directory(progressDir/"puzzles.tmp");
    CHECK(puzzle.apply(puzzle.game.solution[0]));
    CHECK(puzzle.solved.count(0)&&puzzle.solved.count(1)&&puzzle.pendingProgress);
    CHECK(puzzle.toast.find("Progress")!=std::string::npos);
    CHECK(contents(progressDir/"puzzles")=="1\n");
    puzzle.home();CHECK(puzzle.screen!=Screen::Home&&puzzle.pendingProgress&&puzzle.solved.count(0));
    puzzle.key(KEY_Q);CHECK(!puzzle.quit&&puzzle.pendingProgress);
    CHECK(contents(progressDir/"puzzles")=="1\n");
    std::filesystem::remove_all(progressDir/"puzzles.tmp");puzzle.key(KEY_Q);
    CHECK(puzzle.quit&&!puzzle.pendingProgress);
    App reopenedProgress(progressDir);CHECK(reopenedProgress.solved.count(0)&&reopenedProgress.solved.count(1));

    const auto settingsDir=directory/"settings";std::filesystem::create_directories(settingsDir);
    {std::ofstream old(settingsDir/"settings");old<<"0 1\n";}
    App settings(settingsDir);CHECK(!settings.haveGame&&!settings.contrast&&settings.motion);
    std::filesystem::create_directory(settingsDir/"settings.tmp");
    settings.screen=Screen::Setup;settings.page="preferences";settings.menu=0;settings.key(KEY_ENTER);
    CHECK(settings.contrast&&settings.pendingSettings);
    CHECK(settings.toast.find("Settings")!=std::string::npos);
    CHECK(contents(settingsDir/"settings")=="0 1\n");
    settings.key(KEY_Q);CHECK(!settings.quit&&settings.pendingSettings);
    CHECK(contents(settingsDir/"settings")=="0 1\n");
    std::filesystem::remove_all(settingsDir/"settings.tmp");settings.key(KEY_Q);
    CHECK(settings.quit&&!settings.pendingSettings);
    App reopenedSettings(settingsDir);CHECK(reopenedSettings.contrast&&reopenedSettings.motion);
}

}
int main() {
    const auto dir=std::filesystem::temp_directory_path()/("tyrell-chess-flow-round2-"+
        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    try {
        terminalOffers(dir);liveLeave(dir);progressAndSettingsRetry(dir);std::filesystem::remove_all(dir);
        std::puts("Second-round flow tests passed: terminal draw safety, deliberate LAN leave/quit, and checked progress/settings retry.");return 0;
    }catch(const std::exception& error) {
        std::filesystem::remove_all(dir);std::fprintf(stderr,"%s\n",error.what());return 1;
    }
}
