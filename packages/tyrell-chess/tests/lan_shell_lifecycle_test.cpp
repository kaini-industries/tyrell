// A3-LAN-2: controlled C++ opt-in drives the production loop with real TCP and
// the actual credential-bound shell link. Native syscalls use inert fixtures.
// This does not open linked LAN admission or qualify physical hardware.
#define main chess_application_main
#include "../src/main.cpp"
#undef main

#include "native_io_fixture.hpp"
#include <csignal>
#include <fcntl.h>
#include <iterator>
#include <map>
#include <sys/socket.h>
#include <unistd.h>

#define REQUIRE(value) do {if(!(value))throw std::runtime_error( \
    std::string(__FILE__)+":"+std::to_string(__LINE__)+": " #value);} while(false)

namespace {
using Role=chess::LanRole;
using End=chess::LanEndReason;
namespace io=tyrell::game::test;

template<class Predicate> void until(Predicate predicate) {
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(3);
    while(!predicate()) {
        REQUIRE(std::chrono::steady_clock::now()<deadline);
        std::this_thread::yield(); // Kernel delivery only; game time is injected.
    }
}

struct ShellPair {
    int peer=-1,endpoint=-1;
    ShellPair() {
        int descriptors[2];
        REQUIRE(socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0,descriptors)==0);
        peer=descriptors[0];endpoint=descriptors[1];
    }
    ~ShellPair() {
        if(peer>=0)::close(peer);
        if(endpoint>=0)::close(endpoint);
        unsetenv("GH0ST_SHELL_FD");
    }
    void send(const std::string& packet) {
        REQUIRE(::send(peer,packet.data(),packet.size(),MSG_DONTWAIT|MSG_NOSIGNAL)==ssize_t(packet.size()));
    }
    void pauseBehindBatch() {
        for(unsigned n=0;n<40;++n)send("future");
        send("pause");
    }
    tyrell::app::ShellLink adopt() {
        REQUIRE(setenv("GH0ST_SHELL_FD",std::to_string(endpoint).c_str(),1)==0);
        auto result=tyrell::app::ShellLink::from_environment();
        REQUIRE(result.connected());endpoint=-1;return result;
    }
    unsigned output() {
        unsigned acknowledgments=0;
        for(unsigned n=0;n<8192;++n) {
            char packet[512];const auto count=recv(peer,packet,sizeof(packet),MSG_DONTWAIT);
            if(count<0&&(errno==EAGAIN||errno==EWOULDBLOCK))return acknowledgments;
            REQUIRE(count>0);
            const std::string value(packet,size_t(count));
            REQUIRE(value=="hello\t1"||value=="paused");
            acknowledgments+=value=="paused";
        }
        throw std::runtime_error("Unbounded shell output");
    }
    void disconnect() {REQUIRE(peer>=0);::close(peer);peer=-1;}
};

struct FileState {
    std::filesystem::file_type type;
    std::filesystem::file_time_type modified;
    std::string bytes;
    bool operator==(const FileState& other)const {
        return type==other.type&&modified==other.modified&&bytes==other.bytes;
    }
};
using Tree=std::map<std::string,FileState>;
Tree files(const std::filesystem::path& directory) {
    Tree result;
    result.emplace(".",FileState{std::filesystem::file_type::directory,
        std::filesystem::last_write_time(directory),{}});
    for(const auto& entry:std::filesystem::recursive_directory_iterator(directory)) {
        std::string bytes;
        if(entry.is_regular_file()) {
            std::ifstream input(entry.path(),std::ios::binary);
            bytes.assign(std::istreambuf_iterator<char>(input),{});
        }
        result.emplace(entry.path().lexically_relative(directory).string(),
            FileState{entry.symlink_status().type(),entry.last_write_time(),bytes});
    }
    return result;
}
void retainOfflineSave(App& app) {
    app.setup.mode=Mode::Local;app.setup.time=TimeControl::None;app.startGame();
    REQUIRE(app.game.moveUci("d2d4",app.clockNow()));REQUIRE(app.persist());
}

