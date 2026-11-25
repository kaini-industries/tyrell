#include "lan.hpp"
#include <cerrno>
#include <cstring>
#include <array>
#include <stdexcept>
#include <chrono>
#include <algorithm>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <fcntl.h>
#include <unistd.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <poll.h>
#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif
namespace chess {
namespace {
bool nonblocking(int fd) {
#ifdef SO_NOSIGPIPE
 int yes=1;setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&yes,sizeof(yes));
#endif
 return fcntl(fd,F_SETFL,O_NONBLOCK)>=0 && fcntl(fd,F_SETFD,FD_CLOEXEC)>=0;
}
bool privateAddress(uint32_t ip) {return (ip>>24)==10 || (ip>>24)==127 || (ip>>16)==0xc0a8 || (ip>>20)==0xac1;}
}
Lan::~Lan(){close();}
bool Lan::begin_session(){if(peer_>=0||listener_>=0)return false;sealed_=false;error_.clear();incoming_.clear();outgoing_.clear();messages_.clear();return true;}
void Lan::close(){if(peer_>=0)::close(peer_);if(listener_>=0)::close(listener_);peer_=listener_=-1;connected_=connecting_=false;incoming_.clear();outgoing_.clear();messages_.clear();}
bool Lan::host(std::string& error){
 if(sealed_){error="Previous LAN session ended";return false;}
 close();error_.clear();listener_=socket(AF_INET,SOCK_STREAM,0);int yes=1;
 if(listener_<0){error="Cannot create LAN socket";return false;}
 setsockopt(listener_,SOL_SOCKET,SO_REUSEADDR,&yes,sizeof(yes));
 sockaddr_in addr{};addr.sin_family=AF_INET;addr.sin_port=htons(port);addr.sin_addr.s_addr=htonl(INADDR_ANY);
 if(!nonblocking(listener_)||bind(listener_,reinterpret_cast<sockaddr*>(&addr),sizeof(addr))<0||listen(listener_,1)<0){error=std::strerror(errno);close();return false;}return true;
}
bool Lan::join(const std::string& address,std::string& error){
 if(sealed_){error="Previous LAN session ended";return false;}
 close();error_.clear();sockaddr_in addr{};addr.sin_family=AF_INET;addr.sin_port=htons(port);
 if(inet_pton(AF_INET,address.c_str(),&addr.sin_addr)!=1||!privateAddress(ntohl(addr.sin_addr.s_addr))){error="Enter a local IPv4 address";return false;}
 peer_=socket(AF_INET,SOCK_STREAM,0);if(peer_<0||!nonblocking(peer_)){error="Cannot create LAN socket";close();return false;}
 int result=connect(peer_,reinterpret_cast<sockaddr*>(&addr),sizeof(addr));
 if(result<0&&errno!=EINPROGRESS){error=std::strerror(errno);close();return false;}
 connecting_=result<0;connected_=result==0;return true;
}
bool Lan::send(const std::string& line){if(sealed_||!connected_||line.size()>256||line.find('\n')!=std::string::npos||outgoing_.size()+line.size()+1>8192)return false;outgoing_+=line+'\n';return true;}
void Lan::poll(){
 if(sealed_)return;
 if(listener_>=0&&peer_<0){sockaddr_in remote{};socklen_t n=sizeof(remote);int fd=accept(listener_,reinterpret_cast<sockaddr*>(&remote),&n);if(fd>=0){if(!privateAddress(ntohl(remote.sin_addr.s_addr))||!nonblocking(fd)){::close(fd);return;}peer_=fd;connected_=true;::close(listener_);listener_=-1;}}
 if(connecting_){pollfd fd{peer_,POLLOUT,0};if(::poll(&fd,1,0)>0){int code=0;socklen_t n=sizeof(code);if(getsockopt(peer_,SOL_SOCKET,SO_ERROR,&code,&n)<0||code){error_="Could not connect";close();return;}connecting_=false;connected_=true;}}
 if(!connected_)return;
 if(!outgoing_.empty()){auto n=::send(peer_,outgoing_.data(),outgoing_.size(),MSG_NOSIGNAL);if(n>0)outgoing_.erase(0,n);else if(n<0&&errno!=EAGAIN&&errno!=EWOULDBLOCK&&errno!=EINTR){error_="Connection lost";close();return;}}
 std::array<char,1024> buffer{};bool eof=false;
 for(int batch=0;batch<8;++batch){auto n=recv(peer_,buffer.data(),buffer.size(),0);if(n>0){incoming_.append(buffer.data(),n);if(incoming_.size()>4096){error_="Invalid peer data";close();return;}}else if(n==0){eof=true;break;}else {if(errno!=EAGAIN&&errno!=EWOULDBLOCK&&errno!=EINTR){error_="Connection lost";close();}break;}}
 size_t end;while((end=incoming_.find('\n'))!=std::string::npos){if(end>256||messages_.size()>=32){error_="Invalid peer message";close();return;}messages_.push_back(incoming_.substr(0,end));incoming_.erase(0,end+1);}
 if(eof){auto received=std::move(messages_);error_="Opponent disconnected";close();messages_=std::move(received);}
}
std::vector<std::string> Lan::receive(){if(sealed_)return {};auto out=std::move(messages_);messages_.clear();return out;}
std::string localAddress(){ifaddrs* list=nullptr;if(getifaddrs(&list)!=0)return "Wi-Fi unavailable";std::string out="Wi-Fi unavailable";for(auto* i=list;i;i=i->ifa_next){if(!i->ifa_addr||i->ifa_addr->sa_family!=AF_INET||(i->ifa_flags&IFF_LOOPBACK))continue;auto* a=reinterpret_cast<sockaddr_in*>(i->ifa_addr);if(privateAddress(ntohl(a->sin_addr.s_addr))){std::array<char,INET_ADDRSTRLEN> text{};inet_ntop(AF_INET,&a->sin_addr,text.data(),text.size());out=text.data();if(std::string(i->ifa_name).find("wl")==0)break;}}freeifaddrs(list);return out;}
}
