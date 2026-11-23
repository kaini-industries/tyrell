// Optional differential-test adapter. This is never linked into the app.
#include "game.hpp"
#include "chess_rules.h"
#include <algorithm>
#include <cctype>
#include <iostream>
#include <sstream>
#include <stdexcept>

namespace {
ChessBoard loadPosition(std::istringstream& in) {
    int variant, position;
    std::string placement, turn, rights, ep;
    unsigned halfmove, fullmove;
    if (!(in >> variant >> position >> placement >> turn >> rights >> ep >> halfmove >> fullmove))
        throw std::runtime_error("Incomplete oracle position");
    ChessBoard board;
    board.setVariant(ChessVariant(variant));
    board.setPositionIndex(position);
    board.reset();
    for (int row=0; row<8; ++row)
        for (int col=0; col<8; ++col) board.set(col,row,Piece{});
    int row=7, col=0;
    for (char ch:placement) {
        if (ch=='/') {--row;col=0;}
        else if (ch>='1' && ch<='8') col+=ch-'0';
        else {
            const auto type=std::string("pnbrqk").find(char(std::tolower(ch)));
            if (type==std::string::npos) throw std::runtime_error("Bad oracle piece");
            board.set(col++,row,Piece(PieceType(type+1),std::isupper(ch)?PieceColor::White:PieceColor::Black));
        }
    }
    board.setSideToMove(turn=="w"?PieceColor::White:PieceColor::Black);
    unsigned flags=0;
    for (char ch:rights) {
        if (ch=='K' || ch=='A'+board.initRookKS()) flags|=CastleRights::WhiteKing;
        if (ch=='Q' || ch=='A'+board.initRookQS()) flags|=CastleRights::WhiteQueen;
        if (ch=='k' || ch=='a'+board.initRookKS()) flags|=CastleRights::BlackKing;
        if (ch=='q' || ch=='a'+board.initRookQS()) flags|=CastleRights::BlackQueen;
    }
    board.setCastleRights(flags);
    board.setEnPassantTarget(ep=="-"?NO_SQUARE:Square(ep[0]-'a',ep[1]-'1'));
    board.setHalfmoveClock(halfmove);
    board.setFullmoveNumber(fullmove);
    return board;
}
void position(std::istringstream& in) {
    auto board=loadPosition(in);
    MoveList legal;
    ChessRules::generateLegal(board,legal);
    std::vector<std::string> moves;
    for (int i=0;i<legal.count;++i) moves.push_back(chess::uci(legal.moves[i]));
    std::sort(moves.begin(),moves.end());
    for (const auto& move:moves) std::cout<<move<<' ';
    std::cout<<"| "<<ChessRules::hasInsufficientMatingMaterial(board,PieceColor::White)
             <<' '<<ChessRules::hasInsufficientMatingMaterial(board,PieceColor::Black)<<std::endl;
}
void sequence(std::istringstream& in) {
    int index;
    if (!(in>>index)) throw std::runtime_error("Missing oracle index");
    chess::Game game;
    chess::Options options;
    if (index>=0) {options.variant=ChessVariant::Chess960;options.position=index;}
    game.start(options,0);
    std::string move;
    while (in>>move) if (!game.moveUci(move,0)) throw std::runtime_error("Illegal oracle sequence");
    std::cout<<(game.san.empty()?"-":game.san.back())<<'|'<<chess::fen(game.board)<<std::endl;
}
}
int main() {
    std::string line;
    while (std::getline(std::cin,line)) {
        try {
            std::istringstream in(line);
            std::string mode;in>>mode;
            if (mode=="position") position(in);
            else if (mode=="sequence") sequence(in);
            else throw std::runtime_error("Unknown oracle command");
        } catch (const std::exception& error) {
            std::cout<<"error: "<<error.what()<<std::endl;
        }
    }
}