struct Peers {
    uint64_t now=1000;
    App host,joiner;
    explicit Peers(const std::filesystem::path& directory)
      :host(directory/"host",-1,false,[this]{return now;}),
       joiner(directory/"joiner",-1,false,[this]{return now;}) {
        retainOfflineSave(host);retainOfflineSave(joiner);
    }
    ~Peers(){host.completeLanTeardown();joiner.completeLanTeardown();host.lan.close();joiner.lan.close();}
    void open(TimeControl time=TimeControl::Blitz3) {
        REQUIRE(host.beginLanSession(Role::Host));REQUIRE(joiner.beginLanSession(Role::Joiner));
        host.setup=chess::Options{};host.setup.mode=Mode::Host;host.setup.time=time;
        joiner.setup=chess::Options{};joiner.setup.mode=Mode::Join;
        std::string error;REQUIRE(host.lan.host(error));host.networkHost=true;
        host.screen=Screen::Network;host.page="waiting";host.networkStartedAt=1;
        REQUIRE(joiner.lan.join("127.0.0.1",error));joiner.networkHost=false;
        joiner.screen=Screen::Network;joiner.page="waiting";joiner.networkStartedAt=1;
    }
    void pair() {
        until([&]{host.update(2);joiner.update(2);return host.page=="pair"&&joiner.page=="pair";});
        REQUIRE(host.pairCode==joiner.pairCode);
    }
    void start(TimeControl time=TimeControl::Blitz3) {
        open(time);pair();host.menu=joiner.menu=0;host.activate();joiner.activate();
        until([&]{host.update(2);joiner.update(2);return host.networkPlaying&&joiner.networkPlaying;});
    }
    Move legal(App& app,const std::string& text) {
        MoveList moves;ChessRules::generateLegal(app.game.board,moves);
        for(int n=0;n<moves.count;++n)if(chess::uci(moves.moves[n])==text)return moves.moves[n];
        throw std::runtime_error("Illegal fixture move "+text);
    }
    void move(const std::string& text) {
        const auto count=host.game.history.size();
        auto& app=host.game.board.sideToMove()==PieceColor::White?host:joiner;
        REQUIRE(app.apply(legal(app,text)));
        until([&]{host.update(2);joiner.update(2);return host.game.history.size()==count+1&&joiner.game.history.size()==count+1;});
        REQUIRE(chess::fen(host.game.board)==chess::fen(joiner.game.board));
    }
    void queue(App& sender,App& receiver,const std::string& packet) {
        const auto count=receiver.lan.pending_messages();REQUIRE(sender.lan.send(packet));
        until([&]{sender.lan.poll();receiver.lan.poll();return receiver.lan.pending_messages()>count;});
    }
};

tyrell::game::RuntimeOptions nativeOptions() {
    io::reset_native_fixture();auto options=io::native_fixture_options();
    options.restore_framebuffer_on_close=false;return options;
}
bool sameClock(const ChessClockSnapshot& a,const ChessClockSnapshot& b) {
    return a.timeControl==b.timeControl&&a.whiteRemainingMs==b.whiteRemainingMs&&
        a.blackRemainingMs==b.blackRemainingMs&&a.activeSide==b.activeSide&&
        a.started==b.started&&a.paused==b.paused&&a.hasFlaggedSide==b.hasFlaggedSide&&
        a.flaggedSide==b.flaggedSide;
}

// Deliver pause from inside native input polling. Its 40 preceding packets
// require more than one bounded lifecycle batch; no input, network packet or
// render may slip through between those batches.
void pauseDuringInputPoll(const std::filesystem::path& directory,bool hostSide,int phase) {
    Peers peers(directory);
    if(phase==0){peers.open();peers.pair();}
    else {
        peers.start();
        if(phase==2) {
            for(const auto* move:{"f2f3","e7e5","g2g4","d8h4"})peers.move(move);
            until([&]{peers.host.update(2);peers.joiner.update(2);return peers.joiner.game.over();});
        } else {
            peers.move("e2e4");
            if(hostSide)peers.queue(peers.joiner,peers.host,"REQ 1 e7e5");
            else {
                REQUIRE(peers.joiner.apply(peers.legal(peers.joiner,"e7e5")));
                REQUIRE(peers.joiner.awaitingMove);
                // A buffered host clock/result must not override retained
                // authority once the local pause has been observed.
                peers.queue(peers.host,peers.joiner,"SYNC 1 1 2");
                peers.queue(peers.host,peers.joiner,"END 1-0 Resigned");
            }
        }
    }
    auto& app=hostSide?peers.host:peers.joiner;
    const auto position=chess::fen(app.game.board),result=app.game.result;
    const auto history=app.game.history.size(),id=app.lanTerminal.session_id();
    const auto flipped=app.flipped,contrast=app.contrast;
    const auto saved=files(app.dir);
    ShellPair shell;shell.send("hello\t1");auto link=shell.adopt();chess::OfflineLifecycle lifecycle;
    REQUIRE(awaitShell(link,lifecycle)==1);Args args;args.script="f";
    unsigned waits=0,frames=0;
    {
        tyrell::game::Runtime runtime(nativeOptions());
        io::on_next_native_read([&]{shell.pauseBehindBatch();io::queue_native_key(20,1);}); // T
        const auto outcome=runSession(app,args,runtime,&link,&lifecycle,[&]{
            ++waits;REQUIRE(waits<=3);
            REQUIRE(!runtime.foreground()&&runtime.suspended()&&app.lifecyclePaused);
            REQUIRE(app.lanTerminal.sealed()&&app.lanTerminal.session_id()==id);
            REQUIRE(!app.beginLanSession(hostSide?Role::Host:Role::Joiner));
            REQUIRE(io::native_io_stats().open_keyboards==0);
            REQUIRE(shell.output()>=1);
            REQUIRE(files(app.dir)==saved);
            io::write_foreground_pixels(0x369c);
            if(waits<3) {
                io::set_native_held({16});io::queue_native_resume_key(16,1); // Held Q must be suppressed.
                shell.send("resume");
            } else shell.disconnect();
        },[&](int frame){
            frames=unsigned(frame);REQUIRE(frame<=2&&!app.quit);
            REQUIRE(app.lanTerminal.sealed()&&!app.lanTerminal.teardown_pending());
            REQUIRE(app.lanTerminal.interruption().reason==End::ShellPause);
            REQUIRE(app.lanTerminal.interruption().hadMatch==(phase!=0));
            REQUIRE(chess::fen(app.game.board)==position&&app.game.history.size()==history);
            REQUIRE(app.game.result==result&&app.flipped==flipped&&app.contrast==contrast);
            REQUIRE(!app.networkPlaying&&!app.lan.connected()&&!app.lan.listening());
            REQUIRE(app.view.sessionEnded&&!app.view.clocksRunning);
            REQUIRE(app.view.authorityNotice=="Remote unconfirmed");
            if(phase==1&&!hostSide)REQUIRE(app.lanTerminal.interruption().pendingMoveRequest);
            REQUIRE(files(app.dir)==saved);shell.send("pause");
        });
        REQUIRE(outcome.frames==2&&frames==2&&waits==3&&!outcome.saveAllowed);
        REQUIRE(!runtime.foreground()&&runtime.suspended());
    }
    REQUIRE(io::foreground_pixels_equal(0x369c));
    REQUIRE(io::native_io_stats().commits==2&&io::native_io_stats().fb_closes==1);
    REQUIRE(files(app.dir)==saved);
}

