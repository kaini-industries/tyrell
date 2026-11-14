#include <tyrell/game/net/transport.hpp>
#include <enet/enet.h>
#include <algorithm>
#include <limits>
#include <new>
#include <stdexcept>
#include <unordered_map>

namespace tyrell::game::net {
namespace {
struct Runtime {
    bool ready=enet_initialize()==0;
    ~Runtime() { if(ready) enet_deinitialize(); }
};
bool runtime_ready() { static Runtime runtime;return runtime.ready; }
void validate(const Config& config) {
    if(config.max_peers<1 || config.max_peers>ENET_PROTOCOL_MAXIMUM_PEER_ID ||
       config.channels<1 || config.channels>ENET_PROTOCOL_MAXIMUM_CHANNEL_COUNT ||
       config.max_packet_bytes<1 || config.max_packet_bytes>65536 ||
       config.max_waiting_bytes<config.max_packet_bytes || config.max_waiting_bytes>64*1024*1024 ||
       config.max_queued_bytes<config.max_packet_bytes || config.max_queued_bytes>64*1024*1024 ||
       config.max_queued_packets<1 || config.max_queued_packets>65536 ||
       config.timeout_limit<1 || config.timeout_min_ms<1 || config.timeout_max_ms<config.timeout_min_ms)
        throw std::invalid_argument("Invalid bounded ENet transport configuration");
}
}
struct Transport::Impl {
    Config config;
    ENetHost* host=nullptr;
    std::unordered_map<PeerId,ENetPeer*> peers;
    std::unordered_map<ENetPeer*,PeerId> ids;
    PeerId next=1;
    std::size_t queued_bytes=0,queued_packets=0;
    std::string error;
    explicit Impl(Config value):config(value) {validate(config);(void)runtime_ready();}
    ~Impl() {close();}
    void close() {
        if(host) {
            for(std::size_t i=0;i<host->peerCount;++i) enet_peer_disconnect_now(&host->peers[i],0);
            enet_host_flush(host);enet_host_destroy(host);
        }
        host=nullptr;peers.clear();ids.clear();
    }
    void configure_host() {
        host->maximumPacketSize=config.max_packet_bytes;
        host->maximumWaitingData=config.max_waiting_bytes;
    }
    PeerId identify(ENetPeer* peer) {
        const auto found=ids.find(peer);
        if(found!=ids.end()) return found->second;
        if(next==kInvalidPeer) throw std::overflow_error("ENet peer identifier space exhausted");
        const auto id=next++;
        peers.emplace(id,peer);ids.emplace(peer,id);
        enet_peer_timeout(peer,config.timeout_limit,config.timeout_min_ms,config.timeout_max_ms);
        return id;
    }
    void forget(PeerId id) {
        const auto found=peers.find(id);
        if(found==peers.end()) return;
        ids.erase(found->second);peers.erase(found);
    }
    struct Pending { Impl* owner;std::size_t bytes; };
    static void released(ENetPacket* packet) noexcept {
        auto* pending=static_cast<Pending*>(packet->userData);
        pending->owner->queued_bytes-=pending->bytes;
        --pending->owner->queued_packets;
        delete pending;
    }
};
Transport::Transport(Config config):impl_(std::make_unique<Impl>(config)) {}
Transport::~Transport()=default;
bool Transport::host(std::uint16_t port) {
    impl_->close();impl_->error.clear();
    if(!runtime_ready()) {impl_->error="ENet initialization failed";return false;}
    ENetAddress address{};address.host=ENET_HOST_ANY;address.port=port;
    impl_->host=enet_host_create(&address,impl_->config.max_peers,impl_->config.channels,0,0);
    if(!impl_->host) {impl_->error="Could not bind UDP port "+std::to_string(port);return false;}
    impl_->configure_host();return true;
}
PeerId Transport::connect(const std::string& hostname,std::uint16_t port,std::uint32_t connect_data) {
    impl_->close();impl_->error.clear();
    if(!runtime_ready()) {impl_->error="ENet initialization failed";return kInvalidPeer;}
    ENetAddress address{};address.port=port;
    if(hostname.empty() || enet_address_set_host(&address,hostname.c_str())!=0) {
        impl_->error="Could not resolve host";return kInvalidPeer;
    }
    impl_->host=enet_host_create(nullptr,1,impl_->config.channels,0,0);
    if(!impl_->host) {impl_->error="Could not create client socket";return kInvalidPeer;}
    impl_->configure_host();
    auto* peer=enet_host_connect(impl_->host,&address,impl_->config.channels,connect_data);
    if(!peer) {impl_->close();impl_->error="Could not connect";return kInvalidPeer;}
    return impl_->identify(peer);
}
bool Transport::poll(Event& output) {
    if(!impl_->host) return false;
    ENetEvent event{};
    const auto result=enet_host_service(impl_->host,&event,0);
    if(result<=0) {
        if(result<0) impl_->error="ENet socket service failed";
        return false;
    }
    output.type=EventType::Disconnected;output.peer=kInvalidPeer;output.reliable=false;
    output.payload.clear();output.data=event.data;output.channel=event.channelID;
    const auto found=impl_->ids.find(event.peer);
    if(event.type==ENET_EVENT_TYPE_CONNECT) {
        output.type=EventType::Connected;output.peer=impl_->identify(event.peer);return true;
    }
    if(event.type==ENET_EVENT_TYPE_RECEIVE) {
        // ENet's maximumPacketSize already bounds incoming reassembly. Keep an
        // explicit boundary here too before allocating the application copy.
        const bool accepted=found!=impl_->ids.end() && event.packet &&
                            event.packet->dataLength<=impl_->config.max_packet_bytes;
        if(accepted) {
            output.type=EventType::Received;output.peer=found->second;
            output.reliable=(event.packet->flags&ENET_PACKET_FLAG_RELIABLE)!=0;
            try {if(event.packet->dataLength) output.payload.assign(event.packet->data,event.packet->data+event.packet->dataLength);}
            catch(...) {enet_packet_destroy(event.packet);throw;}
        }
        if(event.packet) enet_packet_destroy(event.packet);
        if(accepted) return true;
        impl_->error="Discarded invalid or oversized ENet packet";return false;
    }
    if(event.type==ENET_EVENT_TYPE_DISCONNECT && found!=impl_->ids.end()) {
        output.type=EventType::Disconnected;output.peer=found->second;
        impl_->forget(output.peer);return true;
    }
    return false;
}
bool Transport::send(PeerId peer,std::uint8_t channel,const std::vector<std::uint8_t>& payload,bool reliable) {
    const auto found=impl_->peers.find(peer);
    if(!impl_->host || found==impl_->peers.end()) {impl_->error="Unknown or closed peer";return false;}
    if(channel>=impl_->config.channels || payload.size()>impl_->config.max_packet_bytes) {
        impl_->error="Packet exceeds configured size or channel bounds";return false;
    }
    if(payload.size()>impl_->config.max_queued_bytes-impl_->queued_bytes ||
       impl_->queued_packets>=impl_->config.max_queued_packets) {
        impl_->error="Outgoing packet queue is full";return false;
    }
    auto* packet=enet_packet_create(payload.data(),payload.size(),reliable?ENET_PACKET_FLAG_RELIABLE:0);
    if(!packet) {impl_->error="Could not allocate ENet packet";return false;}
    auto* pending=new(std::nothrow) Impl::Pending{impl_.get(),payload.size()};
    if(!pending) {enet_packet_destroy(packet);impl_->error="Could not allocate ENet queue budget";return false;}
    packet->userData=pending;packet->freeCallback=Impl::released;
    impl_->queued_bytes+=payload.size();++impl_->queued_packets;
    if(enet_peer_send(found->second,channel,packet)!=0) {
        enet_packet_destroy(packet);impl_->error="Could not queue ENet packet";return false;
    }
    return true;
}
void Transport::disconnect(PeerId peer,std::uint32_t reason) {
    const auto found=impl_->peers.find(peer);
    if(found==impl_->peers.end()) return;
    enet_peer_disconnect_now(found->second,reason);impl_->forget(peer);
}
void Transport::flush() {if(impl_->host) enet_host_flush(impl_->host);}
void Transport::close() {impl_->close();}
bool Transport::active() const {return impl_->host!=nullptr;}
std::uint16_t Transport::port() const {return impl_->host?impl_->host->address.port:0;}
std::string Transport::error() const {return impl_->error;}
} // namespace tyrell::game::net
