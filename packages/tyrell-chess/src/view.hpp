#pragma once
#include "chess_board.h"
#include <string>
#include <vector>

// A renderer-only snapshot. The controller owns all rules, input, and timing.
namespace chessui {
enum class Screen { Home, Setup, Board, Help, Confirm, Promotion, Network };
struct View {
    Screen screen=Screen::Home;
    ChessBoard board;
    Square cursor{4,1}, selected=NO_SQUARE, lastFrom=NO_SQUARE,lastTo=NO_SQUARE;
    Square hintFrom=NO_SQUARE,hintTo=NO_SQUARE;
    MoveList legal;
    std::vector<std::string> moveLines,menu;
    std::string whiteClock="--:--",blackClock="--:--";
    std::string title="CHESS",subtitle,status,footer,reviewLabel,notice;
    bool noticeError=false;
    int menuIndex=0;
    std::size_t helpOffset=0;
    bool flipped=false,monochrome=false,thinking=false,review=false;
    bool gameOver=false,clocksEnabled=false,clocksRunning=false;
    Piece animatedPiece;
    Square animationFrom=NO_SQUARE,animationTo=NO_SQUARE;
    float animationProgress=1;
    double time=0;
};
// Optional audit records the actual fitted strings and draw coordinates. It is
// used by headless semantic layout checks; ordinary rendering allocates none.
struct TextBounds {
    std::string original,rendered;
    int x=0,y=0,width=0,height=0,maximumWidth=0;
};
void draw(const View& view,std::vector<TextBounds>* audit=nullptr);
}
