// Exercise real menu/controller transitions without opening a display. Including
// the app entry point keeps its internal controller private to production.
#define main chess_application_main
#include "../src/main.cpp"
#undef main

#define CHECK(expression) do { if (!(expression)) throw std::runtime_error( \
    std::string(__FILE__) + ":" + std::to_string(__LINE__) + ": " #expression); } while (false)

namespace {
template<class Predicate> void until(Predicate predicate) {
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
    while(!predicate()) {
        if(std::chrono::steady_clock::now()>deadline)throw std::runtime_error("Controller test timeout");
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
}
void localGame(App& app) {app.setup=chess::Options{};app.setup.mode=Mode::Local;app.startGame();}
void saveAndHome(App& app) {app.key(KEY_ESCAPE);CHECK(app.page=="pause");app.menu=1;app.activate();CHECK(app.screen==Screen::Home);}

void keyboardAndIsolation(const std::filesystem::path& directory) {
    App app(directory/"offline");localGame(app);
    // The real keyboard flow selects e2 then jumps to legal e4 with Right.
    app.key(KEY_ENTER);CHECK(app.selected==Square(4,1));
    app.key(KEY_SPACE);CHECK(app.cursor==Square(4,3));app.key(KEY_ENTER);
    CHECK(app.game.history.size()==1&&app.game.san.back()=="e4");
    const auto position=chess::fen(app.game.board);
    saveAndHome(app);
    Game saved;std::string error;CHECK(saved.load(app.dir/"active.save",nowMs(),error));
    CHECK(chess::fen(saved.board)==position);

    app.menu=3;app.activate();CHECK(app.page=="puzzles");app.menu=0;app.activate();CHECK(app.game.options.mode==Mode::Puzzle);
    CHECK(app.screen==Screen::Board);app.key(KEY_H);CHECK(app.hintFrom.valid());
    saveAndHome(app);
    CHECK(saved.load(app.dir/"active.save",nowMs(),error));CHECK(chess::fen(saved.board)==position);
    app.menu=0;app.activate();CHECK(app.screen==Screen::Board);
    CHECK(app.game.options.mode==Mode::Local&&chess::fen(app.game.board)==position);
    saveAndHome(app);

    Game completed;chess::Options options;options.mode=Mode::Local;completed.start(options,nowMs());
    CHECK(completed.moveUci("d2d4",nowMs()));completed.finish("0-1","Resigned",nowMs());
    CHECK(completed.save(app.dir/"games"/"test.save",nowMs(),error));
    app.menu=5;app.activate();CHECK(app.page=="library"&&app.archives.size()==1);
    app.menu=0;app.activate();CHECK(app.review&&app.game.over());
    app.key(KEY_LEFT);CHECK(app.reviewPly==0);
    app.key(KEY_ESCAPE);CHECK(app.screen==Screen::Home);
    CHECK(saved.load(app.dir/"active.save",nowMs(),error));CHECK(chess::fen(saved.board)==position);
    app.menu=0;app.activate();CHECK(app.game.options.mode==Mode::Local&&chess::fen(app.game.board)==position);

    // Promotion choices go through the same confirmation UI as physical keys.
    localGame(app);
    for(int row=0;row<8;++row)for(int col=0;col<8;++col)app.game.board.set(col,row,{});
    app.game.board.setCastleRights(0);app.game.board.set(7,0,{PieceType::King,PieceColor::White});
    app.game.board.set(7,7,{PieceType::King,PieceColor::Black});app.game.board.set(0,6,{PieceType::Pawn,PieceColor::White});
    app.cursor={0,6};app.key(KEY_ENTER);app.key(KEY_RIGHT);app.key(KEY_ENTER);
    CHECK(app.screen==Screen::Promotion);app.key(KEY_RIGHT);app.key(KEY_ENTER);
    CHECK(app.game.board.at(0,7).type==PieceType::Knight&&app.screen==Screen::Board);
}

void workerHome(const std::filesystem::path& directory) {
    App app(directory/"worker");app.setup=chess::Options{};app.setup.human=PieceColor::Black;
    app.startGame();const auto before=chess::fen(app.game.board);app.update(1);
    CHECK(app.worker.valid());app.home();app.worker.wait();app.update(2);
    CHECK(app.screen==Screen::Home&&chess::fen(app.game.board)==before);
}

void confirmations(const std::filesystem::path& directory) {
    App app(directory/"confirmation");localGame(app);
    app.pauseMenu();app.menu=4;app.activate();CHECK(app.screen==Screen::Confirm&&!app.game.over());
    app.menu=0;app.activate();CHECK(app.screen==Screen::Board&&!app.game.over());
    app.pauseMenu();app.menu=5;app.activate();CHECK(app.screen==Screen::Confirm&&!app.game.over());
    app.key(KEY_ESCAPE);CHECK(app.screen==Screen::Board&&!app.game.over());
    app.pauseMenu();app.menu=5;app.activate();app.menu=1;app.activate();
    CHECK(app.game.result=="1/2-1/2"&&app.game.reason=="Draw agreed");
    app.setup=chess::Options{};app.startGame();CHECK(app.game.moveUci("e2e4",nowMs()));
    CHECK(app.game.board.sideToMove()==PieceColor::Black);
    app.pauseMenu();app.menu=4;app.activate();app.menu=1;app.activate();
    CHECK(app.game.result=="0-1"&&app.game.reason=="Resigned");
}

void connectApps(App& host,App& joiner,TimeControl control,ChessVariant variant=ChessVariant::Standard,uint16_t position=518) {
    host.setup=chess::Options{};host.setup.mode=Mode::Host;host.setup.time=control;host.setup.variant=variant;host.setup.position=position;
    std::string error;CHECK(host.lan.host(error));host.networkHost=true;
    host.page="waiting";host.screen=Screen::Network;host.networkStartedAt=1;
    CHECK(joiner.lan.join("127.0.0.1",error));joiner.networkHost=false;
    joiner.page="waiting";joiner.screen=Screen::Network;joiner.networkStartedAt=1;
    until([&]{host.update(2);joiner.update(2);return host.page=="pair"&&joiner.page=="pair";});
    CHECK(host.pairCode==joiner.pairCode);
    host.menu=0;host.activate();joiner.menu=0;joiner.activate();
    until([&]{host.update(2);joiner.update(2);return host.networkPlaying&&joiner.networkPlaying;});
}
void onlineMove(App& host,App& joiner,const std::string& text) {
    const auto count=host.game.history.size();
    App& mover=host.game.board.sideToMove()==PieceColor::White?host:joiner;
    MoveList moves;ChessRules::generateLegal(mover.game.board,moves);
    bool applied=false;for(int i=0;i<moves.count;++i)if(chess::uci(moves.moves[i])==text)applied=mover.apply(moves.moves[i]);CHECK(applied);
    until([&]{host.update(2);joiner.update(2);return host.game.history.size()==count+1&&joiner.game.history.size()==count+1;});
    CHECK(chess::fen(host.game.board)==chess::fen(joiner.game.board));
}
void networkGame(const std::filesystem::path& directory) {
    App host(directory/"host"),joiner(directory/"joiner");
    connectApps(host,joiner,TimeControl::Blitz3);
    CHECK(host.game.clock.activeSide()==PieceColor::White&&joiner.game.clock.activeSide()==PieceColor::White);
    onlineMove(host,joiner,"e2e4");
    CHECK(host.game.clock.activeSide()==PieceColor::Black&&joiner.game.clock.activeSide()==PieceColor::Black);
    onlineMove(host,joiner,"e7e5");
    CHECK(host.game.clock.activeSide()==PieceColor::White&&joiner.game.clock.activeSide()==PieceColor::White);
    CHECK(!std::filesystem::exists(host.dir/"active.save")&&!std::filesystem::exists(joiner.dir/"active.save"));
    joiner.lan.close();until([&]{host.update(2);return host.page=="disconnected";});
    CHECK(!host.game.clock.isRunning());
}
void networkEndings(const std::filesystem::path& directory) {
    {
        App host(directory/"mate-host"),joiner(directory/"mate-joiner");
        connectApps(host,joiner,TimeControl::None);
        for(const auto* text:{"f2f3","e7e5","g2g4","d8h4"})onlineMove(host,joiner,text);
        until([&]{host.update(2);joiner.update(2);return host.game.over()&&joiner.game.over();});
        CHECK(host.game.result=="0-1"&&joiner.game.result=="0-1");
        CHECK(host.game.reason=="Checkmate"&&joiner.game.reason=="Checkmate");
        CHECK(host.screen==Screen::Board&&joiner.screen==Screen::Board);
        CHECK(!host.game.clock.isRunning()&&!joiner.game.clock.isRunning());
    }
    {
        App host(directory/"flag-host"),joiner(directory/"flag-joiner");
        connectApps(host,joiner,TimeControl::Blitz3);
        CHECK(host.game.clock.setRemainingMs(PieceColor::White,1,nowMs()));
        std::this_thread::sleep_for(std::chrono::milliseconds(3));
        until([&]{host.update(2);joiner.update(2);return joiner.game.over();});
        CHECK(host.game.result=="0-1"&&joiner.game.result=="0-1");
        CHECK(host.game.reason=="Time expired"&&joiner.game.reason=="Time expired");
        CHECK(!host.game.clock.isRunning()&&!joiner.game.clock.isRunning());
    }
    {
        App host(directory/"invalid-host"),joiner(directory/"invalid-joiner");
        connectApps(host,joiner,TimeControl::None);
        CHECK(joiner.lan.send("REQ 0 e7e5"));
        until([&]{joiner.update(2);host.update(2);return host.page=="disconnected";});
        CHECK(host.game.history.empty());CHECK(host.status=="Invalid peer move sequence");
    }
    {
        App host(directory/"pair-timeout");std::string error;
        CHECK(host.lan.host(error));host.networkHost=true;host.page="waiting";host.networkStartedAt=1;
        host.update(92);CHECK(host.page=="disconnected"&&host.status=="Pairing timed out");
    }
}

void networkAgreement(const std::filesystem::path& directory) {
    for(bool hostOffers:{true,false}) {
        App host(directory/(hostOffers?"host-offer-host":"join-offer-host"));
        App joiner(directory/(hostOffers?"host-offer-join":"join-offer-join"));
        connectApps(host,joiner,TimeControl::Blitz3);
        App& offerer=hostOffers?host:joiner;
        App& accepter=hostOffers?joiner:host;
        offerer.pauseMenu();offerer.menu=5;offerer.activate();
        until([&]{host.update(2);joiner.update(2);return accepter.incomingDrawOffer;});
        accepter.key(KEY_Y);accepter.key(KEY_DOWN);accepter.key(KEY_ENTER);
        until([&]{host.update(2);joiner.update(2);return host.game.over()&&joiner.game.over();});
        CHECK(host.game.result=="1/2-1/2"&&joiner.game.result=="1/2-1/2");
        CHECK(host.screen==Screen::Board&&joiner.screen==Screen::Board);
    }
    {
        App host(directory/"unsolicited-host"),joiner(directory/"unsolicited-join");
        connectApps(host,joiner,TimeControl::None);CHECK(joiner.lan.send("ACCEPTDRAW"));
        for(int n=0;n<10;++n){host.update(2);joiner.update(2);std::this_thread::sleep_for(std::chrono::milliseconds(1));}
        CHECK(host.networkPlaying&&joiner.networkPlaying);
        CHECK(!host.game.over()&&!joiner.game.over()&&host.game.history.empty()&&joiner.game.history.empty());
    }
    {
        App host(directory/"resign-host"),joiner(directory/"resign-join");
        connectApps(host,joiner,TimeControl::None);
        joiner.pauseMenu();joiner.menu=4;joiner.activate();CHECK(!joiner.game.over());
        joiner.menu=1;joiner.activate();
        until([&]{host.update(2);joiner.update(2);return host.game.over()&&joiner.game.over();});
        CHECK(host.game.result=="1-0"&&joiner.game.result=="1-0");
        CHECK(host.game.reason=="Resigned"&&joiner.game.reason=="Resigned");
    }
}

void staleDrawOffer(const std::filesystem::path& directory) {
    for(bool hostOffers:{true,false}) {
        App host(directory/(hostOffers?"stale-host-offers-h":"stale-join-offers-h"));
        App joiner(directory/(hostOffers?"stale-host-offers-j":"stale-join-offers-j"));
        connectApps(host,joiner,TimeControl::None);
        App& offerer=hostOffers?host:joiner;
        App& accepter=hostOffers?joiner:host;
        offerer.pauseMenu();offerer.menu=5;offerer.activate();
        until([&]{host.update(2);joiner.update(2);return accepter.incomingDrawOffer;});
        onlineMove(host,joiner,"e2e4");
        CHECK(!host.drawOffered&&!joiner.drawOffered);
        CHECK(host.status.empty()&&joiner.status.empty());
        // The now ordinary Right/D key must not accept an expired offer or
        // cause either side to reject a phantom ACCEPTDRAW packet.
        accepter.key(KEY_D);
        for(int i=0;i<5;++i){host.update(2);joiner.update(2);std::this_thread::sleep_for(std::chrono::milliseconds(1));}
        CHECK(host.networkPlaying&&joiner.networkPlaying);
        CHECK(!host.game.over()&&!joiner.game.over());
    }
}
void aiAcrossOverlays(const std::filesystem::path& directory) {
    for(bool help:{true,false}) {
        App app(directory/(help?"ai-help":"ai-pause"));
        app.setup=chess::Options{};app.setup.human=PieceColor::Black;
        app.setup.time=TimeControl::Bullet1;app.startGame();
        app.update(1);CHECK(app.worker.valid());
        if(help)app.key(KEY_I);else app.pauseMenu();
        const auto overlay=app.screen;
        app.worker.wait();app.update(2);
        CHECK(app.game.history.size()==1);
        CHECK(app.game.clock.activeSide()==PieceColor::Black);
        CHECK(app.screen==overlay&&!app.game.over());
    }
}


void moveAtFlagFall(const std::filesystem::path& directory) {
    for(bool flagBeforeRequest:{true,false}) {
        App host(directory/(flagBeforeRequest?"flag-before-request-h":"flag-in-request-h"));
        App joiner(directory/(flagBeforeRequest?"flag-before-request-j":"flag-in-request-j"));
        connectApps(host,joiner,TimeControl::Blitz3);
        onlineMove(host,joiner,"e2e4");
        CHECK(host.game.clock.setRemainingMs(PieceColor::Black,1,nowMs()));
        std::this_thread::sleep_for(std::chrono::milliseconds(3));
        if(flagBeforeRequest){host.update(2);CHECK(host.game.over());}
        CHECK(joiner.lan.send("REQ 1 e7e5"));joiner.lan.poll();
        std::this_thread::sleep_for(std::chrono::milliseconds(3));
        until([&]{host.update(2);joiner.update(2);CHECK(host.page!="disconnected"&&joiner.page!="disconnected");return host.game.over()&&joiner.game.over();});
        CHECK(host.game.result=="1-0"&&joiner.game.result=="1-0");
        CHECK(host.game.reason=="Time expired"&&joiner.game.reason=="Time expired");
        CHECK(host.game.history.size()==1&&joiner.game.history.size()==1);
    }
}

void pendingMoveInput(const std::filesystem::path& directory) {
    App host(directory/"pending-input-h"),joiner(directory/"pending-input-j");
    connectApps(host,joiner,TimeControl::None);onlineMove(host,joiner,"e2e4");
    // Model congested Wi-Fi: the UI gets two ordinary select/destination/confirm
    // sequences before the host's acknowledgement has reached the joiner.
    for(int attempt=0;attempt<2;++attempt){joiner.cursor={4,6};joiner.key(KEY_ENTER);joiner.key(KEY_SPACE);joiner.key(KEY_ENTER);}
    until([&]{host.update(2);joiner.update(2);CHECK(host.page!="disconnected"&&joiner.page!="disconnected");return host.game.history.size()==2&&joiner.game.history.size()==2;});
    CHECK(chess::fen(host.game.board)==chess::fen(joiner.game.board));
    onlineMove(host,joiner,"g1f3");onlineMove(host,joiner,"b8c6");
    CHECK(host.networkPlaying&&joiner.networkPlaying);
}

void networkChess960Castle(const std::filesystem::path& directory) {
    App host(directory/"castle-960-h"),joiner(directory/"castle-960-j");
    connectApps(host,joiner,TimeControl::None,ChessVariant::Chess960,12);
    for(auto text:{"h2h3","a7a6","g1h2","a6a5","f1g1c"})onlineMove(host,joiner,text);
    CHECK(host.game.history.back().move.isCastle&&joiner.game.history.back().move.isCastle);
    CHECK(host.game.board.at(5,0).type==PieceType::Rook&&joiner.game.board.at(5,0).type==PieceType::Rook);
    CHECK(host.game.san.back()=="O-O"&&joiner.game.san.back()=="O-O");
    CHECK(chess::fen(host.game.board)==chess::fen(joiner.game.board));
}

void cursorFocus(const std::filesystem::path& directory) {
    App computer(directory/"cursor-ai");computer.setup.human=PieceColor::Black;computer.startGame();
    CHECK((computer.cursor==Square{4,6}));
    MoveList moves;ChessRules::generateLegal(computer.game.board,moves);
    for(int i=0;i<moves.count;++i)if(chess::uci(moves.moves[i])=="e2e4")CHECK(computer.apply(moves.moves[i]));
    CHECK((computer.cursor==Square{4,6}));
    App host(directory/"cursor-host"),joiner(directory/"cursor-join");connectApps(host,joiner,TimeControl::None);
    CHECK((joiner.cursor==Square{4,6}));
    MoveList legal;ChessRules::generateLegal(host.game.board,legal);for(int i=0;i<legal.count;++i)if(chess::uci(legal.moves[i])=="e2e4")CHECK(host.apply(legal.moves[i]));until([&]{host.update(2);joiner.update(2);return joiner.game.history.size()==1;});
    CHECK((joiner.cursor==Square{4,6}));
}

}
int main() {
    const auto directory=std::filesystem::temp_directory_path()/
        ("tyrell-chess-controller-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    try {
        keyboardAndIsolation(directory);workerHome(directory);confirmations(directory);networkGame(directory);networkEndings(directory);networkAgreement(directory);staleDrawOffer(directory);aiAcrossOverlays(directory);moveAtFlagFall(directory);pendingMoveInput(directory);cursorFocus(directory);networkChess960Castle(directory);
        std::filesystem::remove_all(directory);
        std::puts("Chess controller passed: keyboard, promotion, isolation, workers, confirmations, TCP pairing/moves, mate/timeout, draw/resign, malformed requests, disconnect.");
        return 0;
    } catch(const std::exception& error) {
        std::filesystem::remove_all(directory);std::fprintf(stderr,"%s\n",error.what());return 1;
    }
}