// A pause arriving during Lan.poll must win before consuming its real TCP
// queue. This also settles host expiry and retains joiner request uncertainty.
void pauseAfterNetworkPoll(const std::filesystem::path& directory,bool hostSide) {
    Peers peers(directory);peers.start(TimeControl::Bullet1);peers.move("e2e4");
    auto& app=hostSide?peers.host:peers.joiner;
    if(hostSide) {
        REQUIRE(app.game.clock.setRemainingMs(PieceColor::Black,10,app.clockNow()));
        REQUIRE(app.admitLanWork());peers.queue(peers.joiner,peers.host,"REQ 1 e7e5");
    } else {
        peers.queue(peers.host,peers.joiner,"SYNC 1 100 50");app.network();
        REQUIRE(app.apply(peers.legal(app,"e7e5"))&&app.awaitingMove);
        peers.queue(peers.host,peers.joiner,"SYNC 1 1 2");
    }
    const auto position=chess::fen(app.game.board);
    const auto confirmed=app.game.clock.snapshot(app.clockNow());
    const auto saved=files(app.dir);
    ShellPair shell;shell.send("hello\t1");auto link=shell.adopt();chess::OfflineLifecycle lifecycle;
    REQUIRE(awaitShell(link,lifecycle)==1);unsigned polls=0,waits=0;
    app.afterLanPoll=[&]{if(++polls==1){peers.now+=hostSide?10:100;shell.pauseBehindBatch();}};
    {
        tyrell::game::Runtime runtime(nativeOptions());Args args;
        const auto outcome=runSession(app,args,runtime,&link,&lifecycle,[&]{
            ++waits;REQUIRE(waits==1&&app.lanTerminal.sealed());
            REQUIRE(!runtime.foreground()&&io::native_io_stats().open_keyboards==0);
            REQUIRE(shell.output()>=1);
            REQUIRE(chess::fen(app.game.board)==position&&app.game.history.size()==1);
            if(hostSide) {
                REQUIRE(app.game.result=="1-0"&&app.game.reason=="Time expired");
                REQUIRE(app.game.clock.hasFlaggedSide());
            } else {
                const auto& interrupted=app.lanTerminal.interruption();
                REQUIRE(interrupted.pendingMoveRequest&&interrupted.pendingMove=="e7e5");
                REQUIRE(interrupted.confirmedResult=="*"&&sameClock(interrupted.clock,confirmed));
                REQUIRE(app.game.result=="*"&&!app.game.clock.hasFlaggedSide());
            }
            io::write_foreground_pixels(0x4a4a);shell.disconnect();
        });
        REQUIRE(outcome.frames==0&&!outcome.saveAllowed&&waits==1&&polls==1);
    }
    REQUIRE(io::foreground_pixels_equal(0x4a4a)&&io::native_io_stats().commits==0);
    REQUIRE(files(app.dir)==saved);
}

