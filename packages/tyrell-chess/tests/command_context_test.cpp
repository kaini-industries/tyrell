// The actual controller, not a parallel model of its key/help behavior.
#define main chess_application_main
#include "../src/main.cpp"
#undef main
#define CHECK(x) do{if(!(x))throw std::runtime_error(std::string(__FILE__)+":"+std::to_string(__LINE__)+": " #x);}while(false)

namespace {
Command action(const App& app,Action id) {
    const auto commands=app.commands();
    const auto found=std::find_if(commands.begin(),commands.end(),[&](const auto& c){return c.id==id;});
    CHECK(found!=commands.end());return *found;
}
void start(App& app,Mode mode=Mode::Local,TimeControl clock=TimeControl::None) {
    app.setup.mode=mode;app.setup.time=clock;app.startGame();
}
void consistent(const App& app) {
    CHECK(tyrell::app::command_errors(app.commands()).empty());
    for(const auto& command:app.commands())for(int key:command.keys) {
        const auto table=app.commands();const auto* resolved=tyrell::app::find_command(table,key);
        CHECK(resolved&&resolved->id==command.id);
        CHECK(app.repeatable(key)==(command.enabled&&command.repeat==Repeat::Allow&&command.confirmation!=Confirmation::Required));
    }
}
void contexts(const std::filesystem::path& root) {
    App app(root);consistent(app);
    app.setupMenu(Mode::Local);consistent(app);
    app.screen=Screen::Network;app.page="address";consistent(app);
    app.key(KEY_KP_1);app.key(KEY_PERIOD);app.key(KEY_KP_2);CHECK(app.networkAddress=="1.2");
    app.key(KEY_BACKSPACE,true);CHECK(app.networkAddress=="1.");
    app.key(KEY_DELETE,true);CHECK(app.networkAddress=="1.");
    app.key(KEY_DELETE);CHECK(app.networkAddress=="1");
    app.networkAddress="192.168.111.222";CHECK(!action(app,Action::AddressCharacter).enabled);
    app.key(KEY_THREE);CHECK(app.networkAddress=="192.168.111.222");
    app.page.clear();start(app);consistent(app);
    app.key(KEY_ENTER);CHECK(app.selected.valid());consistent(app);
    app.screen=Screen::Promotion;consistent(app);
    CHECK(action(app,Action::Confirm).confirmation==Confirmation::Required);
    app.screen=Screen::Confirm;app.dialog="castle";consistent(app);
}
void availability(const std::filesystem::path& root) {
    App app(root);start(app);
    CHECK(action(app,Action::Hint).enabled&&!action(app,Action::Undo).enabled);
    CHECK(app.game.moveUci("e2e4",nowMs()));CHECK(action(app,Action::Undo).enabled);
    app.key(KEY_U);CHECK(app.game.history.empty());
    app.game.clock.configure(TimeControl::Bullet1);
    CHECK(!action(app,Action::Hint).enabled&&!action(app,Action::Undo).enabled&&!action(app,Action::Review).enabled);
    app.key(KEY_H);CHECK(app.toast=="Hints are for untimed offline games");
    app.setup.mode=Mode::Computer;app.setup.time=TimeControl::None;app.setup.human=PieceColor::Black;app.startGame();
    CHECK(!app.worker.valid()&&!app.game.humanTurn());
    CHECK(!action(app,Action::Confirm).enabled); // Before the AI worker starts.
    app.key(KEY_ENTER);CHECK(!app.selected.valid());
}
void contextualHelp(const std::filesystem::path& root) {
    App app(root);start(app,Mode::Local,TimeControl::Bullet1);
    const auto expected=tyrell::app::help_lines(app.commands());
    app.key(KEY_I);CHECK(app.screen==Screen::Help);
    CHECK(app.contextHelp()==expected);
    CHECK(std::none_of(expected.begin(),expected.end(),[](const auto& line){return line.rfind("H   ",0)==0||line.rfind("U   ",0)==0;}));
    CHECK(expected.size()>8);app.key(KEY_RIGHT);CHECK(app.helpPage==1);
    app.key(KEY_RIGHT,true);CHECK(app.helpPage==1);
    app.key(KEY_LEFT);CHECK(app.helpPage==0);app.key(KEY_I);CHECK(app.screen==Screen::Board);
    app.pauseMenu();app.menu=6;app.activate();CHECK(app.screen==Screen::Help&&app.previous==Screen::Setup);
    CHECK(app.contextHelp()==tyrell::app::help_lines(app.commands(Screen::Setup)));
    app.key(KEY_ESCAPE);CHECK(app.screen==Screen::Setup&&app.page=="pause");
}
void protectedConfirmation(const std::filesystem::path& root) {
    App app(root);start(app,Mode::Host);app.networkHost=app.networkPlaying=true;
    CHECK(action(app,Action::Exit).confirmation==Confirmation::Required);
    app.key(KEY_Q,true);CHECK(app.screen==Screen::Board&&!app.quit);
    app.key(KEY_Q);CHECK(app.screen==Screen::Confirm&&app.menu==0&&!app.quit);
    app.key(KEY_DOWN);CHECK(app.menu==1);
    app.key(KEY_KP_ENTER,true);CHECK(!app.quit&&app.networkPlaying);
    app.key(KEY_KP_ENTER);CHECK(app.quit&&!app.networkPlaying);
}
void uiClockIsSeparate(const std::filesystem::path& root) {
    App app(root,100);start(app);app.time=7;
    app.notify("Fixed-time feedback");CHECK(app.toastUntil==103&&app.time==7);
    MoveList moves;ChessRules::generateLegal(app.game.board,moves);CHECK(moves.count>0);
    CHECK(app.apply(moves.moves[0]));CHECK(app.animationStart==100&&app.time==7);
    CHECK(app.uiNow()==100);
}
}
int main(){
    const auto root=std::filesystem::temp_directory_path()/("chess-command-context-"+std::to_string(nowMs()));
    try{contexts(root/"contexts");availability(root/"availability");contextualHelp(root/"help");protectedConfirmation(root/"confirm");uiClockIsSeparate(root/"ui-clock");std::filesystem::remove_all(root);std::puts("Context commands passed: aliases, availability, real help contexts/pages, repeat-safe LAN confirmations and independent UI clock.");return 0;}
    catch(const std::exception& error){std::filesystem::remove_all(root);std::fprintf(stderr,"%s\n",error.what());return 1;}
}
