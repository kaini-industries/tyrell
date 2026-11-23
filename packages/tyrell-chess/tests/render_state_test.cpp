// Test visible clock state, rather than only controller flags: paused/finished
// games must not present a running-clock highlight on the native LCD.
#include "view.hpp"
#include "tyrell/game/runtime.hpp"
#include "raylib.h"
#include <chrono>
#include <filesystem>
#include <iostream>
#include <stdexcept>

namespace {
void require(bool value,const char* message) {
    if(!value)throw std::runtime_error(message);
}
bool same(Color left,Color right) {
    return left.r==right.r&&left.g==right.g&&left.b==right.b;
}
}
int main() {
    const auto directory=std::filesystem::temp_directory_path()/
        ("chess-render-state-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    try {
        std::filesystem::create_directories(directory);
        tyrell::game::RuntimeOptions options;
        options.backend=tyrell::game::Backend::Headless;options.fps=0;
        tyrell::game::Runtime runtime(options);
        chessui::View view;view.screen=chessui::Screen::Board;
        view.whiteClock=view.blackClock="1:00";
        auto sample=[&](const char* name,int y) {
            runtime.begin_frame();chessui::draw(view);runtime.end_frame();
            const auto path=(directory/name).string();runtime.screenshot(path);
            Image image=LoadImage(path.c_str());
            require(image.data!=nullptr,"Clock screenshot was not readable");
            const Color pixel=GetImageColor(image,170,y);UnloadImage(image);return pixel;
        };
        constexpr Color active{55,73,130,255},inactive{221,224,211,255};
        require(same(sample("untimed.png",48),active),"Untimed current player lost its turn highlight");
        view.clocksEnabled=true;view.clocksRunning=true;
        require(same(sample("running.png",48),active),"Running White clock must look active");
        view.board.setSideToMove(PieceColor::Black);
        require(same(sample("black-running.png",23),active),"Running Black clock must look active");
        require(same(sample("inactive-white.png",48),inactive),"Inactive White clock must not look active");
        view.clocksRunning=false;
        require(same(sample("paused.png",23),inactive),"Paused clock must not look active");
        view.gameOver=true;view.clocksRunning=true;
        require(same(sample("finished.png",23),inactive),"Finished game must never show an active clock");
        view.gameOver=false;view.review=true;
        require(same(sample("review.png",23),inactive),"Replay must not show an active clock");
        view.notice="Progress not saved. Retry or free some space.";view.noticeError=true;
        sample("notice.png",160);
        Image notice=LoadImage((directory/"notice.png").string().c_str());
        Image before=LoadImage((directory/"review.png").string().c_str());
        require(same(GetImageColor(notice,315,160),Color{220,91,103,255}),"Error notice must use the full-width warning bar");
        bool readablePastSidebar=false;
        for(int y=158;y<168;++y)for(int x=190;x<280;++x)if(same(GetImageColor(notice,x,y),Color{31,38,66,255}))readablePastSidebar=true;
        require(readablePastSidebar,"Long feedback must remain readable beyond the old narrow status area");
        for(int y=0;y<154;++y)for(int x=0;x<320;++x)require(same(GetImageColor(notice,x,y),GetImageColor(before,x,y)),"Feedback must not obscure pieces or clocks");
        UnloadImage(notice);UnloadImage(before);
        std::filesystem::remove_all(directory);
        std::cout<<"Native clock highlight states passed.\n";
        return 0;
    } catch(const std::exception& error) {
        std::filesystem::remove_all(directory);
        std::cerr<<error.what()<<'\n';return 1;
    }
}