void unknownBatchPreservesTcp(const std::filesystem::path& directory,bool hostSide) {
    Peers peers(directory);peers.start();peers.move("e2e4");
    auto& app=hostSide?peers.host:peers.joiner;
    if(hostSide)peers.queue(peers.joiner,peers.host,"REQ 1 e7e5");
    else {
        REQUIRE(peers.host.apply(peers.legal(peers.host,"e7e5"),true));
        until([&]{peers.host.lan.poll();peers.joiner.lan.poll();return peers.joiner.lan.pending_messages()>0;});
    }
    ShellPair shell;shell.send("hello\t1");auto link=shell.adopt();chess::OfflineLifecycle lifecycle;
    REQUIRE(awaitShell(link,lifecycle)==1);unsigned polls=0;
    app.afterLanPoll=[&]{if(++polls==1)for(unsigned n=0;n<40;++n)shell.send("future");};
    {
        tyrell::game::Runtime runtime(nativeOptions());Args args;args.frames=1;
        const auto outcome=runSession(app,args,runtime,&link,&lifecycle,{},[&](int frame){
            REQUIRE(frame==1&&!app.lanTerminal.sealed()&&app.networkPlaying);
            REQUIRE(app.game.history.size()==2&&chess::uci(app.game.history.back().move)=="e7e5");
        });
        REQUIRE(outcome.frames==1&&!outcome.saveAllowed&&polls==1);
    }
}

void observationPrecedesRelease(const std::filesystem::path& directory) {
    Peers peers(directory);peers.start(TimeControl::Bullet1);peers.move("e2e4");auto& app=peers.host;
    REQUIRE(app.game.clock.setRemainingMs(PieceColor::Black,20,app.clockNow()));REQUIRE(app.admitLanWork());
    const auto saved=files(app.dir);
    ShellPair shell;shell.send("hello\t1");auto link=shell.adopt();chess::OfflineLifecycle lifecycle;
    REQUIRE(awaitShell(link,lifecycle)==1);bool releaseObserved=false;
    {
        tyrell::game::Runtime runtime(nativeOptions());Args args;
        io::on_next_native_read([&]{peers.now=1010;shell.send("pause");});
        io::on_next_native_keyboard_close([&]{
            // Do not throw through a destructor's close syscall. Record the
            // observation and make release take a long injected interval.
            releaseObserved=app.lanTerminal.sealed()&&app.lanTerminal.teardown_pending()&&
                app.lanTerminal.interruption().observedAtMs==1010&&runtime.suspended()&&app.game.result=="*";
            peers.now+=1000;
        });
        const auto outcome=runSession(app,args,runtime,&link,&lifecycle,[&]{
            REQUIRE(releaseObserved&&shell.output()>=1&&!runtime.foreground());
            REQUIRE(app.game.result=="*"&&!app.game.clock.hasFlaggedSide());
            REQUIRE(app.game.clock.remainingMs(PieceColor::Black)==10);
            REQUIRE(app.lanTerminal.interruption().clock.blackRemainingMs==10);
            REQUIRE(app.lanTerminal.interruption().observedAtMs==1010&&peers.now==2010);
            REQUIRE(files(app.dir)==saved);shell.disconnect();
        });
        REQUIRE(outcome.frames==0&&!outcome.saveAllowed);
    }
}

void stalePoppedKey(const std::filesystem::path& directory) {
    Peers peers(directory);peers.start();auto& app=peers.host;
    ShellPair shell;shell.send("hello\t1");auto link=shell.adopt();chess::OfflineLifecycle lifecycle;
    REQUIRE(awaitShell(link,lifecycle)==1);bool injected=false;
    {
        tyrell::game::Runtime runtime(nativeOptions());Args args;
        const auto outcome=runSession(app,args,runtime,&link,&lifecycle,{},[&](int frame){
            REQUIRE(frame==1);
            const auto admission=app.lifecycleAdmission;
            app.lifecycleAdmission=[&,admission]{
                if(!injected){injected=true;shell.send("pause");shell.send("resume");}
                return admission();
            };
            // This Q was already popped before its admission callback observed
            // a complete pause/resume cycle. It must not exit the ended view.
            app.key(KEY_Q);
            REQUIRE(injected&&!app.quit&&app.lanTerminal.sealed()&&runtime.foreground());
            REQUIRE(shell.output()>=1);
            app.key(KEY_Q);REQUIRE(app.quit); // A genuinely fresh Q may exit.
        });
        REQUIRE(outcome.frames==1&&!outcome.saveAllowed);
    }
}

