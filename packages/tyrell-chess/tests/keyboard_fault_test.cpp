// Independent keyboard/menu fault regressions. No graphics or sockets opened.
#define main chess_application_main
#include "../src/main.cpp"
#undef main

#define CHECK(expression) do { if(!(expression)) throw std::runtime_error( \
    std::string(__FILE__)+":"+std::to_string(__LINE__)+": " #expression); } while(false)

namespace {
void onlineArchiveAndMenus(const std::filesystem::path& directory) {
    App app(directory);app.setup.mode=Mode::Local;app.startGame();
    CHECK(app.game.moveUci("e2e4",nowMs()));CHECK(app.persist());
    const auto offline=chess::fen(app.game.board);
    app.setup.mode=Mode::Host;app.game.start(app.setup,nowMs());
    app.haveGame=true;app.networkHost=true;app.networkPlaying=true;app.screen=Screen::Board;
    std::ofstream(directory/"games")<<"Deliberately obstruct archive directory";
    app.game.finish("1/2-1/2","Draw agreed",nowMs());app.finished();
    CHECK(app.pendingArchive&&app.game.over());
    app.key(KEY_ESCAPE);CHECK(app.page=="pause");app.key(KEY_DOWN);app.key(KEY_ENTER);
    CHECK(app.screen!=Screen::Home&&app.game.over()&&app.pendingArchive);
    CHECK(app.toast.find("Archive failed")!=std::string::npos);
    // A Home departure must also honor pending archival before library replaces
    // the visible game object. This matches a resumed terminal offline save.
    app.screen=Screen::Home;app.page.clear();app.menu=5;app.key(KEY_ENTER);
    CHECK(app.screen==Screen::Home&&app.page.empty()&&app.pendingArchive&&app.game.over());
    std::filesystem::remove(directory/"games");app.screen=Screen::Setup;app.page="pause";app.menu=1;app.key(KEY_ENTER);
    CHECK(app.screen==Screen::Home&&!app.pendingArchive&&app.game.options.mode==Mode::Local);
    CHECK(chess::fen(app.game.board)==offline);
    bool pgn=false;for(const auto& path:std::filesystem::directory_iterator(directory/"games"))pgn|=path.path().extension()==".pgn";
    CHECK(pgn);
}
void ipEditingAndBack(const std::filesystem::path& directory) {
    App app(directory);app.screen=Screen::Network;app.page="address";
    app.key(KEY_ONE);app.key(KEY_PERIOD);app.key(KEY_TWO);app.key(KEY_THREE);app.key(KEY_BACKSPACE);
    CHECK(app.networkAddress=="1.2"&&app.menu==0);
    app.key(KEY_DOWN);CHECK(app.menu==1);app.key(KEY_DOWN);CHECK(app.menu==2);
    app.key(KEY_ENTER);CHECK(app.page=="lan"&&app.menu==0);
    app.menu=1;app.key(KEY_ENTER);CHECK(app.page=="address"&&app.networkAddress.empty());
    app.key(KEY_DOWN);app.key(KEY_DOWN);CHECK(app.menu==2);
    app.key(KEY_ONE);CHECK(app.menu==0&&app.networkAddress=="1");
    app.key(KEY_PERIOD);CHECK(app.menu==0&&app.networkAddress=="1.");
    app.key(KEY_S);CHECK(app.menu==1);app.key(KEY_W);CHECK(app.menu==0);
    app.key(KEY_ESCAPE);CHECK(app.page=="lan"&&app.menu==0);
}
}
int main(){
    const auto directory=std::filesystem::temp_directory_path()/("tyrell-chess-keyboard-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    try{onlineArchiveAndMenus(directory/"archive");ipEditingAndBack(directory/"ip");std::filesystem::remove_all(directory);std::puts("Keyboard fault regressions passed: online archive preservation, Home departure guard, ordered IP editing and accessible Back.");return 0;}
    catch(const std::exception& error){std::filesystem::remove_all(directory);std::fprintf(stderr,"%s\n",error.what());return 1;}
}
