#include "lan.hpp"
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#include <chrono>
#include <cstdio>
#include <stdexcept>
#include <thread>

#define CHECK(expression) do { if (!(expression)) throw std::runtime_error( \
    std::string(__FILE__) + ":" + std::to_string(__LINE__) + ": " #expression); } while (false)

namespace {
template<class Predicate> void until(Predicate predicate) {
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(3);
    while(!predicate()) {
        if(std::chrono::steady_clock::now()>deadline)throw std::runtime_error("LAN test timeout");
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
}
int rawPeer() {
    const int fd=socket(AF_INET,SOCK_STREAM,0);CHECK(fd>=0);
    sockaddr_in address{};address.sin_family=AF_INET;address.sin_port=htons(chess::Lan::port);
    CHECK(inet_pton(AF_INET,"127.0.0.1",&address.sin_addr)==1);
    CHECK(connect(fd,reinterpret_cast<sockaddr*>(&address),sizeof(address))==0);
    return fd;
}
void sendRaw(int fd,const std::string& text) {
    CHECK(::send(fd,text.data(),text.size(),0)==static_cast<ssize_t>(text.size()));
}
}
int main() {
    try {
        std::string error;
        chess::Lan host,joiner;
        for(const auto* invalid:{"example.com","8.8.8.8","0.0.0.0","172.32.0.1","192.169.0.1","127.0.0.999"})
            CHECK(!joiner.join(invalid,error));
        CHECK(host.host(error));CHECK(host.listening());
        CHECK(joiner.join("127.0.0.1",error));
        until([&]{host.poll();joiner.poll();return host.connected()&&joiner.connected();});
        CHECK(!host.listening());
        CHECK(joiner.send("HELLO 1"));CHECK(joiner.send("MOVE 1 e2e4"));
        CHECK(!joiner.send("BAD\nMESSAGE"));CHECK(!joiner.send(std::string(257,'x')));
        std::vector<std::string> received;
        until([&]{joiner.poll();host.poll();auto lines=host.receive();received.insert(received.end(),lines.begin(),lines.end());return received.size()==2;});
        CHECK(received==std::vector<std::string>({"HELLO 1","MOVE 1 e2e4"}));
        CHECK(host.send("ACK 1"));received.clear();
        until([&]{host.poll();joiner.poll();received=joiner.receive();return !received.empty();});
        CHECK(received==std::vector<std::string>({"ACK 1"}));
        joiner.close();until([&]{host.poll();return !host.connected();});
        CHECK(host.error()=="Opponent disconnected");

        // A partial TCP record spans polls. A complete final record survives EOF.
        CHECK(host.host(error));int fd=rawPeer();until([&]{host.poll();return host.connected();});
        sendRaw(fd,"MO");host.poll();CHECK(host.receive().empty());
        sendRaw(fd,"VE 2 e7e5\nEND 2 1-0\n");CHECK(shutdown(fd,SHUT_WR)==0);
        until([&]{host.poll();return !host.connected();});
        CHECK(host.receive()==std::vector<std::string>({"MOVE 2 e7e5","END 2 1-0"}));::close(fd);

        CHECK(host.host(error));fd=rawPeer();until([&]{host.poll();return host.connected();});
        sendRaw(fd,std::string(257,'x')+"\n");
        until([&]{host.poll();return !host.connected();});
        CHECK(host.error()=="Invalid peer message");CHECK(host.receive().empty());::close(fd);
        std::puts("Chess LAN passed: numeric/private addresses, connection, bounded framing, duplex exchange, partial lines, EOF, oversized peer data.");
        return 0;
    } catch(const std::exception& error) {std::fprintf(stderr,"%s\n",error.what());return 1;}
}