void staleOfflineKeySecondDrain(const std::filesystem::path& directory,int key) {
    uint64_t now=1000;App app(directory,-1,true,[&]{return now;});
    retainOfflineSave(app);app.cursor={4,6}; // Black's e7 pawn after d2-d4.
    const auto position=chess::fen(app.game.board);
    ShellPair shell;shell.send("hello\t1");auto link=shell.adopt();chess::OfflineLifecycle lifecycle;
    REQUIRE(awaitShell(link,lifecycle)==1);unsigned callbacks=0;
    {
        tyrell::game::Runtime runtime(nativeOptions());Args args;
        const auto outcome=runSession(app,args,runtime,&link,&lifecycle,{},[&](int frame){
            REQUIRE(frame==1&&!app.lanTerminal.begun());const auto saved=files(app.dir);
            const auto admission=app.lifecycleAdmission;
            app.lifecycleAdmission=[&,admission]{
                if(++callbacks==2){shell.send("pause");shell.send("resume");}
                return admission();
            };
            // key() drains once, then admitLanWork() drains again. A pause and
            // resume in that second callback invalidates this already popped
            // offline key even though no LAN terminal instance was begun.
            app.key(key);
            REQUIRE(callbacks==2&&!app.quit&&!app.selected.valid());
            REQUIRE(!app.lanTerminal.begun()&&runtime.foreground()&&!app.lifecyclePaused);
            REQUIRE(chess::fen(app.game.board)==position&&files(app.dir)==saved);
            REQUIRE(shell.output()>=1);
            app.key(key); // Only the fresh foreground key may take effect.
            if(key==KEY_ENTER){REQUIRE(app.selected.valid());app.key(KEY_Q);}
            REQUIRE(app.quit);
        });
        REQUIRE(outcome.frames==1);
    }
}

void pauseDuringReadyOfflineWorker(const std::filesystem::path& directory) {
    uint64_t now=1000;App app(directory,-1,true,[&]{return now;});
    app.setup=chess::Options{};app.setup.mode=Mode::Computer;
    app.setup.time=TimeControl::None;app.setup.difficulty=AIDifficulty::Easy;
    app.setup.human=PieceColor::White;app.startGame();
    REQUIRE(app.game.moveUci("e2e4",app.clockNow())&&app.persist());
    MoveList moves;ChessRules::generateLegal(app.game.board,moves);Move ready;bool found=false;
    for(int n=0;n<moves.count;++n)if(chess::uci(moves.moves[n])=="e7e5"){ready=moves.moves[n];found=true;break;}
    REQUIRE(found&&app.activeComputer());
    std::promise<Move> promise;app.worker=promise.get_future();promise.set_value(ready);
    app.workerGeneration=app.generation;app.workerHash=ChessZobrist::hash(app.game.board);app.workerHint=false;
    const auto cancellation=std::make_shared<std::atomic_bool>(false);app.workerCancel=cancellation;
    const auto saved=files(app.dir);const auto position=chess::fen(app.game.board);
    ShellPair shell;shell.send("hello\t1");auto link=shell.adopt();chess::OfflineLifecycle lifecycle;
    REQUIRE(awaitShell(link,lifecycle)==1);bool injected=false;unsigned waits=0;
    {
        tyrell::game::Runtime runtime(nativeOptions());Args args;
        io::on_next_native_read([&]{
            const auto admission=app.lifecycleAdmission;
            app.lifecycleAdmission=[&,admission]{
                // update() has consumed the ready future, but apply() has not
                // admitted its move. A pause in this inner drain must return
                // from the whole update before relaunching AI or saving.
                if(!injected&&!app.worker.valid()&&app.game.history.size()==1) {
                    injected=true;now=7000;shell.send("pause");
                }
                return admission();
            };
        });
        const auto outcome=runSession(app,args,runtime,&link,&lifecycle,[&]{
            ++waits;REQUIRE(waits==1&&injected&&shell.output()>=1);
            REQUIRE(app.lifecyclePaused&&!runtime.foreground()&&io::native_io_stats().open_keyboards==0);
            REQUIRE(!app.worker.valid()&&app.workerCancel==cancellation&&cancellation->load());
            REQUIRE(!app.lanTerminal.begun()&&app.game.history.size()==1&&chess::fen(app.game.board)==position);
            REQUIRE(files(app.dir)==saved&&app.lastSave==1000);
            const auto lastSave=app.lastSave;
            REQUIRE(!app.persist()&&app.lastSave==lastSave); // Defense in depth for hidden callers.
            REQUIRE(files(app.dir)==saved);
            io::write_foreground_pixels(0x9f9f);shell.disconnect();
        });
        REQUIRE(outcome.frames==0&&!outcome.saveAllowed&&waits==1);
    }
    REQUIRE(io::native_io_stats().commits==0&&io::foreground_pixels_equal(0x9f9f));
    REQUIRE(files(app.dir)==saved);
}

