// Actual draw calls at deterministic UI time; no pixel-perfect golden images.
#define main chess_application_main
#include "../src/main.cpp"
#undef main
#include <iomanip>
#define CHECK(x) do{if(!(x))throw std::runtime_error(std::string(__FILE__)+":"+std::to_string(__LINE__)+": " #x);}while(false)

namespace {
std::vector<chessui::TextBounds> render(App& app,tyrell::game::Runtime& runtime,
                                      const std::filesystem::path& directory,
                                      const std::string& name,double at=2,bool allowNotice=false) {
    std::vector<chessui::TextBounds> bounds;
    runtime.begin_frame();app.draw(at,&bounds);runtime.end_frame();
    runtime.screenshot((directory/(name+".png")).string());
    CHECK(allowNotice||app.view.notice.empty());
    CHECK(!bounds.empty());bool footer=false;
    for(const auto& text:bounds) {
        CHECK(text.x>=0&&text.y>=0&&text.width>=0&&text.height>0);
        CHECK(text.x+text.width<=320&&text.y+text.height<=170);
        CHECK(text.width<=text.maximumWidth);
        CHECK(text.rendered.find_first_of("\r\n\t")==std::string::npos);
        if(text.original==app.view.footer){
            if(text.rendered!=text.original)throw std::runtime_error(name+": essential footer clipped: "+text.original+" => "+text.rendered);
            footer=true;
        }
    }
    CHECK(footer);
    std::ofstream out(directory/(name+"-bounds.json"));
    out<<"{\"width\":320,\"height\":170,\"ui_time\":"<<at<<",\"texts\":[";
    bool first=true;for(const auto& text:bounds){if(!first)out<<',';first=false;
        out<<"{\"text\":"<<std::quoted(text.rendered)<<",\"x\":"<<text.x<<",\"y\":"<<text.y
           <<",\"width\":"<<text.width<<",\"height\":"<<text.height<<'}';}
    out<<"]}\n";return bounds;
}
bool contains(const std::vector<chessui::TextBounds>& bounds,const std::string& value) {
    return std::any_of(bounds.begin(),bounds.end(),[&](const auto& text){return text.original==value;});
}
void start(App& app){app.setup.mode=Mode::Local;app.startGame();}
}
int main(){
    const auto state=std::filesystem::temp_directory_path()/("chess-ui-layout-"+std::to_string(nowMs()));
    const std::filesystem::path images=CHESS_UI_PREVIEW_DIR;
    try{
        std::filesystem::create_directories(images);
        tyrell::game::RuntimeOptions options;options.backend=tyrell::game::Backend::Headless;options.fps=0;
        tyrell::game::Runtime runtime(options);App app(state);
        render(app,runtime,images,"home");start(app);
        app.time=2;app.notify(std::string(160,'x')+"\nsecond error line\tend");
        auto visible=render(app,runtime,images,"long-error-notice",4.99,true);
        CHECK(!app.view.notice.empty());
        render(app,runtime,images,"notice-expired",5);CHECK(app.view.notice.empty());
        app.toast.clear(); // Later snapshots use another fixed time; don't revive this old notice.
        CHECK(app.time==2); // Rendering did not change network/puzzle/controller time.
        app.key(KEY_I);size_t visited=0;
        for(size_t page=0;page<(app.contextHelp().size()+7)/8;++page){
            auto help=render(app,runtime,images,"help-"+std::to_string(page));
            for(size_t i=app.view.helpOffset;i<std::min(app.view.menu.size(),app.view.helpOffset+8);++i){
                CHECK(contains(help,app.view.menu[i]));++visited;
            }
            app.key(KEY_RIGHT);
        }
        CHECK(visited==app.contextHelp().size());app.key(KEY_ESCAPE);
        app.review=true;app.reviewPly=0;render(app,runtime,images,"replay-footer");app.review=false;
        app.screen=Screen::Promotion;auto promotion=render(app,runtime,images,"promotion");
        for(const auto& choice:{"Queen","Knight","Rook","Bishop"})CHECK(contains(promotion,choice));
        CHECK(app.view.footer.find("Left/Right Choose")!=std::string::npos&&app.view.footer.find("Enter Confirm")!=std::string::npos);
        app.screen=Screen::Confirm;app.dialog="castle";app.menu=1;
        auto castle=render(app,runtime,images,"castle-choice");CHECK(contains(castle,"Move king")&&contains(castle,"Castle"));
        app.screen=Screen::Network;app.page="waiting";app.networkHost=false;app.networkAddress="192.168.111.222";
        render(app,runtime,images,"network-address");
        app.page="disconnected";app.status=std::string(180,'z')+"\nDetails\r\tignored control separators";
        render(app,runtime,images,"network-long-error");
        app.page.clear();app.screen=Screen::Board;app.motion=false;
        app.animatedPiece={PieceType::Queen,PieceColor::White};app.animatedMove={{0,0},{7,7}};app.animationStart=2;
        render(app,runtime,images,"reduced-motion",2.05);CHECK(app.view.animatedPiece.empty());
        render(app,runtime,images,"reduced-motion-later",2.15);
        auto bytes=[](const std::filesystem::path& path){std::ifstream in(path,std::ios::binary);return std::string(std::istreambuf_iterator<char>(in),{});};
        CHECK(bytes(images/"reduced-motion.png")==bytes(images/"reduced-motion-later.png"));
        const auto before=chess::fen(app.game.board);app.game.clock.configure(TimeControl::Bullet1);
        CHECK(app.game.clock.start(nowMs()));render(app,runtime,images,"ui-clock-only",5000);
        CHECK(app.game.clock.isRunning()&&app.time==2&&chess::fen(app.game.board)==before);
        app.game.clock.configure(TimeControl::None);app.setup.mode=Mode::Computer;app.setup.human=PieceColor::Black;app.startGame();
        render(app,runtime,images,"computer-turn-footer");
        CHECK(app.view.footer.find("I Help")!=std::string::npos&&app.view.footer.find("Esc Menu")!=std::string::npos);
        app.setup.mode=Mode::Host;app.startGame();app.networkHost=app.networkPlaying=true;app.pauseMenu();
        render(app,runtime,images,"live-lan-menu");CHECK(app.view.status==app.commandHelp(Action::Exit));
        CHECK(app.view.status.find("Confirm leaving")!=std::string::npos);
        app.networkPlaying=false;app.screen=Screen::Setup;app.page="puzzles";
        render(app,runtime,images,"puzzle-category");CHECK(app.view.status==app.commandHelp(Action::Confirm));
        std::filesystem::remove_all(state);std::puts("Deterministic UI layout passed: real draw bounds, complete help pages, notice boundaries, choices, network text and reduced motion.");return 0;
    }catch(const std::exception& error){std::filesystem::remove_all(state);std::fprintf(stderr,"%s\n",error.what());return 1;}
}
