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
    // Sealing closes admission immediately without socket I/O. Only an
    // explicitly begun, fully closed session can reopen admission.
    void seal() { sealed_=true; }
    bool begin_session();
    void close();
    void poll();
    bool send(const std::string& line);
    std::vector<std::string> receive();
    bool connected() const { return !sealed_&&connected_; }
    bool listening() const { return !sealed_&&listener_>=0; }
    size_t pending_messages() const { return messages_.size(); }
    const std::string& error() const { return error_; }
    static constexpr int port=28773;
private:
    int listener_=-1,peer_=-1;
    bool connected_=false,connecting_=false,sealed_=false;
    std::string incoming_,outgoing_,error_;
    std::vector<std::string> messages_;
};
std::string localAddress();
}