void pauseResumeCancelsOpenFrame(const std::filesystem::path& directory,bool hostSide) {
    Peers peers(directory);peers.start();auto& app=hostSide?peers.host:peers.joiner;
    const auto position=chess::fen(app.game.board);const auto id=app.lanTerminal.session_id();
    ShellPair shell;shell.send("hello\t1");auto link=shell.adopt();chess::OfflineLifecycle lifecycle;
    REQUIRE(awaitShell(link,lifecycle)==1);unsigned attempts=0,frames=0;
    {
        tyrell::game::Runtime runtime(nativeOptions());Args args;args.frames=1;
        const auto outcome=runSession(app,args,runtime,&link,&lifecycle,{},[&](int frame){
            frames=unsigned(frame);REQUIRE(frame==1&&attempts==2);
            REQUIRE(app.lanTerminal.sealed()&&app.lanTerminal.session_id()==id);
            REQUIRE(app.view.sessionEnded&&app.view.footer=="Read-only  Q Exit");
            REQUIRE(chess::fen(app.game.board)==position&&!app.networkPlaying);
            REQUIRE(io::native_io_stats().commits==1&&io::native_io_stats().key_opens==2);
            REQUIRE(shell.output()>=1);
        },[&]{
            ++attempts;
            if(attempts==1){shell.send("pause");shell.send("resume");}
        });
        REQUIRE(outcome.frames==1&&frames==1&&attempts==2&&!outcome.saveAllowed);
    }
    REQUIRE(io::native_io_stats().commits==1);
}

void acknowledgmentBackpressure(const std::filesystem::path& directory) {
    Peers peers(directory);peers.start();auto& app=peers.host;
    const auto position=chess::fen(app.game.board);const auto saved=files(app.dir);
    ShellPair shell;shell.send("hello\t1");auto link=shell.adopt();chess::OfflineLifecycle lifecycle;
    REQUIRE(awaitShell(link,lifecycle)==1);
    int size=1024;REQUIRE(setsockopt(link.fd(),SOL_SOCKET,SO_SNDBUF,&size,sizeof(size))==0);
    unsigned filled=0;
    for(;filled<8192;++filled) {
        const auto count=::send(link.fd(),"hello\t1",7,MSG_DONTWAIT|MSG_NOSIGNAL);
        if(count<0&&(errno==EAGAIN||errno==EWOULDBLOCK))break;
        REQUIRE(count==7);
    }
    REQUIRE(filled>0&&filled<8192);unsigned waits=0;
    {
        tyrell::game::Runtime runtime(nativeOptions());Args args;
        io::on_next_native_read([&]{shell.send("pause");});
        const auto outcome=runSession(app,args,runtime,&link,&lifecycle,[&]{
            ++waits;REQUIRE(waits==1&&app.lanTerminal.sealed());
            REQUIRE(!runtime.foreground()&&io::native_io_stats().open_keyboards==0);
            REQUIRE(link.pending_output());
            REQUIRE(shell.output()==0); // Only filled hello packets reached the shell.
            shell.send("resume"); // Cancels the queued, undelivered pause acknowledgment.
        },[&](int frame){
            REQUIRE(frame==1&&waits==1&&app.lanTerminal.sealed());
            REQUIRE(!link.pending_output()&&shell.output()==0);
            REQUIRE(chess::fen(app.game.board)==position&&files(app.dir)==saved);
            io::write_foreground_pixels(0x5a5a);shell.disconnect();
        });
        REQUIRE(outcome.frames==1&&!outcome.saveAllowed);
    }
    REQUIRE(io::foreground_pixels_equal(0x5a5a));
    REQUIRE(files(app.dir)==saved);
}

void failedResume(const std::filesystem::path& directory,bool hostSide,bool resync) {
    Peers peers(directory);peers.start();auto& app=hostSide?peers.host:peers.joiner;
    const auto position=chess::fen(app.game.board);const auto saved=files(app.dir);
    ShellPair shell;shell.send("hello\t1");auto link=shell.adopt();chess::OfflineLifecycle lifecycle;
    REQUIRE(awaitShell(link,lifecycle)==1);unsigned waits=0;
    {
        tyrell::game::Runtime runtime(nativeOptions());Args args;
        io::on_next_native_read([&]{shell.send("pause");});
        const auto outcome=runSession(app,args,runtime,&link,&lifecycle,[&]{
            ++waits;REQUIRE(waits==1&&shell.output()>=1);
            REQUIRE(app.lanTerminal.sealed()&&!runtime.foreground());
            io::write_foreground_pixels(0x6b6b);
            if(resync)io::fail_native_keyboard_resync(true);
            else io::fail_native_keyboard_open(true);
            shell.send("resume");
        });
        REQUIRE(outcome.frames==0&&!outcome.saveAllowed&&lifecycle.exiting());
        REQUIRE(app.lanTerminal.sealed()&&!runtime.foreground()&&runtime.suspended());
        REQUIRE(chess::fen(app.game.board)==position&&files(app.dir)==saved);
    }
    REQUIRE(io::native_io_stats().commits==0&&io::native_io_stats().open_keyboards==0);
    REQUIRE(io::foreground_pixels_equal(0x6b6b));
}

