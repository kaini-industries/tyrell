#include "draft2_link.h"

#include <array>
#include <cerrno>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <fcntl.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

using namespace tyrell::video;
namespace {
const std::string app(32, 'a'), incarnation(32, 'b'), lease(32, 'c'), second_lease(32, 'd'), receipt(32, 'e');
const std::string caps = "async-drain,epoch-barrier,helper-receipts";
void require(bool value, const char* message) { if(!value) throw std::runtime_error(message); }
struct Pair {
    int peer = -1, local = -1;
    Pair() { int descriptors[2]; require(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, descriptors) == 0, "socketpair failed"); peer = descriptors[0]; local = descriptors[1]; }
    ~Pair() { if(peer >= 0) close(peer); if(local >= 0) close(local); }
    std::unique_ptr<Draft2Link> adopt(int pid = getpid()) {
        std::string error;
        auto result = Draft2Link::adopt({local, getuid(), pid, getuid(), app}, error);
        require(bool(result), error.c_str()); local = -1; return result;
    }
    void send(const std::string& packet) { require(::send(peer, packet.data(), packet.size(), MSG_NOSIGNAL) == static_cast<ssize_t>(packet.size()), "send failed"); }
    std::string receive() { std::array<char, 1024> bytes{}; const auto n = recv(peer, bytes.data(), bytes.size(), MSG_DONTWAIT); require(n > 0, "expected app packet"); return {bytes.data(), static_cast<std::size_t>(n)}; }
};
std::string tuple(unsigned epoch, unsigned request) { return incarnation + '\t' + app + '\t' + std::to_string(epoch) + '\t' + std::to_string(request); }
void negotiate(Pair& pair, Draft2Link& link) {
    require(pair.receive() == "TVH2\thello\t" + app + '\t' + caps, "wrong hello");
    pair.send("TVH2\twelcome\t" + incarnation + '\t' + app + '\t' + caps);
    pair.send("TVH2\tgrant\t" + tuple(1, 1) + '\t' + lease);
    const auto events = link.pump();
    require(events.size() == 2 && events[0].kind == Draft2EventKind::Welcome && events[1].kind == Draft2EventKind::Grant, "handshake events missing");
    require(link.state() == Draft2State::AwaitReady && !link.input_allowed(), "grant prematurely reopened input");
    require(link.ready() && link.input_allowed(), "trusted readiness refused");
    require(pair.receive() == "TVH2\tready\t" + tuple(1, 1) + '\t' + lease, "wrong ready");
}
void lifecycle() {
    Pair pair; const int inherited = dup(pair.local); require(inherited >= 0, "dup failed");
    const int file_flags = fcntl(inherited, F_GETFL); auto link = pair.adopt();
    require(fcntl(inherited, F_GETFL) == file_flags && !(file_flags & O_NONBLOCK), "transport mutated shared blocking flags"); close(inherited);
    require(fcntl(link->fd(), F_GETFD) & FD_CLOEXEC, "channel can escape to child");
    require(fcntl(link->peer_pidfd(), F_GETFD) & FD_CLOEXEC, "pidfd can escape to child");
    negotiate(pair, *link);
    pair.send("TVH2\tquiesce\t" + tuple(1, 2) + "\t10000");
    auto events = link->pump(); require(events.size() == 1 && events[0].kind == Draft2EventKind::Quiesce && events[0].budget_ms == 10000, "quiesce not surfaced");
    require(!link->input_allowed() && !link->render_allowed() && !link->ready(), "quiesce did not immediately gate presentation");
    pair.send("TVH2\tquiesce\t" + tuple(1, 2) + "\t10000"); pair.send("TVH2\tcancel\t" + tuple(1, 2));
    events = link->pump(); require(events.size() == 1 && events[0].kind == Draft2EventKind::Cancel, "idempotent drain/cancel handling failed");
    require(!link->input_allowed(), "cancel reopened old epoch");
    require(link->progress("graph") && pair.receive() == "TVH2\tquiescing\t" + tuple(1, 2) + "\tgraph", "wrong progress");
    require(!link->progress("made-up"), "unknown progress phase accepted");
    require(link->local_done() && link->local_done() && !link->local_done(receipt), "local-done replay changed checkpoint");
    require(pair.receive() == "TVH2\tlocal-done\t" + tuple(1, 2) + "\tnone", "wrong local-done");
    pair.send("TVH2\tcommit\t" + tuple(1, 2) + '\t' + receipt);
    events = link->pump(); require(events.size() == 1 && events[0].kind == Draft2EventKind::Commit && link->state() == Draft2State::Hidden, "commit did not hide old epoch");
    pair.send("TVH2\tcommit\t" + tuple(1, 2) + '\t' + receipt); require(link->pump().empty(), "duplicate commit is not idempotent");
    pair.send("TVH2\tgrant\t" + tuple(2, 3) + '\t' + second_lease);
    events = link->pump(); require(events.size() == 1 && events[0].kind == Draft2EventKind::Grant && !link->input_allowed(), "fresh grant skipped rebuild gate");
    require(link->ready() && pair.receive() == "TVH2\tready\t" + tuple(2, 3) + '\t' + second_lease, "fresh readiness failed");
    close(pair.peer); pair.peer = -1;
    events = link->pump(); require(link->state() == Draft2State::Lost && !link->input_allowed() && events.size() == 1 && events[0].kind == Draft2EventKind::Lost, "EOF reopened or retained foreground");
    require(link->pump().empty(), "loss event replayed");
}
void invalid_lifecycle() {
    for(const auto& packet : {std::string("hello\t1"), std::string(513, 'a'), "TVH2\twelcome\t" + incarnation + '\t' + app + "\tasync-drain", std::string("TVH2\twelcome\tbad")}) {
        Pair pair; auto link = pair.adopt(); pair.receive(); pair.send(packet); link->pump(); require(!link->connected() && !link->input_allowed(), "malformed/legacy peer admitted");
    }
    for(const auto& packet : {"TVH2\tgrant\t" + tuple(1, 2) + '\t' + second_lease, "TVH2\tcommit\t" + tuple(1, 2) + '\t' + receipt,
                             "TVH2\tquiesce\t" + tuple(1, 2) + "\t30001", "TVH2\tquiesce\t" + tuple(1, 1) + "\t10000"}) {
        Pair pair; auto link = pair.adopt(); negotiate(pair, *link); pair.send(packet); link->pump(); require(!link->connected(), "stale/unsolicited lifecycle accepted");
    }
    Pair pair; auto link = pair.adopt(); negotiate(pair, *link);
    pair.send("TVH2\tquiesce\t" + tuple(1, 2) + "\t10000"); link->pump();
    pair.send("TVH2\tfailed\t" + tuple(1, 2) + "\texpired");
    const auto events = link->pump(); require(events.size() == 2 && events[0].kind == Draft2EventKind::Failed && !link->input_allowed(), "expired request can reopen");
}
void invalid_adoption() {
    char name[] = "/tmp/tyrell-d5-adoption-XXXXXX"; const int fd = mkstemp(name); require(fd > 2, "regular fixture failed"); unlink(name);
    const int flags = fcntl(fd, F_GETFD), status = fcntl(fd, F_GETFL); std::string error;
    require(!Draft2Link::adopt({fd, getuid(), getpid(), getuid(), app}, error), "regular file adopted");
    require(fcntl(fd, F_GETFD) == flags && fcntl(fd, F_GETFL) == status, "invalid adoption mutated unrelated file"); close(fd);
    Pair pair; const int socket_flags = fcntl(pair.local, F_GETFD);
    require(!Draft2Link::adopt({pair.local, getuid(), getpid(), static_cast<unsigned>(getuid()+1), app}, error), "foreign creator accepted");
    require(fcntl(pair.local, F_GETFD) == socket_flags, "foreign creator mutation");
    Pair silent; std::uint64_t now = 0;
    auto link=Draft2Link::adopt({silent.local,getuid(),getpid(),getuid(),app},error,[&]{return now;});
    require(bool(link),"clock fixture adoption failed");silent.local=-1;silent.receive();now=1000000000ULL;link->pump();
    require(link->state()==Draft2State::Lost&&link->last_error()=="negotiation-expired","live silent shell retained negotiation indefinitely");
}
void bounded_pumping_and_output() {
    Pair pair;auto link=pair.adopt();negotiate(pair,*link);
    for(unsigned n=0;n<64;++n)pair.send("TVH2\tgrant\t"+tuple(1,1)+'\t'+lease);
    pair.send("TVH2\tquiesce\t"+tuple(1,2)+"\t10000");
    require(link->pump().empty()&&link->input_allowed(),"One wake consumed more than 64 packets");
    require(link->pump().size()==1&&!link->input_allowed(),"Following wake lost the deferred revocation");
    const int size=4096;require(setsockopt(link->fd(),SOL_SOCKET,SO_SNDBUF,&size,sizeof(size))==0,"send buffer setup failed");
    const std::string padding(512,'p');
    while(send(link->fd(),padding.data(),padding.size(),MSG_DONTWAIT|MSG_NOSIGNAL)>0){}
    require(errno==EAGAIN||errno==EWOULDBLOCK,"send buffer did not fill");
    unsigned accepted=0;while(link->progress("graph")){++accepted;require(accepted<=16,"Output reservation exceeded 16 packets");}
    require(accepted==16&&link->pending_output()&&!link->input_allowed(),"Backpressure lost bounded progress or reopened input");
}
std::size_t fd_count() { std::size_t count = 0; for(const auto& entry : std::filesystem::directory_iterator("/proc/self/fd")) { (void)entry; ++count; } return count; }
void foreign_sender_and_rights() {
    {
        Pair pair; auto link = pair.adopt(); pair.receive();
        const auto child = fork(); require(child >= 0, "fork failed");
        if(!child) { const auto packet = "TVH2\twelcome\t" + incarnation + '\t' + app + '\t' + caps; send(pair.peer, packet.data(), packet.size(), MSG_NOSIGNAL); _exit(0); }
        int status; waitpid(child, &status, 0); link->pump(); require(!link->connected(), "forked sender inherited registered identity");
    }
    {
        Pair pair; auto link = pair.adopt(); pair.receive(); int extra[2]; require(pipe(extra) == 0, "pipe failed");
        const auto before = fd_count(); const auto packet = "TVH2\twelcome\t" + incarnation + '\t' + app + '\t' + caps;
        iovec vector{const_cast<char*>(packet.data()), packet.size()}; alignas(cmsghdr) std::array<char, CMSG_SPACE(sizeof(int))> controls{};
        msghdr message{}; message.msg_iov = &vector; message.msg_iovlen = 1; message.msg_control = controls.data(); message.msg_controllen = controls.size();
        auto* cmsg = CMSG_FIRSTHDR(&message); cmsg->cmsg_level = SOL_SOCKET; cmsg->cmsg_type = SCM_RIGHTS; cmsg->cmsg_len = CMSG_LEN(sizeof(int)); std::memcpy(CMSG_DATA(cmsg), &extra[0], sizeof(int));
        require(sendmsg(pair.peer, &message, MSG_NOSIGNAL) > 0, "send rights failed"); link->pump();
        require(!link->connected() && fd_count()+2 == before, "unexpected rights leaked or were accepted"); close(extra[0]); close(extra[1]);
    }
    {
        Pair pair; int gate[2]; require(pipe(gate) == 0, "gate failed");
        const auto child = fork(); require(child >= 0, "fork failed");
        if(!child) { close(gate[1]); char value; read(gate[0], &value, 1); _exit(0); }
        close(gate[0]); auto link = pair.adopt(child); pair.receive(); close(gate[1]); int status; waitpid(child, &status, 0);
        link->pump(); require(!link->connected() && link->last_error() == "registered-process-exited", "pidfd lifetime loss ignored");
    }
}
} // namespace
int main() {
    try { lifecycle(); invalid_lifecycle(); invalid_adoption(); bounded_pumping_and_output(); foreign_sender_and_rights(); std::cout << "PASS: authenticated TVH2 app transport, fresh-grant gates, bounded framing/output, sender/pidfd loss and descriptor rejection\n"; return 0; }
    catch(const std::exception& error) { std::cerr << "Draft-2 app: " << error.what() << '\n'; return 1; }
}
