// A3-LAN-1: exercise the actual controller, protocol-1 pairing and TCP queues.
// These portable tests do not enable linked LAN or qualify physical hardware.
#define main chess_application_main
#include "../src/main.cpp"
#undef main

#include <map>

#define CHECK(expression) do { if (!(expression)) throw std::runtime_error( \
    std::string(__FILE__)+":"+std::to_string(__LINE__)+": " #expression); } while(false)

namespace {
using Role=chess::LanRole;
using End=chess::LanEndReason;
constexpr uint64_t wrap=uint64_t(1)<<32;

template<class Predicate> void until(Predicate predicate) {
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(3);
    while(!predicate()) {
        if(std::chrono::steady_clock::now()>deadline)
            throw std::runtime_error("LAN terminal TCP fixture timed out");
        // No game time or correctness depends on a wall-clock sleep. Only the
        // kernel's loopback delivery is asynchronous; all policy time is fake.
        std::this_thread::yield();
    }
}

void openPeers(App& host,App& joiner,TimeControl control,bool begin=true) {
    if(begin) {
        CHECK(host.beginLanSession(Role::Host));
        CHECK(joiner.beginLanSession(Role::Joiner));
    }
    host.setup=chess::Options{};host.setup.mode=Mode::Host;host.setup.time=control;
    joiner.setup=chess::Options{};joiner.setup.mode=Mode::Join;
    std::string error;
    if(!host.lan.host(error))throw std::runtime_error("TCP host "+host.dir.string()+": "+error);
    host.networkHost=true;
    host.page="waiting";host.screen=Screen::Network;host.networkStartedAt=1;
    CHECK(joiner.lan.join("127.0.0.1",error));joiner.networkHost=false;
    joiner.page="waiting";joiner.screen=Screen::Network;joiner.networkStartedAt=1;
}
void pairPeers(App& host,App& joiner) {
    until([&]{host.update(2);joiner.update(2);return host.page=="pair"&&joiner.page=="pair";});
    CHECK(host.pairCode==joiner.pairCode);
    CHECK(!host.networkPlaying&&!joiner.networkPlaying);
}
void startPeers(App& host,App& joiner,TimeControl control,bool begin=true) {
    openPeers(host,joiner,control,begin);pairPeers(host,joiner);
    host.menu=0;host.activate();joiner.menu=0;joiner.activate();
    until([&]{host.update(2);joiner.update(2);return host.networkPlaying&&joiner.networkPlaying;});
}
Move legalMove(App& app,const std::string& text) {
    MoveList moves;ChessRules::generateLegal(app.game.board,moves);
    for(int i=0;i<moves.count;++i)if(chess::uci(moves.moves[i])==text)return moves.moves[i];
    throw std::runtime_error("Illegal test move: "+text);
}
void movePeers(App& host,App& joiner,const std::string& text) {
    const auto count=host.game.history.size();
    auto& mover=host.game.board.sideToMove()==PieceColor::White?host:joiner;
    CHECK(mover.apply(legalMove(mover,text)));
    until([&]{host.update(2);joiner.update(2);return host.game.history.size()==count+1&&joiner.game.history.size()==count+1;});
    CHECK(chess::fen(host.game.board)==chess::fen(joiner.game.board));
}
void queuePackets(App& sender,App& receiver,std::initializer_list<std::string> lines) {
    const auto before=receiver.lan.pending_messages();
    for(const auto& line:lines)CHECK(sender.lan.send(line));
    until([&]{sender.lan.poll();receiver.lan.poll();return receiver.lan.pending_messages()>=before+lines.size();});
}
void closePeers(App& host,App& joiner) {
    host.completeLanTeardown();joiner.completeLanTeardown();
    host.lan.close();joiner.lan.close();
}
bool sameClock(const ChessClockSnapshot& a,const ChessClockSnapshot& b) {
    return a.timeControl==b.timeControl&&a.whiteRemainingMs==b.whiteRemainingMs&&
        a.blackRemainingMs==b.blackRemainingMs&&a.activeSide==b.activeSide&&
        a.started==b.started&&a.paused==b.paused&&a.hasFlaggedSide==b.hasFlaggedSide&&
        a.flaggedSide==b.flaggedSide&&a.hasLastAddTimeEvent==b.hasLastAddTimeEvent&&
        a.lastAddTimeEventId==b.lastAddTimeEventId;
}

struct FileState {
    std::filesystem::file_type type;
    std::filesystem::file_time_type modified;
    std::string bytes;
    bool operator==(const FileState& other)const {
        return type==other.type&&modified==other.modified&&bytes==other.bytes;
    }
};
using Tree=std::map<std::string,FileState>;
Tree files(const std::filesystem::path& dir) {
    Tree result;
    result.emplace(".",FileState{std::filesystem::file_type::directory,
        std::filesystem::last_write_time(dir),{}});
    for(const auto& entry:std::filesystem::recursive_directory_iterator(dir)) {
        std::string bytes;
        if(entry.is_regular_file()) {
            std::ifstream input(entry.path(),std::ios::binary);
            bytes.assign(std::istreambuf_iterator<char>(input),{});
        }
        result.emplace(entry.path().lexically_relative(dir).string(),
            FileState{entry.symlink_status().type(),entry.last_write_time(),bytes});
    }
    return result;
}
void offlineSave(App& app) {
    app.setup=chess::Options{};app.setup.mode=Mode::Local;app.startGame();
    CHECK(app.game.moveUci("d2d4",app.clockNow()));CHECK(app.persist());
    CHECK(std::filesystem::exists(app.dir/"active.save"));
}

void setupTermination(const std::filesystem::path& directory) {
    for(bool paired:{false,true})for(bool hostSide:{true,false}) {
        uint64_t now=1000;
        const auto name=std::string(paired?"pair-":"connect-")+(hostSide?"host":"joiner");
        App host(directory/(name+"-h"),-1,false,[&]{return now;});
        App joiner(directory/(name+"-j"),-1,false,[&]{return now;});
        openPeers(host,joiner,TimeControl::Bullet1);
        if(paired)pairPeers(host,joiner);
        auto& app=hostSide?host:joiner;
        const auto id=app.lanTerminal.session_id();
        CHECK(app.sealLanSession(End::ShellPause,now));
        CHECK(app.lanTerminal.sealed()&&app.lanTerminal.teardown_pending());
        CHECK(!app.lanTerminal.interruption().hadMatch);
        CHECK(!app.networkPlaying&&!app.haveGame&&!app.canPlay());
        CHECK(app.commands().empty()&&app.items().empty());
        CHECK(app.commandFooter().find("Read-only")!=std::string::npos);
        // Public status closes immediately; the old descriptors still require
        // cleanup and cannot be reused by the transport's own reset operation.
        CHECK(!app.lan.connected()&&!app.lan.listening());
        CHECK(!app.lan.begin_session());
        CHECK(!app.beginLanSession(hostSide?Role::Host:Role::Joiner));
        app.resumeSession(3);app.activate();app.network();app.update(3);
        CHECK(app.lanTerminal.sealed()&&app.lanTerminal.session_id()==id);
        CHECK(!app.haveGame&&!app.networkPlaying);
        app.completeLanTeardown();app.completeLanTeardown();
        CHECK(!app.lanTerminal.teardown_pending());
        CHECK(!app.lan.connected()&&!app.lan.listening());
        closePeers(host,joiner);
    }
}

void queuedHostRequest(const std::filesystem::path& directory) {
    uint64_t now=1000;
    App host(directory/"req-h",-1,false,[&]{return now;});
    App joiner(directory/"req-j",-1,false,[&]{return now;});
    startPeers(host,joiner,TimeControl::Blitz3);movePeers(host,joiner,"e2e4");
    queuePackets(joiner,host,{"REQ 1 e7e5"});
    const auto before=chess::fen(host.game.board);
    const auto history=host.game.san;
    const auto move=legalMove(host,"e7e5");
    CHECK(host.sealLanSession(End::ShellPause,now));
    CHECK(!host.lan.connected()&&host.lanTerminal.teardown_pending());
    CHECK(!host.lan.begin_session());
    CHECK(!host.canPlay()&&!host.apply(move,true));
    CHECK(host.commands().empty()&&host.items().empty());
    CHECK(host.commandFooter().find("Read-only")!=std::string::npos);
    CHECK(!host.lan.send("PLY 2 e7e5 0 1 1"));
    host.network();host.update(3);host.key(KEY_ENTER);host.activate();
    CHECK(chess::fen(host.game.board)==before&&host.game.san==history);
    CHECK(host.game.result=="*"&&!host.game.clock.isRunning());
    CHECK(!host.networkPlaying&&!host.awaitingMove);
    closePeers(host,joiner);
}

void queuedHostAuthority(const std::filesystem::path& directory) {
    uint64_t now=1000;
    App host(directory/"packets-h",-1,false,[&]{return now;});
    App joiner(directory/"packets-j",-1,false,[&]{return now;});
    startPeers(host,joiner,TimeControl::Bullet1);
    const auto before=chess::fen(joiner.game.board);
    const auto clock=joiner.game.clock.snapshot(joiner.clockNow());
    CHECK(host.apply(legalMove(host,"e2e4")));
    // The ordinary host queued a valid PLY; add an ordinary clock/result packet.
    // Poll TCP only, so none reaches the controller before its terminal latch.
    queuePackets(host,joiner,{"SYNC 1 1 2","END 1-0 Resigned"});
    CHECK(joiner.lan.pending_messages()>=3);
    CHECK(joiner.sealLanSession(End::ShellPause,now));
    joiner.network();joiner.update(3);joiner.resumeSession(3);joiner.network();
    CHECK(chess::fen(joiner.game.board)==before&&joiner.game.history.empty());
    CHECK(joiner.game.result=="*"&&joiner.game.reason.empty());
    CHECK(joiner.game.clock.remainingMs(PieceColor::White)==clock.whiteRemainingMs);
    CHECK(joiner.game.clock.remainingMs(PieceColor::Black)==clock.blackRemainingMs);
    CHECK(!joiner.game.clock.isRunning()&&!joiner.networkPlaying);
    closePeers(host,joiner);
}

void expiryAtSeal(const std::filesystem::path& directory) {
    for(bool fullWrap:{false,true}) {
        uint64_t now=fullWrap?uint64_t(UINT32_MAX)-20:1000;
        const auto name=std::string(fullWrap?"wrap":"boundary");
        App host(directory/(name+"-h"),-1,false,[&]{return now;});
        App joiner(directory/(name+"-j"),-1,false,[&]{return now;});
        startPeers(host,joiner,TimeControl::Blitz3);movePeers(host,joiner,"e2e4");
        CHECK(host.game.clock.setRemainingMs(PieceColor::Black,10,host.clockNow()));
        CHECK(host.admitLanWork());
        queuePackets(joiner,host,{"REQ 1 e7e5"});
        const auto before=chess::fen(host.game.board);
        const auto tree=files(host.dir);
        now+=fullWrap?wrap+10:10;
        if(fullWrap)host.network();
        else CHECK(host.sealLanSession(End::ShellPause,now));
        CHECK(host.lanTerminal.sealed());
        CHECK(host.lanTerminal.interruption().reason==(fullWrap?End::ServiceGap:End::ShellPause));
        CHECK(host.lanTerminal.interruption().observedAtMs==now);
        CHECK(host.game.result=="1-0"&&host.game.reason=="Time expired");
        CHECK(host.game.clock.hasFlaggedSide()&&host.game.clock.flaggedSide()==PieceColor::Black);
        CHECK(host.game.history.size()==1&&chess::fen(host.game.board)==before);
        CHECK(!host.game.clock.isRunning()&&files(host.dir)==tree);
        host.network();host.update(4);
        CHECK(host.game.history.size()==1&&host.game.result=="1-0"&&files(host.dir)==tree);
        closePeers(host,joiner);
    }
}

void noMatingMaterialAtSeal(const std::filesystem::path& directory) {
    uint64_t now=1000;
    App host(directory/"material-h",-1,false,[&]{return now;});
    App joiner(directory/"material-j",-1,false,[&]{return now;});
    startPeers(host,joiner,TimeControl::Bullet1);
    for(int row=0;row<8;++row)for(int col=0;col<8;++col)host.game.board.set(col,row,{});
    host.game.board.set(0,0,{PieceType::King,PieceColor::White});
    host.game.board.set(7,7,{PieceType::King,PieceColor::Black});
    host.game.board.set(7,6,{PieceType::Pawn,PieceColor::Black});
    host.game.board.setSideToMove(PieceColor::Black);host.game.board.setCastleRights(0);
    auto clock=host.game.clock.snapshot(host.clockNow());clock.activeSide=PieceColor::Black;clock.blackRemainingMs=3;
    CHECK(host.game.clock.loadSnapshot(clock,host.clockNow()));CHECK(host.admitLanWork());
    now+=wrap+3;CHECK(host.sealLanSession(End::ShellLost,now));
    CHECK(host.game.result=="1/2-1/2"&&host.game.reason=="Time / no mating material");
    CHECK(host.lanTerminal.interruption().confirmedResult=="1/2-1/2");
    closePeers(host,joiner);
}

void projectedFlagAcrossMultipleWraps(const std::filesystem::path& directory) {
    uint64_t now=1000;
    App host(directory/"projected-flag-h",-1,false,[&]{return now;});
    App joiner(directory/"projected-flag-j",-1,false,[&]{return now;});
    startPeers(host,joiner,TimeControl::Bullet1);
    CHECK(host.game.clock.setRemainingMs(PieceColor::White,2000,host.clockNow()));
    CHECK(host.admitLanWork());now+=2000;CHECK(host.admitLanWork());
    // The const checkpoint can already establish flag fall while the mutable
    // Game has not been updated. A later wrapped timestamp must not lose it.
    CHECK(host.game.result=="*"&&!host.game.clock.hasFlaggedSide());
    CHECK(host.game.clock.snapshot(host.clockNow()).hasFlaggedSide);
    now+=2*wrap-1000;
    const auto tree=files(host.dir);
    CHECK(host.sealLanSession(End::ShellPause,now));
    CHECK(host.game.result=="0-1"&&host.game.reason=="Time expired");
    CHECK(host.game.clock.hasFlaggedSide()&&host.game.clock.flaggedSide()==PieceColor::White);
    CHECK(host.game.history.empty()&&files(host.dir)==tree);
    closePeers(host,joiner);
}

void pendingJoinerUncertainty(const std::filesystem::path& directory) {
    for(bool fullWrap:{false,true}) {
        uint64_t now=1000;
        const auto name=std::string(fullWrap?"pending-wrap":"pending");
        App host(directory/(name+"-h"),-1,false,[&]{return now;});
        App joiner(directory/(name+"-j"),-1,false,[&]{return now;});
        startPeers(host,joiner,TimeControl::Bullet1);movePeers(host,joiner,"e2e4");
        CHECK(host.game.clock.synchronizeRemainingMs(100,50,host.clockNow()));
        queuePackets(host,joiner,{"SYNC 1 100 50"});joiner.network();
        const auto before=chess::fen(joiner.game.board);
        const auto confirmed=joiner.game.clock.snapshot(joiner.clockNow());
        CHECK(joiner.apply(legalMove(joiner,"e7e5"))&&joiner.awaitingMove);
        CHECK(joiner.game.history.size()==1&&chess::fen(joiner.game.board)==before);
        now+=fullWrap?wrap+100:100;
        CHECK(joiner.game.clock.remainingMsAt(PieceColor::Black,joiner.clockNow())==0);
        if(fullWrap)CHECK(!joiner.admitLanWork());
        else CHECK(joiner.sealLanSession(End::ShellPause,now));
        const auto& interruption=joiner.lanTerminal.interruption();
        CHECK(interruption.pendingMoveRequest&&interruption.pendingMove=="e7e5"&&interruption.pendingPly==1);
        CHECK(interruption.pendingQueuedAtMs==1000&&interruption.confirmedResult=="*");
        CHECK(interruption.confirmedReason.empty()&&sameClock(interruption.clock,confirmed));
        CHECK(joiner.game.result=="*"&&joiner.game.reason.empty()&&!joiner.game.clock.hasFlaggedSide());
        CHECK(joiner.game.clock.remainingMs(PieceColor::Black)==50&&!joiner.game.clock.isRunning());
        CHECK(!joiner.awaitingMove&&chess::fen(joiner.game.board)==before);
        closePeers(host,joiner);
    }
}

void completedResults(const std::filesystem::path& directory) {
    uint64_t now=1000;
    App host(directory/"finished-h",-1,false,[&]{return now;});
    App joiner(directory/"finished-j",-1,false,[&]{return now;});
    startPeers(host,joiner,TimeControl::Bullet1);
    for(const auto* text:{"f2f3","e7e5","g2g4","d8h4"})movePeers(host,joiner,text);
    until([&]{host.update(2);joiner.update(2);return joiner.game.reason=="Checkmate";});
    for(auto* app:{&host,&joiner}) {
        const auto before=chess::fen(app->game.board);
        const auto tree=files(app->dir);
        now+=wrap+3;
        CHECK(app->sealLanSession(End::LinkedTermination,now));
        CHECK(app->game.result=="0-1"&&app->game.reason=="Checkmate");
        CHECK(app->lanTerminal.interruption().confirmedResult=="0-1");
        CHECK(app->lanTerminal.interruption().confirmedReason=="Checkmate");
        app->network();app->update(5);
        CHECK(chess::fen(app->game.board)==before&&app->game.history.size()==4);
        CHECK(files(app->dir)==tree);
    }
    closePeers(host,joiner);
}

void validatedHostAuthority(const std::filesystem::path& directory) {
    {
        uint64_t now=1000;
        App host(directory/"bad-ply-h",-1,false,[&]{return now;});
        App joiner(directory/"bad-ply-j",-1,false,[&]{return now;});
        startPeers(host,joiner,TimeControl::Bullet1);
        const auto before=chess::fen(joiner.game.board);
        const auto confirmed=joiner.game.clock.snapshot(joiner.clockNow());
        Game moved=host.game;CHECK(moved.moveUci("e2e4",host.clockNow()));
        const auto wrongHash=ChessZobrist::hash(moved.board)^uint32_t(1);
        queuePackets(host,joiner,{"PLY 1 e2e4 "+std::to_string(wrongHash)+" 1 2"});
        joiner.network();
        CHECK(joiner.lanTerminal.sealed());
        CHECK(joiner.lanTerminal.interruption().reason==End::TransportError);
        CHECK(chess::fen(joiner.game.board)==before&&joiner.game.history.empty());
        CHECK(joiner.game.result=="*"&&sameClock(joiner.lanTerminal.interruption().clock,confirmed));
        closePeers(host,joiner);
    }
    for(bool zero:{false,true}) {
        uint64_t now=1000;
        const auto name=std::string(zero?"zero-sync":"end-clock");
        App host(directory/(name+"-h"),-1,false,[&]{return now;});
        App joiner(directory/(name+"-j"),-1,false,[&]{return now;});
        startPeers(host,joiner,TimeControl::Bullet1);
        queuePackets(host,joiner,{zero?"SYNC 0 0 54321":"SYNC 0 12345 54321"});joiner.network();
        const auto confirmed=joiner.game.clock.snapshot(joiner.clockNow());
        CHECK(joiner.game.result=="*");
        now+=100;joiner.update(3);
        if(!zero) {
            // END decides a result, but carries no clock fields. Its local
            // arrival time must not turn projection into host-confirmed time.
            queuePackets(host,joiner,{"END 1-0 Resigned"});joiner.network();
            CHECK(joiner.game.result=="1-0"&&joiner.game.reason=="Resigned");
        }
        now+=100;CHECK(joiner.sealLanSession(End::ShellPause,now));
        CHECK(sameClock(joiner.lanTerminal.interruption().clock,confirmed));
        CHECK(joiner.game.clock.remainingMs(PieceColor::White)==(zero?0u:12345u));
        CHECK(joiner.game.result==(zero?"*":"1-0"));
        CHECK(joiner.lanTerminal.interruption().confirmedResult==(zero?"*":"1-0"));
        closePeers(host,joiner);
    }
}

void onlyMatchingRequestIsConfirmed(const std::filesystem::path& directory) {
    for(bool differentMove:{false,true}) {
        uint64_t now=1000;
        const auto name=std::string(differentMove?"different-ack":"matching-ack");
        App host(directory/(name+"-h"),-1,false,[&]{return now;});
        App joiner(directory/(name+"-j"),-1,false,[&]{return now;});
        startPeers(host,joiner,TimeControl::None);movePeers(host,joiner,"e2e4");
        CHECK(joiner.apply(legalMove(joiner,"e7e5"))&&joiner.awaitingMove);
        if(differentMove) {
            // A valid host PLY establishes its board, but a different legal
            // move cannot establish that this joiner's request was accepted.
            CHECK(host.apply(legalMove(host,"d7d5"),true));
        } else {
            until([&]{joiner.lan.poll();host.network();return host.game.history.size()==2;});
        }
        until([&]{host.lan.poll();joiner.network();return joiner.game.history.size()==2;});
        CHECK(!joiner.awaitingMove);
        const auto confirmed=chess::fen(joiner.game.board);
        CHECK(joiner.game.san.back()==(differentMove?"d5":"e5"));
        CHECK(joiner.sealLanSession(End::ShellPause,now));
        const auto& interruption=joiner.lanTerminal.interruption();
        CHECK(interruption.pendingMoveRequest==differentMove);
        if(differentMove) {
            CHECK(interruption.pendingMove=="e7e5"&&interruption.pendingPly==1);
            CHECK(interruption.pendingQueuedAtMs==1000);
        } else CHECK(interruption.pendingMove.empty());
        CHECK(chess::fen(joiner.game.board)==confirmed&&joiner.game.history.size()==2);
        closePeers(host,joiner);
    }
}

void setupPreservesOfflineGame(const std::filesystem::path& directory) {
    uint64_t now=1000;
    const auto dir=directory/"loaded-offline";
    {
        App seed(dir,-1,false,[&]{return now;});
        seed.setup.mode=Mode::Local;seed.setup.time=TimeControl::Bullet1;seed.startGame();
        CHECK(seed.game.moveUci("d2d4",seed.clockNow()));CHECK(seed.persist());
    }
    for(bool hostSide:{true,false}) {
        App app(dir,-1,false,[&]{return now;});
        chess::Lan listener;std::string error;
        const auto before=chess::fen(app.game.board);
        const auto tree=files(dir);
        const auto clock=app.game.clock.snapshot(app.clockNow());
        CHECK(app.beginLanSession(hostSide?Role::Host:Role::Joiner));
        if(hostSide)CHECK(app.lan.host(error));
        else {CHECK(listener.host(error));CHECK(app.lan.join("127.0.0.1",error));}
        app.page="waiting";app.screen=Screen::Network;
        now+=30;CHECK(app.sealLanSession(End::ShellPause,now));
        CHECK(!app.lanTerminal.interruption().hadMatch);
        CHECK(app.game.options.mode==Mode::Local&&chess::fen(app.game.board)==before);
        CHECK(sameClock(app.game.clock.snapshot(app.clockNow()),clock));
        CHECK(files(dir)==tree);
        app.completeLanTeardown();listener.close();
    }
    App linked(directory/"linked",-1,true,[&]{return now;});
    CHECK(!linked.beginLanSession(Role::Host)&&!linked.beginLanSession(Role::Joiner));
    linked.menu=4;linked.activate();CHECK(linked.screen==Screen::Home);
    linked.setupMenu(Mode::Host);CHECK(linked.setup.mode!=Mode::Host);
    linked.setup.mode=Mode::Host;linked.startGame();CHECK(!linked.haveGame);
    CHECK(!linked.lan.listening()&&!linked.lan.connected()&&!linked.lanTerminal.active());
}

void persistenceIsDeferred(const std::filesystem::path& directory) {
    uint64_t now=1000;
    App host(directory/"save-h",-1,false,[&]{return now;});
    App joiner(directory/"save-j",-1,false,[&]{return now;});
    offlineSave(host);offlineSave(joiner);
    // Any synchronous archive attempt would fail at this regular-file barrier.
    {std::ofstream out(host.dir/"games");out<<"archive directory unavailable";}
    {std::ofstream out(joiner.dir/"games");out<<"archive directory unavailable";}
    startPeers(host,joiner,TimeControl::Bullet1);
    CHECK(host.game.clock.setRemainingMs(PieceColor::White,1,host.clockNow()));
    host.pendingProgress=host.pendingSettings=true;joiner.pendingProgress=joiner.pendingSettings=true;
    const auto hostTree=files(host.dir),joinerTree=files(joiner.dir);
    const auto hostSave=host.lastSave,joinerSave=joiner.lastSave;
    now+=1;CHECK(host.sealLanSession(End::ShellPause,now));
    CHECK(joiner.sealLanSession(End::ShellLost,now));
    for(auto* app:{&host,&joiner}) {
        app->network();app->suspendSession();app->update(90);
        app->resumeSession(91);app->update(91);app->key(KEY_P);app->key(KEY_T);
        CHECK(app->pendingProgress&&app->pendingSettings);
        CHECK(app->toast.find("Archive failed")==std::string::npos);
    }
    CHECK(host.game.result=="0-1"&&host.game.reason=="Time expired");
    CHECK(host.lastSave==hostSave&&joiner.lastSave==joinerSave);
    CHECK(files(host.dir)==hostTree&&files(joiner.dir)==joinerTree);
    Game offline;std::string error;
    CHECK(offline.load(host.dir/"active.save",host.clockNow(),error));
    CHECK(offline.options.mode==Mode::Local&&offline.history.size()==1&&offline.san.front()=="d4");
    closePeers(host,joiner);
}

void idempotenceAndFreshIdentity(const std::filesystem::path& directory) {
    uint64_t now=1000;
    App host(directory/"fresh-h",-1,false,[&]{return now;});
    App joiner(directory/"fresh-j",-1,false,[&]{return now;});
    startPeers(host,joiner,TimeControl::Bullet1);movePeers(host,joiner,"e2e4");
    CHECK(joiner.apply(legalMove(joiner,"e7e5")));
    queuePackets(joiner,host,{"REQ 1 e7e5"});
    const auto oldHost=host.lanTerminal.session_id(),oldJoin=joiner.lanTerminal.session_id();
    CHECK(host.sealLanSession(End::ShellPause,now));CHECK(joiner.sealLanSession(End::ShellPause,now));
    const auto hostClock=host.lanTerminal.interruption().clock;
    const auto generation=host.generation;
    now+=50;
    for(auto* app:{&host,&joiner}) {
        CHECK(!app->sealLanSession(End::ShellLost,now));
        app->resumeSession(9);app->network();app->update(9);
        CHECK(app->lanTerminal.interruption().reason==End::ShellPause);
        CHECK(app->lanTerminal.interruption().observedAtMs==1000);
        CHECK(!app->beginLanSession(app==&host?Role::Host:Role::Joiner));
    }
    CHECK(host.generation==generation&&sameClock(host.lanTerminal.interruption().clock,hostClock));
    closePeers(host,joiner);
    CHECK(host.beginLanSession(Role::Host)&&joiner.beginLanSession(Role::Joiner));
    CHECK(host.lanTerminal.session_id()!=oldHost&&joiner.lanTerminal.session_id()!=oldJoin);
    CHECK(!host.lanTerminal.sealed()&&!joiner.lanTerminal.sealed());
    CHECK(!host.awaitingMove&&!joiner.awaitingMove&&!host.helloSent&&!joiner.helloSent);
    CHECK(!host.localReady&&!host.remoteReady&&!joiner.localReady&&!joiner.remoteReady);
    CHECK(host.lan.pending_messages()==0&&joiner.lan.pending_messages()==0);
    startPeers(host,joiner,TimeControl::None,false);
    CHECK(host.game.history.empty()&&joiner.game.history.empty());
    CHECK(!host.lanTerminal.interruption().pendingMoveRequest&&!joiner.lanTerminal.interruption().pendingMoveRequest);
    movePeers(host,joiner,"d2d4");
    CHECK(host.game.san.front()=="d4"&&joiner.game.san.front()=="d4");
    closePeers(host,joiner);
}

void serviceGapAndStaleAuthority(const std::filesystem::path& directory) {
    CHECK(chess::LanTerminal::service_gap_limit_ms==10000);
    CHECK(chess::LanTerminal::stale_authority_limit_ms==10000);
    {
        uint64_t now=1000;
        App host(directory/"gap-h",-1,false,[&]{return now;});
        App joiner(directory/"gap-j",-1,false,[&]{return now;});
        startPeers(host,joiner,TimeControl::None);
        now+=9999;CHECK(host.admitLanWork());CHECK(!host.lanTerminal.sealed());
        now+=10000;host.network();
        CHECK(host.lanTerminal.sealed()&&host.lanTerminal.interruption().reason==End::ServiceGap);
        closePeers(host,joiner);
    }
    {
        uint64_t now=1000;
        App host(directory/"setup-gap",-1,false,[&]{return now;});
        CHECK(host.beginLanSession(Role::Host));std::string error;CHECK(host.lan.host(error));
        host.page="waiting";host.screen=Screen::Network;host.networkHost=true;
        now+=10000;host.network();
        CHECK(host.lanTerminal.sealed()&&!host.lanTerminal.interruption().hadMatch);
        CHECK(host.lanTerminal.interruption().reason==End::ServiceGap);
        host.completeLanTeardown();
    }
    {
        uint64_t now=1000;
        App host(directory/"stale-h",-1,false,[&]{return now;});
        App joiner(directory/"stale-j",-1,false,[&]{return now;});
        startPeers(host,joiner,TimeControl::Bullet1);
        const auto confirmed=joiner.game.clock.snapshot(joiner.clockNow());
        for(const auto offset:{2000,4000,6000,8000,9999}) {
            now=1000+offset;joiner.update(2+offset/1000.0);CHECK(!joiner.lanTerminal.sealed());
        }
        const auto before=chess::fen(joiner.game.board);
        queuePackets(host,joiner,{"SYNC 0 60000 60000","END 1-0 Resigned"});
        now=11000;joiner.network();
        CHECK(joiner.lanTerminal.sealed()&&joiner.lanTerminal.interruption().reason==End::StaleAuthority);
        CHECK(joiner.lanTerminal.interruption().observedAtMs==11000);
        CHECK(sameClock(joiner.lanTerminal.interruption().clock,confirmed));
        CHECK(joiner.game.result=="*"&&chess::fen(joiner.game.board)==before);
        closePeers(host,joiner);
    }
    {
        // Existing ordinary unlinked peers have no new silent-gap policy.
        uint64_t now=1000;
        App host(directory/"ordinary-h",-1,false,[&]{return now;});
        App joiner(directory/"ordinary-j",-1,false,[&]{return now;});
        startPeers(host,joiner,TimeControl::None,false);
        now+=wrap+10000;host.update(3);joiner.update(3);
        CHECK(host.networkPlaying&&joiner.networkPlaying);
        CHECK(!host.lanTerminal.active()&&!joiner.lanTerminal.active());
        CHECK(!host.lanTerminal.sealed()&&!joiner.lanTerminal.sealed());
        host.lan.close();joiner.lan.close();
    }
}

void outboundAdmissionBoundary(const std::filesystem::path& directory) {
    {
        uint64_t now=1000;bool armed=false;unsigned reads=0;
        App host(directory/"outbound-hello-h",-1,false,[&]{
            return armed&&++reads>=3?now+10000:now;
        });
        App joiner(directory/"outbound-hello-j",-1,false,[&]{return now;});
        openPeers(host,joiner,TimeControl::None);
        until([&]{host.lan.poll();joiner.lan.poll();return host.lan.connected()&&joiner.lan.connected();});
        // network() first admits its pre/post-poll work, then the monotonic
        // observation crosses the limit while sendLan() guards HELLO itself.
        armed=true;host.network();
        CHECK(host.lanTerminal.sealed()&&!host.helloSent&&!host.networkPlaying);
        CHECK(host.page=="disconnected"&&host.lanTerminal.interruption().reason==End::ServiceGap);
        CHECK(!host.lanTerminal.interruption().hadMatch);
        joiner.lan.poll();CHECK(joiner.lan.receive().empty());
        closePeers(host,joiner);
    }
    {
        uint64_t now=1000;bool armed=false;unsigned reads=0;
        App host(directory/"outbound-draw-h",-1,false,[&]{return now;});
        App joiner(directory/"outbound-draw-j",-1,false,[&]{
            return armed&&++reads>=2?now+10000:now;
        });
        startPeers(host,joiner,TimeControl::None);
        joiner.incomingDrawOffer=true;joiner.screen=Screen::Confirm;
        joiner.dialog="accept-draw";joiner.menu=1;
        armed=true;joiner.activate();
        CHECK(joiner.lanTerminal.sealed()&&joiner.game.result=="*");
        CHECK(joiner.status.find("service gap")!=std::string::npos);
        CHECK(joiner.status.find("Draw accepted")==std::string::npos);
        CHECK(!joiner.incomingDrawOffer&&!joiner.networkPlaying);
        closePeers(host,joiner);
    }
}

void checkpointCannotHideServiceGap(const std::filesystem::path& directory) {
    for(bool fullWrap:{false,true}) {
        uint64_t now=1000;
        const auto name=std::string(fullWrap?"checkpoint-wrap":"checkpoint-gap");
        App host(directory/(name+"-h"),-1,false,[&]{return now;});
        App joiner(directory/(name+"-j"),-1,false,[&]{return now;});
        startPeers(host,joiner,TimeControl::Blitz3);movePeers(host,joiner,"e2e4");
        CHECK(host.game.clock.setRemainingMs(PieceColor::Black,10,host.clockNow()));
        CHECK(host.admitLanWork());
        const auto before=chess::fen(host.game.board);
        const auto tree=files(host.dir);
        now+=fullWrap?wrap+10:10000;
        // update() calls this after its other potentially deferred work. That
        // final observation must test the gap before refreshing its baseline.
        host.checkpointLanHost(now);
        CHECK(host.lanTerminal.sealed()&&host.lanTerminal.interruption().reason==End::ServiceGap);
        CHECK(host.lanTerminal.interruption().observedAtMs==now);
        CHECK(host.game.result=="1-0"&&host.game.reason=="Time expired");
        CHECK(host.game.history.size()==1&&chess::fen(host.game.board)==before);
        CHECK(files(host.dir)==tree);
        closePeers(host,joiner);
    }
}

void postMoveCheckpointTracksNewSide(const std::filesystem::path& directory) {
    for(bool fullWrap:{false,true}) {
        uint64_t now=1000;bool armed=false;unsigned reads=0;
        const uint64_t gap=fullWrap?wrap+10000:10000;
        const auto name=std::string(fullWrap?"move-side-wrap":"move-side-gap");
        App host(directory/(name+"-h"),-1,false,[&]{
            return armed&&++reads>=2?now+gap:now;
        });
        App joiner(directory/(name+"-j"),-1,false,[&]{return now;});
        startPeers(host,joiner,TimeControl::Bullet1);
        CHECK(host.game.clock.synchronizeRemainingMs(1000,5000,host.clockNow()));
        CHECK(host.admitLanWork());
        const auto move=legalMove(host,"e2e4");
        const auto tree=files(host.dir);
        // apply() admits White's move at t=1000. Its next real clock read is
        // broadcast admission, after the move has activated Black's clock.
        // Sealing must settle against that new side, including a full wrap.
        armed=true;CHECK(host.apply(move));
        CHECK(host.lanTerminal.sealed()&&host.lanTerminal.interruption().reason==End::ServiceGap);
        CHECK(host.lanTerminal.interruption().observedAtMs==now+gap);
        CHECK(host.game.history.size()==1&&host.game.san.back()=="e4");
        CHECK(host.game.board.sideToMove()==PieceColor::Black);
        CHECK(host.game.result=="1-0"&&host.game.reason=="Time expired");
        CHECK(host.game.clock.hasFlaggedSide()&&host.game.clock.flaggedSide()==PieceColor::Black);
        CHECK(host.game.clock.remainingMs(PieceColor::White)==1000);
        CHECK(host.game.clock.remainingMs(PieceColor::Black)==0);
        CHECK(host.lanTerminal.interruption().confirmedResult=="1-0");
        CHECK(!host.networkPlaying&&!host.game.clock.isRunning()&&files(host.dir)==tree);
        CHECK(joiner.game.history.empty()&&joiner.game.result=="*");
        closePeers(host,joiner);
    }
}

void startCheckpointPrecedesOutboundAdmission(const std::filesystem::path& directory) {
    for(bool fullWrap:{false,true}) {
        uint64_t now=1000;bool armed=false;App* observedHost=nullptr;
        const uint64_t gap=fullWrap?wrap+10000:10000;
        const auto name=std::string(fullWrap?"start-wrap":"start-gap");
        App host(directory/(name+"-h"),-1,false,[&]{
            return armed&&observedHost&&observedHost->networkPlaying&&
                observedHost->game.clock.hasStarted()?now+gap:now;
        });
        observedHost=&host;
        App joiner(directory/(name+"-j"),-1,false,[&]{return now;});
        openPeers(host,joiner,TimeControl::Blitz3);pairPeers(host,joiner);
        const auto hostTree=files(host.dir),joinerTree=files(joiner.dir);
        host.menu=0;host.activate();joiner.menu=0;joiner.activate();
        // Pairing observes t=1000 until the host creates the new timed Game.
        // The first outbound START admission then observes the service gap.
        armed=true;
        until([&]{joiner.lan.poll();host.network();return host.lanTerminal.sealed();});
        CHECK(host.lanTerminal.interruption().reason==End::ServiceGap);
        CHECK(host.lanTerminal.interruption().observedAtMs==now+gap);
        CHECK(host.lanTerminal.interruption().hadMatch&&host.game.history.empty());
        CHECK(host.game.clock.remainingMs(PieceColor::Black)==180000);
        if(fullWrap) {
            CHECK(host.game.result=="0-1"&&host.game.reason=="Time expired");
            CHECK(host.game.clock.hasFlaggedSide()&&host.game.clock.flaggedSide()==PieceColor::White);
            CHECK(host.game.clock.remainingMs(PieceColor::White)==0);
        } else {
            CHECK(host.game.result=="*"&&!host.game.clock.hasFlaggedSide());
            CHECK(host.game.clock.remainingMs(PieceColor::White)==170000);
        }
        CHECK(!host.game.clock.isRunning()&&!host.networkPlaying);
        // Exercise the receiving controller: failed START admission cannot
        // establish a match at the joiner or invent a confirmed remote result.
        joiner.network();
        CHECK(!joiner.haveGame&&!joiner.networkPlaying);
        CHECK(joiner.sealLanSession(End::ShellPause,now));
        CHECK(!joiner.lanTerminal.interruption().hadMatch);
        CHECK(joiner.lanTerminal.interruption().confirmedResult=="*");
        CHECK(files(host.dir)==hostTree&&files(joiner.dir)==joinerTree);
        closePeers(host,joiner);
    }
}

#ifdef TYRELL_CHESS_TERMINAL_RENDER_TEST
void endedRender(App& app,tyrell::game::Runtime& runtime,
                 const std::filesystem::path& imagePath,bool hadMatch) {
    std::vector<chessui::TextBounds> bounds;
    runtime.begin_frame();app.draw(20,&bounds);runtime.end_frame();
    CHECK(app.view.sessionEnded&&!app.view.clocksRunning&&app.view.menu.empty());
    CHECK(app.view.title=="CONNECTION ENDED");
    CHECK(app.view.authorityNotice=="Remote unconfirmed");
    CHECK(app.view.footer.find("Read-only")!=std::string::npos);
    CHECK(!bounds.empty());
    for(const auto& text:bounds) {
        CHECK(text.x>=0&&text.y>=0&&text.width>=0&&text.height>0);
        CHECK(text.x+text.width<=320&&text.y+text.height<=170);
        CHECK(text.width<=text.maximumWidth);
        CHECK(text.original.find("Your next good move")==std::string::npos);
        CHECK(text.original.find("starts here.")==std::string::npos);
        CHECK(text.original.find("White to move")==std::string::npos);
        CHECK(text.original.find("Black to move")==std::string::npos);
        CHECK(text.original.find("ENTER move")==std::string::npos);
        CHECK(text.original.find("ENTER play")==std::string::npos);
    }
    auto fullyRendered=[&](const std::string& value) {
        CHECK(!value.empty());
        const auto found=std::find_if(bounds.begin(),bounds.end(),[&](const auto& text){return text.original==value;});
        if(found==bounds.end()||found->rendered!=value)
            throw std::runtime_error("Essential ended-session text missing or clipped: "+value);
    };
    fullyRendered(app.view.title);fullyRendered(app.view.status);
    fullyRendered(app.view.authorityNotice);fullyRendered(app.view.footer);
    if(!hadMatch) {
        CHECK(app.view.subtitle=="No match established");fullyRendered(app.view.subtitle);
    }
    runtime.screenshot(imagePath.string());
    Image image=LoadImage(imagePath.string().c_str());CHECK(image.data!=nullptr);
    CHECK(image.width==320&&image.height==170);
    if(hadMatch) {
        const Color inactive{221,224,211,255};
        for(const int y:{23,48}) {
            const auto pixel=GetImageColor(image,170,y);
            CHECK(pixel.r==inactive.r&&pixel.g==inactive.g&&pixel.b==inactive.b);
        }
    }
    UnloadImage(image);
}
void renderedEndedSessions(const std::filesystem::path& directory) {
    tyrell::game::RuntimeOptions options;options.backend=tyrell::game::Backend::Headless;options.fps=0;
    tyrell::game::Runtime runtime(options);
    const auto images=directory/"ended-renders";std::filesystem::create_directories(images);
    for(bool hadMatch:{false,true})for(bool hostSide:{true,false}) {
        uint64_t now=1000;
        const auto name=std::string(hadMatch?"active-":"setup-")+(hostSide?"host":"joiner");
        App host(directory/("render-"+name+"-h"),-1,false,[&]{return now;});
        App joiner(directory/("render-"+name+"-j"),-1,false,[&]{return now;});
        const auto control=hostSide?TimeControl::Bullet1:TimeControl::None;
        if(hadMatch)startPeers(host,joiner,control);
        else {openPeers(host,joiner,control);pairPeers(host,joiner);}
        auto& app=hostSide?host:joiner;
        CHECK(app.sealLanSession(End::ShellPause,now));
        CHECK(!app.game.over());
        endedRender(app,runtime,images/(name+".png"),hadMatch);
        closePeers(host,joiner);
    }
}
#endif
}