void terminalExits(const std::filesystem::path& directory,bool hostSide,int kind) {
    Peers peers(directory);peers.start();auto& app=hostSide?peers.host:peers.joiner;
    const auto position=chess::fen(app.game.board);const auto saved=files(app.dir);
    ShellPair shell;shell.send("hello\t1");auto link=shell.adopt();chess::OfflineLifecycle lifecycle;
    REQUIRE(awaitShell(link,lifecycle)==1);bool caught=false;
    {
        tyrell::game::Runtime runtime(nativeOptions());Args args;args.script="f";
        io::write_foreground_pixels(0x7c7c);
        if(kind==0)io::on_next_native_read([&]{shell.disconnect();});
        else if(kind==1)io::signal_on_native_read(SIGTERM);
        try {
            const auto outcome=runSession(app,args,runtime,&link,&lifecycle,{},[&](int frame){
                REQUIRE(kind==2&&frame==1);io::write_foreground_pixels(0x7c7c);
                throw std::runtime_error("injected LAN loop failure");
            });
            REQUIRE(kind!=2&&outcome.frames==0&&!outcome.saveAllowed);
        } catch(const std::runtime_error& error) {
            caught=std::string(error.what())=="injected LAN loop failure";
            if(!caught)throw;
        }
        REQUIRE(caught==(kind==2)&&!runtime.foreground());
        REQUIRE(app.lanTerminal.sealed()&&!app.lanTerminal.teardown_pending());
        REQUIRE(app.lanTerminal.interruption().reason==(kind==0?End::ShellLost:End::LinkedTermination));
        REQUIRE(chess::fen(app.game.board)==position&&files(app.dir)==saved);
    }
    REQUIRE(io::foreground_pixels_equal(0x7c7c));
    REQUIRE(io::native_io_stats().commits==(kind==2?1u:0u));
}

void signalDuringMatingBroadcast(const std::filesystem::path& directory) {
    Peers peers(directory);peers.start();
    for(const auto* move:{"f2f3","e7e5","g2g4"})peers.move(move);
    auto& app=peers.host;const auto saved=files(app.dir);
    ShellPair shell;shell.send("hello\t1");auto link=shell.adopt();chess::OfflineLifecycle lifecycle;
    REQUIRE(awaitShell(link,lifecycle)==1);bool injected=false,sealedBeforeReturn=false;
    {
        tyrell::game::Runtime runtime(nativeOptions());Args args;
        const auto outcome=runSession(app,args,runtime,&link,&lifecycle,{},[&](int frame){
            REQUIRE(frame==1&&app.game.history.size()==3&&app.game.result=="*");
            const auto admission=app.lifecycleAdmission;
            app.lifecycleAdmission=[&,admission]{
                const bool interrupt=!injected&&app.game.history.size()==4;
                if(interrupt) {
                    injected=true;REQUIRE(app.game.result=="0-1"&&app.game.reason=="Checkmate");
                    io::write_foreground_pixels(0x8e8e);std::raise(SIGTERM);
                }
                const bool allowed=admission();
                if(interrupt)sealedBeforeReturn=!allowed&&app.lanTerminal.sealed()&&
                    !app.lanTerminal.teardown_pending()&&!runtime.foreground();
                return allowed;
            };
            // The real host consumes this REQ and applies mate. The injected
            // signal arrives at its nested outbound send admission, before
            // apply() could perform ordinary finished-game persistence.
            peers.queue(peers.joiner,app,"REQ 3 d8h4");
        });
        REQUIRE(injected&&sealedBeforeReturn&&outcome.frames==1&&!outcome.saveAllowed);
        REQUIRE(app.lanTerminal.sealed()&&app.lanTerminal.interruption().reason==End::LinkedTermination);
        REQUIRE(app.game.history.size()==4&&app.game.result=="0-1"&&app.game.reason=="Checkmate");
        REQUIRE(app.lanTerminal.interruption().confirmedResult=="0-1");
        REQUIRE(files(app.dir)==saved&&!runtime.foreground());
    }
    REQUIRE(io::foreground_pixels_equal(0x8e8e)&&io::native_io_stats().commits==1);
    REQUIRE(files(app.dir)==saved);
}

