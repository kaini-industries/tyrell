#include "view.hpp"
#include "chess_rules.h"
#include "raylib.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <string>

namespace chessui {
namespace {
// Playful cartridge-game colors. Every important state also has a shape.
constexpr Color paper{244,239,214,255}, ink{31,38,66,255};
constexpr Color muted{78,88,111,255}, blue{55,73,130,255};
constexpr Color gold{249,195,76,255}, cream{255,245,206,255};
constexpr Color squareLight{156,201,164,255}, squareDark{74,121,151,255};
constexpr Color moss{37,74,105,255}, checkRed{220,91,103,255};
constexpr int bx=11,by=9,cell=18,boardSize=144,sx=169;

std::string fit(std::string value,int width,int size=10) {
    if(MeasureText(value.c_str(),size)<=width) return value;
    while(!value.empty() && MeasureText((value+"..").c_str(),size)>width) value.pop_back();
    return value+"..";
}
void text(const std::string& value,int x,int y,Color color=ink,int size=10,int width=1000) {
    const auto clipped=fit(value,width,size);
    DrawText(clipped.c_str(),x,y,size,color);
}
void right(const std::string& value,int edge,int y,Color color=ink,int size=10) {
    text(value,edge-MeasureText(value.c_str(),size),y,color,size);
}
void line(int x,int y,int w,Color color) {DrawRectangle(x,y,w,1,color);}
void frame(int x,int y,int w,int h,Color color,int thickness=1) {
    DrawRectangle(x,y,w,thickness,color);DrawRectangle(x,y+h-thickness,w,thickness,color);
    DrawRectangle(x,y,thickness,h,color);DrawRectangle(x+w-thickness,y,thickness,h,color);
}
Vector2 location(Square q,bool flipped) {
    const int c=flipped?7-q.col:q.col,r=flipped?q.row:7-q.row;
    return {float(bx+c*cell),float(by+r*cell)};
}

// Hand-drawn 13x15 silhouettes. The 1px outline keeps both armies readable on
// either square color; square shapes distinguish pieces without relying on hue.
void piece(Piece p,int x,int y,int scale=1) {
    if(p.empty()) return;
    const Color edge=p.color==PieceColor::White?ink:cream;
    const Color fill=p.color==PieceColor::White?Color{255,247,217,255}:Color{31,38,66,255};
    const Color shade=p.color==PieceColor::White?Color{209,180,107,255}:Color{89,109,166,255};
    auto r=[&](int a,int b,int w,int h,Color color){DrawRectangle(x+a*scale,y+b*scale,w*scale,h*scale,color);};
    auto block=[&](int a,int b,int w,int h){r(a,b,w,h,edge); if(w>2 && h>2) r(a+1,b+1,w-2,h-2,fill);};
    // Shared plinth and stem unite the set.
    block(2,12,11,3);r(3,12,9,1,fill);
    if(p.type==PieceType::Pawn) {
        block(5,3,5,5);r(6,4,2,2,fill);block(5,8,5,4);r(6,8,3,4,fill);
    } else if(p.type==PieceType::Rook) {
        block(3,3,9,4);r(3,2,2,3,edge);r(7,2,1,3,edge);r(10,2,2,3,edge);
        r(4,4,7,2,fill);block(4,7,7,5);r(5,7,4,5,fill);
    } else if(p.type==PieceType::Knight) {
        r(5,2,5,1,edge);r(4,3,7,2,edge);r(3,5,9,3,edge);r(2,7,5,2,edge);
        r(5,4,5,3,fill);r(4,6,6,2,fill);r(3,7,3,1,fill);r(8,5,1,1,edge);
        r(8,8,4,4,edge);r(9,8,2,3,fill);r(5,11,7,1,edge);r(6,11,5,1,fill);
    } else if(p.type==PieceType::Bishop) {
        r(7,1,1,1,edge);r(6,2,3,1,edge);block(5,3,5,5);r(7,3,1,3,edge);
        block(5,8,5,4);r(6,8,3,4,fill);r(4,10,7,2,edge);r(5,10,5,2,fill);
    } else if(p.type==PieceType::Queen) {
        r(2,3,2,2,edge);r(6,1,2,2,edge);r(11,3,2,2,edge);
        r(3,5,9,3,edge);r(4,5,7,2,fill);r(4,8,7,1,edge);
        block(5,9,5,3);r(6,9,3,3,fill);r(4,11,7,1,edge);
    } else if(p.type==PieceType::King) {
        r(7,0,1,5,edge);r(5,2,5,1,edge);block(4,5,7,4);r(5,6,5,2,fill);
        block(5,9,5,3);r(6,9,3,3,fill);r(4,11,7,1,edge);
    }
    r(4,13,7,1,shade);
}

void board(const View& v) {
    const Color light=v.monochrome?Color{226,224,214,255}:squareLight;
    const Color dark=v.monochrome?Color{114,111,101,255}:squareDark;
    DrawRectangle(bx-2,by-2,boardSize+4,boardSize+4,blue);
    for(int row=0;row<8;++row) for(int col=0;col<8;++col) {
        const Square q{static_cast<unsigned char>(col),static_cast<unsigned char>(row)};
        const auto at=location(q,v.flipped); const int x=int(at.x),y=int(at.y);
        Color color=(col+row)%2?light:dark;
        if(q==v.lastFrom || q==v.lastTo) color=(col+row)%2?Color{224,210,112,255}:Color{147,172,99,255};
        if(q==v.selected) color=gold;
        const Piece p=v.board.at(col,row);
        if(p.type==PieceType::King && ChessRules::isInCheck(v.board,p.color)) color=checkRed;
        DrawRectangle(x,y,cell,cell,color);
        // One shadow pixel gives the board a tactile edge without a gradient.
        if(row==0) DrawRectangle(x,y,cell,1,Color{color.r>15?static_cast<unsigned char>(color.r-15):color.r,color.g>15?static_cast<unsigned char>(color.g-15):color.g,color.b,255});
        bool legal=false;
        for(unsigned i=0;i<v.legal.count;++i) if(v.legal.moves[i].to==q) {legal=true;break;}
        if(legal) {
            if(p.empty()) {DrawRectangle(x+7,y+7,4,4,moss);DrawRectangle(x+8,y+8,2,2,cream);}
            else {frame(x+1,y+1,16,16,moss,2);}
        }
        const bool animating=v.animationProgress<1 && q==v.animationTo && !v.animatedPiece.empty();
        if(!animating) piece(p,x+1,y+1);
        if(q==v.hintFrom || q==v.hintTo) {
            DrawRectangle(x+1,y+1,5,2,cream);DrawRectangle(x+1,y+1,2,5,cream);
            DrawRectangle(x+12,y+15,5,2,cream);DrawRectangle(x+15,y+12,2,5,cream);
        }
        if(q==v.cursor) {
            // Four corners remain visible over even the widest pieces.
            const Color c=ink;
            DrawRectangle(x,y,6,2,c);DrawRectangle(x,y,2,6,c);
            DrawRectangle(x+12,y,6,2,c);DrawRectangle(x+16,y,2,6,c);
            DrawRectangle(x,y+16,6,2,c);DrawRectangle(x,y+12,2,6,c);
            DrawRectangle(x+12,y+16,6,2,c);DrawRectangle(x+16,y+12,2,6,c);
        }
    }
    if(v.animationProgress<1 && !v.animatedPiece.empty() && v.animationFrom.valid() && v.animationTo.valid()) {
        const auto a=location(v.animationFrom,v.flipped),b=location(v.animationTo,v.flipped);
        const float t=1-std::pow(1-std::clamp(v.animationProgress,0.f,1.f),4.f);
        piece(v.animatedPiece,int(a.x+(b.x-a.x)*t)+1,int(a.y+(b.y-a.y)*t)+1);
    }
    for(int n=0;n<8;++n) {
        char letter[2]={char('a'+(v.flipped?7-n:n)),0};
        char rank[2]={char('1'+(v.flipped?n:7-n)),0};
        text(letter,bx+n*cell+6,156,muted,10);
        text(rank,2,by+n*cell+4,muted,10);
    }
}

void clockRow(const char* side,const std::string& value,int y,bool active,bool black) {
    DrawRectangle(sx,y,143,22,active?blue:Color{221,224,211,255});
    DrawRectangle(sx+5,y+7,7,7,black?ink:cream);
    frame(sx+5,y+7,7,7,active?cream:ink);
    text(side,sx+17,y+7,active?cream:muted,10);
    right(value,306,y+3,active?cream:ink,20);
    if(active) DrawRectangle(309,y+3,2,16,gold);
}
void sidebar(const View& v) {
    text(v.title.empty()?"CHESS":v.title,sx,7,blue,10,143);
    right(v.thinking?"...":"",311,7,muted,10);
    const bool showActive=!v.review && !v.gameOver && (!v.clocksEnabled || v.clocksRunning);
    clockRow("B",v.blackClock,22,showActive && v.board.sideToMove()==PieceColor::Black,true);
    clockRow("W",v.whiteClock,47,showActive && v.board.sideToMove()==PieceColor::White,false);
    if(v.review) text(v.reviewLabel.empty()?"REVIEW":v.reviewLabel,sx,75,blue,10,143);
    else text(v.status.empty()?(v.board.sideToMove()==PieceColor::White?"White to move":"Black to move"):v.status,sx,75,blue,10,143);
    line(sx,89,143,Color{170,183,180,255});
    const int count=std::min(4,int(v.moveLines.size()));
    for(int i=0;i<count;++i) text(v.moveLines[v.moveLines.size()-count+i],sx,94+i*12,ink,10,143);
    if(v.moveLines.empty()) {
        text("Your next good move",sx,96,muted,10,143);
        text("starts here.",sx,108,muted,10,143);
    }
    line(sx,145,143,Color{170,183,180,255});
    text(v.subtitle,sx,149,muted,10,143);
    if(v.screen==Screen::Board)
        text(v.footer.empty()?"ENTER move  I help":v.footer,sx,161,muted,10,143);
}

void menu(const View& v,int x,int y,int width,int visible=6,int step=17) {
    const int size=int(v.menu.size());
    const int first=std::max(0,std::min(v.menuIndex-visible+1,std::max(0,size-visible)));
    for(int i=first;i<std::min(size,first+visible);++i) {
        const int top=y+(i-first)*step;
        if(i==v.menuIndex) {
            DrawRectangle(x-4,top-3,width+8,step-1,blue);
            DrawTriangle({float(x+1),float(top+1)},{float(x+1),float(top+7)},{float(x+5),float(top+4)},gold);
            text(v.menu[i],x+11,top,cream,10,width-11);
        } else text(v.menu[i],x+11,top,ink,10,width-11);
    }
    if(size>visible) {
        const int track=visible*step-1;
        DrawRectangle(x+width+7,y-3,2,track,Color{188,201,188,255});
        DrawRectangle(x+width+7,y-3+(track-7)*std::clamp(v.menuIndex,0,size-1)/std::max(1,size-1),2,7,blue);
    }
}
void star(int x,int y,Color color) {
    DrawRectangle(x+2,y,2,8,color);DrawRectangle(x,y+2,6,4,color);
}
void home(const View& v) {
    DrawRectangle(0,0,123,170,blue);
    DrawRectangle(0,0,123,4,checkRed);
    // A pixel king on a two-tone board recalls a colorful game cartridge.
    for(int r=0;r<5;++r) for(int c=0;c<5;++c)
        DrawRectangle(-10+c*28,51+r*28,28,28,(r+c)%2?Color{61,96,141,255}:Color{68,116,150,255});
    text("TYRELL",13,14,cream,10);
    text("CHESS",12,29,cream,20);
    star(99,16,gold);star(14,66,Color{245,159,128,255});
    star(96,117,gold);star(89,61,Color{168,218,181,255});
    piece({PieceType::King,PieceColor::White},34,68,4);
    DrawRectangle(0,148,123,22,ink);
    text("CARDPUTER ZERO",13,155,cream,10,107);
    text(v.title=="CHESS"?"LET'S PLAY":v.title,139,12,blue,10,169);
    line(139,27,168,checkRed);
    menu(v,139,38,160,6,18);
    text(v.status,139,148,muted,10,168);
    text(v.footer.empty()?"ENTER select  Q exit":v.footer,139,160,muted,10,168);
}
void setup(const View& v) {
    text(v.title.empty()?"NEW GAME":v.title,12,11,blue,20,294);
    text(v.subtitle,13,36,muted,10,292);
    line(12,51,296,gold);
    menu(v,17,64,280,5,18);
    text(v.status,13,151,muted,10,294);
    text(v.footer.empty()?"Left / right change   ENTER play   ESC back":v.footer,13,161,muted,10,294);
}
void help(const View& v) {
    text(v.title.empty()?"HOW TO PLAY":v.title,12,10,blue,20,296);
    line(12,36,296,gold);
    const std::vector<std::string> defaults={
        "Arrows / WASD / ; , . /   Move cursor",
        "ENTER   Select piece / make move",
        "SPACE   Cycle legal destinations",
        "ESC     Cancel selection / game menu",
        "U undo   H hint   F flip   T contrast",
        "V review   P export PGN   I this help",
        "Review: Left / Right step, ESC return",
        "Q       Save game and exit"};
    const auto& rows=v.menu.empty()?defaults:v.menu;
    for(std::size_t i=0;i<rows.size() && i<9;++i) text(rows[i],13,46+int(i)*12,ink,10,295);
    text(v.footer.empty()?"ENTER / ESC return":v.footer,13,158,muted,10,296);
}
void overlay(const View& v) {
    board(v);sidebar(v);
    DrawRectangle(23,40,279,96,paper);frame(23,40,279,96,blue,2);
    text(v.title,33,49,blue,10,259);
    text(v.status,33,65,muted,10,259);
    if(v.screen==Screen::Promotion) {
        const PieceType types[]={PieceType::Queen,PieceType::Knight,PieceType::Rook,PieceType::Bishop};
        for(int i=0;i<4;++i) {
            const int x=40+i*65;
            DrawRectangle(x,82,52,38,i==v.menuIndex?blue:Color{221,224,211,255});
            piece({types[i],v.board.sideToMove()},x+18,85);
            text(i<int(v.menu.size())?v.menu[i]:"",x+5,105,i==v.menuIndex?cream:ink,10,44);
        }
    } else menu(v,35,87,252,2,18);
    const auto footer=v.footer.empty()
        ?(v.screen==Screen::Promotion?"Left/right choose  ENTER confirm  ESC back":"Up/down choose  ENTER select  ESC back")
        :v.footer;
    text(footer,33,122,muted,10,259);
}
}
void draw(const View& view) {
    ClearBackground(paper);
    switch(view.screen) {
        case Screen::Home:home(view);break;
        case Screen::Setup:setup(view);break;
        case Screen::Help:help(view);break;
        case Screen::Confirm:case Screen::Promotion:overlay(view);break;
        case Screen::Network:setup(view);break;
        case Screen::Board:board(view);sidebar(view);break;
    }
    if(!view.notice.empty()) {
        DrawRectangle(0,154,320,16,view.noticeError?checkRed:blue);
        DrawRectangle(0,154,320,1,gold);
        text(view.notice,7,158,view.noticeError?ink:cream,10,306);
    }

}
}
