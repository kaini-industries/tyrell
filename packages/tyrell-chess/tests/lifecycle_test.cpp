// Adversarial lifecycle regressions: preserve recoverable user state and keep
// save failures visible until the player can retry. No display or sockets.
#define main chess_application_main
#include "../src/main.cpp"
#undef main

#define REQUIRE(expression) do { if(!(expression))throw std::runtime_error( \
    std::string(__FILE__)+":"+std::to_string(__LINE__)+": " #expression); } while(false)
namespace {
std::string read(const std::filesystem::path& path) {
    std::ifstream in(path,std::ios::binary);
    return {std::istreambuf_iterator<char>(in),std::istreambuf_iterator<char>()};
}
void saveFailure(const std::filesystem::path& directory) {
    App app(directory/"failure");app.setup.mode=Mode::Local;app.startGame();
    REQUIRE(app.game.moveUci("e2e4",nowMs()));app.persist();
    const auto saved=read(app.dir/"active.save");
    std::filesystem::create_directory(app.dir/"active.save.tmp");
    REQUIRE(app.game.moveUci("e7e5",nowMs()));
    // A broken/full state volume must not cause a write/log attempt each frame.
    app.lastSave=nowMs()-5001;
    std::ostringstream failures;auto* original=std::cerr.rdbuf(failures.rdbuf());
    app.update(1);app.update(1.01);std::cerr.rdbuf(original);
    const auto log=failures.str();REQUIRE(std::count(log.begin(),log.end(),'\n')==1);
    app.pauseMenu();app.menu=1;app.activate();
    REQUIRE(app.screen!=Screen::Home);REQUIRE(app.game.history.size()==2);
    app.key(KEY_Q);
    REQUIRE(!app.quit);REQUIRE(!app.toast.empty());
    REQUIRE(read(app.dir/"active.save")==saved);
    // Retrying after storage is writable must save the in-memory continuation.
    std::filesystem::remove(app.dir/"active.save.tmp");
    app.key(KEY_Q);REQUIRE(app.quit);
    Game loaded;std::string error;REQUIRE(loaded.load(app.dir/"active.save",nowMs(),error));
    REQUIRE(loaded.history.size()==2);
}
void orientation(const std::filesystem::path& directory) {
    const auto state=directory/"orientation";
    {
        App app(state);app.setup.human=PieceColor::Black;app.startGame();
        REQUIRE(app.flipped);app.key(KEY_Q);REQUIRE(app.quit);
    }
    App app(state);REQUIRE(app.haveGame);app.menu=0;app.activate();
    REQUIRE(app.game.options.human==PieceColor::Black&&app.flipped);
    app.home();app.menu=3;app.activate();app.menu=0;app.activate();
    REQUIRE(app.game.options.mode==Mode::Puzzle);
    app.home();REQUIRE(app.haveGame&&app.game.options.mode==Mode::Computer);
    REQUIRE(app.game.options.human==PieceColor::Black&&app.flipped);
}
void automationIsolation() {
    char program[]="tyrell-chess",demo[]="--demo",stateFlag[]="--state-dir",state[]="/tmp/explicit-chess-state";
    char* unsafe[]={program,demo};bool rejected=false;
    try{(void)arguments(2,unsafe);}catch(const std::runtime_error&){rejected=true;}
    REQUIRE(rejected);
    char* isolated[]={program,demo,stateFlag,state};
    const auto options=arguments(4,isolated);REQUIRE(options.explicitState&&options.state==state);
}
void corruptSave(const std::filesystem::path& directory) {
    const auto state=directory/"corrupt";
    {App app(state);app.setup.mode=Mode::Local;app.startGame();app.key(KEY_Q);}
    {std::ofstream out(state/"active.save",std::ios::binary|std::ios::app);out<<'X';}
    const auto damaged=read(state/"active.save");
    App app(state);REQUIRE(!app.haveGame);REQUIRE(!app.toast.empty());
    bool retained=false;
    for(const auto& entry:std::filesystem::directory_iterator(state))
        if(entry.is_regular_file()&&read(entry.path())==damaged)retained=true;
    REQUIRE(retained);
}
}
int main() {
    const auto directory=std::filesystem::temp_directory_path()/
        ("tyrell-chess-lifecycle-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    try {
        saveFailure(directory);orientation(directory);corruptSave(directory);automationIsolation();
        std::filesystem::remove_all(directory);
        std::puts("Chess lifecycle passed: failed-save retry, Black orientation restore, corrupt-save visibility/preservation, automation isolation.");
        return 0;
    }catch(const std::exception& error){
        std::filesystem::remove_all(directory);std::fprintf(stderr,"%s\n",error.what());return 1;
    }
}