void endedExitAndFreshAdmission(const std::filesystem::path& directory,bool fresh) {
    Peers peers(directory);peers.start();auto& app=peers.host;
    const auto id=app.lanTerminal.session_id();const auto saved=files(app.dir);
    ShellPair shell;shell.send("hello\t1");auto link=shell.adopt();chess::OfflineLifecycle lifecycle;
    REQUIRE(awaitShell(link,lifecycle)==1);unsigned waits=0;
    {
        tyrell::game::Runtime runtime(nativeOptions());Args args;
        io::on_next_native_read([&]{shell.send("pause");});
        const auto outcome=runSession(app,args,runtime,&link,&lifecycle,[&]{
            ++waits;REQUIRE(waits==1&&shell.output()>=1);
            REQUIRE(!app.beginLanSession(Role::Host));
            app.key(KEY_Q);REQUIRE(!app.quit); // Hidden ended UI cannot accept Exit.
            shell.send("resume");
        },[&](int frame){
            REQUIRE(frame==1&&app.lanTerminal.sealed()&&!app.lanTerminal.teardown_pending());
            REQUIRE(app.commands().size()==1&&app.commands()[0].id==Action::Exit);
            REQUIRE(app.view.footer=="Read-only  Q Exit");
            std::vector<chessui::TextBounds> audit;
            runtime.begin_frame();app.draw(-1,&audit);runtime.end_frame();
            bool renderedExit=false;
            for(const auto& text:audit) {
                REQUIRE(text.x>=0&&text.y>=0&&text.x+text.width<=320&&text.y+text.height<=170);
                if(text.original==app.view.footer) {
                    REQUIRE(text.rendered==text.original);renderedExit=true;
                }
            }
            REQUIRE(renderedExit);
            REQUIRE(!app.networkPlaying&&!app.lan.connected()&&!app.lan.listening());
            app.key(KEY_ESCAPE);app.key(KEY_ENTER);REQUIRE(!app.quit&&app.lanTerminal.sealed());
            REQUIRE(app.lanTerminal.session_id()==id);
            if(fresh) {
                REQUIRE(app.beginLanSession(Role::Host));
                REQUIRE(app.lanTerminal.session_id()==id+1&&app.lanTerminal.active());
                REQUIRE(!app.awaitingMove&&app.pairCode==0&&!app.localReady&&!app.remoteReady);
                REQUIRE(!app.lan.connected()&&!app.lan.listening());app.quit=true;
            } else {app.key(KEY_Q);REQUIRE(app.quit);}
        });
        REQUIRE(outcome.frames==1&&!outcome.saveAllowed&&waits==1);
        REQUIRE(app.lanTerminal.sealed()&&!runtime.foreground());
    }
    REQUIRE(files(app.dir)==saved);
}
}

int main() {
    const auto directory=std::filesystem::temp_directory_path()/
        ("tyrell-lan-shell-"+std::to_string(monotonicMs()));
    try {
        for(bool host:{true,false}) {
            for(int phase=0;phase<3;++phase)
                pauseDuringInputPoll(directory/(std::string(host?"host-":"join-")+std::to_string(phase)),host,phase);
            pauseAfterNetworkPoll(directory/(host?"network-host":"network-join"),host);
            unknownBatchPreservesTcp(directory/(host?"unknown-host":"unknown-join"),host);
            pauseResumeCancelsOpenFrame(directory/(host?"frame-host":"frame-join"),host);
            for(bool resync:{false,true})failedResume(directory/(std::string(host?"resume-host-":"resume-join-")+std::to_string(resync)),host,resync);
            for(int kind=0;kind<3;++kind)terminalExits(directory/(std::string(host?"exit-host-":"exit-join-")+std::to_string(kind)),host,kind);
        }
        acknowledgmentBackpressure(directory/"backpressure");
        observationPrecedesRelease(directory/"release-boundary");
        stalePoppedKey(directory/"stale-popped-key");
        staleOfflineKeySecondDrain(directory/"stale-offline-q",KEY_Q);
        staleOfflineKeySecondDrain(directory/"stale-offline-enter",KEY_ENTER);
        pauseDuringReadyOfflineWorker(directory/"ready-offline-worker-pause");
        signalDuringMatingBroadcast(directory/"mating-broadcast-signal");
        endedExitAndFreshAdmission(directory/"ended-exit",false);
        endedExitAndFreshAdmission(directory/"fresh-admission",true);
        std::filesystem::remove_all(directory);
        std::cout<<"LAN shell lifecycle passed: actual loop, TCP, post-poll drains, backpressure, native ownership, authority and ended admission. Physical testing pending.\n";
        return 0;
    } catch(const std::exception& error) {
        std::filesystem::remove_all(directory);std::cerr<<error.what()<<'\n';return 1;
    }
}
