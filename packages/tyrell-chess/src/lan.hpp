#pragma once
#include <string>
#include <vector>
namespace chess {
// One nonblocking TCP peer; only private/local numeric IPv4 addresses accepted.
// Both sides explicitly confirm a pairing code before exchanging game moves.
class Lan {
public:
    Lan()=default;
    ~Lan();
    Lan(const Lan&)=delete;
    Lan& operator=(const Lan&)=delete;
    bool host(std::string& error);
    bool join(const std::string& address,std::string& error);
    void close();
    void poll();
    bool send(const std::string& line);
    std::vector<std::string> receive();
    bool connected() const { return connected_; }
    bool listening() const { return listener_>=0; }
    const std::string& error() const { return error_; }
    static constexpr int port=28773;
private:
    int listener_=-1,peer_=-1;
    bool connected_=false,connecting_=false;
    std::string incoming_,outgoing_,error_;
    std::vector<std::string> messages_;
};
std::string localAddress();
}