int main() {
    const auto directory=std::filesystem::temp_directory_path()/
        ("tyrell-chess-lan-terminal-"+std::to_string(monotonicMs()));
    try {
        setupTermination(directory);queuedHostRequest(directory);queuedHostAuthority(directory);
        expiryAtSeal(directory);noMatingMaterialAtSeal(directory);projectedFlagAcrossMultipleWraps(directory);
        pendingJoinerUncertainty(directory);
        completedResults(directory);validatedHostAuthority(directory);onlyMatchingRequestIsConfirmed(directory);
        setupPreservesOfflineGame(directory);
        persistenceIsDeferred(directory);idempotenceAndFreshIdentity(directory);
        serviceGapAndStaleAuthority(directory);outboundAdmissionBoundary(directory);
        checkpointCannotHideServiceGap(directory);postMoveCheckpointTracksNewSide(directory);
        startCheckpointPrecedesOutboundAdmission(directory);
#ifdef TYRELL_CHESS_TERMINAL_RENDER_TEST
        renderedEndedSessions(directory);
#endif
        std::filesystem::remove_all(directory);
        std::puts("LAN terminal passed: real App/TCP setup, queues, clocks, authority, persistence, teardown and fresh sessions. Physical testing pending.");
        return 0;
    } catch(const std::exception& error) {
        std::filesystem::remove_all(directory);std::fprintf(stderr,"%s\n",error.what());return 1;
    }
}
